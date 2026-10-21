"""Demand witnesses check real data, not covers labels or model self-reports."""
import copy
import json
from pathlib import Path
import pytest
from backend import generation as g, generation_demands as dc, generation_fault_first as ff
from backend import generation_flow as flow, generation_roles as roles, generation_mock as mock
from backend.generation_schema import Request

HISTORY=json.loads((Path(__file__).parent/'fixtures/demand_coverage_failures.json').read_text())


def history(n):
    s=next(x for x in HISTORY if x['sample']==n)
    j=g.create(Request.model_validate(s['request']),direct_build=True,fault_first=True,demand_coverage=True)
    ff.save_asset(j,'project_build',s['bundle']);ff.install(j);ff.save_asset(j,'evaluation',s['evaluation'])
    return j,s


def rule(source,path,op,value=None,minimum=2):return dict(source=source,path=path,op=op,value=value,minimum=minimum)


def demand(id,quote,rules,kind='behavior'):
    return {'id':id,'source':'request.requirement','quote':quote,'objective':'逐项核对原始需求，不依赖覆盖标签或用户程序自报。','kind':kind,'rules':rules}


def install_inventory(j,items):
    items=dc.validate_inventory(j,items)
    j['demand_inventory']={'contract_hash':j['contract_hash'],'source_hash':g.digest(dc.sources(j)),'requirements':items}
    g.put(j)


def review(j,verdict='covered',witnesses=None):
    return {'binding':dc.binding(j),'findings':[{'requirement_id':d['id'],'verdict':verdict,
            'witnesses':(witnesses or {}).get(d['id'],[]),'reason':'依据原需求与固定输入输出证据说明覆盖情况及具体缺项。'} for d in j['demand_inventory']['requirements']], 'omissions':[]}


def workflow_demands():
    quote='输入应能选择在各个步骤完成状态已持久化之后中断'
    return [demand('stop_prepare',quote,[rule('input',['actions','*','action'],'last_eq','run'),
               rule('input',['actions','*','stop_after'],'last_eq','prepare'),rule('expected',['records'],'length_eq',1)],'boundary'),
            demand('stop_transform',quote,[rule('input',['actions','*','action'],'last_eq','run'),
               rule('input',['actions','*','stop_after'],'last_eq','transform'),rule('expected',['records'],'length_eq',2)],'boundary'),
            demand('many_resumes','在一次场景内多次恢复',[rule('input',['actions','*','action'],'count_eq','resume',2),
               rule('expected',['records'],'length_eq',3),rule('expected',['duplicate_counts','prepare'],'eq',0)],'sequence'),
            demand('complete_resume','完整流程再次恢复应没有新增副作用',[rule('input',['actions','*','action'],'last_eq','resume'),
               rule('input',['actions','*','action'],'count_eq','resume',2),rule('expected',['records'],'length_eq',3)],'sequence')]


def additions(s):
    template=copy.deepcopy(s['evaluation']['cases'][0]);out=[]
    for step,size in [('prepare',1),('transform',2)]:
        c=copy.deepcopy(template);c.update(id='stop_'+step,covers=['run','persist'],input={'instance_id':'wf-run-full','actions':[{'action':'run','stop_after':step}]})
        c['expected']['records']=c['expected']['records'][:size]
        c['expected']['status']={k:'completed' if i<size else 'pending' for i,k in enumerate(('prepare','transform','generate'))}
        out.append(c)
    c=copy.deepcopy(template);c.update(id='many_resumes',covers=['resume','persist','close_reopen'],group='target',visibility='hidden')
    c['input']['actions']=[{'action':'run','stop_after':'prepare'},{'action':'resume','stop_after':'transform'},{'action':'resume'},{'action':'resume'}]
    out.append(c);return out


def test_original_single_operation_interface_cannot_claim_shared_state(client):
    j,s=history(1)
    item=demand('different_operations','两个不同逻辑操作即使内容相同也不能合并',[
        rule('input',['operations','*','operation_id'],'distinct'),rule('input',['operations','*','ticket'],'equal'),
        rule('expected',['records'],'length_eq',2)],'shared_state')
    with pytest.raises(ff.Invalid,match='不属于公开接口'):dc.validate_inventory(j,[item])
    # Separate case IDs and singleton IDs are never a same-store witness.
    bad=copy.deepcopy(item);bad['rules'][0]['path']=['operation_id']
    with pytest.raises(ff.Invalid,match='同一场景'):dc.validate_inventory(j,[bad])
    raw={'contract_hash':j['contract_hash'],'decision':'revise','reason':'原需求要求同一存储内多个操作，但接口只能接收一个operation_id。',
         'issues':[{'path':'/input_schema','reason':'增加同一scenario的多操作输入与共享工具状态，不可用独立case替代。'}],
         'questions':[],'output_rules':[],'requirements':[],'interface_gap':True}
    ff.save_asset(j,'spec_review',raw);source=copy.deepcopy(ff.files(j));assert ff.apply_review(j)=='project_build'
    assert j['design_rebuild']['issue']['kind']=='demand_interface_gap' and ff.files(j)==source
    assert roles.schema(j,'project_build') is not __import__('backend.generation_spec_patch',fromlist=['Amendment']).Amendment


