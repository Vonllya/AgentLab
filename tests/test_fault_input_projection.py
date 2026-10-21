"""Impact request projection only; no supplier calls or production data."""
import copy
import json
import pytest
from backend import agent, generation as g, generation_fault_first as ff, generation_flow as flow
from backend.generation_mock import role_response
from backend.generation_schema import Request

FIELDS={'id','input','expected','covers','visibility'}


def project(audited=True):
    j=g.create(Request(requirement='检查影响审核输入隔离，不改变原测试资产'),direct_build=True,fault_first=True)
    if not audited:j.pop('fault_impact_version',None);g.put(j)
    flow.call(j,'project_build');ff.install(j);flow.call(j,'spec_review');ff.apply_review(j)
    flow.call(j,'evaluation')
    return j


def test_adapter_receives_only_business_case_fields_without_mutating_assets(client,monkeypatch):
    j=project();original=copy.deepcopy(g.asset(j,'evaluation'));ref=j['assets']['evaluation']
    before=(g.root(j)/ref/'output.json').read_bytes();seen=[]
    j['mode']='real'
    def complete(messages,**kwargs):
        ctx=json.loads(messages[-1]['content']);seen.append(ctx)
        return {'content':json.dumps(role_response('fingerprint',ctx)),'_meta':{'usage':{'completion_tokens':0}}}
    monkeypatch.setattr(agent,'completion',complete)
    flow.call(j,'fingerprint')
    cases=seen[0]['candidate_cases']
    assert cases==[{k:c[k] for k in ('id','input','expected','covers','visibility')} for c in original['cases']]
    assert all(set(c)==FIELDS for c in cases)
    saved=json.loads((g.root(j)/j['attempts'][-1]['asset']/'input.json').read_text())
    assert json.loads(saved[-1]['content'])==seen[0]
    assert j['assets']['evaluation']==ref and g.asset(j,'evaluation')==original
    assert (g.root(j)/ref/'output.json').read_bytes()==before
    assert len(g.asset(j,'fingerprint')['case_impacts'])==len(original['cases'])


def test_swapping_old_labels_does_not_change_business_inputs_but_changes_asset_hash(client):
    j=project();before=ff.context(j,'fingerprint');original=copy.deepcopy(g.asset(j,'evaluation'))
    old_path=g.root(j)/j['assets']['evaluation']/'output.json';old_bytes=old_path.read_bytes()
    changed=copy.deepcopy(original)
    for c in changed['cases']:
        c['group']='regression' if c['group']=='target' else 'target'
        c['classification_reason']='故意反转的测试分类理由，不应进入影响审核。'
        c['faulty_expected']=999  # Projection must not forward prior guesses either.
    ff.save_asset(j,'evaluation',changed)
    after=ff.context(j,'fingerprint')
    assert before['candidate_cases']==after['candidate_cases']
    assert before['impact_binding']['evaluation_hash']!=after['impact_binding']['evaluation_hash']
    assert old_path.read_bytes()==old_bytes
    assert all(c['input']==o['input'] and c['expected']==o['expected'] for c,o in zip(changed['cases'],original['cases']))


@pytest.mark.parametrize('audited',[True,False])
def test_reference_feedback_lists_all_cases_only_for_new_protocol(client,audited):
    j=project(audited);seen=[];j['mode']='real'
    def complete(messages,**kwargs):
        ctx=json.loads(messages[-1]['content']);seen.append(ctx)
        raw=role_response('fingerprint',ctx)
        raw['checks'][0]['behavior_id']='unknown_behavior'
        return {'content':json.dumps(raw),'_meta':{'usage':{'completion_tokens':0}}}
    # Use the real call path with a deterministic adapter stub, not a supplier.
    from unittest.mock import patch
    with patch.object(agent,'completion',side_effect=complete):
        for _ in range(2):
            with pytest.raises(ff.Invalid) as e:flow.call(j,'fingerprint')
            flow.failure(j,'fingerprint',e.value)
    cases=g.asset(j,'evaluation')['cases'];feedback=seen[1]['validation_feedback']
    if audited:
        assert feedback['allowed_cases']=={c['id']:c['covers'] for c in cases}
        assert 'allowed_target_cases' not in feedback
        assert all(set(c)==FIELDS for c in seen[1]['candidate_cases'])
    else:
        assert feedback['allowed_target_cases']=={c['id']:c['covers'] for c in cases if c['group']=='target'}
        assert 'allowed_cases' not in feedback
        assert seen[1]['candidate_cases']==[c for c in cases if c['group']=='target']
