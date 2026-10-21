"""Coverage interface claims are proposals; only bound independent review grants edits."""
import copy
from typing import Literal
from pydantic import ConfigDict, Field, model_validator
from .generation_schema import Strict, Contract
from . import generation_demands as dc, generation_inventory_review as ir
from . import generation_spec_patch as sp

VERSION = 'interface-review-v2'

def enabled(j):return ir.enabled(j) and dc.enabled(j) and j.get('interface_review_version') in ('interface-review-v1',VERSION)
def pending(j):return enabled(j) and bool(j.get('interface_proposal'))
def building(j):return enabled(j) and bool(j.get('interface_authorization'))

class Proposal(Strict):
    requirement_id: str = Field(max_length=41)
    source: str = Field(max_length=100)
    quote: str = Field(min_length=4,max_length=1200)
    current_fields: list[dc.PublicFieldPath] = Field(min_length=1,max_length=8)
    missing_behavior: str = Field(min_length=15,max_length=1000)
    why_rules_or_tests_insufficient: str = Field(min_length=15,max_length=1000)

class Coverage(ir.Coverage):
    interface_proposals: list[Proposal] = Field(default_factory=list,max_length=6)

class Decision(Strict):
    requirement_id: str = Field(max_length=41)
    action: Literal['correct_rules','add_tests','approve_interface']
    reason: str = Field(min_length=15,max_length=1200)
    replacement_rules: list[dc.Rule] = Field(default_factory=list,max_length=6)
    allowed_paths: list[str] = Field(default_factory=list,max_length=8)
    @model_validator(mode='after')
    def coherent(self):
        if (self.action=='correct_rules') != bool(self.replacement_rules):raise ValueError('correct_rules必须给出完整规则；其他动作不得改规则')
        if (self.action=='approve_interface') != bool(self.allowed_paths):raise ValueError('仅approve_interface必须给出授权字段路径')
        return self

class Review(Strict):
    binding: dict[str,str]
    decisions: list[Decision] = Field(min_length=1,max_length=6)

class RuleAssessment(Strict):
    rule_index: int = Field(ge=0,le=5)
    judgment: Literal['valid','invalid','uncertain']
    reason: str = Field(min_length=15,max_length=600)
    resolution: Literal['preserve','replace','defer']
    unresolved_reason: str = Field(default='',max_length=600)
    @model_validator(mode='after')
    def coherent(self):
        expected={'valid':('preserve',),'invalid':('replace','defer'),'uncertain':('defer',)}[self.judgment]
        if self.resolution not in expected:raise ValueError('规则判断与处理不一致：valid须preserve，invalid须replace或defer，uncertain须defer')
        if (self.resolution=='defer')!=bool(self.unresolved_reason):raise ValueError('仅defer必须说明具体尚不能处理的原因')
        return self

class DecisionV2(Decision):
    action: Literal['correct_rules','add_tests','correct_rules_and_add_tests','approve_interface','keep_rules','unresolved']
    rule_assessments: list[RuleAssessment] = Field(min_length=1,max_length=6)
    missing_scenarios: list[str] = Field(default_factory=list,max_length=4)
    @model_validator(mode='after')
    def coherent(self):
        fixes=self.action in ('correct_rules','correct_rules_and_add_tests')
        tests=self.action in ('add_tests','correct_rules_and_add_tests')
        if fixes!=bool(self.replacement_rules):raise ValueError('修规则动作必须给完整replacement_rules，其他动作不能夹带规则修改')
        if tests!=bool(self.missing_scenarios) or any(len(x)<15 or len(x)>600 for x in self.missing_scenarios):
            raise ValueError('补测试必须指出具体缺失场景，非补测试动作不得夹带场景要求')
        if (self.action=='approve_interface')!=bool(self.allowed_paths):raise ValueError('仅approve_interface必须给授权路径')
        deferred=any(r.resolution=='defer' for r in self.rule_assessments)
        replaced=any(r.resolution=='replace' for r in self.rule_assessments)
        if deferred!=(self.action=='unresolved'):raise ValueError('尚不能处理的规则必须unresolved，不能继续构建或补测试掩盖')
        if fixes!=replaced:raise ValueError('已认定须替换的规则必须有修规则动作，不能仅补测试；修规则须指出对应规则')
        return self

class ReviewV2(Review):
    decisions: list[DecisionV2] = Field(min_length=1,max_length=6)

def review_schema(j):return ReviewV2 if j.get('interface_review_version')==VERSION else Review

def adds_tests(d):return d['action'] in ('add_tests','correct_rules_and_add_tests')
def fixes_rules(d):return d['action'] in ('correct_rules','correct_rules_and_add_tests')