def test_original_workflow_cases_cannot_cover_stops_or_multiple_restores(client):
    j,s=history(5);install_inventory(j,workflow_demands());old=copy.deepcopy(g.asset(j,'evaluation'))
    for d in j['demand_inventory']['requirements']:
        assert not any(dc.evidence(d,c)['matches'] for c in old['cases'])
    raw=review(j,witnesses={d['id']:[old['cases'][1]['id']] for d in j['demand_inventory']['requirements']})
    with pytest.raises(ff.Invalid,match='实际数据'):dc.validate_review(j,raw)
    raw=review(j,'test_gap');ff.save_asset(j,dc.STAGE,raw)
    assert dc.apply_review(j)=='evaluation' and j['demand_test_repair']['evaluation']==old
    ctx=roles.context(j,'evaluation');assert ctx['demand_test_repair']['evaluation']==old
    assert 'faulty' not in ctx and 'files' not in ctx and 'matrix' not in ctx
    fixed=copy.deepcopy(old);fixed['cases']+=additions(s)
    dc.validate_evaluation(j,fixed);ff.save_asset(j,'evaluation',fixed)
    witnesses={d['id']:[c['id'] for c in fixed['cases'] if dc.evidence(d,c)['matches']] for d in j['demand_inventory']['requirements']}
    raw=review(j,witnesses=witnesses);ff.save_asset(j,dc.STAGE,raw)
    assert dc.apply_review(j)=='fingerprint';dc.require_approved(j)
    assert g.asset(j,'evaluation')['cases'][:len(old['cases'])]==old['cases']
    for field in ('expected','input','covers','visibility'):
        corrupt=copy.deepcopy(fixed);corrupt['cases'][0][field]=None
        with pytest.raises(ff.Invalid,match='只能追加'):dc.validate_evaluation({**j,'demand_test_repair':{'evaluation':old,'contract_hash':j['contract_hash']}},corrupt)


def test_shared_witness_needs_same_case_real_ids_and_equal_payload(client):
    d=demand('shared','用户指定的同内容不同操作',[
        rule('input',['ops','*','id'],'distinct'),rule('input',['ops','*','body'],'equal'),rule('expected',['records'],'length_eq',2)],'shared_state')
    good={'id':'shared','input':{'ops':[{'id':'a','body':'same'},{'id':'b','body':'same'}]},'expected':{'records':['a','b']}}
    assert dc.evidence(d,good)['matches']
    for ops in [[{'id':'a','body':'same'}],[{'id':'a','body':'same'},{'id':'a','body':'same'}],
                [{'id':'a','body':'one'},{'id':'b','body':'two'}]]:
        assert not dc.evidence(d,{**good,'input':{'ops':ops}})['matches']
    with pytest.raises(ValueError):dc.Rule.model_validate(rule('input',['ops','*','id'],'distinct',minimum=1))


def test_binding_isolation_missing_inventory_and_resume_no_paid_repeat(client,monkeypatch):
    j,s=history(5);install_inventory(j,workflow_demands());eval=copy.deepcopy(s['evaluation']);eval['cases']+=additions(s);ff.save_asset(j,'evaluation',eval)
    ctx=json.loads(roles.messages(j,dc.STAGE)[-1]['content'])
    assert not {'files','private_fault_requirements','matrix','actual','faulty','reference'} & set(ctx)
    raw=review(j,witnesses={d['id']:[c['id'] for c in eval['cases'] if dc.evidence(d,c)['matches']] for d in j['demand_inventory']['requirements']})
    monkeypatch.setattr(mock,'role_response',lambda stage,payload:raw);flow.call(j,dc.STAGE)
    count=j['request_count'];j.update(status='interrupted',checkpoint=dc.STAGE);g.put(j)
    calls=[];monkeypatch.setattr(g,'launch',lambda id,stage:calls.append(stage))
    ff.resume(j['id']);saved=g.get(j['id']);assert calls==['fingerprint'] and saved['request_count']==count
    dc.require_approved(saved)
    eval['cases'][0]['expected']['records']=[];ff.save_asset(saved,'evaluation',eval)
    with pytest.raises(ff.Invalid,match='变化'):dc.require_approved(saved)
    with pytest.raises(ff.Invalid):g.validate(saved)
    assert j.get('matrix') is None


def test_gap_caps_and_original_quote_validation(client):
    j,s=history(5);items=workflow_demands();bad=copy.deepcopy(items);bad[0]['quote']='not actually in the request'
    with pytest.raises(ff.Invalid,match='原始输入'):dc.validate_inventory(j,bad)
    install_inventory(j,items)
    for _ in range(2):ff.save_asset(j,dc.STAGE,review(j,'test_gap'));assert dc.apply_review(j)=='evaluation'
    ff.save_asset(j,dc.STAGE,review(j,'test_gap'))
    with pytest.raises(ValueError,match='两次'):dc.apply_review(j)


