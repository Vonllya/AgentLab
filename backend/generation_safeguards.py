"""Opt-in adoption reviews and append-only coverage repairs.

Semantic decisions belong to the evaluator; the server binds, preserves and
routes assets. No generated code executes in this module.
"""
import copy
import json
from typing import Annotated, Literal
from pydantic import Field, RootModel, model_validator
from .generation_schema import Strict
from .generation_protocol import BehaviorCase

VERSION = 'adoption-coverage-v1'
STAGE = 'standard_review'


def enabled(job):return job.get('safeguards_version') == VERSION


def rejection_context(job):
    from . import generation as g, generation_demands as dc
    value=job.get('standard_rejection')
    if not enabled(job) or not value:return None
    binding=value['binding']
    if binding['contract_hash']!=job.get('contract_hash') or binding['source_hash']!=g.digest(dc.sources(job)):return None
    return copy.deepcopy(value)


def invalid(reason, **feedback):
    from .generation_fault_first import Invalid
    return Invalid(reason, feedback, category='adoption_coverage_invalid')


def appending(job):
    from .generation_lean import enabled as lean_enabled
    return (enabled(job) or lean_enabled(job)) and not job.get('evaluation_repair') and bool(job.get('demand_test_repair') or job.get('coverage_repair'))


def base_evaluation(job):
    repair = job.get('demand_test_repair')
    return repair['evaluation'] if repair else job['coverage_repair']['base_evaluation']


class Additions(Strict):
    base_hash: str = Field(pattern='^[a-f0-9]{64}$')
    added_cases: list[BehaviorCase] = Field(min_length=1, max_length=6)


def merge_additions(job, raw):
    from . import generation as g
    base = base_evaluation(job)
    if raw['base_hash'] != g.digest(base):
        raise invalid('补测依据过期', field='base_hash', received=raw['base_hash'], expected=g.digest(base), action='复制当前base_hash，仅返回新增用例')
    result = copy.deepcopy(base)
    seen = {c['id'] for c in base['cases']}
    for i, case in enumerate(raw['added_cases']):
        if case['id'] in seen:
            raise invalid('新增检查ID与已有检查重复', field=f'added_cases[{i}].id', received=case['id'], reserved_ids=sorted(seen), action='给新增场景选择唯一ID；修改旧检查须走测试质疑流程')
        if any(case['input'] == c['input'] and case['expected'] == c['expected'] for c in result['cases']):
            raise invalid('新增检查只是重复已有输入和期望', field=f'added_cases[{i}]', received=case['id'], action='提供能观察缺失行为的不同场景，不能只改ID或covers')
        seen.add(case['id']);result['cases'].append(copy.deepcopy(case))
    request=(job.get('demand_test_repair') or {}).get('trigger_coverage_request')
    if request and not any(c['visibility']==request['visibility'] and set(c['covers']) & set(request['behavior_ids']) for c in raw['added_cases']):
        raise invalid('新增用例没有满足所请求的可见性和行为范围',required_visibility=request['visibility'],
                      required_behaviors=request['behavior_ids'],action='追加相应可见性和行为的真实场景；标签满足仍须后续故障审核和Docker执行。')
    return result


class Resolution(Strict):
    gap_id: str = Field(max_length=64)
    decision: Literal['covered', 'out_of_scope']
    witnesses: list[str] = Field(max_length=10)
    source: str = Field(max_length=100)
    quote: str = Field(min_length=4, max_length=1200)
    reason: str = Field(min_length=15, max_length=1200)


def coverage_schema(base):
    # Keep legacy interface/inventory dispute fields and their validation.
    from pydantic import create_model
    return create_model('TrackedCoverageReview', __base__=base,
                        gap_resolutions=(list[Resolution], Field(default_factory=list, max_length=12)))


def open_gaps(job):return [x for x in job.get('coverage_gaps', []) if x['status'] == 'open']


