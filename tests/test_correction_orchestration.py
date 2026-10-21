"""Exercise automatic correction orchestration and restart checkpoints.

The role responses are deterministic fixtures; candidate code still runs only
in Docker. These checks do not measure real model generation quality.
"""
import copy

import pytest

from backend import generation as g, generation_fault_first as ff, generation_flow as flow
from backend import generation_mock as mock, generation_safeguards as guard
from backend import generation_fault_recovery as recovery, generation_corrections as corrections
from backend import generation_roles as roles, storage as s
from backend.generation_schema import Request


def create():
    return g.create(Request(requirement='MOCK验证自动补测和恢复：正常累加所有整数，过滤负数为故障；空列表为零。'),
                    direct_build=True, fault_first=True, demand_coverage=True,
                    safeguards=True, executable_corrections=True)


def added_case():
    return {'id': 'extra_hidden_negative', 'group': 'target', 'visibility': 'hidden',
            'covers': ['sum'], 'input': {'values': [-4, 9]}, 'expected': 5,
            'classification_reason': '离线固定场景补充负数过滤触发，仅验证程序路由与执行。'}


def supplement(j):
    return {'code_hash': recovery.review_context(j)['code_hash'],
            'decision': 'add_coverage', 'requirement_refs': [], 'issue': None,
            'next_change': '追加不同负数输入的隐藏场景，独立确定完整累加期望。',
            'coverage_request': {'source': 'request.requirement',
                'quote': j['request']['requirement'], 'behavior_ids': ['sum'],
                'insufficient_cases': ['negative'], 'visibility': 'hidden',
                'trigger_condition': '使用不同的负整数与正整数输入，验证完整累加与过滤结果不同。'}}


def seed(j, through_evaluation=False):
    flow.call(j, 'project_build'); ff.install(j)
    flow.call(j, 'spec_review'); assert ff.apply_review(j) == guard.STAGE
    if through_evaluation:
        flow.call(j, guard.STAGE); assert guard.apply_standard(j) == 'evaluation'
        flow.call(j, 'evaluation')


def test_coordinator_automatically_repairs_and_finishes_docker_matrix(client, monkeypatch):
    ready, reason = g.executor.availability()
    if not ready:
        pytest.skip(reason)
    j = create(); original = mock.role_response; seen = []; base = []

    def respond(stage, ctx):
        seen.append(stage)
        current = g.get(j['id'])
        if stage == 'fingerprint' and not current.get('trigger_coverage_repairs'):
            # Simulate a malformed fingerprint until the normal bounded
            # recovery route calls fault_review; never select the next stage here.
            return {'decision': 'checks', 'explanation': '', 'checks': [],
                    'case_impacts': [], 'issue': None}
        if stage == 'fault_review':
            base.extend(copy.deepcopy(g.asset(current, 'evaluation')['cases']))
            return supplement(current)
        if stage == 'evaluation' and 'append_only' in ctx:
            return {'base_hash': ctx['append_only']['base_hash'], 'added_cases': [added_case()]}
        return original(stage, ctx)

    monkeypatch.setattr(mock, 'role_response', respond)
    assert g.POOL.acquire(False)
    ff.run(j['id'], 'project_build')
    done = g.get(j['id'])
    assert done['status'] == 'awaiting_review', done.get('error')
    assert done['matrix']['passed'] and done['matrix']['complete']
    assert done['fault_freeze'] and done['fix_acceptance']['passed']
    assert done['trigger_coverage_repairs'] == 1
    assert g.asset(done, 'evaluation')['cases'][:-1] == base
    assert [a['stage'] for a in done['attempts']].count('evaluation') == 2
    start = seen.index('fault_review')
    assert seen[start:start + 4] == ['fault_review', 'evaluation', 'coverage_review', 'fingerprint']
    assert all(c['repeat_count'] == 2 and c['stable'] for c in done['matrix']['checks'])
    assert {'faulty', 'normal', 'reference'} <= {c['version'] for c in done['matrix']['checks']}
    assert done['trigger_supplement_history']


