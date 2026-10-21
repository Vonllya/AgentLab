"""Regressions from six real rejections and the actual correction request path."""
import copy
import json
from pathlib import Path
import pytest
from backend import storage as s, generation as g, generation_budget as budget
from backend import generation_demands as dc, generation_inventory_repair as ir
from backend import generation_flow as flow, generation_roles as roles
from backend import generation_fault_first as ff, generation_fault_recovery as recovery
from backend import generation_mock as mock
from backend.generation_schema import Request

HISTORY = json.loads((Path(__file__).parent / 'fixtures/inventory_rejections.json').read_text())


@pytest.fixture
def job(tmp_path, monkeypatch):
    monkeypatch.setattr(s, 'DATA', tmp_path / 'data')
    monkeypatch.setenv('AGENT_MODE', 'mock')
    s.init()
    j = g.create(Request(requirement='验证相同场景中不同实体的行为和输出，保留独立评测与运行边界。'),
                 direct_build=True, fault_first=True, demand_coverage=True)
    flow.call(j, 'project_build'); ff.install(j)
    return j


def initial(j):
    return mock.role_response('spec_review', roles.context(j, 'spec_review'))


def reject(j, raw, monkeypatch):
    monkeypatch.setattr(mock, 'role_response', lambda stage, payload: raw)
    with pytest.raises(ValueError) as caught:
        flow.call(j, 'spec_review')
    reason = flow.failure(j, 'spec_review', caught.value)
    return caught.value, reason


def patch(j, items=None, **extra):
    return {'binding': ir.binding(j), 'decision': 'patch', 'reason': '仅纠正被拒清单规则，保留原需求和输出判定标准。',
            'replacements': items or [], 'issues': [], 'interface_gap': False, **extra}


@pytest.mark.parametrize('sample', HISTORY, ids=lambda x: str(x['sample']))
def test_six_original_candidates_return_specific_errors(job, sample):
    job.update(request=sample['request'], contract=sample['contract'], contract_hash=g.digest(sample['contract']))
    raw = copy.deepcopy(sample['review']); before = g.digest(raw)
    if sample['sample'] == 5:
        # The historical nested inventory error masked an independent invalid
        # output quote. Do not pretend a local inventory patch can fix that.
        from backend.generation_direct import ReviewEvidenceError
        with pytest.raises(ReviewEvidenceError) as caught:
            ir.inspect_review(job, raw)
        assert caught.value.feedback['errors'][0]['field'] == '/output_rules/0/quote'
        with pytest.raises(ValueError): dc.validate_inventory(job, raw['requirements'])
        assert g.digest(raw) == before
        return
    with pytest.raises(ff.Invalid) as caught:
        ir.inspect_review(job, raw)
    feedback = caught.value.feedback
    assert feedback['kind'] == 'inventory_invalid'
    assert feedback['errors'] and all(e['field'].startswith('requirements') for e in feedback['errors'])
    assert not feedback['execution_performed'] and g.digest(raw) == before
    assert any('expected' in e['message'] or 'distinct' in e['message'] or
               'minimum' in e['field'] or 'valid string' in e['message'] for e in feedback['errors'])


def test_length_semantics_and_legacy_schema_unchanged(job):
    d = {'rules': [{'source': 'expected', 'path': ['tickets', '*', 'id'], 'op': 'length_eq', 'value': 2}]}
    c = {'id': 'two_ids', 'expected': {'tickets': [{'id': 11}, {'id': 12}]}}
    assert not dc.evidence(d, c)['matches']
    d['rules'][0]['path'] = ['tickets']
    assert dc.evidence(d, c)['matches']
    assert ir.SpecReview.model_json_schema()['$defs']['RelationRule']['properties']['minimum']['minimum'] == 2
    assert dc.SpecReview.model_json_schema()['$defs']['Rule']['properties']['minimum']['minimum'] == 1
    job.pop('inventory_correction_version')
    assert roles.schema(job, 'spec_review') is dc.SpecReview
    assert '比较所选集合长度' in roles.messages(job, 'spec_review')[0]['content']