class CodeChange(Strict):
    model_config=ConfigDict(extra="forbid",str_strip_whitespace=False)
    path: str = Field(max_length=40)
    before: str = Field(min_length=1,max_length=24000)
    after: str = Field(max_length=24000)
    reason: str = Field(min_length=10,max_length=500)

class PublicEvidence(Strict):
    source: str = Field(max_length=150)
    quote: str = Field(min_length=4,max_length=1200)

class BuildReply(Strict):
    binding: dict[str,str]
    decision: Literal['patch','return_review']
    contract_changes: list[sp.Change] = Field(default_factory=list,max_length=8)
    code_changes: list[CodeChange] = Field(default_factory=list,max_length=10)
    reason: str = Field(min_length=15,max_length=1200)
    public_evidence: list[PublicEvidence] = Field(default_factory=list,max_length=4)
    @model_validator(mode='after')
    def coherent(self):
        if self.decision=='patch' and (not self.contract_changes or self.public_evidence):raise ValueError('patch须有契约字段修改，不夹带退回证据')
        if self.decision=='return_review' and (self.contract_changes or self.code_changes or not self.public_evidence):raise ValueError('return_review只提供公开引用，不得夹带修改')
        return self

def basis(j):
    from . import generation as g
    return {**dc.binding(j),'bundle_hash':g.digest(g.asset(j,'project_build'))}

def validate_proposals(j,v):
    ids={f['requirement_id'] for f in v['findings'] if f['verdict']=='interface_gap'}
    proposals=v['interface_proposals']
    received=[p['requirement_id'] for p in proposals]
    if len(set(received))!=len(received) or set(received)!=ids:
        raise dc.invalid('interface_gap必须逐项提供可复核提案，不直接授权重建',required_ids=sorted(ids),received_ids=received,
                         action='提供原需求引用、当前接口字段、缺失行为、为何修规则或补测试不足；规则语言限制不等于业务接口缺失')
    demands={d['id']:d for d in j['demand_inventory']['requirements']}
    for p in proposals:
        if p['source']!=demands[p['requirement_id']]['source'] or p['quote'] not in dc.sources(j).get(p['source'],''):
            raise dc.invalid('接口提案必须引用对应原需求',requirement_id=p['requirement_id'])
        for path in p['current_fields']:
            if not path.startswith(('/input_schema/','/output_schema/')):
                examples=['/'+name+'/properties/'+key.replace('~','~0').replace('/','~1') for name in ('input_schema','output_schema')
                          for key in j['contract'][name].get('properties',{})][:6]
                raise dc.invalid('current_fields须使用/input_schema/或/output_schema/开头的绝对JSON指针',
                    path=path,allowed_prefixes=['/input_schema/','/output_schema/'],existing_examples=examples,
                    action='去掉#前缀并保留input_schema/output_schema命名空间；逐项核对全部current_fields，具体字段须在当前公开schema存在。')
            sp.pointer(j['contract'],path)

def begin(j,v,asset):
    from . import generation as g
    if j.get('fault_freeze'):raise dc.invalid('冻结后禁止接口修订，须新版本')
    validate_proposals(j,v)
    j['interface_proposal']={'basis':basis(j),'proposals':copy.deepcopy(v['interface_proposals']),'review_asset':asset}
    j.pop('demand_approval',None);j['checkpoint']=ir.STAGE;g.put(j)
    return ir.STAGE

def binding(j):
    from . import generation as g
    p=j['interface_proposal']
    if p['basis']!=basis(j):raise dc.invalid('接口提案依据过期，禁止应用旧复核')
    return {**p['basis'],'proposal_hash':g.digest(p)}

def context(j):
    from . import generation as g
    from .generation_inventory_repair import POLICY
    ids={p['requirement_id'] for p in j['interface_proposal']['proposals']}
    return {'review_type':j['interface_review_version'],'binding':binding(j),'requirement_sources':dc.sources(j),'contract':j['contract'],
            'allowed_requirement_ids':sorted(ids),'requirements':[d for d in j['demand_inventory']['requirements'] if d['id'] in ids],
            'proposals':j['interface_proposal']['proposals'],'test_data':g.asset(j,'evaluation'),
            'rule_policy':POLICY,'execution_performed':False,
            'builder_public_return':j['interface_proposal'].get('builder_public_return')}

def allowed_path(contract,path):
    # A concrete existing field or new child of an existing object, never an entire schema/contract.
    if path not in ('/input_schema/required','/output_schema/required') and not path.startswith(('/input_schema/properties/','/output_schema/properties/','/behaviors/')):
        raise dc.invalid('接口授权必须限定具体输入输出字段或行为，不能授权整个契约/schema',path=path)
    parent=path.rsplit('/',1)[0];value=sp.pointer(contract,parent)
    if not isinstance(value,dict):raise dc.invalid('授权字段父对象不存在或不是对象',path=path)

