import copy
import json
import pytest
from backend import generation as g, generation_flow as flow, generation_fault_first as ff
from backend import generation_roles as roles, generation_mock as mock, generation_demands as dc
from backend import generation_safeguards as guard, generation_inventory_review as ir
from backend.generation_schema import Request


@pytest.fixture
def job(client):
    j=g.create(Request(requirement='空列表必须返回0，非空整数列表正常求和；负数被过滤是故障。'),
               direct_build=True,fault_first=True,demand_coverage=True,safeguards=True)
    flow.call(j,'project_build');ff.install(j)
    flow.call(j,'spec_review')
    assert ff.apply_review(j)==guard.STAGE
    flow.call(j,guard.STAGE);assert guard.apply_standard(j)=='evaluation'
    flow.call(j,'evaluation')
    return j


def start_append(j):
    old=copy.deepcopy(g.asset(j,'evaluation'))
    j['demand_test_repair']={'contract_hash':j['contract_hash'],'evaluation':old,'findings':[]}
    g.put(j);return old


def new_case(j):
    case=copy.deepcopy(g.asset(j,'evaluation')['cases'][0])
    case.update(id='new_sum',input={'values':[8,2]},expected=10)
    return case


def standard(j,decision='accept'):
    ctx=guard.context(j);source,quote=next(iter(ctx['requirement_sources'].items()))
    return {'binding':ctx['binding'],'decision':decision,
            'reason':'依据原文判断正常行为，不将过滤负数这一故障反转为正确验收标准。',
            'evidence':[{'source':source,'quote':quote}],
            'correction':'candidate.requirements只能验证正常求和；故障症状归私有设计，不应要求正常expected排除负数。' if decision=='revise' else ''}


def coverage(j,verdict='covered',witnesses=None):
    return {'binding':dc.binding(j),'findings':[{'requirement_id':'mock_mechanics', 'verdict':verdict,
              'witnesses':witnesses if witnesses is not None else ['empty'],
              'reason':'检查空输入边界是否有实际固定输入与正确期望，不以标签证明覆盖。'}],
            'omissions':[],'challenges':[],'interface_proposals':[],'gap_resolutions':[]}


def test_append_call_only_accepts_additions_preserves_old_assets(job,monkeypatch):
    old=start_append(job);before=copy.deepcopy(old)
    payload={'base_hash':g.digest(old),'added_cases':[new_case(job)]}
    seen=[]
    def response(stage,ctx):seen.append(ctx);return payload
    monkeypatch.setattr(mock,'role_response',response)
    value=flow.call(job,'evaluation')
    assert value['cases'][:-1]==before['cases']
    assert value['evasion_checks']==before['evasion_checks']
    assert roles.schema(job,'evaluation') is guard.Additions
    assert seen[0]['append_only']['base_hash']==g.digest(old)
    assert not {'faulty','reference','private_fault_requirements','matrix'} & set(seen[0])
    patch=json.loads((g.root(job)/job['assets']['evaluation']/'patch.json').read_text())
    assert set(patch)=={'base_hash','added_cases'}


@pytest.mark.parametrize('bad',['empty','long_id','duplicate_id','duplicate_data','stale','rewrite_old'])
def test_append_failure_feedback_reaches_next_model(job,monkeypatch,bad):
    old=start_append(job);raw={'base_hash':g.digest(old),'added_cases':[new_case(job)]}
    if bad=='empty':raw['added_cases']=[]
    if bad=='long_id':raw['added_cases'][0]['id']='a'*100
    if bad=='duplicate_id':raw['added_cases'][0]['id']=old['cases'][0]['id']
    if bad=='duplicate_data':raw['added_cases']=[old['cases'][0]|{'id':'another_id'}]
    if bad=='stale':raw['base_hash']='0'*64
    if bad=='rewrite_old':raw['cases']=old['cases']
    monkeypatch.setattr(mock,'role_response',lambda *_:raw)
    with pytest.raises(ValueError) as exc:flow.call(job,'evaluation')
    reason=flow.failure(job,'evaluation',exc.value)
    assert reason.get('details') or reason.get('validation_feedback')
    ctx=roles.context(job,'evaluation')
    assert ctx.get('schema_error') or ctx.get('previous_error') or ctx.get('validation_feedback')
    assert ctx['coverage_feedback']['mode']=='append_only'
    assert ctx['coverage_feedback']['base_case_count']==len(old['cases'])
    assert 'missing_behaviors' not in ctx['coverage_feedback']
    assert ctx['append_only']['remaining_slots']==10-len(old['cases'])
    assert g.asset(job,'evaluation')==old


