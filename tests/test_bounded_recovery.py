"""Routing, evidence delivery and finite output recovery from real failure classes."""
import copy
import json
import pytest
from backend import generation as g, generation_flow as flow, generation_fault_first as ff
from backend import generation_safeguards as guard, generation_demands as dc
from backend import generation_inventory_review as ir, generation_interface_review as interface
from backend import generation_fault_recovery as recovery, generation_recovery_policy as rp
from backend import generation_direct as direct, generation_mock as mock, generation_roles as roles
from backend import generation_budget as budget
from backend.generation_schema import Request


@pytest.fixture
def job(client):
    j=g.create(Request(requirement='所有整数必须正常求和，包含负数；空列表返回零。'),
        direct_build=True,fault_first=True,demand_coverage=True,safeguards=True,
        executable_corrections=True,recovery_policy=True)
    flow.call(j,'project_build');ff.install(j)
    flow.call(j,'spec_review');ff.apply_review(j)
    flow.call(j,guard.STAGE);guard.apply_standard(j)
    flow.call(j,'evaluation')
    return j


def pending_rules(j):
    item=j['demand_inventory']['requirements'][0]
    j['inventory_dispute']={'binding':dc.binding(j),'review_asset':'coverage-source',
        'challenges':[{'requirement_id':item['id'],'source':item['source'],'quote':item['quote'],
            'rule_indices':[0], 'case_ids':[], 'reason':'请独立确认现有输出规则能否观察完整求和行为。'}]}
    raw={'binding':ir.binding(j),'decisions':[{'requirement_id':item['id'],'verdict':'correct_rules',
        'reason':'依照原始求和要求修正规则，并保留未被质疑部分。',
        'replacement_rules':copy.deepcopy(item['rules'])}]}
    ff.save_asset(j,ir.STAGE,raw);guard.begin(j,'inventory_rules',j['assets'][ir.STAGE])
    return raw


def reject(j,path):
    source,quote=next(iter(dc.sources(j).items()))
    v={'binding':j['standard_candidate']['binding'],'decision':'revise',
       'reason':'候选规则尚未准确表达对应需求，须根据具体位置纠正。',
       'evidence':[{'source':source,'quote':quote}],
       'correction':'需要按指定位置修订候选，不修改正确期望和其他资产。',
       'issues':[{'path':path,'reason':'该位置与原始要求存在需要纠正的具体差异。',
                  'requested_change':'依据原始需求重新定位有效字段并保留其余约定。'}]}
    ff.save_asset(j,guard.STAGE,v)
    return v


@pytest.mark.parametrize('path',['/decisions/0/replacement_rules','candidate.decisions[0].replacement_rules'])
def test_rule_rejection_returns_to_creator_with_all_feedback(job,path):
    pending_rules(job);before=copy.deepcopy(job['demand_inventory']);code=ff.files(job)
    value=reject(job,path)
    assert guard.apply_standard(job)==ir.STAGE
    restored=g.get(job['id']);ctx=roles.context(restored,ir.STAGE)
    assert ctx['challenges']==job['inventory_dispute']['challenges']
    assert ctx['adoption_rejection']['issues']==value['issues']
    assert ctx['witness_policy']==rp.WITNESS_POLICY
    assert restored['demand_inventory']==before and ff.files(restored)==code
    assert guard.apply_standard(restored)==ir.STAGE


def test_requirement_rejection_returns_to_spec_preserving_identity(job):
    pending_rules(job);reject(job,'/current_requirements/0/objective')
    before=copy.deepcopy(job['demand_inventory']['requirements'])
    assert guard.apply_standard(job)=='spec_review'
    assert job['demand_previous_requirements']==before
    assert job['standard_rejection']['repair_owner']=='requirement'