def validate(j,raw):
    from . import generation as g
    if j.get('fault_freeze'):raise dc.invalid('冻结后禁止接口复核修改')
    v=review_schema(j).model_validate(raw).model_dump()
    if v['binding']!=binding(j):raise dc.invalid('接口复核绑定不匹配',expected=binding(j))
    ids={p['requirement_id'] for p in j['interface_proposal']['proposals']}
    got=[d['requirement_id'] for d in v['decisions']]
    if len(got)!=len(set(got)) or set(got)!=ids:raise dc.invalid('接口复核只能逐项处理提案',required_ids=sorted(ids),received_ids=got)
    items=copy.deepcopy(j['demand_inventory']['requirements'])
    for d in v['decisions']:
        item=next(x for x in items if x['id']==d['requirement_id'])
        if j.get('interface_review_version')==VERSION:
            indices=[r['rule_index'] for r in d['rule_assessments']]
            if len(indices)!=len(set(indices)) or set(indices)!=set(range(len(item['rules']))):
                raise dc.invalid('须逐项判断全部现有规则，不得漏项或重复',requirement_id=item['id'],required_indices=list(range(len(item['rules']))),received_indices=indices)
            if fixes_rules(d):
                # Preserve rules explicitly judged correct. Replacement list may reorder.
                retained=[item['rules'][a['rule_index']] for a in d['rule_assessments'] if a['resolution']=='preserve']
                remaining=copy.deepcopy(d['replacement_rules'])
                for rule in retained:
                    if rule not in remaining:raise dc.invalid('不能修改被判为正确并保留的规则',requirement_id=item['id'],rule=rule)
                    remaining.remove(rule)
                for a in d['rule_assessments']:
                    if a['resolution']=='replace' and item['rules'][a['rule_index']] in d['replacement_rules']:
                        raise dc.invalid('被认定须替换的规则仍原样存在，问题未处理',requirement_id=item['id'],rule_index=a['rule_index'])
        if fixes_rules(d):
            if item['rules']==d['replacement_rules']:raise dc.invalid('规则纠正不能是空修改',requirement_id=item['id'])
            item['rules']=d['replacement_rules']
        for path in d['allowed_paths']:allowed_path(j['contract'],path)
    dc.validate_inventory(j,items)
    return v

def apply(j):
    from . import generation as g
    ref=j['assets'][ir.STAGE]
    if j.get('applied_interface_review')==ref:return j['checkpoint']
    v=validate(j,g.asset(j,ir.STAGE));before=copy.deepcopy(j['demand_inventory'])
    if any(d['action']=='unresolved' for d in v['decisions']):
        from .generation_fault_recovery import Stop
        j.setdefault('interface_unresolved_reviews',[]).append({'asset':ref,'result':v})
        g.put(j)
        reasons=[a['unresolved_reason'] for d in v['decisions'] for a in d.get('rule_assessments',[]) if a['resolution']=='defer']
        raise Stop('复核仍有未处理规则：'+'；'.join(reasons),'interface_unresolved')
    paths=sorted({p for d in v['decisions'] for p in d['allowed_paths']})
    repair_kind='interface' if paths else 'tests' if any(adds_tests(d) for d in v['decisions']) else None
    if repair_kind:
        from .generation_fault_recovery import Stop
        if j.get('demand_repairs',{}).get(repair_kind,0)>=2:
            raise Stop('接口或测试修订已达两次上限，保留本次提案与原资产','demand_coverage_invalid')
    proposal=copy.deepcopy(j['interface_proposal'])
    j.setdefault('interface_review_history',[]).append({'proposal':proposal,'result':v,'asset':ref,'previous_inventory':before})
    for d in v['decisions']:
        if fixes_rules(d):next(x for x in j['demand_inventory']['requirements'] if x['id']==d['requirement_id'])['rules']=d['replacement_rules']
    j.pop('demand_approval',None);j.pop('interface_proposal',None);j.pop('format_error',None)
    if repair_kind:j.setdefault('demand_repairs',{})[repair_kind]=j.get('demand_repairs',{}).get(repair_kind,0)+1
    if paths:
        j['interface_authorization']={'basis':basis(j),'review_asset':ref,'allowed_paths':paths,'result':v,'proposal':proposal}
        stage='project_build'
    elif any(adds_tests(d) for d in v['decisions']):
        j['demand_test_repair']={'contract_hash':j['contract_hash'],'evaluation':copy.deepcopy(g.asset(j,'evaluation')),
                               'review_asset':ref,'findings':v['decisions']}
        j['evaluation_review_guided']=True;stage='evaluation'
    else:stage=dc.STAGE
    j['interface_review_resolution']={'binding':dc.binding(j),'result':v,'review_asset':ref}
    j['applied_interface_review']=ref;j['checkpoint']=stage;g.put(j)
    return stage