def validate_resolutions(job, value):
    from . import generation as g, generation_demands as dc
    gaps = {x['id']: x for x in open_gaps(job)}
    closed = {x['id']:x for x in job.get('coverage_gaps',[]) if x['status']=='closed'}
    seen = set();all_seen=set();cases = {c['id']: c for c in g.asset(job, 'evaluation')['cases']}
    for resolution in value.get('gap_resolutions', []):
        key = resolution['gap_id']
        if key in all_seen:raise invalid('缺口关闭引用重复', gap_id=key)
        all_seen.add(key)
        if key in closed and closed[key]['resolution']==resolution and closed[key]['evaluation_hash']==g.digest(g.asset(job,'evaluation')):
            continue  # Revalidating the exact persisted approval, not new closure.
        if key not in gaps or key in seen:raise invalid('缺口关闭引用不存在或重复', field='gap_resolutions', available_ids=sorted(gaps), received=key)
        seen.add(key)
        if resolution['quote'] not in dc.sources(job).get(resolution['source'], ''):
            raise invalid('关闭依据必须引用原始需求连续原文', gap_id=key, allowed_sources=dc.sources(job))
        if resolution['decision'] == 'covered':
            witnesses = resolution['witnesses']
            if not witnesses or len(set(witnesses)) != len(witnesses) or not set(witnesses) <= set(cases):
                raise invalid('缺口关闭缺少有效测试见证', gap_id=key, available_case_ids=sorted(cases))
            # A prior test gap needs a genuinely added case. Merely relabeling
            # old cases as covered cannot silently erase the original finding.
            if not set(witnesses) - set(gaps[key]['existing_case_ids']):
                raise invalid('缺口关闭未提供新增测试', gap_id=key, action='追加覆盖缺失行为的场景；认为原发现不成立则out_of_scope并引用需求解释')
            finding=next((f for f in value['findings'] if f['requirement_id']==gaps[key]['requirement_id']),None)
            if not finding or finding['verdict']!='covered':
                raise invalid('关闭缺口前须完成对应需求的覆盖判断',gap_id=key,requirement_id=gaps[key]['requirement_id'],action='先处理对应需求；新增边界见证由gap_resolutions关联，不要求它满足其他成功场景的见证规则')
        elif resolution['witnesses']:
            raise invalid('范围排除不能夹带测试通过声明', gap_id=key)
    all_covered = not value['omissions'] and all(f['verdict'] == 'covered' for f in value['findings'])
    if all_covered and seen != set(gaps):
        raise invalid('已提出的覆盖缺口尚未逐项处理', missing_gap_ids=sorted(set(gaps)-seen), open_gaps=list(gaps.values()), action='逐项填写gap_resolutions；covered引用新增见证，out_of_scope引用原需求并解释为何原发现不成立')


def track_review(job, value, asset):
    from . import generation as g
    ledger = job.setdefault('coverage_gaps', [])
    for resolution in value.get('gap_resolutions', []):
        gap = next(x for x in ledger if x['id'] == resolution['gap_id'])
        gap.update(status='closed', resolution=copy.deepcopy(resolution), closed_by=asset,
                   evaluation_hash=g.digest(g.asset(job, 'evaluation')))
    cases = [c['id'] for c in g.asset(job, 'evaluation')['cases']]
    for f in value['findings']:
        if f['verdict'] != 'test_gap':continue
        if any(x['status']=='open' and x['requirement_id']==f['requirement_id'] for x in ledger):continue
        key = g.digest({'review':asset, 'finding':f})
        ledger.append({'id':key, 'requirement_id':f['requirement_id'], 'reason':f['reason'],
                       'review_asset':asset, 'existing_case_ids':cases, 'status':'open'})


class Citation(Strict):
    source: str = Field(max_length=160)
    quote: str = Field(min_length=4, max_length=1200)


class StandardReview(Strict):
    binding: dict[str, str]
    decision: Literal['accept', 'revise']
    reason: str = Field(min_length=15, max_length=1600)
    evidence: list[Citation] = Field(min_length=1, max_length=6)
    correction: str = Field(max_length=1600)
    correction_target: Literal['candidate','review_request'] = 'candidate'
    issues: list[dict] = Field(default_factory=list, max_length=8)
    @model_validator(mode='after')
    def coherent(self):
        if (self.decision == 'revise') != bool(self.correction.strip()):raise ValueError('revise须具体指出应修改的资产和冲突；accept不得夹带纠正动作')
        return self