def test_interface_rejection_proposes_independent_review_without_granting_edit(job):
    pending_rules(job);reject(job,'/input_schema/properties')
    before=copy.deepcopy(job['contract'])
    assert guard.apply_standard(job)==ir.STAGE
    assert not interface.pending(job) and not interface.building(job)
    assert job.get('inventory_dispute') and job['contract']==before
    ctx=roles.context(job,ir.STAGE)
    assert ctx['adoption_rejection']['repair_owner']=='interface'
    assert ctx['adoption_rejection']['issues'][0]['path']=='/input_schema/properties'


def test_unknown_rejection_path_is_not_silent_spec_fallback(job):
    pending_rules(job);raw=reject(job,'/arbitrary/command')
    with pytest.raises(ff.Invalid,match='纠正资产') as exc:guard.validate_standard(job,raw)
    assert '/decisions/0/replacement_rules' in exc.value.feedback['allowed_issue_paths']
    assert job.get('standard_candidate') and job.get('inventory_dispute')


def test_decoded_field_quote_is_valid_without_json_escapes_but_splicing_is_not(job):
    raw=pending_rules(job)
    quote='正常记录步骤为 ["prepare", "finish"]，路径名保持原文。'
    raw['decisions'][0]['reason']=quote
    ff.save_asset(job,ir.STAGE,raw);guard.begin(job,'inventory_rules',job['assets'][ir.STAGE])
    value=reject(job,'/decisions/0/replacement_rules')
    value['evidence']=[{'source':'candidate','quote':quote}]
    assert guard.validate_standard(job,value)['decision']=='revise'
    job.pop('recovery_policy_version')
    with pytest.raises(ff.Invalid,match='连续原文'):guard.validate_standard(job,value)
    job['recovery_policy_version']=rp.VERSION
    value['evidence'][0]['quote']='正常记录步骤为 '+raw['decisions'][0]['requirement_id']
    with pytest.raises(ff.Invalid,match='连续原文'):guard.validate_standard(job,value)
    assert not rp.quoted_field({'a':'连续原文前半段','b':'连续原文后半段'},'前半段连续原文后半段')


@pytest.mark.parametrize('kind',['inventory_rules','public_patch'])
def test_maximum_valid_issues_survive_internal_envelope_and_actual_handoff(job,monkeypatch,kind):
    original_mock=mock.role_response
    if kind=='inventory_rules':
        pending_rules(job);path='/decisions/0/replacement_rules';target=ir.STAGE
    else:
        candidate=copy.deepcopy(g.asset(job,'project_build'))
        candidate['contract']['behaviors']['sum']='正常处理全部整数并正确求和，空列表为零。'
        job['spec_review_feedback']={'reason':'明确完整整数求和的公开规则。',
            'issues':[{'path':'/behaviors/sum','reason':'需要补充完整求和行为的确定性定义。'}]}
        folder=g.root(job)/'public-candidate';folder.mkdir()
        flow.save_candidate(job,'project_build',candidate,folder)
        path='/behaviors/sum';target='project_build'
    source,quote=next(iter(dc.sources(job).items()))
    issues=[{'path':path,'reason':('具体问题'+str(n))*160,
             'requested_change':('完整修订要求'+str(n))*140} for n in range(8)]
    issues=[{**i,'reason':i['reason'][:800],'requested_change':i['requested_change'][:1000]} for i in issues]
    response={'decision':'revise','reason':'八项要求各自满足模型schema上限，内部旧摘要不能误拒这些合法字段。',
        'evidence':[{'source':source,'quote':quote}],'issues':issues,'correction_target':'candidate'}
    monkeypatch.setattr(mock,'role_response',lambda *_:response)
    raw=flow.call(job,guard.STAGE)
    assert raw['issues']==issues and len(raw['correction'])<=1600
    assert guard.apply_standard(job)==target
    captured=[];job['mode']='real'
    def completion(messages,**kwargs):
        captured.extend(messages)
        if kind=='inventory_rules':
            result={'binding':ir.binding(job),'decisions':[{'requirement_id':job['demand_inventory']['requirements'][0]['id'],
                'verdict':'keep_rules','reason':'仅验证完整拒绝意见实际交接，不把此夹具当作语义裁决依据。','replacement_rules':[]}]}
        else:result=original_mock('project_build',roles.context(job,'project_build'))
        return {'content':json.dumps(result),'_meta':{'usage':{'completion_tokens':10}}}
    monkeypatch.setattr(flow.agent,'completion',completion)
    flow.call(job,target)
    payload=json.loads(captured[-1]['content'])
    assert payload['adoption_rejection']['issues']==issues
    assert all(i['requested_change'] in captured[-1]['content'] for i in issues)
    if kind=='public_patch':
        compiled=payload['review_feedback']['issues']
        assert [i['reason'] for i in compiled]==[i['requested_change'] for i in issues]
        assert len(compiled)==8 and all(len(i['reason'])<=1200 for i in compiled)