def test_public_candidate_cannot_install_until_review_accepts(job,monkeypatch):
    original=copy.deepcopy(job['contract']);assets=copy.deepcopy(job['assets']);version=job['contract_version']
    candidate=copy.deepcopy(g.asset(job,'project_build'));candidate['contract']['behaviors']['sum']='正常返回所有整数之和，包括负数；空列表返回0。'
    directory=g.root(job)/'candidate-test';directory.mkdir()
    flow.save_candidate(job,'project_build',candidate,directory)
    assert job['contract']==original and job['assets']==assets
    ctx=guard.context(job)
    assert ctx['candidate']==candidate['contract']
    assert not {'faulty','reference','private_fault_requirements','matrix','files'} & set(ctx)
    monkeypatch.setattr(mock,'role_response',lambda *_:standard(job))
    flow.call(job,guard.STAGE);assert guard.apply_standard(job)=='spec_review'
    assert job['contract']==candidate['contract'] and job['contract_version']==version+1
    assert ff.files(job)==candidate['faulty']


def test_rejection_preserves_standards_and_reaches_owner(job,monkeypatch):
    original=copy.deepcopy(job['contract']);before=g.digest(ff.files(job))
    job.pop('standard_approval')
    flow.call(job,'spec_review');assert ff.apply_review(job)==guard.STAGE
    monkeypatch.setattr(mock,'role_response',lambda *_:standard(job,'revise'))
    flow.call(job,guard.STAGE);assert guard.apply_standard(job)=='spec_review'
    assert job['contract']==original and g.digest(ff.files(job))==before
    assert roles.context(job,'spec_review')['adoption_rejection']['correction']
    assert not job.get('standard_candidate')


def test_erroneous_review_request_returns_to_reviewer_not_builder(job,monkeypatch):
    original=copy.deepcopy(job['contract']);candidate=copy.deepcopy(g.asset(job,'project_build'))
    candidate['contract']['behaviors']['sum']='正常也过滤负数再累加。'
    job['spec_review_feedback']={'reason':'错误要求正常也过滤负数','issues':[{'path':'/behaviors/sum','reason':'错误地要求正常标准描述故障算法。'}]}
    folder=g.root(job)/'wrong-request';folder.mkdir();flow.save_candidate(job,'project_build',candidate,folder)
    raw=standard(job,'revise');raw['correction_target']='review_request'
    monkeypatch.setattr(mock,'role_response',lambda *_:raw)
    flow.call(job,guard.STAGE);assert guard.apply_standard(job)=='spec_review'
    assert job['contract']==original and 'spec_review_feedback' not in job
    assert roles.context(job,'spec_review')['adoption_rejection']['correction_target']=='review_request'


def test_standard_binding_citations_and_bounded_rejections(job,monkeypatch):
    job.pop('standard_approval')
    flow.call(job,'spec_review');assert ff.apply_review(job)==guard.STAGE
    raw=standard(job);raw['binding']['contract_hash']='0'*64
    with pytest.raises(ff.Invalid,match='过期'):guard.validate_standard(job,raw)
    raw=standard(job);raw['evidence'][0]['quote']='not in original source'
    with pytest.raises(ff.Invalid,match='连续原文'):guard.validate_standard(job,raw)
    monkeypatch.setattr(mock,'role_response',lambda *_:standard(job,'revise'))
    for _ in range(2):
        flow.call(job,guard.STAGE);guard.apply_standard(job)
        guard.begin(job,'spec_approval',job['assets']['spec_review'])
    flow.call(job,guard.STAGE)
    with pytest.raises(ValueError,match='两次'):guard.apply_standard(job)
    assert g.get(job['id'])['checkpoint']=='spec_review'
    assert not g.get(job['id']).get('standard_candidate')


def test_standard_completed_review_resume_no_duplicate_request(job,monkeypatch):
    job.pop('standard_approval')
    flow.call(job,'spec_review');assert ff.apply_review(job)==guard.STAGE
    flow.call(job,guard.STAGE);count=job['request_count']
    job.update(status='interrupted',checkpoint=guard.STAGE);g.put(job)
    seen=[];monkeypatch.setattr(g,'launch',lambda id,stage:seen.append(stage))
    ff.resume(job['id']);restored=g.get(job['id'])
    assert seen==['coverage_review'] and restored['request_count']==count
    assert not restored.get('standard_candidate')
    assert guard.apply_standard(restored)=='coverage_review'
    assert restored['request_count']==count