@pytest.mark.parametrize('checkpoint', ['before_standard', 'after_standard', 'before_append', 'after_append'])
def test_restart_reuses_completed_assets_and_preserves_correction_counters(client, monkeypatch, checkpoint):
    j = create(); seed(j, through_evaluation='append' in checkpoint)
    if 'append' in checkpoint:
        original = mock.role_response
        def respond(stage, ctx):
            if stage == 'fault_review': return supplement(j)
            if stage == 'evaluation' and 'append_only' in ctx:
                return {'base_hash': ctx['append_only']['base_hash'], 'added_cases': [added_case()]}
            return original(stage, ctx)
        monkeypatch.setattr(mock, 'role_response', respond)
        flow.call(j, 'fault_review')
        assert recovery.apply_review(j, g.asset(j, 'fault_review')) == 'evaluation'
        if checkpoint == 'after_append': flow.call(j, 'evaluation')
        stage = 'evaluation'
        expected = 'coverage_review' if checkpoint == 'after_append' else 'evaluation'
    else:
        if checkpoint == 'after_standard': flow.call(j, guard.STAGE)
        stage = guard.STAGE
        expected = 'evaluation' if checkpoint == 'after_standard' else guard.STAGE

    corrections.retry(j, stage, {'category': 'schema_shape', 'reason': '离线预置一次不同格式错误，检查恢复不会清零。'})
    count = j['request_count']; counters = copy.deepcopy(j['correction_work_items'])
    assets = copy.deepcopy(j['assets'])
    j.update(stage=stage, checkpoint=stage, status='evaluating' if 'append' in checkpoint else 'reviewing_spec')
    g.put(j)
    launches = []; monkeypatch.setattr(g, 'launch', lambda ident, target: launches.append((ident, target)))
    s.init()  # Same persisted startup recovery used by a new backend process.
    restarted = g.get(j['id'])
    assert restarted['status'] == 'interrupted'
    assert launches == []  # Restart itself must not repeat a paid request.
    ff.resume(j['id'])
    restored = g.get(j['id'])
    assert launches == [(j['id'], expected)]
    assert restored['request_count'] == count
    assert restored['correction_work_items'] == counters
    if 'append' in checkpoint:
        assert restored['trigger_coverage_repairs'] == 1
        assert restored['assets']['evaluation'] == assets['evaluation']
        if checkpoint == 'after_append':
            assert g.asset(restored, 'evaluation')['cases'][-1]['id'] == added_case()['id']
        else:
            assert roles.schema(restored, 'evaluation') is guard.Additions
    elif checkpoint == 'after_standard':
        assert not restored.get('standard_candidate')
        assert restored['standard_approval']['review_asset'] == assets[guard.STAGE]
    else:
        assert restored['standard_candidate']


def test_inflight_restart_records_unknown_outcome_without_automatic_recall(client, monkeypatch):
    j = create(); seed(j)
    j.update(stage=guard.STAGE, checkpoint=guard.STAGE, status='reviewing_spec')
    j['attempts'].append({'stage': guard.STAGE, 'status': 'running', 'reserved_tokens': 100,
                          'asset': 'inflight-fixture'})
    j['request_count'] += 1
    j['budget']['output_charged'] += 100
    corrections.retry(j, guard.STAGE, {'category': 'schema_shape', 'reason': '预置恢复前的纠正次数。'})
    counters = copy.deepcopy(j['correction_work_items']); g.put(j)
    launches = []; monkeypatch.setattr(g, 'launch', lambda *args: launches.append(args))
    s.init(); restored = g.get(j['id'])
    assert restored['status'] == 'interrupted' and launches == []
    assert restored['attempts'][-1]['status'] == 'outcome_unknown'
    assert restored['attempts'][-1]['charged_tokens'] == 100
    assert restored['correction_work_items'] == counters
    unknown = restored['budget']['unknown_usage']
    s.init()  # Repeated startup does not double-count the unknown request.
    assert g.get(j['id'])['budget']['unknown_usage'] == unknown
