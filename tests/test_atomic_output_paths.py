"""Historical path failures; controlled revisions are not model-success evidence."""
import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend import agent, generation as g, generation_fault_first as ff
from backend import generation_fault_obligations as ob, generation_fault_recovery as recovery
from backend import generation_flow as flow, generation_format as fmt, generation_roles as roles
from backend.generation_schema import Request
from test_fault_first import prepared

FIXTURE=Path(__file__).parent/'fixtures/fault_first/atomic_path_failures.json'
HISTORY=json.loads(FIXTURE.read_text())


@pytest.mark.parametrize('sample',HISTORY,ids=lambda s:f"baseline-{s['sample']}")
def test_original_build_rejected_before_downstream_calls_and_revision_is_explicit(client,monkeypatch,sample):
    raw=copy.deepcopy(sample['bundle']);before=copy.deepcopy(raw)
    # The legacy resolver accepted this exact bundle. Keep that old path readable.
    ff.FaultBundle.model_validate(raw)
    with pytest.raises(ValidationError) as e:ff.ReviewedFaultBundle.model_validate(raw)
    error=fmt.classify(e.value)
    allowed=[p for p,_ in ob.schema_leaves(raw['contract']['output_schema'])]
    assert error['validation_feedback']['allowed_symptom_paths']==allowed
    assert not any(p in allowed for p in raw['symptom_paths'])
    assert raw==before
    # Explicitly construct a separate corrected candidate, never normalize old paths.
    revised=copy.deepcopy(raw);revised['symptom_paths']=[allowed[0]]
    assert ff.ReviewedFaultBundle.model_validate(revised).symptom_paths==[allowed[0]]
    j=g.create(Request(requirement='回归历史数组路径协议矛盾，禁止静默改写原资产'),direct_build=True,fault_first=True)
    ff.save_asset(j,'project_build',raw);ff.install(j)
    original_ref=j['assets']['project_build'];original=(g.root(j)/original_ref/'output.json').read_bytes()
    ff.save_asset(j,'evaluation',sample['evaluation'])
    count=j['request_count']
    with pytest.raises(ff.Invalid) as failure:roles.messages(j,'fingerprint')
    assert failure.value.category=='path_protocol_incompatible' and j['request_count']==count
    calls=[];j['mode']='real'
    monkeypatch.setattr(agent,'completion',lambda *args,**kwargs:calls.append(args))
    with pytest.raises(ff.Invalid):flow.call(j,'fingerprint')
    assert not calls and j['request_count']==count
    with pytest.raises(recovery.Stop,match='不重复付费审核'):
        recovery.handle(j,'fingerprint',failure.value,{'category':failure.value.category,'reason':str(failure.value)})
    assert (g.root(j)/original_ref/'output.json').read_bytes()==original
    ff.save_asset(j,'project_build',revised);ff.install(j)
    assert j['contract_version']==2 and j['confirmed_version'] is None
    assert 'evaluation' not in j['assets']
    assert (g.root(j)/original_ref/'output.json').read_bytes()==original


def test_atomic_policy_root_arrays_objects_and_duplicate_paths():
    schema={'type':'object','properties':{'meta':{'type':'object','properties':{'id':{'type':'string'}}},
                                       'items':{'type':'array','items':{'type':'object'}}}}
    assert [p for p,_ in ob.schema_leaves(schema)]==[['meta','id'],['items']]
    assert ob.validate_symptoms(schema,[['items']])==[['items']]
    for path in ([['items','0']], [['items','*']], [['meta']], [['items'],['items']]):
        with pytest.raises(ff.Invalid):ob.validate_symptoms(schema,path)
    for schema,value in [({'type':'array','items':{'type':'string'}},['x']),({'type':'integer'},1)]:
        assert ob.validate_symptoms(schema,[[]])==[[]]
        assert ob.value_at(value,[])==value
    with pytest.raises(ff.Invalid) as e:ob.value_at({'meta':{'items':[1]}},['meta','items','0'])
    assert e.value.feedback['allowed_paths']==[['meta','items']]


@pytest.mark.parametrize('path',[['results','0','decision'],['$.results[*].decision'],['results','*'],[]])
def test_build_uses_same_policy_for_indices_wildcards_jsonpath_and_parent_objects(path):
    raw=copy.deepcopy(HISTORY[0]['bundle']);raw['symptom_paths']=[path]
    with pytest.raises(ValidationError) as e:ff.ReviewedFaultBundle.model_validate(raw)
    assert fmt.classify(e.value)['validation_feedback']['allowed_symptom_paths']==[['results']]


