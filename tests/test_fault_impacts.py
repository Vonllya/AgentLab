"""Exact historical assets exercise routing; controlled candidates test mechanics.

These fixtures are author-only generated assets, not learner content. Hand-made
corrections below are system regression evidence, never model success evidence.
"""
import copy
import json
from pathlib import Path
import pytest
from backend import generation as g, generation_fault_first as ff, generation_flow as flow
from backend import generation_fault_obligations as ob, generation_fault_recovery as recovery
from backend.generation_schema import Request
from test_fault_first import prepared

FIXTURES = Path(__file__).parent/'fixtures/fault_first'


def historical(name):
    return json.loads((FIXTURES/(name+'.json')).read_text())


def load_history(name):
    source=historical(name)
    j=g.create(Request.model_validate(source['request']),direct_build=True,fault_first=True)
    ff.save_asset(j,'project_build',source['assets']['project_build']);ff.install(j)
    for key in ('evaluation','fingerprint','fault_candidate'):
        if key in source['assets']:ff.save_asset(j,key,source['assets'][key])
    return j


def source_ref(j):
    return {'source':'private_fault_requirements'}


def missing_rule(j):
    return {'kind':'public_rule_missing','case_ids':['public_regression_degrade_never_ready'],
            'source_refs':[{'source':'contract/behaviors/degrade_with_diagnostics'}],
            'explanation':'规范仅要求保留诊断，没有规定diagnostics精确文本；不能把测试自行选择的字符串当作公开要求。'}


def reviewed_startup():
    j=load_history('early_exit')
    # Explicit new public-rule fixture, independent from the observed string.
    # This is NOT automatic adaptation of original expectations to faulty code.
    bundle=copy.deepcopy(g.asset(j,'project_build'))
    bundle['contract']['simulation']+=' 降级时diagnostics固定为["backend not ready within max_attempts"]，不要求该文本声称实际探测次数。'
    bundle['faulty']['app.py']=bundle['faulty']['app.py'].replace('backend not ready on first probe','backend not ready within max_attempts')
    ff.save_asset(j,'project_build',bundle);ff.install(j)
    ff.save_asset(j,'evaluation',historical('early_exit')['assets']['evaluation'])
    evaluation=g.asset(j,'evaluation');old=historical('early_exit')['assets']['fingerprint']
    impacts=[]
    for c in evaluation['cases']:
        d=c['input']; affected=[]; assertions=[]
        if d['backend_ready_after_attempts']>1:
            wrong={'status':'degraded','attempts':1,'registered_tools':[],
                   'diagnostics':['backend not ready within max_attempts']}
            for key,value in c['expected'].items():
                if g.digest(wrong[key])!=g.digest(value):
                    affected.append([key]);assertions.append({'path':[key],'equals':wrong[key]})
        impacts.append({'case_id':c['id'],'affected_paths':affected,'fault_assertions':assertions,
                        'source_refs':[source_ref(j)],'reason':'首次未就绪提前结束使探测次数缩短，首次就绪与单次窗口的正确输出保留。'})
    ff.save_asset(j,'fingerprint',ob.normalize(j,{'checks':old['checks'],'case_impacts':impacts,'binding':ob.binding(j)}))
    return j


def test_historical_missing_text_routes_to_public_review_without_implementation(client):
    j=load_history('early_exit');before=copy.deepcopy(g.asset(j,'evaluation'))
    value={'decision':'specification_issue','explanation':'公开诊断文本规则缺失，不能采信精确字符串期望。','checks':[], 'case_impacts':[], 'issue':missing_rule(j)}
    parsed=ff.ReviewedFaultResponse.model_validate(value).model_dump()
    with pytest.raises(recovery.Revision) as e:ff.validate_response(j,'fingerprint',parsed)
    assert recovery.apply_revision(j,e.value.issue)=='spec_review'
    from backend import generation_roles as roles
    ctx=roles.context(j,'spec_review');text=json.dumps(ctx,ensure_ascii=False)
    assert 'def scenario' not in text and 'private_fault_requirements' not in text
    assert 'public_regression_degrade_never_ready' not in text
    assert 'backend not ready within max_attempts' not in text
    assert g.asset(j,'evaluation')==before


