"""Evidence-bound disputes; coverage cannot rewrite the demand inventory."""
import copy
from typing import Literal
from pydantic import Field, RootModel, model_validator
from .generation_schema import Strict
from . import generation_demands as dc

VERSION = 'inventory-review-v1'
STAGE = 'inventory_review'


def enabled(job):return job.get('inventory_review_version') == VERSION


class Challenge(Strict):
    requirement_id: str = Field(max_length=41)
    source: str = Field(max_length=100)
    quote: str = Field(min_length=4, max_length=1200)
    rule_indices: list[int] = Field(min_length=1, max_length=6)
    case_ids: list[str] = Field(default_factory=list, max_length=4)
    reason: str = Field(min_length=15, max_length=1200)


class Finding(dc.Finding):
    verdict: Literal['covered', 'interface_gap', 'test_gap', 'inventory_issue']


class Coverage(dc.Review):
    findings: list[Finding] = Field(min_length=1, max_length=12)
    challenges: list[Challenge] = Field(default_factory=list, max_length=6)


class Decision(Strict):
    requirement_id: str = Field(max_length=41)
    verdict: Literal['correct_rules', 'keep_rules'] = Field(description='correct_rules=质疑成立并纠正规则；keep_rules=质疑不成立、原规则保持。不是判断原规则是否合格。')
    reason: str = Field(min_length=15, max_length=1400)
    replacement_rules: list[dc.Rule] = Field(default_factory=list, max_length=6)

    @model_validator(mode='after')
    def coherent(self):
        if (self.verdict == 'correct_rules') != bool(self.replacement_rules):
            raise ValueError('correct_rules须提供该项完整纠正规则；keep_rules不得修改规则')
        return self


class Review(Strict):
    binding: dict[str, str]
    decisions: list[Decision] = Field(min_length=1, max_length=6)


class InterfaceProposal(Strict):
    requirement_id: str = Field(max_length=41)
    source: str = Field(max_length=100)
    quote: str = Field(min_length=4,max_length=1200)
    current_fields: list[dc.PublicFieldPath] = Field(min_length=1,max_length=8)
    missing_behavior: str = Field(min_length=15,max_length=1000)
    why_rules_or_tests_insufficient: str = Field(min_length=15,max_length=1000)


class InterfaceReturn(Strict):
    binding: dict[str,str]
    decision: Literal['interface_issue']
    proposals: list[InterfaceProposal] = Field(min_length=1,max_length=6)


class RecoverableReview(RootModel[Review | InterfaceReturn]):
    pass


def review_schema(job):
    from .generation_recovery_policy import enabled
    return RecoverableReview if enabled(job) else Review


def interface_return(job, value):
    from . import generation_interface_review as interface
    selected=[p['requirement_id'] for p in value['proposals']]
    questioned={c['requirement_id'] for c in job['inventory_dispute']['challenges']}
    if len(set(selected))!=len(selected) or not set(selected)<=questioned:
        raise dc.invalid('接口退回只能选择当前受影响的被质疑需求，不能重复或增加ID',allowed_ids=sorted(questioned))
    proposal={'interface_proposals':value['proposals'],
        'findings':[{'requirement_id':p['requirement_id'],'verdict':'interface_gap'} for p in value['proposals']]}
    interface.validate_proposals(job,proposal)
    return proposal