def test_two_distinct_corrections_receive_candidate_and_real_feedback(job, monkeypatch):
    raw = initial(job); good = copy.deepcopy(raw['requirements'][0])
    good['kind'] = 'shared_state'
    good['rules'].insert(0, {'source': 'input', 'path': ['values', '*'], 'op': 'distinct', 'minimum': 2})
    bad = copy.deepcopy(good); bad['rules'][0]['minimum'] = 1; bad['rules'].pop()
    raw['requirements'] = [bad]
    accepted_assets = copy.deepcopy(job['assets'])
    exc, reason = reject(job, raw, monkeypatch)
    assert recovery.handle(job, 'spec_review', exc, reason) == 'spec_review'
    ctx = json.loads(roles.messages(job, 'spec_review')[-1]['content'])
    assert ctx['rejected_review'] == raw
    assert any('expected' in x['message'] for x in ctx['validation_feedback']['errors'])
    assert job['assets'] == accepted_assets and job.get('spec_approval') is None
    assert not {'files', 'evaluation', 'matrix', 'private_fault_requirements'} & set(ctx)

    first = copy.deepcopy(good); first['rules'][0]['minimum'] = 1
    exc, reason = reject(job, patch(job, [first]), monkeypatch)
    assert recovery.handle(job, 'spec_review', exc, reason) == 'spec_review'
    ctx2 = json.loads(roles.messages(job, 'spec_review')[-1]['content'])
    assert ctx2['rejected_review']['requirements'] == [first]
    assert ctx2['rejected_review']['output_rules'] == raw['output_rules']
    assert ctx2['corrections_remaining'] == 1 and len(ctx2['previous_attempts']) == 2
    assert not any('缺少expected' in x['message'] for x in ctx2['validation_feedback']['errors'])

    response = patch(job, [good])
    monkeypatch.setattr(mock, 'role_response', lambda stage, payload: response)
    parsed = flow.call(job, 'spec_review')
    assert parsed['requirements'][0]['rules'][0]['minimum'] == 2
    assert parsed['output_rules'] == raw['output_rules']
    assert job['inventory_correction_calls'] == 2 and not ir.pending(job)
    assert ff.apply_review(job) == 'evaluation'
    assert job['budget']['requests'] == 4  # build, original review, two corrections
    assert g.get(job['id'])['spec_approval']['contract_hash'] == job['contract_hash']


def test_limit_duplicate_and_restart_do_not_reset_budget(job, monkeypatch):
    raw = initial(job); raw['requirements'][0]['rules'] = []
    reject(job, raw, monkeypatch)
    base = ir.candidate(job)
    for _ in range(2):
        exc, reason = reject(job, patch(job, [base['requirements'][0]]), monkeypatch)
        assert '没有变化' in str(exc)
    restored = g.get(job['id'])
    assert restored['inventory_correction_calls'] == 2
    assert restored['budget']['requests'] == 4
    with pytest.raises(recovery.Stop, match='两次'):
        recovery.handle(restored, 'spec_review', exc, reason)
    with pytest.raises(recovery.Stop):
        ir.reserve(restored)


def test_patch_binding_source_and_untouched_items(job, monkeypatch):
    raw = initial(job); good = copy.deepcopy(raw['requirements'][0])
    other = copy.deepcopy(good); other['id'] = 'other_requirement'
    raw['requirements'].append(other); raw['requirements'][0]['rules'] = []
    reject(job, raw, monkeypatch)
    merged = ir.merge(job, patch(job, [good]))
    assert merged['requirements'][1] == other and merged['output_rules'] == raw['output_rules']
    stale = patch(job, [good]); stale['binding']['candidate_hash'] = '0' * 64
    with pytest.raises(ff.Invalid, match='绑定'): ir.merge(job, stale)
    changed = copy.deepcopy(good); changed['quote'] = '改变原需求引用'
    with pytest.raises(ff.Invalid, match='原需求引用'): ir.merge(job, patch(job, [changed]))
    changed = copy.deepcopy(good); changed['id'] = 'new_requirement'
    with pytest.raises(ff.Invalid, match='需求ID'): ir.merge(job, patch(job, [changed]))
    job['contract_hash'] = 'f' * 64
    with pytest.raises(ff.Invalid, match='依据已变化'): ir.merge(job, stale)
    with pytest.raises(recovery.Stop, match='依据已变化'): ir.recover(job)