def test_all_bad_citation_sources_are_distinguished_from_bad_quotes(job):
    pending_rules(job);raw=reject(job,'/decisions/0/replacement_rules')
    raw['evidence']=[{'source':'candidate decisions/0/replacement_rules','quote':'不能把字段路径附加到来源键'},
                     {'source':'current_public_specification.behaviors','quote':'第二处来源也应同时指出'},
                     {'source':'request.requirement','quote':'这句话没有出现在用户原始需求中'}]
    with pytest.raises(ff.Invalid) as error:guard.validate_standard(job,raw)
    details=error.value.feedback
    assert [e['field'] for e in details['citation_errors']]==['evidence[0].source','evidence[1].source','evidence[2].quote']
    assert [e['kind'] for e in details['citation_errors']]==['unknown_source','unknown_source','non_contiguous_quote']
    assert details['available_sources']==['candidate','current_public_specification','request.requirement']
    assert all(e['action'] for e in details['citation_errors'])


def test_explicit_new_budget_resets_only_child_limits_and_is_idempotent(job,monkeypatch):
    from backend.app import Regenerate
    keys=('correction_work_items','spec_patch_work_items','public_spec_recovery','output_recoveries','output_recovery')
    for key in keys:job[key]={'exhausted':True}
    job.update(status='no_progress',checkpoint='coverage_review',error={'category':'output_limit','reason':'原批次额度耗尽'})
    g.put(job);before=g.get(job['id']);launched=[]
    monkeypatch.setattr(g,'launch',lambda id,stage:launched.append((id,stage)))
    body=Regenerate(expected_revision=job['revision'],idempotency_key='new-budget-reset-123',
        new_batch=True,budget=budget.BudgetPolicy(requests=5))
    first=flow.regenerate(job['id'],body);second=flow.regenerate(job['id'],body)
    child=g.get(first['id'])
    assert first['id']==second['id'] and len(launched)==1
    assert all(key not in child for key in keys)
    assert g.get(job['id'])==before and child['inherited_failure']==before['error']
    assert g.asset(child,'evaluation')==g.asset(before,'evaluation')
    assert child['budget']['requests']==0
    rp.reserve_spec_revision(child,{'issues':[{'path':'/simulation','reason':'新授权批次应能接收明确的公开规范修订。'}]})
    assert g.get(child['id'])['public_spec_recovery']['count']==1
    assert g.get(job['id'])==before


def test_ordinary_resume_preserves_all_batch_limits(job,monkeypatch):
    keys=('correction_work_items','spec_patch_work_items','public_spec_recovery','output_recoveries','output_recovery')
    for key in keys:job[key]={'exhausted':True}
    job.update(status='interrupted',checkpoint='coverage_review');g.put(job)
    seen=[];monkeypatch.setattr(g,'launch',lambda id,stage:seen.append(stage))
    ff.resume(job['id'])
    saved=g.get(job['id'])
    assert seen==['coverage_review'] and all(saved[key]==job[key] for key in keys)


