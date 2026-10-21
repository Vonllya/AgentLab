"""Original-demand traces and data-only, same-scenario coverage witnesses.

The model judges meaning; this module checks citations, bindings and actual data.
No generated code or evaluation scripts are executed here.
"""
import copy
import json
from typing import Annotated, Any, Literal
from pydantic import Field, model_validator
from .generation_schema import Strict, conforms
from .generation_direct import SpecReview as BaseSpecReview

VERSION='demand-coverage-v1'
STAGE='coverage_review'

# Same prefix whitelist enforced by interface review, also visible to the model.
PublicFieldPath = Annotated[str, Field(pattern=r'^/(input_schema|output_schema)/',
    description='公开schema中实际存在字段的绝对JSON指针；以/input_schema/或/output_schema/开头，不使用#前缀，不省略schema命名空间。')]

def enabled(job):return job.get('demand_coverage_version')==VERSION


class Rule(Strict):
    source:Literal['input','expected']
    path:list[str]=Field(max_length=8)
    op:Literal['eq','length_eq','distinct','equal','count_eq','last_eq']
    value:Any=None
    minimum:int=Field(default=2,ge=1,le=200)
    @model_validator(mode='after')
    def relation_count(self):
        if self.op in ('distinct','equal') and self.minimum<2:raise ValueError('多实体关系至少需要两个实际选择值')
        return self


class Demand(Strict):
    id:str=Field(pattern='^[a-z][a-z0-9_]{0,40}$')
    source:str=Field(max_length=100)
    quote:str=Field(min_length=4,max_length=1200)
    objective:str=Field(min_length=10,max_length=600)
    kind:Literal['behavior','shared_state','boundary','sequence']
    rules:list[Rule]=Field(min_length=1,max_length=6)
    @model_validator(mode='after')
    def evidence(self):
        if not any(r.source=='expected' for r in self.rules):raise ValueError('每项需求须有输出证据规则，不能只写输入或覆盖标签')
        if self.kind=='shared_state' and not any(r.source=='input' and r.op=='distinct' for r in self.rules):
            raise ValueError('共享状态需求须在同一输入包含多个不同实体的证据')
        return self


class SpecReview(BaseSpecReview):
    requirements:list[Demand]=Field(default_factory=list,max_length=12)
    interface_gap:bool=False
    @model_validator(mode='after')
    def inventory(self):
        if (self.decision=='approve')!=bool(self.requirements):raise ValueError('批准时固定原需求证据清单，其他分支不夹带批准清单')
        if self.interface_gap and self.decision!='revise':raise ValueError('接口缺项必须退回构建，不能同时批准')
        return self


class Finding(Strict):
    requirement_id:str=Field(max_length=41)
    verdict:Literal['covered','interface_gap','test_gap']
    witnesses:list[str]=Field(max_length=10)
    reason:str=Field(min_length=15,max_length=1000)


class Omission(Strict):
    source:str=Field(max_length=100)
    quote:str=Field(min_length=4,max_length=1200)
    reason:str=Field(min_length=15,max_length=600)


class Review(Strict):
    binding:dict[str,str]
    findings:list[Finding]=Field(min_length=1,max_length=12)
    omissions:list[Omission]=Field(max_length=6)


def invalid(reason,**feedback):
    from .generation_fault_first import Invalid
    return Invalid(reason,feedback,category='demand_coverage_invalid')


def sources(job):
    from .contract_revision import sources as original_sources
    return original_sources(job)


def schema_node(schema,path):
    node=schema
    for part in path:
        if node.get('type')=='object' and part in node.get('properties',{}):node=node['properties'][part]
        elif node.get('type')=='array' and (part=='*' or (part.isdigit() and str(int(part))==part)):node=node['items']
        else:raise invalid('需求证据选择器不属于公开接口',path=path,action='接口缺失应revise并要求构建修订，不能批准缩水接口或伪造覆盖')
    return node