def build_binding(j):
    if j.get('fault_freeze'):raise dc.invalid('冻结后禁止接口构建')
    from . import generation as g
    a=j['interface_authorization']
    if a['basis']!=basis(j):raise dc.invalid('接口构建授权过期')
    return {**a['basis'],'authorization_hash':g.digest(a)}

def build_context(j):
    from . import generation as g
    return {'binding':build_binding(j),'authorization':j['interface_authorization'],
            'base_bundle':g.asset(j,'project_build'),'previous_error':j.get('format_error') if j.get('format_error',{}).get('stage')=='project_build' else None,
            'instruction':'仅修改allowed_paths和为其适配的局部代码。保留私有故障设计、症状字段与其他契约内容。工单不成立可return_review，引用公开原文；不强造新业务字段。'}

def merge_build(j,raw):
    from . import generation as g, generation_fault_first as ff
    v=BuildReply.model_validate(raw).model_dump()
    if v['binding']!=build_binding(j):raise dc.invalid('构建未绑定本次接口授权',expected=build_binding(j))
    if v['decision']=='return_review':
        sources={**dc.sources(j),**{'contract.behaviors.'+k:x for k,x in j['contract']['behaviors'].items()}}
        for e in v['public_evidence']:
            if e['quote'] not in sources.get(e['source'],''):raise dc.invalid('退回必须引用现有公开原文')
        raise ff.Invalid('构建退回接口工单：'+v['reason'],{'public_evidence':v['public_evidence']},category='interface_return')
    bundle=copy.deepcopy(g.asset(j,'project_build'));paths=j['interface_authorization']['allowed_paths'];seen=set()
    for c in v['contract_changes']:
        path=c['path']
        if path not in paths or path in seen:raise dc.invalid('接口修改超出授权或重复',path=path,allowed_paths=paths)
        seen.add(path);parent,key=path.rsplit('/',1);obj=sp.pointer(bundle['contract'],parent)
        key=key.replace('~1','/').replace('~0','~')
        if obj.get(key)!=c['before']:raise dc.invalid('接口修改before不匹配',path=path)
        obj[key]=copy.deepcopy(c['after'])
    for c in v['code_changes']:
        code=bundle['faulty'].get(c['path'])
        if code is None or code.count(c['before'])!=1:raise dc.invalid('代码补丁位置必须是现有文件且唯一',path=c['path'])
        bundle['faulty'][c['path']]=code.replace(c['before'],c['after'],1)
    if bundle==g.asset(j,'project_build'):raise dc.invalid('接口构建没有实际修改')
    ff.validate_files(bundle['faulty'],Contract.model_validate(bundle['contract']))
    # Existing full-bundle validation also verifies symptoms and runnable protocol.
    ff.ReviewedFaultBundle.model_validate(bundle)
    return bundle

def returned(j,exc):
    from . import generation as g
    from .generation_fault_recovery import Stop
    a=j.pop('interface_authorization');history=j.setdefault('interface_builder_returns',[])
    history.append({'authorization':a,'reason':str(exc),'public_evidence':exc.feedback['public_evidence']})
    if sum(x['authorization']['basis']==a['basis'] for x in history)>1:raise Stop('同一接口依据构建已退回两次，保留证据','interface_return')
    j['interface_proposal']=a['proposal'];j['interface_proposal']['basis']=basis(j);j['interface_proposal']['builder_public_return']=exc.feedback['public_evidence']
    j.pop('format_error',None);j['checkpoint']=ir.STAGE;g.put(j);return ir.STAGE

INSTRUCTION=('独立复核接口缺失提案，只看公开需求/接口/证据规则与未执行测试，不看实现。'
 '规则语言限制或选错业务字段不等于业务接口缺失；完整固定输入/expected可以验证行为，无需清单DSL表达所有关系。'
 '规则错误选correct_rules并给完整replacement_rules；接口已有表达能力但测试不足选add_tests；'
 '只有原始需求确实无法通过现有输入输出表达才approve_interface，列具体JSON指针allowed_paths。'
 '不扩大原需求，不迎合实现，不授权整个schema；不返回代码，不修改测试期望。')
V2_INSTRUCTION=('v2先逐条填写rule_assessments：判断规则valid/invalid/uncertain及preserve/replace/defer。'
 '认定规则错误不能只补测试；须修规则，或unresolved说明具体原因。修规则并补测试可correct_rules_and_add_tests；'
 '补测试必须列出现有测试具体缺少的场景missing_scenarios，已有场景不能算缺失；规则正确且无需修改可keep_rules。')


def instructions(j):return INSTRUCTION+(V2_INSTRUCTION if j.get("interface_review_version")==VERSION else "")