def test_inventory_creator_can_return_missing_interface_without_empty_rules(job):
    pending_rules(job);job.pop('standard_candidate')
    item=job['demand_inventory']['requirements'][0]
    raw={'binding':ir.binding(job),'decision':'interface_issue','proposals':[{
        'requirement_id':item['id'],'source':item['source'],'quote':item['quote'],
        'current_fields':['/input_schema/properties'],
        'missing_behavior':'当前接口缺少表达原始要求所需的操作与可观察结果。',
        'why_rules_or_tests_insufficient':'仅修改字段选择或追加同类输入无法表达缺失的操作。'}]}
    before=copy.deepcopy(job['contract']);ir.validate(job,raw)
    ff.save_asset(job,ir.STAGE,raw)
    assert ir.apply(job)==ir.STAGE and interface.pending(job)
    assert job['contract']==before and not interface.building(job)
    assert len(job['inventory_interface_returns'])==1
    assert ir.apply(job)==ir.STAGE  # Restart must not apply this return as an interface decision.


def test_interface_return_can_target_only_the_affected_requirement(job):
    pending_rules(job);job.pop('standard_candidate')
    item=job['demand_inventory']['requirements'][0]
    other=copy.deepcopy(item);other['id']='other_requirement'
    job['demand_inventory']['requirements'].append(other)
    second=copy.deepcopy(job['inventory_dispute']['challenges'][0]);second['requirement_id']=other['id']
    job['inventory_dispute']['challenges'].append(second)
    job['inventory_dispute']['binding']=dc.binding(job)
    raw={'binding':ir.binding(job),'decision':'interface_issue','proposals':[{
        'requirement_id':item['id'],'source':item['source'],'quote':item['quote'],
        'current_fields':['/input_schema/properties'],
        'missing_behavior':'只有当前这一项需要接口支持，其他质疑仍需后续独立核对。',
        'why_rules_or_tests_insufficient':'当前接口无法表达这一项要求，但不能伪称其他项也缺接口。'}]}
    ir.validate(job,raw);ff.save_asset(job,ir.STAGE,raw);ir.apply(job)
    assert [p['requirement_id'] for p in job['interface_proposal']['proposals']]==[item['id']]
    assert len(job['inventory_interface_returns'][-1]['dispute']['challenges'])==2
    assert dc.context(job)['prior_inventory_challenges'][0]['requirement_id']==other['id']


@pytest.mark.parametrize('path',['#/input_schema/properties/values','#/properties/values','/properties/values'])
def test_interface_path_schema_and_feedback_expose_the_existing_protocol(job,path):
    raw={'requirement_id':job['demand_inventory']['requirements'][0]['id'],
        'source':'request.requirement','quote':job['request']['requirement'],'current_fields':[path],
        'missing_behavior':'检查当前接口是否能够表达原始需求所要求的完整行为。',
        'why_rules_or_tests_insufficient':'只测试当前接口已有内容不能证明所提出的缺失行为。'}
    for schema in (interface.Proposal,ir.InterfaceProposal):
        with pytest.raises(ValueError,match='input_schema'):schema.model_validate(raw)
        pattern=schema.model_json_schema()['properties']['current_fields']['items']['pattern']
        assert pattern=='^/(input_schema|output_schema)/'
    with pytest.raises(ff.Invalid) as error:
        interface.validate_proposals(job,{'findings':[{'requirement_id':raw['requirement_id'],'verdict':'interface_gap'}],
                                          'interface_proposals':[raw]})
    assert error.value.feedback['allowed_prefixes']==['/input_schema/','/output_schema/']
    assert '去掉#' in error.value.feedback['action'] and error.value.feedback['existing_examples']


def test_spec_revisions_are_scope_bounded_persistent_and_finite(job):
    for n,path in enumerate(['/simulation','/input_domain','/constraints','/exclusions']):
        job['assets']['spec_review']=f'review-{n}'
        rp.reserve_spec_revision(job,{'issues':[{'path':path,'reason':'需要补充明确的公开正常行为规则。'}]})
        job=g.get(job['id'])
    assert job['public_spec_recovery']['count']==4
    job['assets']['spec_review']='review-5'
    with pytest.raises(recovery.Stop):rp.reserve_spec_revision(job,{'issues':[{'path':'/scenario','reason':'新位置也不能超过总上限。'}]})


