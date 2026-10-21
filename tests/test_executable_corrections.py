"""Regression of the six-run blockers, including real server routing boundaries."""
import copy
import json
import pytest
from backend import generation as g, generation_flow as flow, generation_fault_first as ff
from backend import generation_safeguards as guard, generation_corrections as corrections
from backend import generation_inventory_repair as inventory, generation_fault_recovery as recovery
from backend import generation_mock as mock, generation_roles as roles
from backend.generation_schema import Request


@pytest.fixture
def job(client):
    j=g.create(Request(requirement='正常对所有整数求和，空列表为零；过滤负数导致结果偏大是故障。'),
               direct_build=True,fault_first=True,demand_coverage=True,safeguards=True,executable_corrections=True)
    flow.call(j,'project_build');ff.install(j)
    flow.call(j,'spec_review');assert ff.apply_review(j)==guard.STAGE
    flow.call(j,guard.STAGE);assert guard.apply_standard(j)=='evaluation'
    flow.call(j,'evaluation')
    return j


def pending(job):
    c=copy.deepcopy(g.asset(job,'project_build'))
    c['contract']['behaviors']['sum']='正确累加所有整数，包含负数；空列表返回零。'
    folder=g.root(job)/('candidate-'+str(job['request_count']));folder.mkdir(exist_ok=True)
    job['spec_review_feedback']={'reason':'澄清正常求和规则','issues':[{'path':'/behaviors/sum','reason':'明确整数求和含负数。'}]}
    flow.save_candidate(job,'project_build',c,folder);g.put(job)
    return c


def acceptance(job):
    source,quote=next(iter(guard.context(job)['requirement_sources'].items()))
    return {'decision':'accept','reason':'本次修改符合原始求和要求，没有把故障结果改为正确标准。','evidence':[{'source':source,'quote':quote}]}


def test_acceptance_server_binds_and_idempotent_adoption(job):
    c=pending(job);old=job['contract_version'];raw=flow.call(job,guard.STAGE)
    assert raw['binding']==job['standard_candidate']['binding']
    assert job['attempts'][-1]['server_bound_standard_response']
    assert raw['correction']==''
    assert guard.apply_standard(job)=='spec_review'
    assert job['contract']==c['contract'] and job['contract_version']==old+1
    assert guard.apply_standard(job)=='spec_review' and job['contract_version']==old+1


def test_model_cannot_supply_binding_or_correction_on_accept(job):
    pending(job)
    for extra in ({'binding':{}},{'correction':'No correction needed.'}):
        with pytest.raises(ValueError):guard.SemanticReview.model_validate(acceptance(job)|extra)


def test_asset_changes_during_model_request_rejects_response(job,monkeypatch):
    pending(job);before=copy.deepcopy(job['contract'])
    def response(stage,payload):
        p=g.root(job)/job['standard_candidate']['asset']/'output.json'
        c=json.loads(p.read_text());c['contract']['behaviors']['sum']='正常也过滤负数再求和。';p.write_text(json.dumps(c))
        return acceptance(job)
    monkeypatch.setattr(mock,'role_response',response)
    with pytest.raises(ValueError,match='变化|过期'):flow.call(job,guard.STAGE)
    assert job['contract']==before


def test_scope_mismatch_returns_to_public_reviewer_without_widening_builder(job,monkeypatch):
    pending(job);before=copy.deepcopy(job['contract'])
    value=acceptance(job)|{'decision':'revise','issues':[{'path':'/symptom','reason':'认为当前症状描述涉及了内部算法细节。','requested_change':'由规范审查先判断该措辞是否属于必要修改。'}],'correction_target':'candidate'}
    monkeypatch.setattr(mock,'role_response',lambda *_:value)
    flow.call(job,guard.STAGE)
    assert guard.apply_standard(job)=='spec_review'
    assert 'spec_review_feedback' not in job
    assert job['standard_rejection']['scope_mismatch']['allowed_paths']==['/behaviors/sum']
    ctx=roles.context(job,'spec_review')
    assert ctx['adoption_rejection']['scope_mismatch']['requested_issues'][0]['path']=='/symptom'
    assert job['contract']==before


def test_reviewer_receives_runtime_and_symptom_boundaries(job):
    pending(job);ctx=guard.context(job)
    assert '独立受限容器' in ctx['runtime_facts']['per_scenario']
    assert 'symptom' in ctx['normal_behavior_policy']
    assert not {'files','private_fault_requirements','matrix'} & set(ctx)


