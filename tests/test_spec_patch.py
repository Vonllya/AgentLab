import copy,json
from pathlib import Path
import pytest
from backend import generation as g, generation_flow as flow, generation_roles as roles
from backend import generation_direct as direct, generation_mock as mock, generation_spec_patch as sp
from backend import generation_format as fmt, generation_budget as budget
from test_fault_model import modeled


def ready():
    j=modeled();j['spec_review_feedback']={'reason':'只补明确的公开边界，不改变故障设计。','issues':[{'path':'/behaviors/sum','reason':'明确输出为整数以及空列表按既定规则处理。'}]};g.put(j);return j


def patch(j):
    base=sp.basis(j);text=j['contract']['behaviors']['sum']
    return {'contract_hash':base['contract_hash'],'bundle_hash':base['bundle_hash'],'decision':'patch','changes':[{'path':'/behaviors/sum','before':text,'after':text+'，结果是整数，空列表按empty规则处理。','reason':'仅明确输出约定，不改变运算含义。'}],'conflict_evidence':[],'explanation':'公开规范补充类型说明，其余资产由程序保留。'}


def test_patch_preserves_all_frozen_assets_and_revalidates(client,monkeypatch):
    j=ready();old=sp.current_bundle(j);raw=patch(j)
    assert roles.schema(j,'project_build') is sp.Amendment
    ctx=roles.context(j,'project_build');assert ctx['frozen_assets']['private_fault_requirements']==j['private_fault_requirements']
    assert 'project' not in roles.context(j,'spec_review')
    monkeypatch.setattr(mock,'role_response',lambda stage,payload:raw)
    before_ref=j['assets']['project_build'];flow.call(j,'project_build')
    assert g.public(j)['attempts'][-1]['specification_patch'] is True
    new=g.asset(j,'project_build')
    assert {k:v for k,v in new.items() if k!='contract'}=={k:v for k,v in old.items() if k!='contract'}
    direct.install_bundle(j)
    assert j['contract_version']==2 and not j['confirmed_version'] and j['matrix'] is None
    assert 'evaluation' not in j['assets'] and 'fingerprint' not in j['assets']
    assert (g.root(j)/before_ref/'output.json').exists()


@pytest.mark.parametrize('defect',['path','before','hash','nochange','duplicate','private','stale_build'])
def test_patch_rejects_specific_unauthorized_or_stale_fields(client,defect):
    j=ready();raw=patch(j)
    if defect=='path':raw['changes'][0]['path']='/title'
    if defect=='before':raw['changes'][0]['before']='wrong'
    if defect=='hash':raw['contract_hash']='0'*64
    if defect=='nochange':raw['changes'][0]['after']=raw['changes'][0]['before']
    if defect=='duplicate':raw['changes']*=2
    if defect=='private':raw['changes'][0]['path']='/private_fault_requirements'
    if defect=='stale_build':
        p=g.root(j)/j['assets']['build']/'output.json';b=json.loads(p.read_text());b['normal']['app.py']+='\n# later version';p.write_text(json.dumps(b))
    with pytest.raises(sp.PatchError) as e:sp.merge(j,raw)
    assert e.value.feedback['path']


def test_preserves_current_build_not_initial_bundle(client):
    j=ready();p=g.root(j)/j['assets']['build']/'output.json';b=json.loads(p.read_text());b['normal']['app.py']+='\n# existing repair';p.write_text(json.dumps(b))
    assert sp.merge(j,patch(j))['project']==b


def test_conflict_must_cite_real_sources_and_never_changes_assets(client):
    j=ready();raw=patch(j);raw.update(decision='conflict',changes=[],conflict_evidence=[{'path':'/private_fault_requirements','quote':j['private_fault_requirements']},{'path':'/project/fault_explanation','quote':g.asset(j,'build')['fault_explanation']}])
    old=copy.deepcopy(j['assets']);bad=copy.deepcopy(raw);bad['conflict_evidence'][0]['quote']='not actually present'
    with pytest.raises(sp.PatchError):sp.merge(j,bad)
    with pytest.raises(Exception) as e:sp.merge(j,raw)
    assert e.value.category=='specification_conflict' and j['assets']==old
    assert g.get(j['id'])['spec_patch_conflict']['conflict_evidence']==raw['conflict_evidence']


def test_mixed_errors_bounded_without_spending_24_calls(client,monkeypatch):
    j=ready();start=j['request_count'];good=patch(j);n=0
    def response(stage,payload):
        nonlocal n;n+=1
        if n==2:raise json.JSONDecodeError('bad json','{',1)
        raw=copy.deepcopy(good);raw['changes'][0]['before']='wrong';return raw
    monkeypatch.setattr(mock,'role_response',response)
    assert g.POOL.acquire(False);flow.run(j['id'],'project_build');end=g.get(j['id'])
    assert end['error']['category']=='spec_patch_exhausted'
    assert end['request_count']-start==3 and end['matrix'] is None
    assert end['contract_version']==1


