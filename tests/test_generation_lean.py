import copy
import json
import time
import pytest
from pydantic import ValidationError
from backend import generation as g, generation_flow as flow, generation_fault_first as ff
from backend import generation_lean as lean, generation_roles as roles, generation_mock as mock
from backend import generation_demands as demands, generation_safeguards as guard
from backend import generation_fault_recovery as recovery, generation_recovery_policy as rp
from backend import generation_corrections as corrections, generation_budget as budget
from backend.generation_schema import Request


@pytest.fixture
def job(client):
    j = g.create(Request(requirement='验证整数求和，包括负数和空列表，不允许过滤负数。'),
                 direct_build=True, fault_first=True, lean=True)
    flow.call(j, 'project_build'); ff.install(j)
    flow.call(j, 'spec_review'); assert ff.apply_review(j) == 'evaluation'
    flow.call(j, 'evaluation')
    return j


def approve(j):
    flow.call(j, 'coverage_review')
    assert lean.apply_coverage(j) == 'fingerprint'


def test_api_and_old_job_protocols_are_separate(client):
    response = client.post('/api/generation/jobs', json={'requirement':'验证本地求和的负数过滤故障。'})
    assert response.status_code == 200
    j = g.get(response.json()['id'])
    assert lean.enabled(j) and rp.enabled(j) and corrections.enabled(j)
    assert not demands.enabled(j) and not guard.enabled(j)
    assert response.json()['lean_version'] == lean.VERSION
    old = g.create(Request(requirement='历史清单任务继续使用旧协议。'), direct_build=True,
                   fault_first=True, demand_coverage=True, safeguards=True)
    assert demands.enabled(old) and guard.enabled(old) and not lean.enabled(old)
    with pytest.raises(ValueError, match='混用'):
        g.create(Request(requirement='禁止新旧清单协议混用测试。'), direct_build=True,
                 fault_first=True, lean=True, demand_coverage=True)


def test_compact_schema_no_dsl_quotes_or_copied_hashes(job):
    schema = roles.schema(job, 'spec_review').model_json_schema()
    text = json.dumps(schema)
    assert not any(k in text for k in ('output_rules', 'requirements', 'contract_hash', 'replacement_rules'))
    result = g.asset(job, 'spec_review')
    assert result['contract_hash'] == job['contract_hash'] and result['output_rules'] == []
    assert 'demand_inventory' not in job and 'standard_candidate' not in job


@pytest.mark.parametrize('stage', ['spec_review', 'evaluation', 'coverage_review', 'fingerprint'])
def test_contexts_do_not_leak_implementation(job, stage):
    if stage == 'fingerprint':approve(job)
    text = json.dumps(roles.context(job, stage), ensure_ascii=False)
    assert 'def total(' not in text and 'actual' not in roles.context(job, stage)
    if stage != 'fingerprint':assert 'private_fault_requirements' not in text
    if stage in ('spec_review', 'coverage_review'):
        assert 'distinct' not in json.dumps(roles.schema(job, stage).model_json_schema())
        assert job['request']['requirement'] in roles.messages(job, stage)[-1]['content']


def test_no_execution_without_current_coverage_opinion(job):
    with pytest.raises(ff.Invalid, match='覆盖复查'):ff.execute_phase(job, 'faulty')
    approve(job); lean.require_approved(job)
    original = copy.deepcopy(job['request'])
    job['request']['requirement'] += '补充新的目标要求'
    with pytest.raises(ff.Invalid):lean.require_approved(job)
    job['request'] = original
    ev = copy.deepcopy(g.asset(job, 'evaluation'));ev['cases'][0]['expected'] = 999
    ff.save_asset(job, 'evaluation', ev)
    with pytest.raises(ff.Invalid):lean.require_approved(job)


def test_append_only_repairs_and_feedback_no_extra_review_layer(job, monkeypatch):
    original = copy.deepcopy(g.asset(job, 'evaluation'))
    gaps = ['缺少含多个负数的输入，必须观察完整求和结果。']
    ff.save_asset(job, 'coverage_review', {'decision':'add_tests','reason':'需要补充多负数场景，已有场景与期望应原样保留。','missing_scenarios':gaps})
    assert lean.apply_coverage(job) == 'evaluation'
    assert lean.apply_coverage(job) == 'evaluation' and job['lean_repairs']['tests'] == 1
    ctx = roles.context(job, 'evaluation')
    assert ctx['missing_scenarios'] == gaps and ctx['append_only']['existing_cases'] == original['cases']
    case = copy.deepcopy(original['cases'][1]);case.update(id='two_negatives', input={'values':[-2,-3,8]}, expected=3)
    payload = {'base_hash':g.digest(original), 'added_cases':[case]}
    monkeypatch.setattr(mock, 'role_response', lambda *_:payload)
    flow.call(job, 'evaluation')
    assert g.asset(job, 'evaluation')['cases'] == original['cases'] + [case | {'faulty_expected':None}]
    assert g.asset(job, 'evaluation')['evasion_checks'] == original['evasion_checks']
    assert not job.get('demand_test_repair') and ff.next_checkpoint(job) == 'coverage_review'
    monkeypatch.setattr(mock, 'role_response', lambda *_:lean.mock_response('coverage_review'))
    approve(job);assert not job.get('lean_pending_gaps')
    assert not {'inventory_review','standard_review'} & {a['stage'] for a in job['attempts']}


