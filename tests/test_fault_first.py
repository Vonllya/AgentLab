import copy,json
from pathlib import Path
import pytest
from pydantic import ValidationError
from backend import generation as g,generation_fault_first as ff,generation_flow as flow,generation_roles as roles,generation_budget as budget,generation_protocol as protocol
from backend.generation_schema import Request


def prepared():
    j=g.create(Request(requirement='训练负数过滤造成总和错误'),direct_build=True,fault_first=True)
    flow.call(j,'project_build');ff.install(j);flow.call(j,'spec_review');ff.apply_review(j)
    flow.call(j,'evaluation');flow.call(j,'fingerprint');return j


def test_fault_only_first_and_independent_contexts(client):
    j=prepared();assert 'build' not in j['assets'] and 'fixed_candidate' not in j['assets']
    assert set(g.asset(j,'project_build'))=={'assessment','rationale','questions','contract','faulty','private_fault_requirements','symptom_paths'}
    for stage in ('evaluation','spec_review','fingerprint'):
        ctx=roles.context(j,stage);serialized=json.dumps(ctx)
        assert 'def total(' not in serialized and 'actual' not in ctx
    assert 'private_fault_requirements' not in roles.context(j,'evaluation')
    with pytest.raises(ff.Invalid,match='未验收'):roles.messages(j,'fix_patch')
    assert g.get(j['id'])['fault_first_version']==ff.VERSION


def test_patch_binding_and_path_validation(client):
    j=prepared();base=ff.files(j)
    p={'base_hash':g.digest(base),'decision':'patch','changes':[{'path':'calculator.py','before':'sum(v for v in values if v >= 0)','after':'sum(values)','reason':'恢复算术和，保留全部输入行为。'}],'explanation':'补丁只修正负数参与累加，不修改固定规范。'}
    fixed=ff.apply_patch(j,p);assert fixed['app.py']==base['app.py']
    for changes in ({'base_hash':'0'*64},{'changes':[dict(p['changes'][0],path='../secret.py')]},{'changes':[dict(p['changes'][0],before='missing-code')]}):
        with pytest.raises(ff.Invalid) as e:ff.apply_patch(j,{**p,**changes})
        assert e.value.feedback
    with pytest.raises(ValidationError):ff.Patch.model_validate({**p,'normal':fixed})
    assert ff.files(j)==base


def test_fingerprint_cannot_use_correct_value_or_auxiliary_path(client):
    j=prepared();raw={'checks':[{'case_id':'negative','behavior_id':'sum','reason':'错误过滤负数使算术和偏大。','assertions':[{'path':[],'equals':2}]},{'case_id':'multiple','behavior_id':'sum','reason':'负数造成算术和主要差异。','assertions':[{'path':[],'equals':2}]}]}
    with pytest.raises(ff.Invalid,match='主要故障'):ff.validate_response(j,'fingerprint',raw)
    raw['checks'][0]['assertions'][0]['equals']=3;raw['checks'][0]['behavior_id']='empty'
    with pytest.raises(ff.Invalid) as e:ff.validate_response(j,'fingerprint',raw)
    assert e.value.feedback['field']=='checks[0]'


def test_budget_and_legacy_compatibility(client):
    old=g.create(Request(requirement='训练启动工具注册故障'),direct_build=True,handoff=True,fault_model=True)
    assert not ff.enabled(old)
    from backend import generation_direct as direct
    flow.call(old,'project_build');direct.install_bundle(old)
    assert all(k in g.asset(old,'build') for k in ('normal','faulty','reference'))
    j=prepared();j['budget']=budget.initialize(budget.BudgetPolicy(requests=1));budget.reserve(j,'fix_patch',100)
    with pytest.raises(budget.Exhausted):budget.reserve(j,'fix_patch',100)


@pytest.mark.skipif(not g.executor.availability()[0],reason='Docker不可用，不降级宿主')
def test_fault_first_docker_patch_and_freeze(client):
    j=prepared();before=copy.deepcopy(g.asset(j,'evaluation'));faulty=copy.deepcopy(ff.files(j))
    m=ff.execute_phase(j,'faulty');assert m['passed'] and m['complete']
    assert all(r['repeat_count']==2 for r in m['checks'])
    assert {r['visibility'] for r in m['checks'] if r['fault_match']}=={'public','hidden'}
    flow.call(j,'fix_patch');assert ff.files(j)==faulty
    ff.execute_phase(j,'fixed');flow.call(j,'evasions');g.validate(j)
    ff.verify_fixed(j);assert j['matrix']['passed']
    assert g.asset(j,'evaluation')==before
    assert g.asset(j,'build')['faulty']==faulty
    old_hash=j['fault_freeze']['binding']['faulty_hash']
    save=copy.deepcopy(g.asset(j,'fixed_candidate'));save['files']['calculator.py']='def total(values):\n return 999\n'
    ff.save_asset(j,'fixed_candidate',save)
    with pytest.raises(ValueError,match='快照'):ff.verify_fixed(j)
    assert j['fault_freeze']['binding']['faulty_hash']==old_hash