class ReviewIssue(Strict):
    path: str = Field(max_length=150)
    reason: str = Field(min_length=10, max_length=800)
    requested_change: str = Field(min_length=10, max_length=1000)


class AcceptedReview(Strict):
    decision: Literal['accept']
    reason: str = Field(min_length=15, max_length=1600)
    evidence: list[Citation] = Field(min_length=1, max_length=6)


class RejectedReview(Strict):
    decision: Literal['revise']
    reason: str = Field(min_length=15, max_length=1600)
    evidence: list[Citation] = Field(min_length=1, max_length=6)
    issues: list[ReviewIssue] = Field(min_length=1, max_length=8)
    correction_target: Literal['candidate', 'review_request'] = 'candidate'


class SemanticReview(RootModel[Annotated[AcceptedReview | RejectedReview, Field(discriminator='decision')]]):
    pass


def review_schema(job):
    from .generation_corrections import enabled as executable
    return SemanticReview if executable(job) else StandardReview


def bind_response(job, raw, audit):
    """Bind only a response from this persisted call; recheck actual assets before adoption."""
    from .generation_corrections import enabled as executable
    if not executable(job):return raw
    from . import generation as g
    current=g.get(job['id'])
    pending = job.get('standard_candidate')
    from . import generation_demands as dc
    if g.digest(dc.sources(current)) != g.digest(dc.sources(job)):
        # Preserve the newer source when the failed call's audit is persisted.
        for key in ('request','requirement_answers'):
            if key in current:job[key]=copy.deepcopy(current[key])
            else:job.pop(key,None)
        raise invalid('采纳调用期间原始需求已变化，拒绝旧决定',action='按新需求重新确认依据，不采纳旧响应')
    if (not pending or current.get('standard_candidate') != pending
            or current.get('contract_hash') != job.get('contract_hash')
            or pending['binding'] != candidate_binding(current,pending['binding']['kind'],pending['asset'])
            or audit.get('standard_request') != pending
            or pending['binding'] != candidate_binding(job,pending['binding']['kind'],pending['asset'])
            or job.get('active_action',{}).get('asset') != audit['asset']):
        raise invalid('采纳响应对应的候选已变化，拒绝迟到决定', call_asset=audit['asset'])
    value = SemanticReview.model_validate(raw).model_dump()
    issues = value.get('issues', [])
    audit['server_bound_standard_response'] = True
    return {'binding':copy.deepcopy(pending['binding']), 'decision':value['decision'],
            'reason':value['reason'], 'evidence':value['evidence'],
            # Legacy display field is bounded at 1600 chars. Structured issues
            # carry the complete work order; never concatenate their (larger)
            # independently valid text into this compatibility envelope.
            'correction':('需按结构化issues修订：'+'、'.join(dict.fromkeys(i['path'] for i in issues))) if issues else '',
            'correction_target':value.get('correction_target','candidate'), 'issues':issues}


def candidate_binding(job, kind, asset):
    from . import generation as g, generation_demands as dc
    binding={'contract_hash':job['contract_hash'], 'source_hash':g.digest(dc.sources(job)),
             'candidate_hash':g.digest(json.loads((g.root(job)/asset/'output.json').read_text())), 'kind':kind}
    if kind=='inventory_rules':binding['inventory_hash']=g.digest(job['demand_inventory'])
    return binding


def begin(job, kind, asset):
    from . import generation as g
    binding=candidate_binding(job,kind,asset)
    approved=job.get('standard_approval')
    if approved and approved['binding']==binding:return False
    job['standard_candidate']={'binding':binding, 'asset':asset}
    job['checkpoint']=STAGE;g.put(job);return True


