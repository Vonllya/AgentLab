import copy
import json
from pathlib import Path

import pytest

from backend import storage as s, generation as g, generation_flow as flow
from backend import generation_fault_first as ff, generation_fault_recovery as recovery
from backend import generation_stage_recovery as sr, generation_budget as budget
from backend import generation_roles as roles, generation_mock as mock
from backend import generation_demands as dc, generation_inventory_repair as ir


@pytest.fixture
def job(tmp_path, monkeypatch):
    from backend.generation_schema import Request
    monkeypatch.setattr(s, 'DATA', tmp_path / 'data')
    monkeypatch.setenv('AGENT_MODE', 'mock')
    s.init()
    j = g.create(Request(requirement='相同逻辑操作重试只能创建一条记录，不同操作不能合并。'),
                 direct_build=True, fault_first=True, demand_coverage=True)
    flow.call(j, 'project_build'); ff.install(j)
    return j


def failure(j, n, reason='具体字段不满足当前检查', category='demand_coverage_invalid'):
    folder = g.root(j) / f'candidate-{n}'; folder.mkdir(exist_ok=True)
    (folder / 'response.txt').write_text(json.dumps({'candidate': n}))
    j['attempts'].append({'stage': 'spec_review', 'asset': folder.name, 'status': 'failed'})
    return {'category': category, 'reason': reason, 'validation_feedback': {'field': f'field-{n}'}}


def test_different_failures_continue_persist_and_reach_actual_request(job, monkeypatch):
    for n in range(3):
        reason = failure(job, n)
        assert recovery.handle(job, 'spec_review', ValueError(reason['reason']), reason) == 'spec_review'
        job = g.get(job['id'])
    ctx = sr.correction_context(job, 'spec_review')
    assert ctx['attempt'] == 3 and ctx['error']['validation_feedback']['field'] == 'field-2'
    assert json.loads(ctx['rejected_response']) == {'candidate': 2}
    raw = mock.role_response('spec_review', roles.context(job, 'spec_review'))
    captured = []
    def completion(messages, **kwargs):
        captured.extend(messages)
        return {'content': json.dumps(raw), '_meta': {'usage': {'completion_tokens': 10}}}
    job['mode'] = 'real'; monkeypatch.setattr(flow.agent, 'completion', completion)
    flow.call(job, 'spec_review')
    assert 'candidate-2' in captured[-1]['content'] and 'field-2' in captured[-1]['content']
    assert job['attempts'][-1]['stage_correction_attempt'] == 3
    assert sr.correction_context(job, 'evaluation') is None


def test_identical_failure_stops_and_changed_candidate_can_continue(job):
    reason = failure(job, 1)
    sr.recover(job, 'spec_review', reason)
    with pytest.raises(recovery.Stop, match='同一候选'):sr.recover(g.get(job['id']), 'spec_review', reason)
    reason = failure(job, 2)
    assert sr.recover(job, 'spec_review', reason) == 'spec_review'


def test_stage_limit_and_shared_budget_remain_enforced(job):
    for n in range(sr.LIMIT):sr.recover(job, 'spec_review', failure(job, n))
    with pytest.raises(recovery.Stop, match='6次'):sr.recover(job, 'spec_review', failure(job, 7))
    job['budget']['requests'] = job['budget']['policy']['requests']
    with pytest.raises(budget.Exhausted):flow.call(job, 'spec_review')


def test_old_jobs_retain_single_correction(job):
    job.pop('stage_recovery_version')
    reason = failure(job, 1)
    assert recovery.handle(job, 'spec_review', ValueError(), reason) == 'spec_review'
    with pytest.raises(recovery.Stop, match='同阶段'):recovery.handle(job, 'spec_review', ValueError(), reason)


def question():
    return {'id': 'retry_semantics', 'text': '超时重试后应该生成一条还是两条记录？',
            'options': ['一条记录', '两条记录'], 'impact': '影响幂等行为的验收标准'}


def start_guard(job):
    review = {'decision': 'need_user', 'reason': '生成规范写两条记录，但原始需求要求重试仅一条。',
              'questions': [question()], 'contract_hash': job['contract_hash'],
              'issues': [], 'output_rules': [], 'requirements': [], 'interface_gap': False}
    ff.save_asset(job, 'spec_review', review)
    assert ff.apply_review(job) == 'contract_review'
    assert job['status'] != 'awaiting_requirement' and not job.get('requirement_question')
    return review