def validate_inventory(job,items):
    demands=[Demand.model_validate(x).model_dump() for x in items]
    if not demands or len({d['id'] for d in demands})!=len(demands):raise invalid('需求清单为空或ID重复')
    original=sources(job)
    by_id={d['id']:d for d in demands}
    from .generation import digest
    previous=job.get('demand_previous_requirements',[]) if job.get('demand_previous_source_hash')==digest(original) else []
    for old in previous:
        new=by_id.get(old['id'])
        if not new or any(new[k]!=old[k] for k in ('source','quote','kind')):
            raise invalid('接口修订不能删除或改写既有原需求义务',requirement_id=old['id'],previous=old,
                          action='保留需求ID、原文与证据类别；按新接口重新定位证据规则，不降低用户要求')
    for d in demands:
        if d['source'] not in original or d['quote'] not in original[d['source']]:raise invalid('需求必须引用原始输入的连续原文',requirement_id=d['id'],sources=original)
        for r in d['rules']:
            schema=job['contract']['input_schema' if r['source']=='input' else 'output_schema']
            node=schema_node(schema,r['path'])
            if r['op'] in ('distinct','equal') and '*' not in r['path']:
                raise invalid('多实体关系需要在同一场景选择多个值，不能用独立case代替',requirement_id=d['id'],rule=r)
            if r['op']=='length_eq' and (node['type'] not in ('array','string','object') or type(r['value']) is not int or r['value']<0):
                raise invalid('长度证据规则类型无效',requirement_id=d['id'],rule=r)
            if r['op'] in ('eq','last_eq','count_eq') and not conforms(r['value'],node):
                raise invalid('需求证据值不符合公开接口类型',requirement_id=d['id'],rule=r)
    return demands


def select(value,path):
    nodes=[value]
    for part in path:
        found=[]
        for n in nodes:
            if isinstance(n,dict) and part in n:found.append(n[part])
            elif isinstance(n,list):
                if part=='*':found.extend(n)
                elif part.isdigit() and str(int(part))==part and int(part)<len(n):found.append(n[int(part)])
        if len(found)>200:raise invalid('证据选择超过200项，需缩小场景')
        nodes=found
    return nodes


def evidence(demand,case):
    from .generation import digest
    results=[]
    for r in demand['rules']:
        values=select(case[r['source']],r['path']);keys=[digest(v) for v in values];wanted=digest(r['value'])
        op=r['op'];ok=False
        if op=='eq':ok=bool(values) and all(k==wanted for k in keys)
        elif op=='length_eq':ok=bool(values) and all(isinstance(v,(list,dict,str)) and len(v)==r['value'] for v in values)
        elif op=='distinct':ok=len(values)>=r['minimum'] and len(set(keys))==len(values)
        elif op=='equal':ok=len(values)>=r['minimum'] and len(set(keys))==1
        elif op=='count_eq':ok=keys.count(wanted)>=r['minimum']
        elif op=='last_eq':ok=bool(keys) and keys[-1]==wanted
        results.append({'rule':r,'observed':values,'matches':ok})
    return {'case_id':case['id'],'matches':all(r['matches'] for r in results),'rules':results}


def binding(job):
    from . import generation as g
    inventory=job.get('demand_inventory') or {}
    if inventory.get('contract_hash')!=job['contract_hash'] or inventory.get('source_hash')!=g.digest(sources(job)):
        raise invalid('原需求清单缺失或版本过期，先重新独立审核规范')
    return {'contract_hash':job['contract_hash'],'source_hash':g.digest(sources(job)),
            'inventory_hash':g.digest(inventory['requirements']),'evaluation_hash':g.digest(g.asset(job,'evaluation'))}


def context(job):
    from . import generation as g
    demands=job['demand_inventory']['requirements'];cases=g.asset(job,'evaluation')['cases']
    summaries={}
    for d in demands:
        summaries[d['id']]=[]
        for c in cases:
            item=evidence(d,c)
            summaries[d['id']].append({'case_id':item['case_id'],'matches':item['matches'],
                                      'failed_rules':[i for i,r in enumerate(item['rules']) if not r['matches']]})
    result={'requirement_sources':sources(job),'contract':job['contract'],'requirements':demands,
            'evaluation':g.asset(job,'evaluation'),'binding':binding(job),
            'data_evidence':summaries,'execution_performed':False}
    from . import generation_recovery_policy as rp
    if rp.enabled(job):
        result['witness_policy']=rp.WITNESS_POLICY
        for entry in reversed(job.get('inventory_interface_returns',[])):
            basis=entry['dispute']['binding']
            if basis['contract_hash']==job['contract_hash'] and basis['source_hash']==binding(job)['source_hash']:
                proposed={p['requirement_id'] for p in entry['value']['proposals']}
                result['prior_inventory_challenges']=[q for q in entry['dispute']['challenges'] if q['requirement_id'] not in proposed]
                result['prior_challenge_policy']='这些是在该次接口退回中未处理的历史质疑，不是当前已证实错误或批准；结合后续resolution、当前规则和全部测试复核，已解决可说明依据，不能因接口提案而宣称它们通过，也不能复述旧质疑要求重复修改。'
                break
    from . import generation_safeguards as guard
    if guard.enabled(job):result['open_gaps']=guard.open_gaps(job)
    from . import generation_inventory_repair as inventory
    if inventory.enabled(job):result['rule_policy']=inventory.POLICY
    from . import generation_inventory_review as dispute
    resolution=job.get('inventory_dispute_resolution')
    if dispute.enabled(job) and resolution and resolution['binding']==binding(job):
        result['inventory_dispute_resolution']=resolution
    resolution=job.get('interface_review_resolution')
    if resolution and resolution['binding']==binding(job):result['interface_review_resolution']=resolution
    if job.get('format_error',{}).get('stage')==STAGE:
        result['previous_error']=job['format_error']['reason']
        result['validation_feedback']=job['format_error'].get('validation_feedback')
    return result