def test_budget_denial_does_not_consume_patch_attempt(client):
    j=ready();j['budget']['requests']=j['budget']['policy']['requests'];g.put(j)
    with pytest.raises(budget.Exhausted):flow.call(j,'project_build')
    assert not j.get('spec_patch_attempts')


def test_stale_bundle_format_correction_not_reused_and_resume_no_paid_repeat(client,monkeypatch):
    j=ready();j['format_correction']={'asset':'missing-old-full-response','contract_version':j['contract_version'],'attempts':1}
    messages=roles.messages(j,'project_build');assert fmt.messages(j,'project_build',messages)==messages
    monkeypatch.setattr(mock,'role_response',lambda stage,payload:patch(j))
    flow.call(j,'project_build');count=j['request_count'];j.update(status='interrupted',checkpoint='project_build');g.put(j)
    calls=[];monkeypatch.setattr(g,'launch',lambda id,stage:calls.append(stage))
    flow.resume(j['id']);done=g.get(j['id']);assert calls==['spec_review'] and done['request_count']==count and done['contract_version']==2


def test_review_cycle_cap_persists_and_resume_routes_to_patch(client,monkeypatch):
    j=ready()
    for _ in range(6):
        j['contract_hash']=g.digest(str(_));sp.reserve(j)
    with pytest.raises(Exception) as e:sp.reserve(j)
    assert e.value.category=='spec_patch_exhausted'
    j.update(status='needs_manual_review',checkpoint='project_build');g.put(j)
    calls=[];monkeypatch.setattr(g,'launch',lambda id,stage:calls.append(stage))
    flow.resume(j['id'],'作者已核对规范补齐记录，需要检查具体获准字段。');assert calls==['project_build']


def test_patch_failure_audit_does_not_masquerade_as_coverage(client,monkeypatch):
    j=ready();raw=patch(j);raw['changes'][0]['before']='wrong'
    monkeypatch.setattr(mock,'role_response',lambda stage,payload:raw)
    with pytest.raises(sp.PatchError):flow.call(j,'project_build')
    assert j['attempts'][-1]['failure_kind']=='spec_patch_invalid'
    assert j['attempts'][-1]['error']['validation_feedback']['path']=='/behaviors/sum'


def test_patch_conflict_audit_preserves_category(client,monkeypatch):
    j=ready();raw=patch(j)
    raw.update(decision='conflict',changes=[],conflict_evidence=[{'path':'/private_fault_requirements','quote':j['private_fault_requirements']},{'path':'/project/fault_explanation','quote':g.asset(j,'build')['fault_explanation']}])
    monkeypatch.setattr(mock,'role_response',lambda stage,payload:raw)
    with pytest.raises(sp.NeedsReview):flow.call(j,'project_build')
    assert j['attempts'][-1]['failure_kind']=='specification_conflict'


def test_patch_context_includes_original_requirement_and_author_note(client):
    j=ready();j['repair_note']='作者说明：保持原需求中已有的输入范围。'
    j['requirement_answers']=[{'answers':{'scope':'仅本地模拟'}}]
    context=roles.context(j,'project_build')
    assert context['requirement']==j['request']
    assert context['requirement_answers']==j['requirement_answers']
    assert context['author_note']==j['repair_note']


def test_new_batch_resets_only_child_patch_budget_and_keeps_parent(client,monkeypatch):
    from backend.app import Regenerate
    j=ready();sp.reserve(j);j.update(status='needs_manual_review',checkpoint='project_build');g.put(j)
    old=copy.deepcopy(g.get(j['id']));calls=[]
    monkeypatch.setattr(g,'launch',lambda id,stage:calls.append((id,stage)))
    body=Regenerate(expected_revision=j['revision'],idempotency_key='spec-patch-new-batch',new_batch=True,budget=budget.BudgetPolicy(requests=3),repair_note='作者核对字段补齐，明确授权新批次。')
    child=flow.regenerate(j['id'],body);saved=g.get(child['id'])
    assert g.get(j['id'])==old and not saved.get('spec_patch_attempts')
    assert calls==[(child['id'],'project_build')]
    assert sp.basis(saved)['bundle_hash']==sp.basis(j)['bundle_hash']
    assert flow.regenerate(j['id'],body)['id']==child['id'] and len(calls)==1


def test_remaining_full_bundle_branch_supplies_both_frozen_values(client):
    j=ready();j.pop('spec_review_feedback')
    assert roles.schema(j,'project_build') is direct.ModeledBundle
    ctx=roles.context(j,'project_build')
    assert ctx['frozen_private_fault_requirements']==j['private_fault_requirements']
    assert ctx['frozen_fault_model']==g.asset(j,'project_build')['fault_model']


