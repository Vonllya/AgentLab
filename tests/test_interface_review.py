import copy
import json
import pytest
from backend import generation as g, storage as s, generation_flow as flow
from backend import generation_fault_first as ff, generation_demands as dc
from backend import generation_interface_review as gate, generation_inventory_review as ir
from backend import generation_mock as mock, generation_fault_recovery as recovery
from backend.generation_schema import Request


@pytest.fixture
def job(tmp_path,monkeypatch):
    monkeypatch.setattr(s,'DATA',tmp_path/'data');monkeypatch.setenv('AGENT_MODE','mock');s.init()
    j=g.create(Request(requirement='空列表必须返回0，非空整数列表应正常求和。'),direct_build=True,fault_first=True,demand_coverage=True)
    j['interface_review_version']='interface-review-v1'
    flow.call(j,'project_build');ff.install(j);ff.save_asset(j,'evaluation',mock.response('evaluation',{}))
    j['demand_inventory']={'contract_hash':j['contract_hash'],'source_hash':g.digest(dc.sources(j)),
        'requirements':[{'id':'empty','source':'request.requirement','quote':'空列表必须返回0',
        'objective':'空列表应返回零，拒绝通过添加业务字段迎合错误规则。','kind':'boundary',
        'rules':[{'source':'expected','path':[],'op':'eq','value':999,'minimum':2}]}]}
    g.put(j);return j


def claim(j):
    return {'binding':dc.binding(j),'findings':[{'requirement_id':'empty','verdict':'interface_gap','witnesses':[],
        'reason':'错误规则与正确输出冲突，声称需要扩展业务接口才能匹配。'}], 'omissions':[],'challenges':[],
        'interface_proposals':[{'requirement_id':'empty','source':'request.requirement','quote':'空列表必须返回0',
            'current_fields':['/input_schema/properties/values'],'missing_behavior':'声称现有输出没有请求标识，因此不能验证空列表结果。',
            'why_rules_or_tests_insufficient':'声称规则语言不能表达动态对应，要求扩展项目接口。'}]}


def begin(j):
    ff.save_asset(j,dc.STAGE,claim(j));assert dc.apply_review(j)==ir.STAGE


def review(j,action='correct_rules'):
    return {'binding':gate.binding(j),'decisions':[{'requirement_id':'empty','action':action,
        'reason':'现有输入输出已经能表达空列表返回零，应纠正规则，不增加业务字段。',
        'replacement_rules':[{'source':'expected','path':[],'op':'eq','value':0,'minimum':2}] if action=='correct_rules' else [],
        'allowed_paths':['/behaviors/empty'] if action=='approve_interface' else []}]}


def adopt(j,action='correct_rules'):
    begin(j);ff.save_asset(j,ir.STAGE,review(j,action));return ir.apply(j)


def build(j):
    return {'binding':gate.build_binding(j),'decision':'patch','reason':'仅调整已授权规则说明并保留故障构造及其他资产。',
        'contract_changes':[{'path':'/behaviors/empty','before':j['contract']['behaviors']['empty'],
            'after':'空列表严格返回整数0。','reason':'仅明确已授权的空列表输出规则，不增加其他业务约束。'}],
        'code_changes':[],'public_evidence':[]}


def test_claim_does_not_rebuild_and_isolated_review_fixes_rule(job,monkeypatch):
    before=copy.deepcopy(job);begin(job)
    assert not job.get('design_rebuild') and not gate.building(job)
    seen=[];job['mode']='real'
    def complete(messages,**kw):
        seen.extend(messages);return {'content':json.dumps(review(job)),'_meta':{'usage':{'completion_tokens':50}}}
    monkeypatch.setattr(flow.agent,'completion',complete)
    flow.call(job,ir.STAGE);assert ir.apply(job)==dc.STAGE
    assert job['contract']==before['contract'] and ff.files(job)==ff.files(before)
    assert g.asset(job,'evaluation')==g.asset(before,'evaluation')
    assert job['demand_inventory']['requirements'][0]['rules'][0]['value']==0
    assert ir.apply(job)==dc.STAGE  # completed review reused after restart
    ctx=json.loads(seen[-1]['content'])
    assert not {'base_bundle','faulty','private_fault_requirements','matrix'} & set(ctx)
    assert 'approve_interface' in seen[0]['content']
    assert dc.context(job)['interface_review_resolution']['result']['decisions'][0]['action']=='correct_rules'
    with pytest.raises(ValueError):dc.require_approved(job)