def validate_review(job,raw):
    from . import generation as g
    from . import generation_inventory_review as dispute
    from . import generation_interface_review as interface
    from . import generation_safeguards as guard
    schema=interface.Coverage if interface.enabled(job) else dispute.Coverage if dispute.enabled(job) else Review
    value=(guard.coverage_schema(schema) if guard.enabled(job) else schema).model_validate(raw).model_dump()
    expected=binding(job)
    if value['binding']!=expected:
        missing=sorted(set(expected)-set(value['binding']))
        extra=sorted(set(value['binding'])-set(expected))
        changed=sorted(k for k in expected.keys() & value['binding'].keys() if expected[k]!=value['binding'][k])
        raise invalid('覆盖复核绑定字段缺失：'+', '.join(missing) if missing else '覆盖复核绑定摘要不匹配',
                      expected=expected,received=value['binding'],missing_fields=missing,
                      extra_fields=extra,changed_fields=changed,action='原样复制当前binding的全部字段；不能省略或自行计算摘要')
    demands={d['id']:d for d in job['demand_inventory']['requirements']};cases={c['id']:c for c in g.asset(job,'evaluation')['cases']}
    if len(value['findings'])!=len(demands) or {f['requirement_id'] for f in value['findings']}!=set(demands):
        raise invalid('必须逐项审核全部原需求，不得遗漏或新增ID',required_ids=sorted(demands))
    for f in value['findings']:
        if len(set(f['witnesses']))!=len(f['witnesses']) or not set(f['witnesses'])<=set(cases):raise invalid('覆盖见证引用不存在或重复',finding=f)
        if f['verdict']=='covered':
            results=[evidence(demands[f['requirement_id']],cases[k]) for k in f['witnesses']]
            if not results or not all(r['matches'] for r in results):
                raise invalid('覆盖声明缺少同一场景内满足证据要求的实际数据',requirement_id=f['requirement_id'],evidence=results,
                              action='不能只增加covers或拼接独立case；无真实见证应test_gap，接口无法表达应interface_gap')
    for o in value['omissions']:
        if o['source'] not in sources(job) or o['quote'] not in sources(job)[o['source']]:
            raise invalid('遗漏项不是原需求原文',omission=o,allowed_sources=list(sources(job)),
                          action='omissions只能引用requirement_sources的连续原文，不能把contract行为当作用户遗漏要求；保留已有test_gap/interface_gap判断，原需求无此项则不新增该遗漏，不修改测试期望')
    if interface.enabled(job):interface.validate_proposals(job,value)
    if dispute.enabled(job):dispute.validate_challenges(job,value)
    if guard.enabled(job):guard.validate_resolutions(job,value)
    return value


def require_approved(job):
    from . import generation_lean as lean
    if lean.enabled(job):return lean.require_approved(job)
    if not enabled(job):return
    from . import generation_safeguards as guard
    if guard.enabled(job) and job.get('standard_candidate'):raise invalid('正常标准候选尚未采纳，禁止执行/发布')
    from . import generation as g
    a=job.get('demand_approval') or {}
    if a.get('binding')!=binding(job) or a.get('asset')!=job['assets'].get(STAGE):raise invalid('需求覆盖尚未复核通过或资产已变化，禁止执行/发布')
    value=validate_review(job,g.asset(job,STAGE))
    from . import generation_safeguards as guard
    if guard.enabled(job) and guard.open_gaps(job):raise invalid('覆盖缺口仍未关闭，禁止执行/发布',open_gaps=guard.open_gaps(job))
    if value['omissions'] or any(f['verdict']!='covered' for f in value['findings']):raise invalid('需求覆盖复核仍有缺项，禁止执行/发布')