def test_historical_indistinguishable_design_rebuild_is_versioned_and_bounded(client):
    j=load_history('indistinguishable');old_assets=copy.deepcopy(j['assets']);before=j['contract_version']
    case=g.asset(j,'evaluation')['cases'][0]['id']
    issue={'kind':'design_indistinguishable','case_ids':[case],'source_refs':[source_ref(j)],
           'explanation':'正常规范和私有设计都把首次未就绪时返回空工具列表作为输出，必须先修订未冻结设计，不能编造相反故障。'}
    assert recovery.apply_revision(j,issue)=='project_build'
    ctx=ff.context(j,'project_build')
    assert ctx['requirement']==j['request'] and 'design_revision' in ctx and 'frozen_private_fault_requirements' not in ctx
    assert 'actual' not in json.dumps(ctx)
    with pytest.raises(recovery.Stop):recovery.apply_revision(j,issue)
    bundle=copy.deepcopy(g.asset(j,'project_build'));bundle['private_fault_requirements']+=' 设计修订用于流程回归；真实需求歧义仍须由用户明确。'
    ff.validate_response(j,'project_build',bundle)
    j['previous_bundle_assets']=copy.deepcopy(j['assets']);ff.save_asset(j,'project_build',bundle);ff.install(j)
    assert j['contract_version']==before+1 and 'fingerprint' not in j['assets'] and 'evaluation' not in j['assets']
    assert j['contract_history'][-1]['assets']==old_assets and j['design_revision_history']
    assert 'fixed_candidate' not in j['assets']


def test_impacts_keep_expected_and_preserve_fields_without_future_special_case(client):
    j=reviewed_startup();ev=g.asset(j,'evaluation');finger=g.asset(j,'fingerprint')
    assert ev==historical('early_exit')['assets']['evaluation']
    case=next(c for c in ev['cases'] if c['id']=='public_regression_degrade_never_ready')
    rules=ob.obligations(j,case)
    assert next(r for r in rules if r['objective']=='preserve_specified_fault')['assertions']==[{'path':['attempts'],'equals':1}]
    preserve=next(r for r in rules if r['objective']=='correct_behavior')['assertions']
    assert {'path':['status'],'equals':'degraded'} in preserve
    assert {'path':['registered_tools'],'equals':[]} in preserve
    assert {'path':['diagnostics'],'equals':['backend not ready within max_attempts']} in preserve
    boundary=next(i for i in finger['case_impacts'] if i['case_id']=='hidden_regression_degrade_diagnostics_present')
    assert boundary['affected_paths']==[]  # max_attempts=1: do not manufacture a difference.
    original=copy.deepcopy(finger)
    finger['case_impacts'][0]['affected_paths'].append(['diagnostics'])
    with pytest.raises(ff.Invalid):ob.normalize(j,finger)  # no blanket exception
    assert g.asset(j,'fingerprint')==original