@pytest.mark.parametrize('bad',['missing','quote','path','duplicate'])
def test_claim_requires_specific_evidence(job,bad):
    v=claim(job)
    if bad=='missing':v['interface_proposals']=[]
    if bad=='quote':v['interface_proposals'][0]['quote']='不是原始需求原文'
    if bad=='path':v['interface_proposals'][0]['current_fields']=['/private_fault_requirements']
    if bad=='duplicate':v['interface_proposals']*=2
    with pytest.raises(ValueError):dc.validate_review(job,v)


@pytest.mark.parametrize('bad',['binding','id','broad_path','private_path','same_rules','wrong_type','frozen'])
def test_review_guards(job,bad):
    begin(job);v=review(job)
    if bad=='binding':v['binding']['contract_hash']='bad'
    if bad=='id':v['decisions'][0]['requirement_id']='other'
    if bad in ('broad_path','private_path'):
        v=review(job,'approve_interface');v['decisions'][0]['allowed_paths']=['/input_schema' if bad=='broad_path' else '/private_fault_requirements']
    if bad=='same_rules':v['decisions'][0]['replacement_rules'][0]['value']=999
    if bad=='wrong_type':v['decisions'][0]['replacement_rules'][0]['value']='zero'
    if bad=='frozen':job['fault_freeze']={'frozen':True}
    with pytest.raises(ValueError):gate.validate(job,v)


def test_real_test_gap_routes_only_to_additive_test_repair(job):
    old=g.asset(job,'evaluation');assert adopt(job,'add_tests')=='evaluation'
    assert job['demand_test_repair']['evaluation']==old and not gate.building(job)
    with pytest.raises(ValueError):dc.validate_evaluation(job,{**old,'cases':old['cases'][1:]})


def test_approved_build_is_scoped_and_keeps_private_design(job,monkeypatch):
    assert adopt(job,'approve_interface')=='project_build';original=g.asset(job,'project_build')
    monkeypatch.setattr(mock,'role_response',lambda stage,ctx:build(job))
    flow.call(job,'project_build');ff.install(job)
    new=g.asset(job,'project_build')
    assert new['faulty']==original['faulty'] and new['private_fault_requirements']==original['private_fault_requirements']
    assert new['symptom_paths']==original['symptom_paths']
    assert new['contract']['behaviors']['empty']=='空列表严格返回整数0。'
    assert not gate.building(job) and job['interface_build_history']
    assert not job.get('demand_approval')


@pytest.mark.parametrize('bad',['binding','outside','before','newfile','private','double'])
def test_builder_cannot_expand_authorization(job,bad):
    adopt(job,'approve_interface');v=build(job)
    if bad=='binding':v['binding']['authorization_hash']='bad'
    if bad=='outside':v['contract_changes'][0]['path']='/input_schema/properties/request_id'
    if bad=='before':v['contract_changes'][0]['before']='not current'
    if bad=='newfile':v['code_changes']=[{'path':'secret.py','before':'x','after':'y','reason':'不得新增未授权任务文件或加载私有模块。'}]
    if bad=='private':v['private_fault_requirements']='changed'
    if bad=='double':v['contract_changes']*=2
    before=copy.deepcopy(job)
    with pytest.raises(ValueError):gate.merge_build(job,v)
    assert job==before