def context(job):
    from . import generation as g, generation_demands as dc
    pending=job['standard_candidate'];kind=pending['binding']['kind']
    if pending['binding']!=candidate_binding(job,kind,pending['asset']):raise invalid('标准采纳审查依据过期', action='重新基于当前资产发起审查，不应用旧决定')
    raw=json.loads((g.root(job)/pending['asset']/'output.json').read_text())
    public_candidate=raw['contract'] if kind=='public_patch' else raw
    result={'binding':copy.deepcopy(pending['binding']), 'requirement_sources':dc.sources(job),
            'current_public_specification':copy.deepcopy(job['contract']), 'candidate':public_candidate,
            'normal_behavior_policy':'公开规范、requirements和expected只描述正确行为；用户描述的错误症状不是正常验收义务。故障算法只能进入私有设计和复现检查。',
            'execution_performed':False}
    if kind=='public_patch':
        from .generation_spec_patch import changed_paths
        result['changed_paths']=list(changed_paths(job['contract'],raw['contract']))
        result['requested_changes']=job.get('spec_review_feedback')
    if kind=='inventory_rules':
        result['current_requirements']=job['demand_inventory']['requirements']
        result['challenges']=job['inventory_dispute']['challenges']
    result['evidence_sources']={**result['requirement_sources'],
        'current_public_specification':json.dumps(result['current_public_specification'],ensure_ascii=False),
        'candidate':json.dumps(public_candidate,ensure_ascii=False)}
    from .generation_corrections import enabled as executable
    if executable(job):
        result['normal_behavior_policy']='只审核本次修改是否使正常义务违背原始需求。公开symptom用于描述待修现象，不是正常expected；不能因描述错误现象就否决。继承的教学措辞不作为本次正常规则补丁阻断项。接口可以为验证原需求合理修订，不因改变当前生成接口就否决。'
        result['runtime_facts']={'per_scenario':'每次scenario独立受限容器，跨容器无共享状态',
                                 'isolation_evidence':'仅分别调用不足以证明同一进程或存储内跨实体不泄漏；所需交互应在一个scenario内表达。'}
        result['review_scope']='围绕changed_paths和正常目标/规则；candidate纠正限当前工单。发现工单范围之外的必要正常规则问题，使用review_request并逐项issues指出路径，由规范审查重新授权。不要把继承症状描述等同正确验收义务。'
    from . import generation_recovery_policy as rp
    if rp.enabled(job):
        result['witness_policy']=rp.WITNESS_POLICY
        result['citation_policy']='candidate/current_public_specification可逐字引用单个解码后字符串字段，不必复制JSON转义符；禁止概括、跨字段或跨段拼接。原始需求仍须连续原文。'
        if kind=='inventory_rules':result['allowed_issue_paths']=rp.rule_issue_targets(job,raw)
    if rejection_context(job):result['previous_rejection']=rejection_context(job)
    if job.get('format_error',{}).get('stage')==STAGE:result['previous_error']=job['format_error']
    return result