@pytest.mark.skipif(not g.executor.availability()[0],reason='Docker不可用，不降级宿主')
def test_historical_startup_fault_and_fix_pass_both_matrices(client):
    j=reviewed_startup();before=copy.deepcopy(g.asset(j,'evaluation'))
    assert ff.execute_phase(j,'faulty')['passed']
    rules=ff.repair_requirements(j,'faulty')['requirements']
    assert all(r['state']=='must_preserve' for r in rules)
    patch={'base_hash':g.digest(ff.files(j)),'decision':'patch','changes':[{
        'path':'app.py','before':ff.files(j)['app.py'],
        'after':'''from probe import probe_backend
from registry import register_tools
def scenario(data):
    for attempt in range(1, data['max_attempts'] + 1):
        if probe_backend(data['backend_ready_after_attempts'], attempt):
            return {'status':'registered','attempts':attempt,'registered_tools':register_tools(data['tools']),'diagnostics':[]}
    return {'status':'degraded','attempts':data['max_attempts'],'registered_tools':[],'diagnostics':['backend not ready within max_attempts']}
''','reason':'正常循环仅观察当前探测结果，不按未来是否就绪特判。'}], 'explanation':'系统功能回归手写正常循环，不计真实模型修复成功。'}
    ff.validate_response(j,'fix_patch',patch);assert ff.execute_phase(j,'fixed')['passed']
    fixed=g.asset(j,'fixed_candidate')['files']
    e1=copy.deepcopy(fixed);e1['registry.py']='def register_tools(tools):\n return []'
    e2=copy.deepcopy(fixed);e2['registry.py']='def register_tools(tools):\n return list(reversed(tools))'
    ff.validate_response(j,'evasions',{'evasions':{'drop':e1,'reverse':e2},'evasion_explanations':{'drop':'遗漏全部工具，首次就绪空清单场景仍正常。','reverse':'反转工具顺序，与公开注册列表约定不符。'}})
    final=g.validate(j)
    assert final['passed'] and final['gates']['fault_preserved_fields'] and final['gates']['fault_behavior']
    assert g.asset(j,'evaluation')==before
    assert ff.files(j)['app.py'].count('probe_backend(ready_after, 1)')==1
    # A candidate that violates an unaffected field must fail the same shared gate.
    c=next(c for c in before['cases'] if c['id']=='public_regression_degrade_never_ready')
    row={'case':c['id'],'status':'failed','stable':True,'actual':{**c['expected'],'attempts':1,'registered_tools':['invented']}}
    assert not ob.annotate(j,c,row)['preservation_passed']
    # Check the real publish serializer when preliminary and reviewed groups
    # differ. Isolated test approval is not a human or learning-outcome claim.
    ff.save_asset(j,'teaching',{'brief':'离线系统功能验证：后端就绪探测可能提前结束，请检查不同探测窗口和工具清单的行为并提交依据。',
        'hints':['比较探测次数与就绪时刻。','检查首次未就绪后的控制流。','区分提前结束与持续探测，并验证降级场景。'],
        'followups':['始终未就绪时应发生什么？','空工具清单如何验证？'],
        'consistency_notes':'确定性系统功能夹具，仅核对公开就绪契约，不是模型生成或真实人工审核。'})
    j['status']='awaiting_review'
    j['review_digest']=g.digest({'contract':j['contract_hash'],'assets':{k:g.digest(g.asset(j,k)) for k in g.review_keys(j)},'matrix':j['matrix']});g.put(j)
    published=g.publish(j['id'],True,'隔离自动化发布测试；不计真实人工审核或学习效果。',j['review_digest'])
    folder=g.s.DATA/'published'/published['task_id']/published['version']
    public={c['id']:c for c in json.loads((folder/'public_cases.json').read_text())}
    assert public['public_target_ready_first_attempt']['group']=='regression'
    assert public['public_regression_degrade_never_ready']['group']=='target'
    assert public['public_regression_degrade_never_ready']['expected']==c['expected']
    assert 'case_impacts' not in (folder/'manifest.json').read_text()


def test_impacts_are_blind_and_every_path_has_real_source_and_assertion(client):
    j=prepared();ctx=ff.context(j,'fingerprint')
    assert len(ctx['candidate_cases'])==len(g.asset(j,'evaluation')['cases'])
    text=json.dumps(ctx);assert 'def total(' not in text and 'actual' not in ctx and 'matrix' not in ctx
    raw=copy.deepcopy(g.asset(j,'fingerprint'))
    raw['case_impacts'][0]['source_refs'][0]['source']='contract/does-not-exist'
    with pytest.raises(ff.Invalid):ob.normalize(j,raw)
    raw=copy.deepcopy(g.asset(j,'fingerprint'));raw['case_impacts']=raw['case_impacts'][:-1]
    with pytest.raises(ff.Invalid):ob.normalize(j,raw)
    raw=copy.deepcopy(g.asset(j,'fingerprint'));raw['binding']['contract_hash']='0'*64
    with pytest.raises(ff.Invalid):ob.normalize(j,raw)