def test_corrected_inventory_rules_are_provisional(job,monkeypatch):
    item=job['demand_inventory']['requirements'][0];item['rules'][0]['value']=999
    old=copy.deepcopy(job['demand_inventory'])
    job['inventory_dispute']={'binding':dc.binding(job),'challenges':[{'requirement_id':item['id'],'rule_indices':[0],'case_ids':[],
        'source':item['source'],'quote':item['quote'],'reason':'空列表应返回0，不应以999作为正确求和结果。'}]}
    raw={'binding':ir.binding(job),'decisions':[{'requirement_id':item['id'],'verdict':'correct_rules',
         'reason':'依据原文将空列表输出规则恢复为零，不改需求引用或其他规则。','replacement_rules':[item['rules'][0]|{'value':0}]}]}
    ff.save_asset(job,ir.STAGE,raw);assert ir.apply(job)==guard.STAGE
    assert job['demand_inventory']==old
    assert guard.context(job)['current_requirements']==old['requirements']
    flow.call(job,guard.STAGE);assert guard.apply_standard(job)==dc.STAGE
    assert item['rules'][0]['value']==0


def test_open_gap_cannot_disappear_and_closed_report_revalidates(job):
    raw=coverage(job,'test_gap',[]);ff.save_asset(job,dc.STAGE,raw);dc.apply_review(job)
    gap=guard.open_gaps(job)[0]
    assert gap in roles.context(job,'evaluation')['open_gaps']
    fixed=coverage(job)
    with pytest.raises(ff.Invalid,match='尚未逐项'):dc.validate_review(job,fixed)
    source,quote=next(iter(dc.sources(job).items()))
    closure={'gap_id':gap['id'],'decision':'covered','witnesses':['empty'], 'source':source,'quote':quote,
             'reason':'补充新的空输入观察，确认正常输出为零，保持旧有场景不变。'}
    fixed['gap_resolutions']=[closure]
    with pytest.raises(ff.Invalid,match='未提供新增'):dc.validate_review(job,fixed)
    ev=copy.deepcopy(g.asset(job,'evaluation'));new=copy.deepcopy(ev['cases'][0]);new.update(id='added_observation',input={'values':[0]},expected=0)
    ev['cases'].append(new);ff.save_asset(job,'evaluation',ev)
    fixed=coverage(job,witnesses=['added_observation']);closure['witnesses']=['added_observation'];fixed['gap_resolutions']=[closure]
    ff.save_asset(job,dc.STAGE,fixed);assert dc.apply_review(job)=='fingerprint'
    assert not guard.open_gaps(job)
    dc.require_approved(job);dc.require_approved(g.get(job['id']))


def test_out_of_scope_requires_explicit_source_and_explanation(job):
    ff.save_asset(job,dc.STAGE,coverage(job,'test_gap',[]));dc.apply_review(job)
    gap=guard.open_gaps(job)[0];raw=coverage(job);source,quote=next(iter(dc.sources(job).items()))
    raw['gap_resolutions']=[{'gap_id':gap['id'],'decision':'out_of_scope','witnesses':[], 'source':source,'quote':quote,
        'reason':'原先发现错误地增加了需求外条件；当前仅验证公开整数求和，不要求外部存储。'}]
    ff.save_asset(job,dc.STAGE,raw);assert dc.apply_review(job)=='fingerprint';dc.require_approved(job)
    duplicate=copy.deepcopy(raw);duplicate['gap_resolutions']*=2
    with pytest.raises(ff.Invalid,match='重复'):dc.validate_review(job,duplicate)