@pytest.mark.parametrize('fault_first',[False,True])
def test_missing_rule_parent_replacement_is_scoped_and_revalidated(client,monkeypatch,fault_first):
    if fault_first:
        from test_fault_first import prepared
        j=prepared()
        j['spec_review_feedback']={'reason':'只补输出规则，不改变原故障。','issues':[]}
    else:j=ready()
    old=copy.deepcopy(j['contract']);frozen=sp.current_bundle(j)
    j['spec_review_feedback']['issues']=[{'path':'/behaviors/output_copy','reason':'补明输出字段复制输入内容的既定规则。'}]
    g.put(j)
    ctx=roles.context(j,'project_build')
    assert ctx['allowed_paths']==['/behaviors']
    target=ctx['patch_targets'][0]
    assert target['before']==old['behaviors']
    assert next(iter(target['value_schema']['patternProperties'].values()))['type']=='string'
    assert target['issues'][0]['requested_path']=='/behaviors/output_copy'
    after={**target['before'],'output_copy':'输出的内容应复制输入中对应字段。'}
    raw={'contract_hash':ctx['contract_hash'],'bundle_hash':ctx['bundle_hash'],'decision':'patch',
         'changes':[{'path':'/behaviors','before':target['before'],'after':after,'reason':'通过已有父字段增加审核要求的规则。'}],
         'conflict_evidence':[],'explanation':'公开规则通过父字段局部补齐，其余资产保留。'}
    # The actual flow and its legacy scope validator must accept the same plan.
    monkeypatch.setattr(mock,'role_response',lambda stage,payload:raw)
    flow.call(j,'project_build')
    if fault_first:
        from backend import generation_fault_first as ff
        ff.install(j)
    else:direct.install_bundle(j)
    assert j['contract_version']==2 and j['confirmed_version'] is None
    assert j['contract']['behaviors']==after
    assert j['spec_review_feedback']['issues'][0]['path']=='/behaviors/output_copy'
    assert {k:v for k,v in g.asset(j,'project_build').items() if k!='contract'}=={k:v for k,v in frozen.items() if k!='contract'}