def test_changed_prose_does_not_reset_scope_or_duplicate_signature(job):
    for n in range(2):
        job['assets']['spec_review']=f'review-{n}'
        rp.reserve_spec_revision(job,{'issues':[{'path':'/simulation','reason':f'不同描述{n}，仍是同一个修改范围。'}]})
    job['assets']['spec_review']='review-3'
    with pytest.raises(recovery.Stop):rp.reserve_spec_revision(job,{'issues':[{'path':'/simulation','reason':'换措辞不能重置次数。'}]})
    assert g.get(job['id'])['public_spec_recovery']['count']==2


def test_public_schema_descriptions_are_citable_but_not_auto_approved(job):
    job['contract']['output_schema']['description']='结果严格等于输入values中全部整数之和，空列表返回零。'
    raw=[{'path':'','quote':job['contract']['output_schema']['description'],'explanation':'明确给出了正确结果的确定性计算方法。'}]
    assert direct.review_evidence_errors(job['contract'],raw)
    assert not direct.review_evidence_errors(job['contract'],raw,True)
    raw[0]['quote']='这个不存在的描述不能伪造为公开证据'
    assert direct.review_evidence_errors(job['contract'],raw,True)
    ctx=roles.context(job,'spec_review')
    assert '/output_schema/description' in ctx['public_rule_sources']
    assert not {'files','private_fault_requirements','matrix'} & set(ctx)


def limited(job,monkeypatch,always=False):
    job['mode']='real';seen=[]
    def completion(messages,**kwargs):
        seen.append((copy.deepcopy(messages),kwargs))
        if len(seen)==1 or always:
            raise flow.agent.ModelFailure('output_limit','输出预算耗尽',
                {'finish_reason':'length','parse_status':'parsed','usage':{'completion_tokens':kwargs['output_limit']}})
        return {'content':json.dumps(mock.role_response('coverage_review',roles.context(job,'coverage_review'))),
            '_meta':{'usage':{'completion_tokens':100},'finish_reason':'stop'}}
    monkeypatch.setattr(flow.agent,'completion',completion)
    return seen


def test_output_recovery_success_keeps_assets_and_does_not_charge_business_retries(job,monkeypatch):
    seen=limited(job,monkeypatch);before=copy.deepcopy(job['assets']);policy=copy.deepcopy(job['budget']['policy'])
    with pytest.raises(flow.agent.ModelFailure) as error:flow.call(job,'coverage_review')
    reason=flow.failure(job,'coverage_review',error.value)
    assert recovery.handle(job,'coverage_review',error.value,reason)=='coverage_review'
    job=g.get(job['id']);flow.call(job,'coverage_review')
    assert seen[1][1]['output_limit']==2*seen[0][1]['output_limit']
    ctx=json.loads(seen[1][0][-1]['content'])
    assert ctx['requirements']==job['demand_inventory']['requirements']
    assert ctx['evaluation']==g.asset(job,'evaluation') and ctx['output_recovery']
    assert not job.get('stage_corrections') and not job.get('output_recovery')
    assert all(job['assets'][k]==v for k,v in before.items()) and job['budget']['policy']==policy
    assert job['attempts'][-1]['output_recovery_of']==job['attempts'][-2]['asset']
    assert job['budget']['output_charged']==sum(a.get('charged_tokens',0) for a in job['attempts'])


def test_output_recovery_final_failure_never_promotes_partial_response(job,monkeypatch):
    limited(job,monkeypatch,True);before=copy.deepcopy(job['assets'])
    for n in range(2):
        with pytest.raises(flow.agent.ModelFailure) as error:flow.call(job,'coverage_review')
        reason=flow.failure(job,'coverage_review',error.value)
        if n==0:recovery.handle(job,'coverage_review',error.value,reason)
        else:
            with pytest.raises(recovery.Stop,match='未形成有效审查结果'):recovery.handle(job,'coverage_review',error.value,reason)
    assert job['assets']==before and not job.get('demand_approval')
    assert not job.get('stage_corrections')