def test_rejected_inventory_returns_to_definition_owner_with_identity_protection(job,monkeypatch):
    original_mock=mock.role_response
    item=job['demand_inventory']['requirements'][0];original=copy.deepcopy(job['demand_inventory'])
    job['inventory_dispute']={'binding':dc.binding(job),'challenges':[{'requirement_id':item['id'],'rule_indices':[0],
        'case_ids':[],'source':item['source'],'quote':item['quote'],'reason':'质疑正常目标与故障表现混用，要求复核其定义与规则。'}]}
    proposal={'binding':ir.binding(job),'decisions':[{'requirement_id':item['id'],'verdict':'correct_rules',
        'reason':'此提案用于验证拒绝后的定义修复入口，不作为正确语义样本。','replacement_rules':[item['rules'][0]|{'value':1}]}]}
    ff.save_asset(job,ir.STAGE,proposal);assert ir.apply(job)==guard.STAGE
    raw=standard(job,'revise');raw['correction']='正常清单目标不能要求故障表现；保留原需求身份，将目标和证据改回正确行为。'
    monkeypatch.setattr(mock,'role_response',lambda *_:raw)
    flow.call(job,guard.STAGE);assert guard.apply_standard(job)=='spec_review'
    assert job['demand_inventory']==original and not job.get('inventory_dispute')
    ctx=roles.context(job,'spec_review')
    assert ctx['adoption_rejection']['correction']==raw['correction']
    assert ctx['previous_requirements']==original['requirements']
    assert 'standard_approval' not in job
    with pytest.raises(ff.Invalid):dc.validate_inventory(job,[])
    fixed=copy.deepcopy(original['requirements']);fixed[0]['objective']='依据原需求修正错误目标为正常求和，不将故障当作正确行为。'
    dc.validate_inventory(job,fixed)
    assert job['standard_review_history'][-1]['original_dispute']['challenges']
    # A follow-up identity error enters the existing local-correction branch;
    # that early-return context must keep the semantic rejection as well.
    def bad_identity(stage,ctx):
        value=original_mock(stage,ctx)
        if stage=='spec_review':value['requirements'][0]['id']='renamed_candidate'
        return value
    monkeypatch.setattr(mock,'role_response',bad_identity)
    with pytest.raises(ff.Invalid):flow.call(job,'spec_review')
    local=roles.context(job,'spec_review')
    assert local['adoption_rejection']['correction']==raw['correction']
    assert local['adoption_rejection']['inventory_scope']['may_correct']==['objective','rules']


def test_resolved_or_stale_rejection_does_not_keep_guiding_models(job,monkeypatch):
    job.pop('standard_approval');flow.call(job,'spec_review');ff.apply_review(job)
    raw=standard(job,'revise');monkeypatch.setattr(mock,'role_response',lambda *_:raw)
    flow.call(job,guard.STAGE);guard.apply_standard(job)
    assert guard.rejection_context(job)
    saved=copy.deepcopy(job['standard_rejection'])
    job['requirement_answers']=[{'id':'clarification','answers':{'scope':'新的用户澄清改变原始依据。'}}]
    assert guard.rejection_context(job) is None and job['standard_rejection']==saved
    job.pop('requirement_answers')
    guard.begin(job,'spec_approval',job['assets']['spec_review'])
    accepted=standard(job);monkeypatch.setattr(mock,'role_response',lambda *_:accepted)
    flow.call(job,guard.STAGE);guard.apply_standard(job)
    assert 'standard_rejection' not in job and job['standard_review_history']


def test_pending_standards_block_execution_and_author_can_see_audit(job):
    ff.save_asset(job,dc.STAGE,coverage(job));dc.apply_review(job)
    source=job['assets']['spec_review'];job.pop('standard_approval');guard.begin(job,'spec_approval',source)
    with pytest.raises(ff.Invalid,match='尚未采纳'):dc.require_approved(job)
    assert ff.next_checkpoint(job)==guard.STAGE
    audit=g.review_assets(job['id'])
    assert audit['standard_candidate']==job['standard_candidate']
    assert audit['standard_review_history']==job['standard_review_history']
    assert 'standard_review_history' not in g.public(job)


def test_new_versions_keep_protocol_and_legacy_records_stay_legacy(job):
    from backend import storage as s
    task='gen_'+job['id'];s.put('task',task,{'version':'1.0.0'})
    job.update(status='published',published_task=task);g.put(job)
    child=g.get(g.fork(job['id'])['id'])
    assert guard.enabled(child) and dc.enabled(child)
    assert not guard.enabled(g.create(Request(requirement='旧任务保持原版本协议不自动升级'),direct_build=True,fault_first=True))


def test_mock_pipeline_new_protocol_actually_executes_docker(client):
    ok,reason=g.executor.availability()
    if not ok:pytest.skip(reason)
    j=g.create(Request(requirement='MOCK 离线验证标准采纳入口和Docker状态机，不作为真实模型质量证据。'),
               direct_build=True,fault_first=True,demand_coverage=True,safeguards=True)
    assert g.POOL.acquire(False)
    ff.run(j['id'],'project_build');done=g.get(j['id'])
    assert done['status']=='awaiting_review',done.get('error')
    assert done['matrix']['passed']
    assert done['standard_review_history'] and done['assets'].get(guard.STAGE)
    assert done['request_count']<=24