def test_noop_original_responses_route_once_then_stop_and_counters_survive(client,monkeypatch):
    j=load_history('no_op');responses=historical('no_op')['responses']
    # The exact original response is AST-identical, never a successful candidate.
    response=responses[-1]
    assert response['base_hash']==g.digest(ff.files(j))
    with pytest.raises(ff.Invalid) as exc:ff.apply_patch(j,response)
    assert exc.value.category=='candidate_no_progress'
    assert recovery.handle(j,'fault_correct',exc.value,{'category':exc.value.category,'reason':str(exc.value)})=='fault_review'
    g.put(j);loaded=g.get(j['id'])
    with pytest.raises(recovery.Stop):recovery.handle(loaded,'fault_correct',exc.value,{'category':exc.value.category,'reason':str(exc.value)})
    assert len(recovery.state(loaded)['events'])==1 and 'fixed_candidate' not in loaded['assets']


def test_review_requires_real_unmet_refs_and_new_candidate_limit(client):
    j=reviewed_startup();j['matrix']={'id':'matrix','checks':[{'id':'actual-check','case':'public_regression_degrade_never_ready','status':'failed','stable':True,'actual':{'status':'degraded','attempts':99,'registered_tools':[],'diagnostics':['backend not ready within max_attempts']}}],'build_hash':g.digest(ff.files(j))}
    recovery.request_review(j,'fault_correct','candidate_no_progress')
    value={'code_hash':g.digest(ff.files(j)),'decision':'code','requirement_refs':['public_regression_degrade_never_ready:fault'],
           'issue':None,'next_change':'检查当前首次探测退出逻辑，保持未受影响输出，不按未来是否就绪特判。'}
    assert recovery.validate_review(j,value)==value
    assert recovery.apply_review(j,value)=='fault_correct'
    with pytest.raises(recovery.Stop):recovery.request_review(j,'fault_validation','candidate_no_progress')
    value['requirement_refs']=['made-up:correct']
    with pytest.raises(ValueError,match='不存在'):recovery.validate_review(j,value)


def test_cycle_detection_is_per_phase_and_ignores_layout(client):
    j=prepared();a=ff.files(j);b={**a,'calculator.py':'def total(values):\n return sum(values)'}
    recovery.remember_candidate(j,'faulty',a,b)
    with pytest.raises(ff.Invalid,match='回摆'):recovery.remember_candidate(j,'faulty',b,a)
    # Fixed generation starts from frozen fault baseline, its own history.
    recovery.remember_candidate(j,'fixed',a,b)


def test_legacy_fault_first_keeps_original_rules(client):
    j=g.create(Request(requirement='旧版故障流程兼容性回归'),direct_build=True,fault_first=True)
    j.pop('fault_impact_version');g.put(j)
    flow.call(j,'project_build');ff.install(j);flow.call(j,'spec_review');ff.apply_review(j)
    flow.call(j,'evaluation');flow.call(j,'fingerprint')
    assert ff.schema(j,'fingerprint') is ff.FaultResponse
    assert 'case_impacts' not in g.asset(j,'fingerprint')