def validate_standard(job, raw):
    value=StandardReview.model_validate(raw).model_dump();ctx=context(job)
    if value['binding']!=ctx['binding']:raise invalid('标准采纳审查依据过期', expected=ctx['binding'], received=value['binding'])
    if value['correction_target']=='review_request' and (value['decision']!='revise' or ctx['binding']['kind']!='public_patch'):
        raise invalid('只有公开补丁的错误审查要求可退回规范审查', field='correction_target', action='本候选自身错误使用candidate；公开补齐要求本身错误才使用review_request')
    from .generation_corrections import enabled as executable
    if executable(job) and value['decision']=='revise':
        if not value['issues']:raise invalid('拒绝必须给出结构化路径和修改要求')
        if ctx['binding']['kind']=='public_patch':
            from .generation_spec_patch import targets
            issues=[{'path':i['path'],'reason':i['requested_change']} for i in value['issues']]
            try:targets(job['contract'],{'issues':issues})
            except (ValueError,KeyError,TypeError) as exc:raise invalid('拒绝路径不能生成合法公开工单',reason_detail=str(exc))
    from . import generation_recovery_policy as rp
    if rp.enabled(job) and value['decision']=='revise' and ctx['binding']['kind']=='inventory_rules':
        rp.issue_owner(job,ctx['candidate'],value['issues'])
    citation_errors=[]
    for index,item in enumerate(value['evidence']):
        # Public current/candidate text is provided as whole JSON evidence;
        # original requirement citations remain individually addressable.
        sources=ctx['evidence_sources']
        if item['source'] not in sources:
            citation_errors.append({'field':f'evidence[{index}].source','kind':'unknown_source','citation':item,
                'action':'source精确选择available_sources中的键，不附加JSON路径；同时逐字核对quote。'})
            continue
        quoted=item['quote'] in sources.get(item['source'],'')
        if not quoted and rp.enabled(job) and item['source'] in ('candidate','current_public_specification'):
            quoted=rp.quoted_field(ctx[item['source']],item['quote'])
        if not quoted:
            citation_errors.append({'field':f'evidence[{index}].quote','kind':'non_contiguous_quote','citation':item,
                'action':'quote须逐字引用所选来源的连续原文，不能概括或拼接。'+('候选可引用单个解码后字符串字段。' if rp.enabled(job) else '逐字复制evidence_sources中的文本。')})
    if citation_errors:
        raise invalid('标准审查来源或引用校验失败：'+('存在未提供的source' if any(e['kind']=='unknown_source' for e in citation_errors) else '引用不是提供来源的连续原文'),
            citation=citation_errors[0]['citation'],field=citation_errors[0]['field'],
            citation_errors=citation_errors,available_sources=sorted(ctx['evidence_sources']),
            action='逐项修正citation_errors中全部具体字段；来源键与quote分别核对，不修改其他已合法字段。')
    return value


def apply_standard(job):
    from . import generation as g, generation_fault_first as ff, generation_inventory_review as inventory
    if (not job.get('standard_candidate') and job.get('applied_standard_review')
            and job.get('standard_approval',{}).get('review_asset')==job['applied_standard_review']):
        return job['checkpoint']
    ref=job['assets'][STAGE]
    if job.get('applied_standard_review')==ref and not job.get('standard_candidate'):return job['checkpoint']
    value=validate_standard(job,g.asset(job,STAGE));pending=job['standard_candidate'];kind=pending['binding']['kind']
    entry={'candidate':copy.deepcopy(pending), 'review_asset':ref, 'result':value}
    if kind=='inventory_rules':entry['original_dispute']=copy.deepcopy(job['inventory_dispute'])
    job.setdefault('standard_review_history',[]).append(entry)
    job['applied_standard_review']=ref
    if value['decision']=='accept':
        job.pop('standard_rejection',None)
        job['standard_approval']={'binding':pending['binding'], 'review_asset':ref}
        job.pop('standard_candidate',None)
        if kind=='public_patch':
            if 'project_build' in job['assets']:job.setdefault('asset_revisions',[]).append({'stage':'project_build','asset':job['assets']['project_build']})
            job['previous_bundle_assets']=copy.deepcopy(job['assets']);job['assets']['project_build']=pending['asset']
            job['checkpoint']='spec_review'
            ff.install(job);stage='spec_review'
        elif kind=='spec_approval':
            job['checkpoint']='coverage_review' if 'evaluation' in job['assets'] else 'evaluation'
            stage=ff.apply_review(job)
        else:
            job['checkpoint']='coverage_review'
            stage=inventory.apply(job)
    else:
        counts=job.setdefault('standard_rejections',{});key=kind+':'+job['contract_hash'];counts[key]=counts.get(key,0)+1
        job['standard_rejection']={**value, 'candidate_asset':pending['asset']}
        job.pop('standard_candidate',None)
        # Keep the original approved standards and send the precise rejection
        # back to the asset owner; do not install the rejected bundle/rules.
        stage={'public_patch':'project_build','spec_approval':'spec_review','inventory_rules':'spec_review'}[kind]
        from . import generation_recovery_policy as rp
        if kind=='inventory_rules' and rp.enabled(job):
            stage=rp.route_inventory_rejection(job,pending,value)
        elif kind=='inventory_rules':
            # Rule-only review cannot repair a wrongly defined objective.
            # Revisit its owner without deleting accepted demand identities.
            from . import generation_demands as dc
            job['demand_previous_requirements']=copy.deepcopy(job['demand_inventory']['requirements'])
            job['demand_previous_source_hash']=g.digest(dc.sources(job))
            job.pop('standard_approval',None);job.pop('demand_approval',None)
            job.pop('inventory_dispute',None)
            job['standard_rejection']['inventory_scope']={'owner':'spec_review',
                'preserve':['id','source','quote','kind'], 'may_correct':['objective','rules'],
                'instruction':'保留原需求身份与原文引用；可按完整原需求纠正目标和证据规则，不能恢复错误目标或把故障要求当正确行为。'}
        from .generation_corrections import enabled as executable
        if executable(job) and kind=='public_patch':
            allowed=[i['path'] for i in (job.get('spec_review_feedback') or {}).get('issues',[])]
            outside=[i for i in value['issues'] if not any(i['path']==p or i['path'].startswith(p+'/') for p in allowed)]
            if outside:
                job['standard_rejection']['correction_target']='review_request'
                job['standard_rejection']['scope_mismatch']={'allowed_paths':allowed,'requested_issues':outside,
                    'instruction':'当前工单不能修改这些路径；独立核对是否为必要正常规则问题，成立才重新给出issues授权，不因继承症状或合法接口扩展要求返工。'}
                value={**value,'correction_target':'review_request'}
            elif value['correction_target']=='candidate':
                old_feedback=copy.deepcopy(job.get('spec_review_feedback',{}))
                updated={i['path'] for i in value['issues']}
                issues=[i for i in old_feedback.get('issues',[]) if i['path'] not in updated]
                issues.extend({'path':i['path'],'reason':i['requested_change']} for i in value['issues'])
                job.setdefault('standard_scope_history',[]).append({'original':old_feedback,'review_asset':ref})
                job['spec_review_feedback']={'reason':value['reason'],'issues':issues}
        if kind=='public_patch' and value['correction_target']=='review_request':
            job.pop('spec_review_feedback',None)
            stage='spec_review'
        if counts[key]>2:
            from .generation_fault_recovery import Stop
            job['checkpoint']=stage;g.put(job)
            raise Stop('正常标准候选已纠正两次仍不成立；保留具体冲突与原标准','normal_standard_conflict')
    job['checkpoint']=stage;g.put(job);return stage