def test_actual_build_correction_request_receives_legal_paths(client,monkeypatch):
    raw=HISTORY[0]['bundle'];seen=[]
    j=g.create(Request(requirement='验证构建路径错误反馈真正进入下一次模型请求'),direct_build=True,fault_first=True)
    j['mode']='real'
    def complete(messages,**kwargs):
        seen.append(json.loads(messages[-1]['content']))
        response=copy.deepcopy(raw)
        if len(seen)>1:response['symptom_paths']=[['results']]
        return {'content':json.dumps(response),'_meta':{'usage':{'completion_tokens':0}}}
    monkeypatch.setattr(agent,'completion',complete)
    with pytest.raises(ValidationError) as e:flow.call(j,'project_build')
    assert 'project_build' not in j['assets']
    flow.failure(j,'project_build',e.value)
    flow.call(j,'project_build')
    assert seen[1]['validation_feedback']['allowed_symptom_paths']==[['results']]
    assert g.asset(j,'project_build')['symptom_paths']==[['results']]
    # A deterministic adapter stub exercises real request construction, not provider quality.
    assert j['request_count']==2


@pytest.mark.parametrize('sample',HISTORY,ids=lambda s:f"baseline-{s['sample']}")
def test_auditor_and_reviewer_receive_identical_paths_and_atomic_assertions_work(client,sample):
    j=g.create(Request(requirement='验证影响审核与失败复核共用程序合法路径'),direct_build=True,fault_first=True)
    raw=copy.deepcopy(sample['bundle']);root=next(ob.schema_leaves(raw['contract']['output_schema']))[0]
    raw['symptom_paths']=[root];ff.save_asset(j,'project_build',raw);ff.install(j)
    ff.save_asset(j,'evaluation',sample['evaluation'])
    audit=json.loads(roles.messages(j,'fingerprint')[-1]['content'])
    reviewer=json.loads(roles.messages(j,'fault_review')[-1]['content'])
    assert audit['path_policy']==reviewer['path_policy']==ob.path_policy(j)
    assert 'files' not in audit and 'actual' not in audit and 'execution_evidence' not in audit
    assert 'files' in reviewer
    cases=sample['evaluation']['cases'];impacts=[];checks=[];selected=set()
    for c in cases:
        # Controlled wrong, type-valid whole-array value; not a claim about real code output.
        expected=ob.value_at(c['expected'],root)
        wrong=copy.deepcopy(expected)+copy.deepcopy(expected[:1])
        affect=bool(expected) and c['visibility'] not in selected
        if affect:selected.add(c['visibility'])
        assertions=[{'path':root,'equals':wrong}] if affect else []
        impacts.append({'case_id':c['id'],'affected_paths':[root] if affect else [],'fault_assertions':assertions,
                        'source_refs':[{'source':'private_fault_requirements'}],'reason':'受控数组整体断言用于验证协议及路径一致性。'})
        if affect:checks.append({'case_id':c['id'],'behavior_id':c['covers'][0],
                                 'reason':'选择公开及隐藏场景验证数组整体主要故障断言。','paths':[root]})
    assert selected=={'public','hidden'}
    result=ff.validate_response(j,'fingerprint',{'decision':'checks','explanation':'受控协议回归，不评价模型语义。',
                                              'checks':checks,'case_impacts':impacts,'issue':None})
    assert result['checks'] and all(a['path']==root for c in result['checks'] for a in c['assertions'])
    # Even when persisted, an invalid internal-array assertion cannot advance.
    bad=copy.deepcopy(result);item=next(i for i in bad['case_impacts'] if i['affected_paths'])
    item['affected_paths']=[root+['0']];item['fault_assertions']=[{'path':root+['0'],'equals':{}}]
    with pytest.raises(ff.Invalid,match='数组'):ob.normalize(j,bad)