def install_proposal(job, decision):
    key = next(iter(job['contract']['behaviors']))
    text = job['request']['requirement']
    proposal = {'contract_hash': job['contract_hash'], 'decision': decision,
                'reason': '原始需求明确，生成规范与需求的冲突不是用户需求歧义。',
                'questions': [question()] if decision == 'need_user' else [],
                'changes': [{'path': '/behaviors/' + key, 'before': job['contract']['behaviors'][key],
                             'after': text, 'source': 'request.requirement', 'quote': text,
                             'reason': '正常标准应遵循明确的单次写入要求，故障现象另行验证。'}] if decision == 'correction' else []}
    from backend import contract_revision as cr
    proposal = cr.validate_proposal(job, proposal)
    job['contract_candidate'] = {'asset': 'proposal', 'hash': g.digest(proposal), 'proposal': proposal}
    job['contract_decision'] = {'asset': 'check', 'result': {'proposal_hash': g.digest(proposal),
                               'verdict': 'approve', 'reason': '仅依据原始需求核对提案，未使用任何实现和测试输出。'}}


def test_generated_conflict_returns_to_builder_without_mutating_assets(job):
    start_guard(job)
    for stage in ('contract_review', 'contract_check'):
        if stage == 'contract_check':install_proposal(job, 'correction')
        ctx = roles.context(job, stage)
        assert not {'files', 'evaluation', 'matrix', 'private_fault_requirements'} & set(ctx)
        assert ctx['scope']['proposed_ambiguity']['questions'] == [question()]
    before = copy.deepcopy(job['contract']); files = copy.deepcopy(ff.files(job))
    assert sr.apply_clarification(job) == 'project_build'
    assert job['contract'] == before and ff.files(job) == files
    assert job['spec_review_feedback']['issues'][0]['path'].startswith('/behaviors/')
    assert not job.get('requirement_question') and job['clarification_reviews']
    assert 'review_feedback' in roles.context(job, 'project_build') or 'review_feedback' in json.dumps(roles.context(job, 'project_build'))


def test_genuine_ambiguity_requires_checked_proposal_and_survives_reload(job):
    start_guard(job);install_proposal(job, 'need_user');g.put(job)
    restored = g.get(job['id'])
    restored['contract_decision']['result']['verdict'] = 'revise'
    assert sr.apply_clarification(restored) == 'contract_review'
    assert not restored.get('requirement_question')
    restored['contract_decision']['result']['verdict'] = 'approve'
    assert sr.apply_clarification(restored) is None
    assert g.get(job['id'])['status'] == 'awaiting_requirement'


def test_historical_restoration_through_call_and_review(job, monkeypatch):
    # Replay the complete actual restoration envelope, not just a merge helper.
    root = Path(__file__).parent / 'fixtures'
    e = json.loads((root / 'inventory_obligation_drift.json').read_text())
    response = json.loads((root / 'inventory_restoration_response.json').read_text())['patch']
    job.update(request=e['request'], contract=e['contract'], contract_hash=g.digest(e['contract']),
               inventory_correction=e['state'], demand_previous_requirements=e['previous'],
               demand_previous_source_hash=e['previous_source_hash'])
    folder = g.root(job) / e['state']['asset'];folder.mkdir()
    (folder / 'inventory-candidate.json').write_text(json.dumps(e['candidate']))
    response['binding'] = ir.binding(job)
    monkeypatch.setattr(mock, 'role_response', lambda stage, ctx: response)
    before = ff.files(job)
    result = flow.call(job, 'spec_review')
    assert ff.apply_review(job) == 'evaluation'
    assert ff.files(job) == before
    assert g.get(job['id'])['spec_approval']['contract_hash'] == job['contract_hash']
    assert all(any(x['id'] == old['id'] and x['quote'] == old['quote'] for x in result['requirements']) for old in e['previous'])