def validate_challenges(job, value):
    items = {d['id']: d for d in job['demand_inventory']['requirements']}
    issues = {f['requirement_id'] for f in value['findings'] if f['verdict'] == 'inventory_issue'}
    challenges = value['challenges']
    if len({c['requirement_id'] for c in challenges}) != len(challenges) or {c['requirement_id'] for c in challenges} != issues:
        raise dc.invalid('清单质疑必须与inventory_issue逐项对应，不得重复或夹带修改', required_ids=sorted(issues))
    from . import generation as g
    cases = {c['id'] for c in g.asset(job, 'evaluation')['cases']}
    for c in challenges:
        item = items[c['requirement_id']]
        if c['source'] != item['source'] or c['quote'] not in dc.sources(job).get(c['source'], ''):
            raise dc.invalid('清单质疑须引用对应原需求原文', requirement_id=item['id'], original=item)
        indices = c['rule_indices']
        if len(set(indices)) != len(indices) or any(type(i) is not int or not 0 <= i < len(item['rules']) for i in indices):
            raise dc.invalid('质疑规则下标不存在或重复', requirement_id=item['id'], rule_count=len(item['rules']))
        if len(set(c['case_ids'])) != len(c['case_ids']) or not set(c['case_ids']) <= cases:
            raise dc.invalid('质疑引用不存在或重复的测试证据', requirement_id=item['id'], available_case_ids=sorted(cases))
        for previous in job.get('inventory_dispute_history', []):
            if previous['dispute']['binding']==dc.binding(job) and c in previous['dispute']['challenges']:
                decision=next(d for d in previous['result']['decisions'] if d['requirement_id']==item['id'])
                if decision['verdict']=='keep_rules':
                    raise dc.invalid('相同依据的清单质疑已被驳回，须纠正覆盖判断或提供新证据',
                                     requirement_id=item['id'],rejection=decision,
                                     action='不得原样重提；根据驳回理由修正测试/覆盖判断，确有新证据再提交新质疑')


def begin(job, value, asset):
    from . import generation as g
    if job.get('fault_freeze'):raise dc.invalid('冻结后不能修改清单，须显式新版本修订')
    job['inventory_dispute'] = {'binding': dc.binding(job), 'review_asset': asset,
                                'challenges': copy.deepcopy(value['challenges'])}
    job.pop('demand_approval', None)
    job['checkpoint']=STAGE
    g.put(job)
    return STAGE


def binding(job):
    from . import generation_interface_review as interface
    if interface.pending(job):return interface.binding(job)
    from . import generation as g
    dispute = job['inventory_dispute']
    if dispute['binding'] != dc.binding(job):raise dc.invalid('清单质疑依据已变化，禁止应用旧复核')
    return {**dispute['binding'], 'challenge_hash': g.digest(dispute['challenges'])}


def context(job):
    from . import generation_interface_review as interface
    if interface.pending(job):return interface.context(job)
    from . import generation as g
    from .generation_inventory_repair import POLICY
    dispute = job['inventory_dispute']
    ids = {c for q in dispute['challenges'] for c in q['case_ids']}
    allowed={c['requirement_id'] for c in dispute['challenges']}
    from .generation_safeguards import rejection_context
    result = {'adoption_rejection':rejection_context(job), 'binding': binding(job), 'requirement_sources': dc.sources(job), 'contract': job['contract'],
            'allowed_requirement_ids':sorted(allowed),
            'requirements': [d for d in job['demand_inventory']['requirements'] if d['id'] in allowed],
            'challenges': dispute['challenges'],
            'selected_test_data': [c for c in g.asset(job, 'evaluation')['cases'] if c['id'] in ids],
            'execution_performed': False, 'rule_policy': POLICY,
            'previous_error': job.get('format_error') if job.get('format_error', {}).get('stage') == STAGE else None}

    from . import generation_recovery_policy as rp
    if rp.enabled(job):
        result['witness_policy']=rp.WITNESS_POLICY
        result['interface_return_policy']='无法用当前接口表达原需求时返回InterfaceReturn：decision=interface_issue、当前binding及proposals；只提出可复核接口问题，不给空replacement_rules，不直接授权构建。'
    return result