def test_interface_revision_routes_to_builder_without_changing_expectations(job, monkeypatch):
    raw = initial(job); raw['requirements'][0]['rules'] = []
    reject(job, raw, monkeypatch)
    original = copy.deepcopy(job['contract']); code = copy.deepcopy(ff.files(job))
    response = patch(job, [], decision='revise', interface_gap=True,
                     issues=[{'path': '/input_schema', 'reason': '原需求的同存储多实体操作无法由现有接口表达，需要增加相关输入。'}])
    monkeypatch.setattr(mock, 'role_response', lambda stage, payload: response)
    flow.call(job, 'spec_review')
    assert ff.apply_review(job) == 'project_build'
    assert job['contract'] == original and ff.files(job) == code
    assert job['design_rebuild']['issue']['kind'] == 'demand_interface_gap'
    assert job['inventory_correction_calls'] == 1


def test_output_rules_invalid_does_not_enter_inventory_correction(job, monkeypatch):
    raw = initial(job); raw['requirements'][0]['rules'] = []
    raw['output_rules'][0]['quote'] = '不存在的正确输出约定'
    exc, reason = reject(job, raw, monkeypatch)
    assert not ir.pending(job)
    assert reason['validation_feedback']['kind'] == 'review_evidence_invalid'


def test_global_budget_and_no_correction_marker_are_respected(job, monkeypatch):
    raw = initial(job); raw['requirements'][0]['rules'] = []
    reject(job, raw, monkeypatch)
    job['budget']['policy']['requests'] = job['budget']['requests']
    with pytest.raises(budget.Exhausted): flow.call(job, 'spec_review')
    assert job.get('inventory_correction_calls', 0) == 0
    job.pop('inventory_correction_version')
    assert roles.schema(job, 'spec_review') is dc.SpecReview
    assert 'rejected_review' not in roles.context(job, 'spec_review')


def test_explicit_new_batch_copies_candidate_and_resets_only_child(job, monkeypatch):
    from backend.app import Regenerate
    raw = initial(job); raw['requirements'][0]['rules'] = []
    reject(job, raw, monkeypatch)
    job.update(status='no_progress', checkpoint='spec_review', inventory_correction_calls=2)
    g.put(job); parent = g.get(job['id'])
    launched = []
    monkeypatch.setattr(g, 'launch', lambda id, stage: launched.append((id, stage)))
    body = Regenerate(expected_revision=job['revision'], idempotency_key='inventory-new-batch',
                      new_batch=True, budget=budget.BudgetPolicy(requests=3), repair_note='明确授权新预算继续核对原清单。')
    result = flow.regenerate(job['id'], body)
    assert flow.regenerate(job['id'], body)['id'] == result['id']
    child = g.get(result['id'])
    assert child['inventory_correction_calls'] == 0
    assert ir.candidate(child) == raw and g.get(job['id']) == parent
    assert launched == [(child['id'], 'spec_review')]


def test_scope_policy_reaches_both_initial_and_coverage_requests_without_auto_approval(job):
    raw = initial(job)
    items = dc.validate_inventory(job, raw['requirements'])
    job['demand_inventory'] = {'contract_hash': job['contract_hash'], 'source_hash': g.digest(dc.sources(job)),
                               'requirements': items}
    ff.save_asset(job, 'evaluation', mock.role_response('evaluation', roles.context(job, 'evaluation')))
    initial_messages = roles.messages(job, 'spec_review')
    coverage_messages = roles.messages(job, dc.STAGE)
    assert ir.POLICY in initial_messages[0]['content']
    assert ir.POLICY in coverage_messages[0]['content']
    ctx = json.loads(coverage_messages[-1]['content'])
    assert ctx['rule_policy'] == ir.POLICY and not ctx['execution_performed']
    assert not {'files', 'private_fault_requirements', 'matrix'} & set(ctx)
    # Sharing instructions is not an automatic covered verdict. No case IDs
    # still means no evidence, irrespective of the policy or covers labels.
    fake = {'binding': dc.binding(job), 'findings': [{'requirement_id': items[0]['id'],
            'verdict': 'covered', 'witnesses': [], 'reason': '不能用环境说明或覆盖标签代替行为的真实数据见证。'}], 'omissions': []}
    with pytest.raises(ff.Invalid, match='实际数据'): dc.validate_review(job, fake)
    job.pop('inventory_correction_version')
    assert 'rule_policy' not in dc.context(job)
    assert ir.POLICY not in roles.messages(job, dc.STAGE)[0]['content']