def test_distinct_work_items_keep_retries_but_response_changes_do_not_reset(job):
    pending(job);reason={'category':'schema_shape','reason':'missing reason'}
    key=corrections.work_key(job,guard.STAGE)
    assert corrections.retry(job,guard.STAGE,reason)==guard.STAGE
    assert corrections.retry(job,guard.STAGE,reason|{'reason':'missing evidence'})==guard.STAGE
    with pytest.raises(recovery.Stop):corrections.retry(job,guard.STAGE,reason|{'reason':'different response'})
    saved=g.get(job['id']);assert saved['correction_work_items'][key]['retries']==2
    job['standard_candidate']['binding']['kind']='spec_approval'
    assert corrections.work_key(job,guard.STAGE)!=key
    assert corrections.retry(job,guard.STAGE,reason)==guard.STAGE


def invalid_inventory(job):
    raw=copy.deepcopy(g.asset(job,'spec_review'));raw['requirements'][0]['id']='requirement_'+'x'*45
    folder=g.root(job)/'invalid-inventory';folder.mkdir()
    with pytest.raises(ValueError) as exc:inventory.inspect_review(job,raw)
    inventory.rejected(job,raw,folder,{'category':'inventory_invalid','reason':str(exc.value)})
    g.put(job);return raw


def test_unapproved_invalid_id_rename_preserves_every_other_field(job):
    raw=invalid_inventory(job);old=raw['requirements'][0];binding=inventory.binding(job)
    patch={'binding':binding,'decision':'patch','reason':'只将未批准的非法名称缩短，其他义务和规则不变。',
           'rename_invalid_ids':[{'candidate_id':old['id'],'new_id':'valid_sum'}]}
    inventory.correction_schema(job).model_validate(patch)
    merged=inventory.merge(job,patch)
    assert merged['requirements'][0]==old|{'id':'valid_sum'}
    inventory.inspect_review(job,merged)


@pytest.mark.parametrize('kind',['approved','legal','collision','many_to_one'])
def test_id_rename_cannot_change_accepted_or_legal_ids_or_merge(job,kind):
    raw=invalid_inventory(job);old=raw['requirements'][0]['id'];name='valid_sum'
    if kind=='approved':
        from backend import generation_demands as dc
        job['demand_previous_requirements']=raw['requirements'];job['demand_previous_source_hash']=g.digest(dc.sources(job))
    if kind=='legal':
        raw['requirements'][0]['id']='already_legal';old='already_legal'
        (g.root(job)/job['inventory_correction']['asset']/'inventory-candidate.json').write_text(json.dumps(raw))
        job['inventory_correction']['candidate_hash']=g.digest(raw)
    if kind=='collision':name=old
    changes=[{'candidate_id':old,'new_id':name}]
    if kind=='many_to_one':changes.append({'candidate_id':'missing','new_id':name})
    with pytest.raises(ValueError):inventory.merge(job,{'binding':inventory.binding(job),'decision':'patch','reason':'不允许这种身份变更，不得改变已批准义务。','rename_invalid_ids':changes})


def supplement(job):
    quote=job['request']['requirement'];case=g.asset(job,'evaluation')['cases'][0]
    return {'code_hash':recovery.review_context(job)['code_hash'],'decision':'add_coverage','requirement_refs':[],
        'issue':None,'next_change':'追加另一个能观察负数过滤故障的隐藏输入，独立确定正确值。',
        'coverage_request':{'source':'request.requirement','quote':quote,'behavior_ids':['sum'],
            'insufficient_cases':[case['id']],'visibility':'hidden','trigger_condition':'选择包含负整数的不同多元素输入，正常行为仍累加所有整数。'}}


def test_trigger_gap_routes_to_append_without_leaking_code_or_altering_old_cases(job):
    before=copy.deepcopy(g.asset(job,'evaluation'));value=recovery.ExecutableReview.model_validate(supplement(job)).model_dump()
    recovery.validate_review(job,value)
    assert recovery.apply_review(job,value)=='evaluation'
    assert roles.schema(job,'evaluation') is guard.Additions
    ctx=roles.context(job,'evaluation')
    assert not {'files','private_fault_requirements','matrix','fault_review'} & set(ctx)
    assert ctx['demand_test_repair']['trigger_coverage_request']==value['coverage_request']
    assert 'code_aware' in ctx['demand_test_repair']['provenance']
    assert job['demand_test_repair']['evaluation']==before
    assert job['trigger_coverage_repairs']==1 and job['checkpoint']=='evaluation'