@pytest.mark.parametrize('kind', ['stale', 'duplicate', 'rewrite'])
def test_bad_append_preserves_tests_and_returns_actual_error(job, monkeypatch, kind):
    old = copy.deepcopy(g.asset(job, 'evaluation'))
    ff.save_asset(job, 'coverage_review', {'decision':'add_tests','reason':'还需要一个独立观察负数的具体边界场景。', 'missing_scenarios':['多个负数和正数混合的输入及完整结果。']})
    lean.apply_coverage(job)
    raw={'base_hash':g.digest(old), 'added_cases':[copy.deepcopy(old['cases'][0])]}
    if kind=='stale':raw['base_hash']='0'*64
    if kind=='rewrite':raw['cases']=old['cases']
    monkeypatch.setattr(mock,'role_response',lambda *_:raw)
    with pytest.raises(ValueError) as exc:flow.call(job,'evaluation')
    flow.failure(job,'evaluation',exc.value)
    assert g.asset(job,'evaluation') == old
    assert roles.context(job,'evaluation').get('schema_error')
    assert job.get('demand_test_repair')


def test_interface_gap_returns_directly_to_builder_with_concrete_issue(job):
    before = copy.deepcopy(job['contract'])
    value={'decision':'specification_issue','reason':'要在同次调用比较两个会话，当前接口只能传一个会话。',
           'issues':[{'path':'/input_schema','reason':'增加同次调用内的两个独立会话输入以验证共享状态隔离。'}]}
    ff.save_asset(job,'coverage_review',value)
    assert lean.apply_coverage(job)=='project_build'
    assert job['contract']==before and job['design_rebuild']['issue']['issues']==value['issues']
    ctx=roles.context(job,'project_build')
    assert ctx['design_revision']['issue']['issues']==value['issues']
    assert not guard.enabled(job) and not job.get('interface_proposal')


def test_spec_patch_direct_install_preserves_faulty_code_and_rechecks(job):
    original=copy.deepcopy(ff.files(job));contract=copy.deepcopy(job['contract'])
    v={'decision':'revise','reason':'正常算术和规则需要明确负数参与计算，其他范围保持。',
       'issues':[{'path':'/behaviors/sum','reason':'明确所有整数包括负数均参与算术求和。'}]}
    assert lean.request_spec(job,v)=='project_build'
    flow.call(job,'project_build');assert not job.get('standard_candidate')
    ff.install(job)
    assert ff.files(job)==original and job['contract_version']==2
    assert ff.next_checkpoint(job)=='spec_review'
    assert job['contract']['behaviors']['sum']==contract['behaviors']['sum']+'（MOCK局部澄清夹具）'
    assert job['contract']['input_schema']==contract['input_schema']


def test_limits_and_output_retry_do_not_need_old_checklist_flags(job):
    reason={'category':'output_limit','reason':'供应商正文输出耗尽'}
    assert recovery.handle(job,'evaluation',ValueError('length'),reason)=='evaluation'
    assert rp.recovering_output(job,'evaluation')
    before=budget.reserve(job,'evaluation',10)
    assert before>0
    with pytest.raises(recovery.Stop,match='仍耗尽'):
        recovery.handle(job,'evaluation',ValueError('length'),reason)
    lean.reserve_repair(job,'tests');lean.reserve_repair(job,'tests')
    with pytest.raises(recovery.Stop):lean.reserve_repair(job,'tests')


def test_restart_reuses_completed_review_without_model_calls(job, monkeypatch):
    flow.call(job,'coverage_review')
    job.update(status='interrupted',checkpoint='coverage_review');g.put(job)
    targets=[];monkeypatch.setattr(g,'launch',lambda id,stage:targets.append(stage))
    ff.resume(job['id'])
    assert targets==['fingerprint']
    loaded=g.get(job['id']);lean.require_approved(loaded)
    assert len(loaded['lean_coverage_reviews'])==1