def test_first_fix_still_rejects_schema_change_incompatible_with_frozen_symptoms(client):
    from backend import generation_spec_patch as sp
    j=prepared();j['spec_review_feedback']={'reason':'只修改明确授权的输出接口字段。','issues':[{'path':'/output_schema','reason':'验证规范补丁不能绕过现存症状路径兼容校验。'}]}
    ctx=sp.basis(j);raw={'contract_hash':ctx['contract_hash'],'bundle_hash':ctx['bundle_hash'],'decision':'patch',
        'changes':[{'path':'/output_schema','before':j['contract']['output_schema'],
                    'after':{'type':'object','properties':{'results':{'type':'array','items':{'type':'integer'}}},'required':['results'],'additionalProperties':False},
                    'reason':'该接口改变会令原标量症状路径不再兼容。'}],
        'conflict_evidence':[],'explanation':'权限合法不代表路径兼容，不能绕过后续校验。'}
    with pytest.raises(ff.Invalid,match='不兼容'):ff.validate_response(j,'project_build',sp.merge(j,raw))


@pytest.mark.parametrize('sample',HISTORY,ids=lambda s:f"baseline-{s['sample']}")
def test_historical_explicit_revision_fault_and_controlled_fix_in_docker(client,sample,record_property):
    ok,reason=g.executor.availability()
    if not ok:pytest.skip(reason)
    j=g.create(Request(requirement='历史数组路径修订后的实际Docker故障和受控修复验收'),direct_build=True,fault_first=True)
    raw=copy.deepcopy(sample['bundle']);root=next(ob.schema_leaves(raw['contract']['output_schema']))[0]
    raw['symptom_paths']=[root];ff.ReviewedFaultBundle.model_validate(raw)
    ff.save_asset(j,'project_build',raw);ff.install(j);ff.save_asset(j,'evaluation',sample['evaluation'])
    evaluation_hash=g.digest(g.asset(j,'evaluation'));impacts=[];checks=[]
    for c in sample['evaluation']['cases']:
        affected=c['group']=='target';wrong=copy.deepcopy(ob.value_at(c['expected'],root))
        if affected:
            if sample['sample']==3:
                wrong[0].update(decision='allow',reason='tool in session allowlist',
                                execution_records=[copy.deepcopy(c['input']['operations'][0]['request'])])
            else:
                wrong[0]['decisions'][1].update(decision='allowed',reason='allowed_by_trusted_policy')
                request=c['input']['sessions'][0]['requests'][1]
                wrong[0]['execution_records'].append({**request,'log':f"executed:{request['operation']}:{request['target']}"})
        impacts.append({'case_id':c['id'],'affected_paths':[root] if affected else [],
                        'fault_assertions':[{'path':root,'equals':wrong}] if affected else [],
                        'source_refs':[{'source':'private_fault_requirements'}],
                        'reason':'依据原私有机制和固定输入手工构造预期错误，实际容器结果验证是否命中。'})
        if affected:checks.append({'case_id':c['id'],'behavior_id':c['covers'][0],
                                   'reason':'原不可信授权场景应稳定展示错误允许及模拟记录。','paths':[root]})
    fingerprint=ff.validate_response(j,'fingerprint',{'decision':'checks','explanation':'受控协议验收，不代表真实模型重新生成。',
                              'checks':checks,'case_impacts':impacts,'issue':None})
    ff.save_asset(j,'fingerprint',fingerprint)
    faulty=ff.execute_phase(j,'faulty')
    assert faulty['passed'] and faulty['complete'] and all(faulty['gates'].values())
    # Author-only controlled fixes; never import generated business modules here.
    fixed=copy.deepcopy(raw['faulty'])
    if sample['sample']==3:
        code=fixed['policy.py'];start=code.index('    material_lower =');end=code.index('    if tool not in TOOLS:')
        fixed['policy.py']=code[:start]+code[end:]
    else:
        code=fixed['app.py'];start=code.index('        # Fault:');end=code.index('        decisions:')
        fixed['app.py']=code[:start]+code[end:]
    ff.save_asset(j,'fixed_candidate',{'files':fixed})
    reference=ff.execute_phase(j,'fixed')
    assert reference['passed'] and reference['complete'] and all(reference['gates'].values())
    assert g.digest(g.asset(j,'evaluation'))==evaluation_hash and ff.files(j)==sample['bundle']['faulty']
    assert all(c.get('repeat_count')==2 and c.get('stable') for m in (faulty,reference) for c in m['checks'])
    record_property('historical_path_replay',json.dumps({'sample':sample['sample'],'original_build_sha256':sample['build_sha256'],
                    'original_symptom_paths':sample['bundle']['symptom_paths'],'revised_symptom_paths':[root],
                    'unchanged_evaluation_hash':evaluation_hash,'faulty_matrix':faulty,'reference_matrix':reference},ensure_ascii=False))