def validate_evaluation(job,value):
    base=job.get('demand_test_repair')
    if not base:return
    old=base['evaluation'];new={c['id']:c for c in value['cases']}
    if base['contract_hash']!=job['contract_hash'] or any(new.get(c['id'])!=c for c in old['cases']) or value['evasion_checks']!=old['evasion_checks']:
        raise invalid('补覆盖只能追加场景，不能删除或修改原输入、正确期望、可见性及规避要求')
    if len(new)<=len(old['cases']):raise invalid('补覆盖未增加实际场景，不能只改覆盖声明')


def rebuild(job,issue):
    from . import generation as g
    from .generation_fault_recovery import Stop
    if job.get('fault_freeze'):raise Stop('需求接口缺项涉及冻结资产，须显式新版本修订','demand_coverage_invalid')
    counts=job.setdefault('demand_repairs',{});counts['interface']=counts.get('interface',0)+1
    if counts['interface']>2:raise Stop('接口覆盖修订已达两次上限，保留具体缺项','demand_coverage_invalid')
    job['design_rebuild']={'issue':{'kind':'demand_interface_gap',**issue},'previous_contract_hash':job['contract_hash']}
    job['demand_previous_requirements']=copy.deepcopy(job.get('demand_inventory',{}).get('requirements',job.get('demand_previous_requirements',[])))
    job['demand_previous_source_hash']=g.digest(sources(job))
    job.pop('demand_test_repair',None);job.pop('demand_approval',None)
    job['checkpoint']='project_build';g.put(job);return 'project_build'


def apply_review(job):
    from . import generation as g
    from .generation_fault_recovery import Stop
    ref=job['assets'][STAGE]
    if job.get('applied_demand_review')==ref:return job['checkpoint']
    v=validate_review(job,g.asset(job,STAGE));job['applied_demand_review']=ref
    from . import generation_safeguards as guard
    if guard.enabled(job):guard.track_review(job,v,ref)
    job.setdefault('demand_reviews',[]).append({'asset':ref,'binding':v['binding'],'findings':v['findings'],'omissions':v['omissions']})
    from . import generation_inventory_review as dispute
    from . import generation_interface_review as interface
    if interface.enabled(job) and v['interface_proposals']:
        return interface.begin(job,v,ref)
    if dispute.enabled(job) and v['challenges']:
        stage=dispute.begin(job,v,ref);job['checkpoint']=stage;g.put(job);return stage
    gap='interface' if any(f['verdict']=='interface_gap' for f in v['findings']) else 'inventory' if v['omissions'] else 'tests' if any(f['verdict']=='test_gap' for f in v['findings']) else None
    if gap:
        if job.get('fault_freeze'):raise Stop('覆盖缺项涉及已冻结资产，须显式新版本修订','demand_coverage_invalid')
        if gap=='interface':return rebuild(job,{'findings':v['findings'],'omissions':v['omissions']})
        counts=job.setdefault('demand_repairs',{});counts[gap]=counts.get(gap,0)+1
        if counts[gap]>2:raise Stop('需求覆盖修订已达两次上限，保留具体缺项，不降低标准','demand_coverage_invalid')
        job.pop('demand_approval',None)
        if gap=='inventory':
            job['demand_previous_requirements']=copy.deepcopy(job['demand_inventory']['requirements'])
            job['demand_previous_source_hash']=g.digest(sources(job))
            job['demand_inventory_issue']=[{k:o[k] for k in ('source','quote')} for o in v['omissions']]
            stage='spec_review'
        else:
            job['demand_test_repair']={'contract_hash':job['contract_hash'],'evaluation':copy.deepcopy(g.asset(job,'evaluation')),
                                     'review_asset':ref,'findings':v['findings']}
            job['evaluation_review_guided']=True;stage='evaluation'
    else:
        job['demand_approval']={'binding':v['binding'],'asset':ref};job.pop('demand_test_repair',None);stage='fingerprint'
    job['checkpoint']=stage;g.put(job);return stage


INSTRUCTION='独立核对原始需求、公开接口和固定测试，不看实现/私有故障/运行输出。逐项核对requirements，还须对照原文发现清单遗漏并填omissions。covered必须有同一case真实见证，程序data_evidence只证明数据关系，不证明语义。共享状态须同一scenario内多个实体，独立case不能证明隔离。中断需观察停止时刻或可信固定期望中的轨迹，最终完成不能替代；多次恢复、完成后再次恢复、关闭重开按原需求检查。接口不能表达选interface_gap，接口支持但测试缺失选test_gap。不得因为实现失败改expected，不得把用户程序日志或covers标签当通过证据。说明具体缺少什么及如何补，不能添加原需求外条件。此为有限覆盖复核，不证明全面正确。'