@pytest.mark.skipif(not g.executor.availability()[0], reason='Docker不可用，不降级宿主')
def test_full_lean_coordinator_docker_freeze_and_publish_gate(client):
    j=g.create(Request(requirement='MOCK验证求和故障平台完整编排，不代表真实生成。'),direct_build=True,fault_first=True,lean=True)
    g.launch(j['id'],'project_build');deadline=time.monotonic()+180
    while j['id'] in g.ACTIVE and time.monotonic()<deadline:time.sleep(.1)
    j=g.get(j['id'])
    assert j['status']=='awaiting_review',j.get('error')
    stages=[a['stage'] for a in j['attempts']]
    assert stages==['project_build','spec_review','evaluation','coverage_review','fingerprint','fix_patch','evasions','teaching']
    assert j['matrix']['complete'] and j['matrix']['passed'] and all(j['matrix']['gates'].values())
    assert j['fault_freeze'] and j['fix_acceptance']['passed']
    assert 'demand_inventory' not in j and not j.get('standard_candidate')
    ff.verify_fixed(j);lean.require_approved(j)
    old=g.digest(g.asset(j,'evaluation'))
    ff.save_asset(j,'evaluation',{**g.asset(j,'evaluation'),'evasion_checks':['已改变期望依据','不能沿用旧审核']})
    assert g.digest(g.asset(j,'evaluation')) != old
    with pytest.raises(ValueError):g.publish(j['id'],True,'隔离测试：改动证据后不可沿用旧批准。',j['review_digest'])


def test_stored_spec_review_cannot_rebind_to_changed_contract(job):
    stored=copy.deepcopy(g.asset(job,'spec_review'))
    job['contract_hash']='a'*64
    with pytest.raises(ff.Invalid,match='旧版本'):lean.validate_spec(job,stored)


def test_published_fork_keeps_lean_and_preserves_parent(job):
    from backend import storage as s
    job.update(status='published',published_task='gen_fixture',published_version='1.0.0')
    g.put(job);s.put('task','gen_fixture',{'version':'1.0.0'})
    before=copy.deepcopy(g.get(job['id']))
    child=g.fork(job['id'])
    created=g.get(child['id'])
    assert lean.enabled(created) and not demands.enabled(created) and not guard.enabled(created)
    assert corrections.enabled(created) and rp.enabled(created)
    assert created['target_version']=='1.0.1' and g.get(job['id'])==before


def test_provider_failure_does_not_erase_pending_validation_feedback(job,monkeypatch):
    from backend import agent
    approve(job)
    pending={'category':'classification_or_impact_error','reason':'主要复现字段必须来自已审核错误断言',
             'validation_feedback':{'case_id':'negative','received_paths':[['wrong']],
                                    'available_fault_paths':[[]]}}
    job['attempts'].append({'stage':'fingerprint','status':'failed',
        'contract_version':job['contract_version'],'error':pending,'asset':'original-rejection'})
    job['attempts'].append({'stage':'fingerprint','status':'failed',
        'contract_version':job['contract_version'],
        'error':{'category':'provider_http','reason':'供应商返回 HTTP 402'},'asset':'provider-refusal'})
    flow.failure(job,'fingerprint',agent.ModelFailure('provider_http','供应商返回 HTTP 402'))
    seen=[];original=mock.role_response
    def reply(stage,payload):
        seen.append(payload);return original(stage,payload)
    monkeypatch.setattr(mock,'role_response',reply)
    flow.call(job,'fingerprint')
    assert seen[0]['validation_feedback']==pending['validation_feedback']
    assert seen[0]['error']==pending['reason']
    assert job['failure_history'][-1]['error']['category']=='provider_http'


@pytest.mark.parametrize('barrier',['completed','new_version','other_stage'])
def test_restored_validation_guidance_cannot_cross_a_new_basis(job,barrier):
    pending={'category':'schema_shape','reason':'旧版本的字段校验错误'}
    job['attempts']=[{'stage':'evaluation','status':'failed','error':pending,
                     'contract_version':job['contract_version']}]
    if barrier=='completed':job['attempts'].append({'stage':'evaluation','status':'completed','contract_version':job['contract_version']})
    if barrier=='new_version':job['contract_version']+=1
    if barrier=='other_stage':job['attempts'].append({'stage':'spec_review','status':'completed','contract_version':job['contract_version']})
    job['format_error']={'stage':'evaluation','reason':'供应商返回 HTTP 402'}
    lean.restore_validation_feedback(job,'evaluation')
    assert job['format_error']['reason']=='供应商返回 HTTP 402'
