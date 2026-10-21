import copy
import pytest
from backend import generation as g,generation_fault_first as ff,generation_flow as flow
from backend.generation_schema import Request
from test_fault_first import prepared


def test_program_handoff_keeps_both_goals_without_competing_expected(client):
    j=prepared();ev=g.asset(j,'evaluation')
    rows=[]
    for c in ev['cases']:
        actual=c['expected'] if c['group']=='regression' else 3 if c['id']=='negative' else 2
        rows.append({'id':'run-'+c['id'],'case':c['id'],'status':'passed' if actual==c['expected'] else 'failed','actual':actual,'stable':True})
    j['matrix']={'id':'matrix-current','checks':rows,'build_hash':g.digest(ff.files(j))}
    ctx=ff.context(j,'fault_correct');e=ctx['execution_evidence']
    assert all(r['state']=='must_preserve' for r in e['requirements'])
    assert {r['objective'] for r in e['requirements']}=={'correct_behavior','preserve_specified_fault'}
    for r in e['requirements']:
        if r['objective']=='preserve_specified_fault':assert 'expected' not in r and r['required_assertions'][0]['equals']==r['actual']
    rows[0]['status']='failed';rows[0]['actual']=999
    e=ff.repair_requirements(j,'faulty');assert e['requirements'][0]['state']=='unmet'
    assert e['requirements'][1]['state']=='must_preserve'


@pytest.mark.skipif(not g.executor.availability()[0],reason='Docker不可用，不降级宿主')
def test_corrected_fault_never_accepts_normalized_code_or_execution_error(client):
    j=prepared();normal={'app.py':ff.files(j)['app.py'],'calculator.py':'def total(values):\n    return sum(values)\n'}
    ff.save_asset(j,'fault_candidate',{'files':normal})
    with pytest.raises(ff.Invalid):ff.execute_phase(j,'faulty')
    assert not j.get('fault_freeze') and not g.asset(j,'fault_acceptance')['passed']
    j['status']='interrupted';g.put(j)
    patch={'base_hash':g.digest(normal),'decision':'patch','changes':[{'path':'calculator.py','before':'sum(values)','after':'sum(v for v in values if v >= 0)','reason':'校正故障构造，保留指定的负数过滤错误。'}],'explanation':'只恢复约定的负数过滤故障，同时保留正数与空集。'}
    ff.validate_response(j,'fault_correct',patch);assert ff.execute_phase(j,'faulty')['passed']
    old=copy.deepcopy(j['fault_freeze']);ev=copy.deepcopy(g.asset(j,'evaluation'))
    ev['cases'][0]['expected']=999;ff.save_asset(j,'evaluation',ev)
    with pytest.raises(ff.Invalid,match='已变化'):ff.require_frozen(j)
    assert j['fault_freeze']==old


def test_phase_error_is_not_target_fault(client,monkeypatch):
    j=prepared()
    monkeypatch.setattr(g.executor,'availability',lambda:(True,''))
    import subprocess
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:type('Result',(),{'stdout':'fake-image'})())
    def fail(*args,**kwargs):raise TimeoutError('模拟容器执行超时')
    monkeypatch.setattr(g.executor,'execute',fail)
    with pytest.raises(ff.Invalid):ff.execute_phase(j,'faulty')
    assert all(r['status']=='error' for r in j['matrix']['checks'])
    assert not j['matrix']['gates']['fault_trigger'] and not j.get('fault_freeze')


def test_new_batch_copies_freeze_and_checkpoints_without_modifying_parent(client,monkeypatch):
    from backend.app import Regenerate as RegenerateRequest
    j=prepared();j.update(status='budget_exhausted',checkpoint='fault_validation');g.put(j)
    original=copy.deepcopy(g.get(j['id']));calls=[]
    monkeypatch.setattr(g,'launch',lambda id,stage:calls.append((id,stage)))
    body=RegenerateRequest(expected_revision=j['revision'],idempotency_key='fault-first-test',repair_note='核对同一故障基线继续执行，不改变测试。',new_batch=True,budget={'requests':4})
    result=flow.regenerate(j['id'],body)
    child=g.get(result['id']);assert ff.enabled(child) and child['checkpoint']=='fault_validation'
    assert calls[0][1]=='fault_validation'
    assert g.get(j['id'])==original
    assert flow.regenerate(j['id'],body)['id']==child['id'] and len(calls)==1


def test_patch_preserves_indentation_and_canonical_snapshot_bytes(client):
    j=prepared();base=ff.files(j)
    patch={'base_hash':g.digest(base),'decision':'patch','changes':[{'path':'calculator.py','before':'    return sum(v for v in values if v >= 0)','after':'    return sum(values)\n','reason':'精确替换含缩进的返回语句，恢复全部整数累加。'}],'explanation':'修复补丁须保留Python缩进和规范化快照一致性。'}
    parsed=ff.Patch.model_validate(patch).model_dump()
    assert parsed['changes'][0]['before'].startswith('    ')
    result=ff.apply_patch(j,parsed)
    assert result['calculator.py']=='def total(values):\n    return sum(values)'
    from backend.generation_schema import Project
    from backend.generation_mock import response
    legacy=response('build',{});legacy.update(normal=result,reference=result,faulty=base)
    assert Project.model_validate(legacy).model_dump()['normal']==result


def test_stage_image_change_routes_environment_not_patch(client,monkeypatch):
    j=prepared();ff.save_asset(j,'fixed_candidate',{'files':ff.files(j)})
    monkeypatch.setattr(ff,'require_frozen',lambda job:{})
    j['fault_freeze']={'environment':'frozen-image'}
    monkeypatch.setattr(g.executor,'availability',lambda:(True,''))
    import subprocess
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:type('Result',(),{'stdout':'different-image'})())
    with pytest.raises(EnvironmentError,match='镜像'):ff.execute_phase(j,'fixed')
    assert not j.get('fix_acceptance')


def test_final_image_change_is_detected_before_candidate_execution(client,monkeypatch):
    j=prepared()
    from backend.generation_mock import response
    ff.save_asset(j,'build',response('build',{}))
    monkeypatch.setattr(ff,'verify_fixed',lambda job:None)
    j['fault_freeze']={'environment':'frozen-image'}
    monkeypatch.setattr(g.executor,'availability',lambda:(True,''))
    import subprocess
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:type('Result',(),{'stdout':'different-image'})())
    executed=[];monkeypatch.setattr(g.executor,'execute',lambda *a,**k:executed.append(True))
    with pytest.raises(EnvironmentError,match='镜像'):g.validate(j)
    assert not executed


def test_preliminary_author_assets_show_fault_without_fake_normal(client):
    j=prepared();r=g.review_assets(j['id'])
    assert 'faulty' in r['assets']['project_build'] and 'build' not in r['assets']
    assert r['summary']['fault']==j['private_fault_requirements']
    assert r['summary']['reference_changed_files']==[]