def test_rejected_quote_drift_can_restore_previously_accepted_obligation(job, monkeypatch):
    raw = initial(job); original = copy.deepcopy(raw['requirements'][0])
    job['demand_previous_requirements'] = [original]
    job['demand_previous_source_hash'] = g.digest(dc.sources(job))
    # Both quotes are literal substrings of the same request. Only the old,
    # accepted obligation is authoritative; the rejected drift must not lock it.
    raw['requirements'][0]['quote'] = original['quote'][4:]
    exc, reason = reject(job, raw, monkeypatch)
    assert reason['category'] == 'inventory_invalid'
    assert ir.pending(job)
    ctx = roles.context(job, 'spec_review')
    assert ctx['previous_requirements'] == [original]
    assert any(e.get('required', {}).get('quote') == original['quote'] for e in ctx['validation_feedback']['errors'])
    response = patch(job, [original])
    monkeypatch.setattr(mock, 'role_response', lambda stage, payload: response)
    result = flow.call(job, 'spec_review')
    assert result['requirements'][0]['quote'] == original['quote']
    assert job['inventory_correction_calls'] == 1


def test_real_rejected_restoration_patch_is_allowed_and_other_drift_is_reported(job):
    evidence = json.loads((Path(__file__).parent / 'fixtures/inventory_obligation_drift.json').read_text())
    job.update(request=evidence['request'], contract=evidence['contract'],
               contract_hash=g.digest(evidence['contract']), inventory_correction=evidence['state'],
               demand_previous_requirements=evidence['previous'], demand_previous_source_hash=evidence['previous_source_hash'])
    folder = g.root(job) / evidence['state']['asset']; folder.mkdir()
    (folder / 'inventory-candidate.json').write_text(json.dumps(evidence['candidate'], ensure_ascii=False))
    before = g.digest(evidence['candidate'])
    merged = ir.merge(job, evidence['patch'])
    by_id = {x['id']: x for x in merged['requirements']}
    previous = {x['id']: x for x in evidence['previous']}
    assert by_id['ordered_workflow_steps']['quote'] == previous['ordered_workflow_steps']['quote']
    # Do not claim the whole patch is correct: the old feedback exposed only
    # the first drift. Both remaining obligations must now reach the model.
    with pytest.raises(ff.Invalid) as caught: ir.inspect_review(job, merged)
    outstanding = {x['requirement_id'] for x in caught.value.feedback['errors']}
    assert outstanding == {'resume_skip_no_side_effects', 'interrupt_and_reopen'}
    assert all('required' in x for x in caught.value.feedback['errors'])
    assert g.digest(ir.candidate(job)) == before


def restoration_case(job):
    evidence = json.loads((Path(__file__).parent / 'fixtures/inventory_obligation_drift.json').read_text())
    response = json.loads((Path(__file__).parent / 'fixtures/inventory_restoration_response.json').read_text())
    job.update(request=evidence['request'], contract=evidence['contract'],
               contract_hash=g.digest(evidence['contract']), inventory_correction=evidence['state'],
               demand_previous_requirements=evidence['previous'], demand_previous_source_hash=evidence['previous_source_hash'])
    folder = g.root(job) / evidence['state']['asset']; folder.mkdir()
    (folder / 'inventory-candidate.json').write_text(json.dumps(evidence['candidate'], ensure_ascii=False))
    return evidence, response['patch']