def test_builder_can_return_public_evidence_and_it_reaches_review(job):
    adopt(job,'approve_interface')
    v={'binding':gate.build_binding(job),'decision':'return_review','reason':'公开原文已经唯一确定空列表输出，接口修改没有必要。',
        'contract_changes':[],'code_changes':[],'public_evidence':[{'source':'request.requirement','quote':'空列表必须返回0'}]}
    with pytest.raises(ff.Invalid) as caught:gate.merge_build(job,v)
    assert recovery.handle(job,'project_build',caught.value,{'category':'interface_return'})==ir.STAGE
    ctx=gate.context(job)
    assert ctx['builder_public_return']==v['public_evidence'] and not gate.building(job)
    assert 'private_fault_requirements' not in ctx
    adopt_result=review(job,'correct_rules');ff.save_asset(job,ir.STAGE,adopt_result)
    assert ir.apply(job)==dc.STAGE


def test_old_jobs_keep_old_coverage_protocol(job):
    job.pop('interface_review_version');v=claim(job);v.pop('interface_proposals')
    ff.save_asset(job,dc.STAGE,v)
    assert dc.apply_review(job)=='project_build'
    assert job['design_rebuild'] and not gate.pending(job)


@pytest.mark.parametrize('action,kind',[('approve_interface','interface'),('add_tests','tests')])
def test_limits_do_not_consume_proposal_or_mutate_assets(job,action,kind):
    begin(job);job['demand_repairs']={kind:2};ff.save_asset(job,ir.STAGE,review(job,action))
    before=copy.deepcopy(job)
    with pytest.raises(recovery.Stop):ir.apply(job)
    assert job==before and gate.pending(job)


def test_approved_patch_preserves_code_indentation_and_rejects_stale_authority(job):
    adopt(job,'approve_interface');v=build(job)
    v['code_changes']=[{'path':'app.py','before':'    return total(data["values"])',
        'after':'    return total(data["values"])  # local adapter','reason':'测试局部代码适配保持缩进、文件集合及故障模块。'}]
    parsed=gate.merge_build(job,v)
    assert '    return total' in parsed['faulty']['app.py']
    assert parsed['faulty']['calculator.py']==ff.files(job)['calculator.py']
    job['demand_inventory']['requirements'][0]['rules'][0]['value']=111
    with pytest.raises(ValueError,match='过期'):gate.merge_build(job,v)


def test_builder_returned_review_remains_isolated_and_cannot_repeat_forever(job):
    adopt(job,'approve_interface')
    evidence=[{'source':'request.requirement','quote':'空列表必须返回0'}]
    first=ff.Invalid('保留详细退回说明用于作者审计',{'public_evidence':evidence},category='interface_return')
    gate.returned(job,first)
    assert '保留详细退回说明' not in json.dumps(gate.context(job),ensure_ascii=False)
    ff.save_asset(job,ir.STAGE,review(job,'approve_interface'));ir.apply(job)
    with pytest.raises(recovery.Stop):gate.returned(job,first)


def test_completed_review_resume_uses_saved_output_without_model(job,monkeypatch):
    begin(job);reply=review(job);monkeypatch.setattr(mock,'role_response',lambda stage,ctx:reply)
    flow.call(job,ir.STAGE);job['status']='interrupted';g.put(job)
    seen=[];monkeypatch.setattr(g,'launch',lambda ident,stage:seen.append(stage))
    ff.resume(job['id'])
    assert seen==[dc.STAGE]
    restored=g.get(job['id'])
    assert restored['demand_inventory']['requirements'][0]['rules'][0]['value']==0


@pytest.fixture
def job_v2(job):
    job['interface_review_version']=gate.VERSION
    return job


def review_v2(j,action='correct_rules'):
    v=review(j,'correct_rules' if action=='correct_rules_and_add_tests' else action)
    d=v['decisions'][0];d['action']=action
    fixing=action in ('correct_rules','correct_rules_and_add_tests')
    d['rule_assessments']=[{'rule_index':0,'judgment':'invalid' if fixing else 'valid',
        'resolution':'replace' if fixing else 'preserve',
        'reason':'比较当前规则与原文要求，返回999无法证明空列表正确返回零。','unresolved_reason':''}]
    d['missing_scenarios']=['缺少单场景中多个请求的逐项对应验证，应追加具有明确期望的新场景。'] if action in ('add_tests','correct_rules_and_add_tests') else []
    return v