def test_real_adapter_receives_noop_feedback_and_only_one_review(client,monkeypatch):
    from backend import agent
    j=prepared();j['mode']='real';seen=[]
    code=ff.files(j)['calculator.py']
    def complete(messages,**kwargs):
        ctx=json.loads(messages[-1]['content']);seen.append(ctx)
        if 'failure' in ctx:
            raw={'code_hash':ctx['code_hash'],'decision':'need_evidence','requirement_refs':[],'issue':None,
                 'next_change':'当前没有真实执行证明故障构造不符，保留原代码和证据并停止本条重复纠错路径。'}
        else:
            raw={'base_hash':ctx['base_hash'],'decision':'patch','changes':[{'path':'calculator.py','before':code,'after':code,'reason':'模拟历史无变化修改，不能被接受。'}],
                 'explanation':'确定性适配器模拟无变化响应，不作为真实模型联调。'}
        return {'content':json.dumps(raw),'_meta':{}}
    monkeypatch.setattr(agent,'completion',complete)
    monkeypatch.setattr(g.executor,'execute',lambda *a,**k:pytest.fail('无效补丁不得进入Docker'))
    j.update(coordinator_token='test-token',status='interrupted');g.put(j)
    assert g.POOL.acquire(blocking=False);g.ACTIVE.add(j['id'])
    ff.run(j['id'],'fault_correct');result=g.get(j['id'])
    assert result['status']=='no_progress' and result['request_count']==j['request_count']+2
    assert result['error']['category']=='insufficient_evidence'
    assert len(seen)==2 and seen[1]['failure']['validation_feedback']['before_equals_after']
    assert seen[1]['execution_evidence']['requirements']
    assert all(not r['execution_observed'] for r in seen[1]['execution_evidence']['requirements'])
    assert [a['stage'] for a in result['attempts'][-2:]]==['fault_correct','fault_review']


def test_spec_resume_does_not_apply_an_old_success_after_new_provider_failure(client,monkeypatch):
    j=prepared();j.update(status='waiting_provider',checkpoint='spec_review')
    j['attempts'].append({'stage':'spec_review','status':'failed'})
    g.put(j);calls=[];monkeypatch.setattr(g,'launch',lambda id,stage:calls.append(stage))
    ff.resume(j['id']);assert calls==['spec_review']


def test_completed_design_rebuild_resume_installs_without_another_model_call(client,monkeypatch):
    j=prepared();before=j['contract_version'];v=copy.deepcopy(g.asset(j,'project_build'))
    v['private_fault_requirements']+=' 本次新增设计审计说明。'
    j['previous_bundle_assets']=copy.deepcopy(j['assets']);ff.save_asset(j,'project_build',v)
    j.update(status='interrupted',checkpoint='project_build',design_rebuild={'issue':{'explanation':'测试设计重建完成后的断点恢复。'}})
    j['attempts'].append({'stage':'project_build','status':'completed'})
    g.put(j);calls=[];monkeypatch.setattr(g,'launch',lambda id,stage:calls.append(stage))
    ff.resume(j['id']);after=g.get(j['id'])
    assert calls==['spec_review'] and after['contract_version']==before+1
    assert 'design_rebuild' not in after and 'evaluation' not in after['assets']


def test_structured_conflict_cannot_claim_nonexistent_requirements(client):
    j=prepared();value={'base_hash':g.digest(ff.files(j)),'decision':'needs_review','changes':[],
                       'explanation':'测试明确拒绝分支引用要求，不能只提供笼统冲突结论。',
                       'conflicts':[{'left_ref':'negative:fault','right_ref':'made-up:preserve','explanation':'需要具体指出两个实际要求和适用输入条件。'}]}
    with pytest.raises(ff.Invalid) as e:ff.apply_patch(j,value)
    assert e.value.category=='patch_invalid' and 'negative:fault' in e.value.feedback['available_requirements']
    from pydantic import ValidationError
    with pytest.raises(ValidationError):ff.ReviewedPatch.model_validate({**value,'decision':'patch','changes':[{'path':'calculator.py','before':'a','after':'b','reason':'测试动作与冲突条目必须互斥。'}]})


def test_partial_field_scope_rejects_arrays_types_and_equal_values(client):
    j=reviewed_startup();raw=copy.deepcopy(g.asset(j,'fingerprint'))
    affected=next(i for i in raw['case_impacts'] if i['affected_paths'])
    affected['affected_paths']=[['registered_tools','0']]
    affected['fault_assertions']=[{'path':['registered_tools','0'],'equals':'not allowed'}]
    with pytest.raises(ff.Invalid,match='数组'):ob.normalize(j,raw)
    raw=copy.deepcopy(g.asset(j,'fingerprint'));affected=next(i for i in raw['case_impacts'] if i['affected_paths'])
    affected['affected_paths']=[['attempts']];affected['fault_assertions']=[{'path':['attempts'],'equals':True}]
    with pytest.raises(ff.Invalid,match='类型'):ob.normalize(j,raw)


