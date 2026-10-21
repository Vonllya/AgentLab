import copy
import json
import pytest
from backend import storage as s, generation as g, generation_flow as flow
from backend import generation_fault_first as ff, generation_demands as dc
from backend import generation_inventory_review as ir, generation_mock as mock
from backend import generation_roles as roles
from backend.generation_schema import Request


@pytest.fixture
def job(tmp_path, monkeypatch):
    monkeypatch.setattr(s,'DATA',tmp_path/'data');monkeypatch.setenv('AGENT_MODE','mock');s.init()
    j=g.create(Request(requirement='空列表必须返回0，非空整数列表应正常求和。'),direct_build=True,fault_first=True,demand_coverage=True)
    flow.call(j,'project_build');ff.install(j)
    ff.save_asset(j,'evaluation',mock.response('evaluation',{}))
    rules=[{'source':'input','path':['values'],'op':'length_eq','value':0,'minimum':2},
           {'source':'expected','path':[],'op':'eq','value':0,'minimum':2}]
    j['demand_inventory']={'contract_hash':j['contract_hash'],'source_hash':g.digest(dc.sources(j)),
      'requirements':[{'id':'empty','source':'request.requirement','quote':'空列表必须返回0',
       'objective':'检查空输入时的正确返回值，不添加原需求外条件。','kind':'boundary','rules':rules}]}
    g.put(j);return j


def coverage(j, challenge=True):
    return {'binding':dc.binding(j),'findings':[{'requirement_id':'empty',
        'verdict':'inventory_issue' if challenge else 'covered',
        'witnesses':[] if challenge else [next(c['id'] for c in g.asset(j,'evaluation')['cases'] if c['input']=={'values':[]})],
        'reason':'对照空输入规则及原始要求审查，不把质疑当作已证实事实。'}],
        'omissions':[], 'challenges':[{'requirement_id':'empty','source':'request.requirement','quote':'空列表必须返回0',
          'rule_indices':[1],'case_ids':[],'reason':'当前规则的返回值与原始需求不一致，请独立判断是否需要纠正。'}] if challenge else []}


def begin(j):
    raw=coverage(j);ff.save_asset(j,dc.STAGE,raw)
    assert dc.apply_review(j)==ir.STAGE
    return raw


def decision(j, verdict):
    return {'binding':ir.binding(j),'decisions':[{'requirement_id':'empty','verdict':verdict,
        'reason':'原文明确空列表返回零，应根据原文判断原规则，不迎合错误测试。',
        'replacement_rules':copy.deepcopy(j['demand_inventory']['requirements'][0]['rules']) if verdict=='correct_rules' else []}]}


def test_valid_challenge_repairs_only_questioned_rule_through_actual_call(job,monkeypatch):
    job['demand_inventory']['requirements'][0]['rules'][1]['value']=999
    before=copy.deepcopy(job['demand_inventory']);ev=copy.deepcopy(g.asset(job,'evaluation'));code=ff.files(job)
    raw=begin(job)
    assert job['demand_inventory']==before and not job.get('demand_approval')
    response=decision(job,'correct_rules');response['decisions'][0]['replacement_rules'][1]['value']=0
    seen=[];job['mode']='real'
    def complete(messages,**kwargs):
        seen.extend(messages);return {'content':json.dumps(response),'_meta':{'usage':{'completion_tokens':10}}}
    monkeypatch.setattr(flow.agent,'completion',complete)
    flow.call(job,ir.STAGE);assert ir.apply(job)==dc.STAGE
    assert job['demand_inventory']['requirements'][0]['rules'][1]['value']==0
    assert job['inventory_dispute_history'][-1]['previous_inventory']==before
    assert g.asset(job,'evaluation')==ev and ff.files(job)==code
    ctx=json.loads(seen[-1]['content'])
    assert not {'files','private_fault_requirements','matrix','build','evaluation'} & set(ctx)
    assert ctx['challenges']==raw['challenges'] and not ctx['execution_performed']
    with pytest.raises(ff.Invalid):dc.require_approved(job)
    ff.save_asset(job,dc.STAGE,coverage(job,False));assert dc.apply_review(job)=='fingerprint'
    dc.require_approved(job)