def test_restore_accepted_ids_explicitly_without_changing_item_count_or_other_assets(job):
    from backend.generation_direct import validate_review
    evidence, response = restoration_case(job)
    # Mapping is an explicit engineering fixture addition to a real response,
    # not misrepresented as the provider having emitted the new field.
    merged = ir.merge(job, response)
    ir.inspect_review(job, merged)
    validate_review(job, merged)
    assert len(merged['requirements']) == len(evidence['candidate']['requirements'])
    assert merged['output_rules'] == evidence['candidate']['output_rules']
    previous = {x['id']: x for x in evidence['previous']}
    for item in merged['requirements']:
        if item['id'] in previous:
            assert all(item[k] == previous[item['id']][k] for k in ('source', 'quote', 'kind'))
    assert ir.candidate(job) == evidence['candidate']


@pytest.mark.parametrize('invalid', ['new_id', 'protected_id', 'existing_target', 'duplicate_source', 'duplicate_target', 'missing_item'])
def test_id_restore_cannot_rename_arbitrarily_or_erase_accepted_obligations(job, invalid):
    evidence, response = restoration_case(job)
    if invalid == 'new_id': response['restore_ids'][0]['accepted_id'] = 'arbitrary_new_id'
    elif invalid == 'protected_id': response['restore_ids'][0]['candidate_id'] = 'ordered_workflow_steps'
    elif invalid == 'existing_target': response['restore_ids'][0]['accepted_id'] = 'ordered_workflow_steps'
    elif invalid == 'duplicate_source': response['restore_ids'].append(copy.deepcopy(response['restore_ids'][0]))
    elif invalid == 'duplicate_target': response['restore_ids'][1]['accepted_id'] = response['restore_ids'][0]['accepted_id']
    elif invalid == 'missing_item': response['replacements'] = [x for x in response['replacements'] if x['id'] != 'resume_skip_no_side_effects']
    with pytest.raises(ff.Invalid): ir.merge(job, response)
    assert ir.candidate(job) == evidence['candidate']


def missing_inventory(job,monkeypatch,empty=False):
    good=initial(job);raw=copy.deepcopy(good)
    if empty:raw['requirements']=[]
    else:raw.pop('requirements')
    reject(job,raw,monkeypatch)
    return raw,good['requirements']


def supply(j,items):
    return {'binding':ir.binding(j),'decision':'supply_inventory','reason':'仅补齐缺失清单，保留已验证的公开审核与输出规则。','requirements':items}


@pytest.mark.parametrize('empty',[False,True])
def test_missing_whole_inventory_can_be_supplied_and_adopted(job,monkeypatch,empty):
    raw,items=missing_inventory(job,monkeypatch,empty)
    before_contract=copy.deepcopy(job['contract']);before_files=ff.files(job)
    assert roles.schema(job,'spec_review') is ir.SupplyCorrection
    assert roles.context(job,'spec_review')['correction_mode']=='supply_inventory'
    assert 'decision=supply_inventory' in roles.messages(job,'spec_review')[0]['content']
    monkeypatch.setattr(mock,'role_response',lambda stage,ctx:supply(job,items))
    result=flow.call(job,'spec_review')
    assert {k:v for k,v in result.items() if k!='requirements'}=={k:v for k,v in {**raw,'interface_gap':False}.items() if k!='requirements'}
    assert result['requirements']==items and not ir.pending(job)
    assert job['contract']==before_contract and ff.files(job)==before_files
    assert ff.apply_review(job)=='evaluation'