def test_interface_rebuild_preserves_original_obligations(client):
    j,s=history(5);items=workflow_demands();install_inventory(j,items)
    dc.rebuild(j,{'reason':'接口须增加同一存储的多实例输入，不能丢失已有恢复要求。'})
    with pytest.raises(ff.Invalid,match='既有原需求'):dc.validate_inventory(j,items[:-1])
    bad=copy.deepcopy(items);bad[0]['kind']='behavior'
    with pytest.raises(ff.Invalid,match='既有原需求'):dc.validate_inventory(j,bad)
    j['requirement_answers']=[{'id':'clarification','answers':{'scope':'作者明确变更原要求，重新审核覆盖清单。'}}]
    assert dc.validate_inventory(j,items[:-1])


def test_invalid_omission_returns_exact_source_restriction_to_next_request(client):
    j,s=history(5);install_inventory(j,workflow_demands());raw=review(j,'test_gap')
    raw['omissions']=[{'source':'contract.behaviors.run','quote':'not user original text',
                      'reason':'契约约定不能冒充用户原始要求，须保留原审核发现并改正引用。'}]
    with pytest.raises(ff.Invalid) as e:dc.validate_review(j,raw)
    flow.failure(j,dc.STAGE,e.value)
    ctx=json.loads(roles.messages(j,dc.STAGE)[-1]['content'])
    assert ctx['validation_feedback']['allowed_sources']==list(dc.sources(j))
    assert 'contract' in ctx['validation_feedback']['action']


def test_only_new_inventory_review_has_larger_stage_cap(client):
    from backend import generation_budget as budget
    j,s=history(5);j['budget']=budget.initialize(budget.BudgetPolicy(output_multiplier=1))
    assert budget.reserve(j,'spec_review',10)==6000
    j.pop('demand_coverage_version');j['budget']=budget.initialize(budget.BudgetPolicy(output_multiplier=1))
    assert budget.reserve(j,'spec_review',10)==4000
    assert j['budget']['policy']['requests']==24 and j['budget']['policy']['seconds']==1200


def test_inventory_omission_rechecks_spec_without_rebuilding_or_rewriting_tests(client):
    j,s=history(5);install_inventory(j,workflow_demands());old=copy.deepcopy(g.asset(j,'evaluation'))
    raw=review(j,'test_gap');raw['omissions']=[{'source':'request.requirement','quote':'不同工作流实例的状态必须隔离',
                                             'reason':'清单未收录该原要求，先对照原需求重查接口与清单。'}]
    ff.save_asset(j,dc.STAGE,raw);assert dc.apply_review(j)=='spec_review'
    assert 'design_rebuild' not in j and g.asset(j,'evaluation')==old
    ctx=roles.context(j,'spec_review')
    assert ctx['inventory_omissions']==[{'source':'request.requirement','quote':'不同工作流实例的状态必须隔离'}]
    assert 'evaluation' not in ctx and 'matrix' not in ctx and 'private_fault_requirements' not in ctx


def test_new_api_jobs_enable_gate_but_legacy_jobs_do_not(client):
    j=client.post('/api/generation/jobs',json={'requirement':'验证默认需求覆盖流程入口'}).json()
    assert j['lean_version']=='lean-generation-v1' and j['demand_coverage_version'] is None
    old=g.create(Request(requirement='原故障优先流程兼容性验证'),direct_build=True,fault_first=True)
    assert not dc.enabled(old);dc.require_approved(old)


def test_historical_evasion_is_rejected_by_added_stop_case_in_docker(client,record_property):
    ok,reason=g.executor.availability()
    if not ok:pytest.skip(reason)
    from backend.generation import materialize
    from backend import storage as s
    sample=HISTORY[1];checkpoint=additions(sample)[0]
    result={}
    for name,code in [('reference',sample['reference']),('late_stop_missing',sample['evasions']['late_stop_missing'])]:
        folder=s.DATA/'demand-replay'/name;materialize(folder,code)
        actual=g.executor.execute(folder,checkpoint['input'],allowed_files=[*code,'solution.py'])
        result[name]=actual
    assert result['reference']==checkpoint['expected']
    assert result['late_stop_missing']!=checkpoint['expected']
    assert len(result['late_stop_missing']['records'])==3 and len(result['reference']['records'])==1
    record_property('demand_docker_replay',json.dumps({'case':checkpoint,'actual':result},ensure_ascii=False))


def test_mock_new_pipeline_runs_coverage_stage_and_still_passes_docker(client):
    ok,reason=g.executor.availability()
    if not ok:pytest.skip(reason)
    j=g.create(Request(requirement='离线验证新增需求覆盖状态机和Docker完整生成流程'),direct_build=True,fault_first=True,demand_coverage=True)
    assert g.POOL.acquire(False)
    ff.run(j['id'],'project_build');done=g.get(j['id'])
    assert done['status']=='awaiting_review',done.get('error')
    assert done['matrix']['passed'] and dc.STAGE in done['assets']
    assert [a['stage'] for a in done['attempts']].index(dc.STAGE)<[a['stage'] for a in done['attempts']].index('fingerprint')
    assert done['request_count']<=24 and done['demand_reviews']
    dc.require_approved(done)