def test_frozen_revision_and_impact_change_are_rejected(client):
    j=prepared();frozen=copy.deepcopy(ff.binding(j))
    j['fault_freeze']={'binding':frozen,'matrix_id':'accepted','environment':'test-image'}
    ff.save_asset(j,'fault_acceptance',{'id':'accepted','complete':True,'passed':True,'build_hash':frozen['faulty_hash'],'environment':'test-image'})
    ff.require_frozen(j)
    issue={'kind':'classification_or_impact_error','case_ids':['negative'],'source_refs':[source_ref(j)],
           'explanation':'即使给出有效引用，也不能原地重写已经冻结的影响审核资产。'}
    with pytest.raises(recovery.Stop,match='冻结'):recovery.apply_revision(j,issue)
    raw=copy.deepcopy(g.asset(j,'fingerprint'));raw['case_impacts'][0]['reason']+=' 修改审核说明也影响摘要。'
    ff.save_asset(j,'fingerprint',raw)
    with pytest.raises(ff.Invalid,match='已变化'):ff.require_frozen(j)


def test_new_batch_only_resets_local_limits_after_explicit_budget(client,monkeypatch):
    from backend.app import Regenerate
    j=prepared();recovery.request_review(j,'fault_correct','candidate_no_progress')
    j.update(status='no_progress',checkpoint='fault_correct');g.put(j);original=copy.deepcopy(g.get(j['id']))
    with pytest.raises(ValueError,match='新预算'):ff.resume(j['id'])
    monkeypatch.setattr(g,'launch',lambda *args:{})
    body=Regenerate(expected_revision=j['revision'],idempotency_key='case-impact-budget',repair_note='明确授权新的隔离批次，保留旧记录和执行依据。',new_batch=True,budget={'requests':3})
    result=flow.regenerate(j['id'],body);child=g.get(result['id'])
    assert 'fault_recovery' not in child and child.get('handoff_version') is None
    assert g.get(j['id'])==original
    assert flow.regenerate(j['id'],body)['id']==child['id']


def test_reviewer_guidance_cannot_leak_actual_or_code_back_into_blind_impacts(client):
    j=prepared();issue={'kind':'classification_or_impact_error','case_ids':['negative'],
        'source_refs':[source_ref(j)],'explanation':'已看过实际代码的诊断包含 def secret_marker(): 和 actual_observed_999，不能传回独立影响审核。'}
    assert recovery.apply_revision(j,issue)=='fingerprint'
    text=json.dumps(ff.context(j,'fingerprint'))
    assert 'secret_marker' not in text and 'actual_observed_999' not in text
    assert g.asset(j,'evaluation')


def test_primary_error_values_are_bound_once_not_generated_twice(client):
    j=prepared();finger=g.asset(j,'fingerprint')
    from backend.generation_mock import role_response
    raw=role_response('fingerprint',ff.context(j,'fingerprint'))
    assert all('assertions' not in c and 'paths' in c for c in raw['checks'])
    parsed=ff.ReviewedFaultResponse.model_validate(raw).model_dump()
    output=ff.validate_response(j,'fingerprint',parsed)
    assert output['checks']==finger['checks']


def test_review_wire_correction_is_once_not_another_semantic_loop(client):
    j=prepared();recovery.request_review(j,'fault_correct','candidate_no_progress')
    reason={'category':'schema_shape','reason':'复核输出缺少decision，须填写allowed_decisions中的动作。'}
    assert recovery.handle(j,'fault_review',ValueError(reason['reason']),reason)=='fault_review'
    with pytest.raises(recovery.Stop):recovery.handle(j,'fault_review',ValueError(reason['reason']),reason)