def test_false_challenge_rejected_and_feedback_reaches_coverage_model(job,monkeypatch):
    before=copy.deepcopy(job['demand_inventory']);original=begin(job)
    response=decision(job,'keep_rules')
    monkeypatch.setattr(mock,'role_response',lambda stage,ctx:response)
    flow.call(job,ir.STAGE);ir.apply(job)
    assert job['demand_inventory']==before
    ctx=roles.context(job,dc.STAGE)
    assert ctx['inventory_dispute_resolution']['decisions'][0]['verdict']=='keep_rules'
    with pytest.raises(ff.Invalid,match='已被驳回'):dc.validate_review(job,original)
    seen=[];job['mode']='real';response2=coverage(job,False)
    def complete(messages,**kwargs):
        seen.extend(messages);return {'content':json.dumps(response2),'_meta':{'usage':{'completion_tokens':10}}}
    monkeypatch.setattr(flow.agent,'completion',complete)
    flow.call(job,dc.STAGE);dc.apply_review(job)
    assert 'inventory_dispute_resolution' in seen[-1]['content'] and 'keep_rules' in seen[-1]['content']
    assert job['demand_inventory']==before;dc.require_approved(job)


@pytest.mark.parametrize('bad',['binding','unknown_id','no_change','unquestioned_rule','invalid_type'])
def test_reviewer_cannot_override_boundaries(job,bad):
    job['demand_inventory']['requirements'][0]['rules'][1]['value']=999
    begin(job);raw=decision(job,'correct_rules');raw['decisions'][0]['replacement_rules'][1]['value']=0
    if bad=='binding':raw['binding']['inventory_hash']='0'*64
    if bad=='unknown_id':raw['decisions'][0]['requirement_id']='other'
    if bad=='no_change':raw['decisions'][0]['replacement_rules'][1]['value']=999
    if bad=='unquestioned_rule':raw['decisions'][0]['replacement_rules'][0]['value']=1
    if bad=='invalid_type':raw['decisions'][0]['replacement_rules'][1]['value']='zero'
    before=copy.deepcopy(job['demand_inventory'])
    with pytest.raises(ValueError):ir.validate(job,raw)
    assert job['demand_inventory']==before


@pytest.mark.parametrize('bad',['missing','index','case','source','frozen'])
def test_challenge_validation_and_freeze(job,bad):
    raw=coverage(job)
    if bad=='missing':raw['challenges']=[]
    if bad=='index':raw['challenges'][0]['rule_indices']=[-1]
    if bad=='case':raw['challenges'][0]['case_ids']=['not_present']
    if bad=='source':raw['challenges'][0]['quote']='不存在的原始需求引文'
    if bad=='frozen':job['fault_freeze']={'exists':True}
    with pytest.raises(ValueError):
        ff.save_asset(job,dc.STAGE,raw);dc.apply_review(job)


def test_restart_reuses_completed_review_and_apply_is_idempotent(job,monkeypatch):
    begin(job);ff.save_asset(job,ir.STAGE,decision(job,'keep_rules'))
    job.update(status='interrupted',checkpoint=ir.STAGE,attempts=[{'stage':ir.STAGE,'status':'completed'}]);g.put(job)
    seen=[];monkeypatch.setattr(g,'launch',lambda id,stage:seen.append(stage) or {})
    ff.resume(job['id']);assert seen==[dc.STAGE]
    restored=g.get(job['id']);assert ir.apply(restored)==dc.STAGE
    assert len(restored['inventory_dispute_history'])==1


def test_old_jobs_do_not_accept_new_dispute_schema(job):
    job.pop('inventory_review_version')
    with pytest.raises(ValueError):dc.validate_review(job,coverage(job))


def test_review_input_only_contains_questioned_items_and_unambiguous_actions(job):
    other=copy.deepcopy(job['demand_inventory']['requirements'][0]);other['id']='not_questioned'
    job['demand_inventory']['requirements'].append(other)
    raw=coverage(job)
    raw['findings'].append({'requirement_id':'not_questioned','verdict':'test_gap','witnesses':[],
                           'reason':'未在本轮重新确认此项，不能将它当成本次质疑目标。'})
    ff.save_asset(job,dc.STAGE,raw);dc.apply_review(job)
    ctx=roles.context(job,ir.STAGE)
    assert ctx['allowed_requirement_ids']==['empty']
    assert [d['id'] for d in ctx['requirements']]==['empty']
    for ambiguous in ('accept','reject'):
        response=decision(job,'keep_rules');response['decisions'][0]['verdict']=ambiguous
        with pytest.raises(ValueError):ir.validate(job,response)
    response=decision(job,'keep_rules');response['decisions'][0]['requirement_id']='not_questioned'
    with pytest.raises(ff.Invalid) as caught:ir.validate(job,response)
    assert caught.value.feedback['extra_ids']==['not_questioned']
    assert caught.value.feedback['missing_ids']==['empty']