@pytest.mark.parametrize('sample',json.loads((Path(__file__).parent/'fixtures/spec_patch_review_failures.json').read_text()),ids=lambda s:f"baseline-{s['sample']}")
def test_original_failed_handoffs_have_executable_scoped_targets(client,monkeypatch,sample):
    j=ready();j['contract']=copy.deepcopy(sample['contract']);j['contract_hash']=g.digest(j['contract'])
    j['spec_review_feedback']=copy.deepcopy(sample['review_feedback'])
    bundle={'contract':copy.deepcopy(j['contract']),'private_fault_requirements':'Frozen private mechanism must not change.',
            'project':{'normal':{'app.py':'frozen'},'reference':{'app.py':'frozen'}},'symptom_paths':[['records']]}
    monkeypatch.setattr(sp,'current_bundle',lambda job:copy.deepcopy(bundle))
    before=copy.deepcopy(j)
    # Run public review validation as well as the patch protocol on original data.
    review={'contract_hash':j['contract_hash'],'decision':'revise',**j['spec_review_feedback'],'questions':[],'output_rules':[]}
    if sample['sample']==4:
        # Five additions to six behaviors cannot fit the unchanged limit of ten.
        # Reject that work order at review, then replay an explicitly reauthorized
        # existing field. This is a controlled review correction, not model output.
        with pytest.raises(sp.PatchError,match='字段上限') as e:direct.validate_review(j,review)
        assert e.value.feedback['expected']['remaining_slots']==4
        assert '/simulation' in e.value.feedback['expected']['existing_paths']
        requests=j['request_count']
        flow.failure(j,'spec_review',e.value)
        reviewer=json.loads(roles.messages(j,'spec_review')[-1]['content'])
        assert reviewer['validation_feedback']==e.value.feedback
        assert reviewer['validation_feedback']['contract_hash']==j['contract_hash']
        assert j['request_count']==requests
        original=copy.deepcopy(j)
        j['spec_review_feedback']={'reason':review['reason'],'issues':[{'path':'/simulation','reason':'在既有模拟说明补充审核指出的五项输出规则，不新增行为ID。'}]}
        assert direct.validate_review(j,{**review,**j['spec_review_feedback']})['issues']==j['spec_review_feedback']['issues']
        ctx=sp.basis(j);target=ctx['patch_targets'][0]
        assert ctx['allowed_paths']==['/simulation']
        text=target['before']+'\n输出规则补充：results按输入操作顺序一一对应；status由停止事件决定；attempt_count只统计实际调用；record_id按scenario内首次写入顺序递增；记录保留首次输入的operation_id和内容，列表按分配顺序返回。'
        raw={'contract_hash':j['contract_hash'],'bundle_hash':ctx['bundle_hash'],'decision':'patch',
             'changes':[{'path':'/simulation','before':target['before'],'after':text,'reason':'经重新审核明确授权，只补充既有模拟说明。'}],
             'conflict_evidence':[],'explanation':'受控重新授权后执行原审核要求，保持行为ID和其余资产不变。'}
        merged=sp.merge(j,raw)
        assert merged['contract']['simulation']==text
        assert merged['contract']['behaviors']==original['contract']['behaviors']
        assert {k:v for k,v in merged.items() if k!='contract'}=={k:v for k,v in bundle.items() if k!='contract'}
        stale=copy.deepcopy(raw);stale['changes']=sample['rejected_changes']
        with pytest.raises(sp.PatchError,match='未授权字段'):sp.merge(j,stale)
        return
    assert direct.validate_review(j,review)['issues']==sample['review_feedback']['issues']
    ctx=sp.basis(j)
    assert ctx['allowed_paths']==['/behaviors']
    target=ctx['patch_targets'][0]
    after=copy.deepcopy(target['before'])
    for change in sample['rejected_changes']:
        # Sample 4 also submitted objects where Contract requires strings.
        # Use its prose as a controlled correction, not a model-success claim.
        value=change['after']
        after[change['path'].split('/')[-1]]=value['description'] if isinstance(value,dict) else value
    raw={'contract_hash':j['contract_hash'],'bundle_hash':ctx['bundle_hash'],'decision':'patch',
         'changes':[{'path':'/behaviors','before':target['before'],'after':after,'reason':'保留所有原有规则，只添加本次审核指出的规则。'}],
         'conflict_evidence':[],'explanation':'重放原失败工单，验证授权与执行范围一致。'}
    merged=sp.merge(j,raw)
    assert merged['contract']['behaviors']==after
    assert {k:v for k,v in merged.items() if k!='contract'}=={k:v for k,v in bundle.items() if k!='contract'}
    assert j==before
    # Previously rejected direct insertion must remain rejected, with usable feedback.
    bad=copy.deepcopy(raw);bad['changes']=sample['rejected_changes']
    with pytest.raises(sp.PatchError) as e:sp.merge(j,bad)
    assert e.value.feedback['expected']==ctx['patch_targets']
    # Replacing a parent does not grant permission to edit/delete siblings.
    for modification in ('change','delete'):
        bad=copy.deepcopy(raw);key=next(iter(target['before']))
        if modification=='change':bad['changes'][0]['after'][key]='Unauthorized unrelated rule.'
        else:del bad['changes'][0]['after'][key]
        with pytest.raises(sp.PatchError,match='审核事项之外'):sp.merge(j,bad)


def test_mixed_existing_and_missing_rules_share_one_bounded_parent(client):
    j=ready();j['spec_review_feedback']['issues'].append({'path':'/behaviors/new_rule','reason':'增加审核明确要求的新输出映射规则。'})
    ctx=sp.basis(j);assert ctx['allowed_paths']==['/behaviors']
    assert {i['requested_path'] for i in ctx['patch_targets'][0]['issues']}=={'/behaviors/sum','/behaviors/new_rule'}
    after={**ctx['patch_targets'][0]['before'],'sum':'明确的算术和规则。','new_rule':'新字段对应输入中同名值。'}
    raw=patch(j);raw['changes']=[{'path':'/behaviors','before':ctx['patch_targets'][0]['before'],'after':after,'reason':'仅改两个审核要求字段，保持其他内容。'}]
    assert sp.merge(j,raw)['contract']['behaviors']==after


def test_invalid_review_authority_rejected_before_paid_patch(client):
    j=ready();j['spec_review_feedback']['issues']=[{'path':'/private_fault_requirements','reason':'不能授权私有资产或任意新根字段。'}]
    requests=j['request_count']
    with pytest.raises(ValueError):roles.messages(j,'project_build')
    assert j['request_count']==requests


def test_parent_target_keeps_exact_before_and_string_schema(client):
    j=ready();j['spec_review_feedback']['issues']=[{'path':'/behaviors/output_copy','reason':'增加审核明确要求的输出复制规则。'}]
    ctx=sp.basis(j);target=ctx['patch_targets'][0]
    raw={**patch(j),'changes':[{'path':'/behaviors','before':target['before'],
         'after':{**target['before'],'output_copy':{'description':'Not a string'}},'reason':'验证父字段补丁仍受原契约类型约束。'}]}
    with pytest.raises(ValueError,match='valid string'):sp.merge(j,raw)
    raw['changes'][0]['after']['output_copy']='输出复制输入内容。'
    raw['changes'][0]['before']={}
    with pytest.raises(sp.PatchError,match='before不匹配') as e:sp.merge(j,raw)
    assert e.value.feedback['expected']==target['before']