def test_v2_repairs_rules_and_requests_tests_in_same_adoption(job_v2,monkeypatch):
    j=job_v2;begin(j);v=review_v2(j,'correct_rules_and_add_tests')
    monkeypatch.setattr(mock,'role_response',lambda stage,ctx:v)
    flow.call(j,ir.STAGE);assert ir.apply(j)=='evaluation'
    assert j['demand_inventory']['requirements'][0]['rules'][0]['value']==0
    assert j['demand_test_repair']['findings'][0]['missing_scenarios']
    assert not gate.building(j)
    assert ir.apply(j)=='evaluation'


@pytest.mark.parametrize('bad',['only_tests','no_scenario','missing_assessment','duplicate','outside','unchanged_error','change_valid','contradiction'])
def test_v2_requires_every_acknowledged_problem_to_be_handled(job_v2,bad):
    j=job_v2
    if bad=='change_valid':j['demand_inventory']['requirements'][0]['rules'].append({'source':'input','path':['values'],'op':'length_eq','value':0,'minimum':2})
    begin(j);v=review_v2(j);d=v['decisions'][0]
    if bad=='only_tests':
        d.update(action='add_tests',replacement_rules=[],missing_scenarios=['需要追加多个请求在同一输入中的决策对应场景。'])
    if bad=='no_scenario':v=review_v2(j,'add_tests');v['decisions'][0]['missing_scenarios']=[]
    if bad=='missing_assessment':d.pop('rule_assessments')
    if bad=='duplicate':d['rule_assessments']*=2
    if bad=='outside':d['rule_assessments'][0]['rule_index']=1
    if bad=='unchanged_error':d['replacement_rules'][0]['value']=999
    if bad=='change_valid':
        d['rule_assessments'].append({'rule_index':1,'judgment':'valid','resolution':'preserve','reason':'该输入为空的约束正确，需原样保留以维护原要求。','unresolved_reason':''})
    if bad=='contradiction':d['rule_assessments'][0].update(judgment='valid',resolution='replace')
    before=copy.deepcopy(j)
    with pytest.raises(ValueError):gate.validate(j,v)
    assert j==before


def test_v2_unresolved_has_specific_reason_and_cannot_progress(job_v2):
    j=job_v2;begin(j);v=review_v2(j,'keep_rules');d=v['decisions'][0]
    d['action']='unresolved';d['rule_assessments'][0].update(judgment='uncertain',resolution='defer',unresolved_reason='缺少如何计算该输出的明确公开规则，无法给出可靠替换。')
    ff.save_asset(j,ir.STAGE,v);before=copy.deepcopy(j['demand_inventory'])
    with pytest.raises(recovery.Stop,match='缺少如何计算'):ir.apply(j)
    assert j['demand_inventory']==before and gate.pending(j) and not gate.building(j)


def test_v2_old_payload_is_rejected_with_actionable_missing_fields(job_v2,monkeypatch):
    j=job_v2;begin(j);old=review(j,'add_tests');seen=[];j['mode']='real'
    def complete(messages,**kwargs):
        seen.append(messages);value=old if len(seen)==1 else review_v2(j)
        return {'content':json.dumps(value),'_meta':{'usage':{'completion_tokens':10}}}
    monkeypatch.setattr(flow.agent,'completion',complete)
    with pytest.raises(ValueError) as caught:flow.call(j,ir.STAGE)
    error=flow.failure(j,ir.STAGE,caught.value)
    assert 'rule_assessments' in error['reason']
    recovery.handle(j,ir.STAGE,caught.value,error)
    flow.call(j,ir.STAGE);ir.apply(j)
    assert 'rule_assessments' in seen[1][-1]['content']
    assert j['demand_inventory']['requirements'][0]['rules'][0]['value']==0


def test_v1_saved_decisions_and_schema_remain_unchanged(job):
    begin(job);assert gate.review_schema(job) is gate.Review
    assert 'rule_assessments' not in gate.review_schema(job).model_json_schema()['$defs']['Decision']['properties']
    assert 'rule_assessments' not in gate.instructions(job)
    assert gate.validate(job,review(job,'add_tests'))['decisions'][0]['action']=='add_tests'