@pytest.mark.parametrize('bad',['private_source','unknown_case','unknown_behavior','frozen','capacity','budget'])
def test_supplement_bounds(job,bad):
    value=supplement(job)
    if bad=='private_source':value['coverage_request']['source']='private_fault_requirements'
    if bad=='unknown_case':value['coverage_request']['insufficient_cases']=['invented']
    if bad=='unknown_behavior':value['coverage_request']['behavior_ids']=['invented']
    if bad=='frozen':job['fault_freeze']={'hash':'frozen'}
    if bad=='capacity':
        evaluation=g.asset(job,'evaluation');evaluation['cases']*=3;ff.save_asset(job,'evaluation',evaluation)
    if bad=='budget':job['trigger_coverage_repairs']=2
    with pytest.raises(ValueError):recovery.apply_review(job,value)


def test_in_scope_rejection_updates_actual_work_order(job,monkeypatch):
    pending(job)
    value=acceptance(job)|{'decision':'revise','issues':[{'path':'/behaviors/sum','reason':'正常求和规则需要显式包括零和负数。','requested_change':'明确包含全部整数且空列表仍返回零，不改变原始目标。'}],'correction_target':'candidate'}
    monkeypatch.setattr(mock,'role_response',lambda *_:value)
    flow.call(job,guard.STAGE);assert guard.apply_standard(job)=='project_build'
    ctx=roles.context(job,'project_build')
    assert ctx['allowed_paths']==['/behaviors/sum']
    assert '明确包含全部整数' in ctx['review_feedback']['issues'][0]['reason']


def test_new_protocol_full_mock_pipeline_executes_docker(client):
    ok,reason=g.executor.availability()
    if not ok:pytest.skip(reason)
    j=g.create(Request(requirement='MOCK完整验证可执行纠正协议和受限Docker，不作为真实模型质量证据。'),
               direct_build=True,fault_first=True,demand_coverage=True,safeguards=True,executable_corrections=True)
    assert g.POOL.acquire(False)
    ff.run(j['id'],'project_build');done=g.get(j['id'])
    assert done['status']=='awaiting_review',done.get('error')
    assert done['matrix']['passed'] and done['matrix']['complete']
    assert all(c.get('repeat_count')==2 and c.get('stable') for c in done['matrix']['checks'])
    assert any(a.get('server_bound_standard_response') for a in done['attempts'])


def test_patch_entry_uses_same_work_item_scope_and_preserves_total_cap(job):
    from backend import generation_spec_patch as sp
    pending(job);job['spec_patch_attempts']={'legacy':3}
    for _ in range(3):sp.reserve(job)
    with pytest.raises(ValueError):sp.reserve(job)
    job['spec_review_feedback']['reason']='这是新审查授权的具体纠正任务。'
    sp.reserve(job)
    assert sorted(job['spec_patch_work_items'].values())==[1,3]
    for _ in range(2):sp.reserve(job)
    job['spec_review_feedback']['reason']='另一个任务也不能突破六次规范补丁总上限。'
    with pytest.raises(ValueError):sp.reserve(job)


def test_supplement_rejects_wrong_visibility_with_precise_feedback(job):
    request=supplement(job)['coverage_request'];recovery.apply_review(job,supplement(job))
    old=job['demand_test_repair']['evaluation'];c=copy.deepcopy(old['cases'][0])
    c.update(id='another_negative',input={'values':[-3,8]},expected=5,visibility='public',classification_reason='不同负数输入需要验证隐藏触发，不应只追加公开检查。')
    with pytest.raises(ff.Invalid) as error:guard.merge_additions(job,{'base_hash':g.digest(old),'added_cases':[c]})
    assert error.value.feedback['required_visibility']=='hidden'
    assert request['behavior_ids']==error.value.feedback['required_behaviors']


def test_persisted_review_suggestion_reaches_reanalysis_without_claiming_execution(job):
    proposal={'decision':'need_evidence','next_change':'已有隐藏输入不会触发，需追加新的隐藏负数输入。'}
    recovery.state(job)['reviews']['faulty']={'result':proposal}
    ctx=recovery.review_context(job)
    assert ctx['prior_review']==proposal
    assert '不是执行事实' in ctx['prior_review_policy']
    assert 'coverage_sources' in ctx and ctx['coverage_slots_remaining']>0


def test_requirement_change_during_call_rejects_old_decision(job,monkeypatch):
    pending(job);before=copy.deepcopy(job['contract'])
    def response(stage,payload):
        current=g.get(job['id']);current['request']['requirement']+=' 新增约束必须重新审核，旧决定不得继续使用。';g.put(current)
        return acceptance(job)
    monkeypatch.setattr(mock,'role_response',response)
    with pytest.raises(ValueError,match='变化|过期'):flow.call(job,guard.STAGE)
    assert job['contract']==before
    assert '新增约束必须重新审核' in g.get(job['id'])['request']['requirement']