def validate(job, raw):
    from . import generation_interface_review as interface
    if interface.pending(job):return interface.validate(job, raw)
    if job.get('fault_freeze'):raise dc.invalid('冻结后禁止应用清单复核，须显式新版本修订')
    value = review_schema(job).model_validate(raw).model_dump()
    if value['binding'] != binding(job):raise dc.invalid('清单复核未绑定当前质疑与资产', expected=binding(job))
    if value.get('decision')=='interface_issue':
        interface_return(job,value)
        return value
    questioned = {c['requirement_id'] for c in job['inventory_dispute']['challenges']}
    if len(value['decisions']) != len(questioned) or {d['requirement_id'] for d in value['decisions']} != questioned:
        received=[d['requirement_id'] for d in value['decisions']]
        raise dc.invalid('须逐项处理当前质疑，不能处理未质疑项',field='decisions',
                         required_ids=sorted(questioned),received_ids=received,
                         missing_ids=sorted(questioned-set(received)),extra_ids=sorted(set(received)-questioned),
                         action='只返回allowed_requirement_ids中的ID；规则确有错误选correct_rules并给出修正规则，质疑错误选keep_rules且不带修改')
    items = copy.deepcopy(job['demand_inventory']['requirements'])
    decisions = {d['requirement_id']: d for d in value['decisions']}
    for item in items:
        decision = decisions.get(item['id'])
        if decision and decision['verdict'] == 'correct_rules':
            new=decision['replacement_rules']
            if new==item['rules']:raise dc.invalid('接受质疑但规则没有变化',requirement_id=item['id'])
            challenge=next(c for c in job['inventory_dispute']['challenges'] if c['requirement_id']==item['id'])
            preserved=[r for i,r in enumerate(item['rules']) if i not in challenge['rule_indices']]
            remaining=iter(new)
            if any(not any(r==candidate for candidate in remaining) for r in preserved):
                raise dc.invalid('不得删除或改写未被质疑的规则',requirement_id=item['id'],must_preserve=preserved)
            item['rules'] = new
    # Retain IDs, original quotes, kinds, objectives and all unchallenged rules.
    dc.validate_inventory(job, items)
    return value


def apply(job):
    from . import generation_interface_review as interface
    if interface.pending(job) and job['interface_proposal']['review_asset']==job['assets'].get(STAGE):return STAGE
    if interface.pending(job) or (job.get("applied_interface_review")==job["assets"].get(STAGE)):return interface.apply(job)
    from . import generation as g
    if job.get('applied_inventory_review')==job['assets'][STAGE]:return job['checkpoint']
    value = validate(job, g.asset(job, STAGE))
    if value.get('decision')=='interface_issue':
        proposal=interface_return(job,value)
        job.setdefault('inventory_interface_returns',[]).append({'asset':job['assets'][STAGE],'value':copy.deepcopy(value),'dispute':copy.deepcopy(job['inventory_dispute'])})
        interface.begin(job,proposal,job['assets'][STAGE])
        job.pop('inventory_dispute',None)
        g.put(job)
        return STAGE
    from . import generation_safeguards as guard
    if guard.enabled(job) and any(d['verdict']=='correct_rules' for d in value['decisions']) and guard.begin(job,'inventory_rules',job['assets'][STAGE]):return guard.STAGE
    before = copy.deepcopy(job['demand_inventory'])
    job.setdefault('inventory_dispute_history', []).append({'dispute': copy.deepcopy(job['inventory_dispute']),
        'review_asset': job['assets'][STAGE], 'result': copy.deepcopy(value), 'previous_inventory': before})
    decisions = {d['requirement_id']: d for d in value['decisions']}
    for item in job['demand_inventory']['requirements']:
        decision = decisions.get(item['id'])
        if decision and decision['verdict'] == 'correct_rules':item['rules'] = copy.deepcopy(decision['replacement_rules'])
    job['inventory_dispute_resolution'] = {'binding': dc.binding(job), 'decisions': value['decisions'],
                                          'review_asset': job['assets'][STAGE]}
    job.pop('inventory_dispute', None);job.pop('demand_approval', None)
    job.pop('format_error', None);job['checkpoint'] = dc.STAGE
    job['applied_inventory_review']=job['assets'][STAGE]
    g.put(job)
    return dc.STAGE


INSTRUCTION = ('你是评测Agent的清单独立复核阶段，重新依据原始需求、公开接口、原清单和质疑证据判断。'
 '质疑不是事实，创建者也不天然正确。只处理allowed_requirement_ids，不能审核其他项。'
 '原规则确有错误选correct_rules并提供该项完整replacement_rules；质疑错误选keep_rules且不带规则修改。'
 '不得删除要求、改原文/类别/目标、降低行为要求或修改测试；保留规则时给具体依据和评测应如何纠正。'
 '测试数据是未经执行的检查资产，不证明实现已通过。不见任何实现、故障设计或实际输出。'
 '业务字段选错应修证据规则，不能用特殊测试数据迎合错误规则；环境约束不能靠新增业务输出证明。')