INSTRUCTION=('你是评测Agent的正常标准采纳审查阶段。独立对照原始需求与候选，不看实现、私有设计、实际输出。'
 '判断正常行为与故障症状是否被混淆。用户说“恢复后重复执行”通常是待修症状，正常期望应跳过已完成步骤；'
 '这只是解释示例，必须依据当前原文，不能机械套用。合法澄清可以接受，不得增加用户外要求。'
 'public_patch审核公开修改；spec_approval审核requirements的目标和规则是否验证正确行为；'
 'inventory_rules审核纠正规则，不能为了证明故障而把正常expected改成错误结果。'
 '冲突选revise并在correction写具体位置、冲突原文与应改资产：私有故障细节应移出正常标准，'
 '已有正常标准被污染则要求恢复正确行为；不要让构建去修好故障版。'
 'revise时correction_target=candidate表示候选错误、回到其创建阶段；public_patch若requested_changes本身要求正常行为符合故障算法，则review_request退回规范审查纠正要求，不让构建反复服从错误工单。其他情况使用candidate。'
 'evidence.source只选evidence_sources的键，quote复制其连续原文。accept只能说明候选可采纳，不是人工发布批准。')

SEMANTIC_INSTRUCTION=('你是评测Agent的正常标准采纳审查。独立对照原始需求和本次正常规则修改，合法澄清可接受。公开symptom可以描述待修故障，不是正确expected；不要求把所有公开字段改成正常结果。接口为表达原需求可合理扩展。accept仅返回decision/reason/evidence，不填写摘要、correction或修改动作。revise返回结构化issues(path/reason/requested_change)与correction_target。candidate只限当前修改许可；工单本身错误或必要修改超范围用review_request。证据逐字引用所提供来源，不能迎合实现。程序负责绑定和路由，采纳不等于测试通过或人工批准。')