def test_output_recovery_honors_shared_budget(job,monkeypatch):
    limited(job,monkeypatch)
    with pytest.raises(flow.agent.ModelFailure) as error:flow.call(job,'coverage_review')
    recovery.handle(job,'coverage_review',error.value,flow.failure(job,'coverage_review',error.value))
    job['budget']['requests']=job['budget']['policy']['requests']
    with pytest.raises(budget.Exhausted):flow.call(job,'coverage_review')


def test_invalid_recovery_response_returns_concrete_error_to_normal_correction(job,monkeypatch):
    limited(job,monkeypatch)
    with pytest.raises(flow.agent.ModelFailure) as error:flow.call(job,'coverage_review')
    recovery.handle(job,'coverage_review',error.value,flow.failure(job,'coverage_review',error.value))
    monkeypatch.setattr(flow.agent,'completion',lambda *a,**k:{'content':'{}','_meta':{'usage':{'completion_tokens':2}}})
    with pytest.raises(ValueError) as error:flow.call(job,'coverage_review')
    assert not job.get('output_recovery')
    reason=flow.failure(job,'coverage_review',error.value)
    assert recovery.handle(job,'coverage_review',error.value,reason)=='coverage_review'
    from backend.generation_stage_recovery import correction_context
    assert correction_context(job,'coverage_review')['error']['category']=='schema_shape'


def test_output_recovery_does_not_consume_another_public_patch_attempt(job,monkeypatch):
    from backend import generation_spec_patch as sp
    job['spec_review_feedback']={'reason':'补充已授权的正常求和字段规则。',
        'issues':[{'path':'/behaviors/sum','reason':'明确该字段按照全部整数求和，包含负数。'}]}
    job['mode']='real';captured=[]
    def completion(messages,**kwargs):
        captured.append(messages)
        if len(captured)==1:
            raise flow.agent.ModelFailure('output_limit','输出耗尽',{'usage':{'completion_tokens':kwargs['output_limit']}})
        raw=mock.role_response('project_build',roles.context(job,'project_build'))
        return {'content':json.dumps(raw),'_meta':{'usage':{'completion_tokens':100}}}
    monkeypatch.setattr(flow.agent,'completion',completion)
    with pytest.raises(flow.agent.ModelFailure) as error:flow.call(job,'project_build')
    counts=copy.deepcopy(job['spec_patch_work_items'])
    recovery.handle(job,'project_build',error.value,flow.failure(job,'project_build',error.value))
    flow.call(job,'project_build')
    assert job['spec_patch_work_items']==counts and sum(counts.values())==1
    assert job.get('standard_candidate')


def test_new_api_enables_policy_without_migrating_existing_jobs(client):
    response=client.post('/api/generation/jobs',json={'requirement':'本地模拟正常求和与负数过滤故障。'})
    assert response.status_code==200
    assert rp.enabled(g.get(response.json()['id']))
    old=g.create(Request(requirement='旧协议显式创建继续保留原有行为。'),
        direct_build=True,fault_first=True,demand_coverage=True,safeguards=True,executable_corrections=True)
    assert not rp.enabled(old)


def test_legacy_job_keeps_original_policy(job):
    job.pop('recovery_policy_version')
    assert ir.review_schema(job) is ir.Review
    assert not rp.enabled(job)


def test_new_policy_full_mock_pipeline_executes_docker(client):
    ok,reason=g.executor.availability()
    if not ok:pytest.skip(reason)
    j=g.create(Request(requirement='MOCK用于验证恢复协议与Docker编排，不代表真实生成质量。'),
        direct_build=True,fault_first=True,demand_coverage=True,safeguards=True,
        executable_corrections=True,recovery_policy=True)
    assert g.POOL.acquire(False)
    ff.run(j['id'],'project_build');done=g.get(j['id'])
    assert done['status']=='awaiting_review',done.get('error')
    assert done['matrix']['passed'] and all(c.get('repeat_count')==2 for c in done['matrix']['checks'])