def test_supply_errors_reach_next_real_adapter_request_then_local_patch(job,monkeypatch):
    raw,items=missing_inventory(job,monkeypatch);bad=copy.deepcopy(items)
    bad[0]['source']='behaviors.sum';bad[0]['quote']=job['contract']['behaviors']['sum']
    job['mode']='real';seen=[]
    def complete(messages,**kwargs):
        seen.append(messages)
        value=supply(job,bad) if len(seen)==1 else patch(job,items)
        return {'content':json.dumps(value),'_meta':{'usage':{'completion_tokens':10}}}
    monkeypatch.setattr(flow.agent,'completion',complete)
    with pytest.raises(ff.Invalid) as caught:flow.call(job,'spec_review')
    reason=flow.failure(job,'spec_review',caught.value)
    assert reason['category']=='inventory_invalid' and '原始输入' in reason['reason']
    assert ir.candidate(job)['requirements']==bad
    assert roles.schema(job,'spec_review') is ir.Correction
    recovery.handle(job,'spec_review',caught.value,reason)
    flow.call(job,'spec_review')
    assert '原始输入' in seen[1][-1]['content'] and 'behaviors.sum' in seen[1][-1]['content']
    assert not ir.pending(job) and g.asset(job,'spec_review')['requirements']==items


def test_supply_missing_expected_reports_specific_item_not_keyerror(job,monkeypatch):
    _,items=missing_inventory(job,monkeypatch)
    items[0]['rules']=[{'source':'input','path':['values'],'op':'length_eq','value':0,'minimum':2}]
    monkeypatch.setattr(mock,'role_response',lambda stage,ctx:supply(job,items))
    with pytest.raises(ff.Invalid) as caught:flow.call(job,'spec_review')
    assert any(e.get('requirement_id')==items[0]['id'] and 'expected' in e['message'] for e in caught.value.feedback['errors'])


@pytest.mark.parametrize('bad',['stale','extra','empty','oversize','frozen','legacy'])
def test_supply_boundaries(job,monkeypatch,bad):
    _,items=missing_inventory(job,monkeypatch);v=supply(job,items)
    if bad=='stale':v['binding']['candidate_hash']='0'*64
    if bad=='extra':v['output_rules']=[]
    if bad=='empty':v['requirements']=[]
    if bad=='oversize':v['requirements']*=13
    if bad=='frozen':job['fault_freeze']={'frozen':True}
    if bad=='legacy':job['inventory_correction_version']='inventory-correction-v1'
    before=copy.deepcopy(job)
    with pytest.raises(ValueError):ir.merge(job,v)
    assert job==before


def test_supply_cannot_replace_nonempty_candidate(job,monkeypatch):
    raw=initial(job);raw['requirements'][0]['rules']=[]
    reject(job,raw,monkeypatch)
    assert roles.schema(job,'spec_review') is ir.Correction
    with pytest.raises(ff.Invalid,match='已有清单'):ir.merge(job,supply(job,initial(job)['requirements']))


def test_supply_protects_previous_accepted_identity(job,monkeypatch):
    raw,items=missing_inventory(job,monkeypatch)
    job['demand_previous_requirements']=copy.deepcopy(items)
    job['demand_previous_source_hash']=g.digest(dc.sources(job))
    bad=copy.deepcopy(items);bad[0]['id']='renamed_without_authorization'
    candidate=ir.merge(job,supply(job,bad))
    with pytest.raises(ff.Invalid,match='恢复此前已接受'):ir.inspect_review(job,candidate)
    accepted=ir.merge(job,supply(job,items));ir.inspect_review(job,accepted)


@pytest.mark.parametrize('malformed',[{},'broken',[{'id':'same'},{'id':'same'}]])
def test_structural_damage_is_not_silently_treated_as_missing_inventory(job,monkeypatch,malformed):
    raw=initial(job);raw['requirements']=malformed
    reject(job,raw,monkeypatch)
    assert ir.correction_mode(job)=='invalid_structure'
    with pytest.raises(recovery.Stop,match='结构损坏'):ir.check_allowance(job)
    with pytest.raises(ff.Invalid):ir.merge(job,supply(job,[]))


def test_supply_non_object_response_has_specific_error_and_obeys_existing_limit(job,monkeypatch):
    missing_inventory(job,monkeypatch)
    with pytest.raises(ff.Invalid,match='JSON对象') as caught:ir.merge(job,[])
    assert caught.value.feedback['field']=='$'
    job['inventory_correction_calls']=ir.LIMIT
    with pytest.raises(recovery.Stop,match='两次上限'):ir.check_allowance(job)