def test_api_new_jobs_opt_in_without_rewriting_history(client):
    j=client.post('/api/generation/jobs',json={'requirement':'启动就绪探测丢失重试'}).json()
    assert j['fault_first_version']==ff.VERSION
    assert j['handoff_version'] is None


def test_design_conflict_does_not_force_opposite_fault(client):
    j=prepared()
    from backend.generation_handoff import NeedsReview
    value=ff.FaultResponse.model_validate({'decision':'design_conflict','checks':[],'explanation':'正常规范要求未就绪时为空，私有设计也仅要求为空，无法构成主要行为差异。'}).model_dump()
    with pytest.raises(NeedsReview,match='无法区分'):ff.validate_response(j,'fingerprint',value)
    assert 'fixed_candidate' not in j['assets']


def test_failed_partial_or_modified_matrix_cannot_freeze_or_publish(client):
    j=prepared();j['status']='awaiting_review';j['matrix']={'id':'fake','passed':True};g.put(j)
    with pytest.raises(ff.Invalid):g.publish(j['id'],True,'核对不存在的执行证据不能批准','0'*64)
    j['fault_freeze']={'binding':ff.binding(j),'matrix_id':'fake','environment':'fake'}
    ff.save_asset(j,'fault_acceptance',{'id':'fake','passed':False,'complete':False})
    with pytest.raises(ValueError):ff.require_frozen(j)


def test_completed_checkpoint_does_not_repeat_paid_call(client,monkeypatch):
    j=prepared();j['status']='interrupted';j['checkpoint']='fingerprint';g.put(j)
    launched=[];monkeypatch.setattr(g,'launch',lambda id,stage:launched.append(stage) or {})
    ff.resume(j['id']);assert launched==['fault_validation']


def test_restart_interrupts_new_phases_without_auto_calls(client):
    j=prepared();j.update(status='validating',stage='fault_validation',checkpoint='fault_validation');g.put(j)
    before=j['request_count'];g.recover();after=g.get(j['id'])
    assert after['status']=='interrupted' and after['request_count']==before
    assert after['checkpoint']=='fault_validation'


def test_context_received_by_adapter_and_feedback_not_unused(client,monkeypatch):
    j=prepared();base=ff.files(j);seen=[]
    from backend import agent
    j['mode']='real'
    raw={'base_hash':'0'*64,'decision':'patch','changes':[{'path':'calculator.py','before':'missing','after':'sum(values)','reason':'故障构造校正，仅测试错误反馈。'}],'explanation':'模拟错误补丁摘要，不能被接受。'}
    monkeypatch.setattr(agent,'completion',lambda messages,**kwargs:seen.append(messages) or {'content':json.dumps(raw),'_meta':{}})
    with pytest.raises(ff.Invalid) as e:flow.call(j,'fault_correct')
    flow.failure(j,'fault_correct',e.value)
    with pytest.raises(ff.Invalid):flow.call(j,'fault_correct')
    ctx=json.loads(seen[-1][-1]['content']);assert ctx['validation_feedback']['field']=='base_hash'
    assert ctx['validation_feedback']['required']==g.digest(base)
    assert ctx['base_files']==base and 'fixed_candidate' not in j['assets']


def test_spec_revision_cannot_change_code_or_unlisted_fields(client):
    j=prepared();old=g.asset(j,'project_build');j['spec_review_feedback']={'issues':[{'path':'/scenario','reason':'规范场景文字需明确解释。'}]}
    raw=copy.deepcopy(old);raw['contract']['scenario']+='补充本地模拟场景。'
    assert ff.validate_response(j,'project_build',raw)==raw
    raw['faulty']['calculator.py']='def total(v):\n return 0\n'
    with pytest.raises(ValueError,match='源码'):ff.validate_response(j,'project_build',raw)
    raw=copy.deepcopy(old);raw['contract']['behaviors']['sum']='任意返回数字即可'
    with pytest.raises(ff.Invalid) as e:ff.validate_response(j,'project_build',raw)
    assert e.value.feedback['allowed']==['/scenario']