def test_development_budget_is_explicit_and_defaults_unchanged():
    normal = budget.BudgetPolicy(); dev = budget.BudgetPolicy.development()
    assert normal.requests == 24 and normal.seconds == 1200
    assert dev.requests == 64 and dev.seconds == 7200 and dev.output_multiplier == 4


def test_missing_binding_is_not_reported_as_asset_version_change(job, monkeypatch):
    expected = {'contract_hash': job['contract_hash'], 'source_hash': 'a'*64,
                'inventory_hash': 'b'*64, 'evaluation_hash': 'c'*64}
    monkeypatch.setattr(dc, 'binding', lambda j: expected)
    raw = {'binding': {k:v for k,v in expected.items() if k != 'inventory_hash'},
           'findings': [{'requirement_id': 'example', 'verdict': 'test_gap', 'witnesses': [],
                         'reason': '检查完整绑定字段，不误报为已经发生资产版本改变。'}], 'omissions': []}
    with pytest.raises(ff.Invalid, match='字段缺失') as caught:dc.validate_review(job, raw)
    assert caught.value.feedback['missing_fields'] == ['inventory_hash']
    assert caught.value.feedback['changed_fields'] == []


def test_clarification_real_coordinator_reaches_builder_and_restart_reuses_checks(job, monkeypatch):
    start_guard(job);install_proposal(job, 'correction')
    proposal = copy.deepcopy(job['contract_candidate']['proposal'])
    check = copy.deepcopy(job['contract_decision']['result'])
    job.pop('contract_candidate');job.pop('contract_decision');g.put(job)
    calls = []
    def respond(stage, ctx):
        calls.append(stage)
        if stage == 'contract_review':return proposal
        if stage == 'contract_check':return check
        if stage == 'project_build':
            assert ctx['review_feedback']['reviewed_correction'] == proposal['changes']
            raise InterruptedError('验证到达真实构建入口后停止，不执行任务代码')
        raise AssertionError(stage)
    monkeypatch.setattr(mock, 'role_response', respond)
    g.POOL.acquire();g.ACTIVE.add(job['id'])
    ff.run(job['id'], 'contract_review')
    assert calls == ['contract_review', 'contract_check', 'project_build']
    restored = g.get(job['id'])
    assert restored['clarification_reviews'] and not restored.get('requirement_question')
    # Simulate a restart after a successful check but before application.
    start_guard(restored);install_proposal(restored, 'correction')
    restored.update(status='interrupted', checkpoint='contract_check',
                    attempts=[{'stage': 'contract_check', 'status': 'completed'}])
    g.put(restored);launched = []
    monkeypatch.setattr(g, 'launch', lambda id, stage: launched.append(stage) or {})
    ff.resume(restored['id'])
    assert launched == ['contract_apply']


def test_public_version_semantics_is_shared_citable_and_new_job_only(job):
    from backend import contract_revision as cr
    start_guard(job);install_proposal(job, 'correction')
    policy = cr.published_defaults(job)['version_semantics']
    for stage in ('contract_review', 'contract_check'):
        ctx = roles.context(job, stage)
        assert ctx['published_defaults']['version_semantics'] == policy
        assert not {'files', 'evaluation', 'private_fault_requirements', 'matrix'} & set(ctx)
    proposal = copy.deepcopy(job['contract_candidate']['proposal'])
    proposal['changes'][0].update(source='default.version_semantics', quote=policy)
    with pytest.raises(ValueError, match='不能决定用户业务语义'):cr.validate_proposal(job, proposal)
    proposal['changes'][0].update(path='/simulation', before=job['contract']['simulation'],
                                  after=job['contract']['simulation']+'；正常与参考版本遵循正确标准，故障版用于复现症状。')
    assert cr.validate_proposal(job, proposal)['changes'][0]['source'] == 'default.version_semantics'
    job.pop('stage_recovery_version')
    assert 'version_semantics' not in cr.published_defaults(job)
    with pytest.raises(ValueError):cr.validate_proposal(job, proposal)


def test_existing_protocol_incompatibility_still_stops_before_retry(job):
    reason = {'category': 'path_protocol_incompatible', 'reason': '已有路径不能用当前协议解释'}
    with pytest.raises(recovery.Stop, match='显式新版本'):
        recovery.handle(job, 'evaluation', ValueError(), reason)
    assert not job.get('stage_corrections')