def test_public_rule_amendment_reuses_existing_protocol_and_copies_latest_code(client,monkeypatch):
    from backend import agent, generation_spec_patch as sp
    j=prepared();code=copy.deepcopy(ff.files(j));code['calculator.py']+='\n# Latest author candidate'
    ff.save_asset(j,'fault_candidate',{'files':code})
    private=j['private_fault_requirements'];symptoms=g.asset(j,'project_build')['symptom_paths']
    j['spec_review_feedback']={'reason':'补充公开模拟细节，不改代码或故障设计。','issues':[{'path':'/simulation','reason':'明确固定模拟的运行规则。'}]}
    j['mode']='real';seen=[]
    def complete(messages,**kwargs):
        ctx=json.loads(messages[-1]['content']);seen.append(ctx)
        assert 'allowed_paths' in ctx and ctx['frozen_assets']['faulty']==code
        raw={'contract_hash':ctx['contract_hash'],'bundle_hash':ctx['bundle_hash'],'decision':'patch',
             'changes':[{'path':'/simulation','before':ctx['public_specification']['simulation'],
                         'after':ctx['public_specification']['simulation']+' 明确补充的公开模拟约定。','reason':'仅补齐审核授权的公开规则字段。'}],
             'conflict_evidence':[],'explanation':'协议回归夹具；服务端复制其余冻结资产，模型不重发代码。'}
        return {'content':json.dumps(raw),'_meta':{}}
    monkeypatch.setattr(agent,'completion',complete)
    assert sp.enabled(j) and ff.schema(j,'project_build') is sp.Amendment
    flow.call(j,'project_build');new=g.asset(j,'project_build')
    assert len(seen)==1 and new['faulty']==code and new['private_fault_requirements']==private and new['symptom_paths']==symptoms
    old_hash=j['contract_hash'];ff.install(j)
    assert j['contract_hash']!=old_hash and 'evaluation' not in j['assets'] and 'fingerprint' not in j['assets']
    assert j['attempts'][-1]['specification_patch'] is True


def test_terminal_no_progress_exposes_exact_unmet_ids_and_author_evidence(client):
    j=prepared();j['matrix']={'id':'observed','build_hash':g.digest(ff.files(j)),
        'gates':{'fault_trigger':False},'checks':[{'id':'run-negative','case':'negative','actual':2,'status':'passed','stable':True}]}
    recovery.request_review(j,'fault_correct','candidate_no_progress')
    with pytest.raises(recovery.Stop) as e:recovery.request_review(j,'fault_validation','fault_first_invalid')
    assert 'negative:fault' in str(e.value) and 'fault_trigger' in str(e.value)
    details=g.asset(j,'recovery_stop')
    requirement=next(r for r in details['unmet_requirements'] if r['requirement_id']=='negative:fault')
    assert requirement['actual']==2 and requirement['required_assertions']==[{'path':[],'equals':3}]
    assert requirement['input']=={'values':[-1,3]} and details['matrix_id']=='observed'


def test_impact_assets_have_explicit_size_limit(client):
    j=prepared();raw=copy.deepcopy(g.asset(j,'fingerprint'));raw['untrusted_extra']='x'*48001
    with pytest.raises(ff.Invalid,match='48KiB'):ob.normalize(j,raw)


def test_invalid_impact_asset_cannot_break_failure_reporting_or_authorize_code(client):
    j=prepared();raw=g.asset(j,'fingerprint');raw['case_impacts'].pop();ff.save_asset(j,'fingerprint',raw)
    ctx=recovery.review_context(j)
    assert 'code' not in ctx['allowed_decisions'] and not ctx['execution_evidence']['requirements']
    issue=ctx['execution_evidence']['asset_issue']
    assert issue['validation_feedback']['required_case_ids']
    recovery.request_review(j,'fault_correct','classification_or_impact_error')
    with pytest.raises(recovery.Stop):recovery.request_review(j,'fault_correct','classification_or_impact_error')
    assert g.asset(j,'recovery_stop')['asset_issue']==issue
