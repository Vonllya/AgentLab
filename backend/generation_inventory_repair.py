"""New-job-only, bounded local correction of rejected demand inventories.

Candidates and diagnostics are author assets. No project code or test expectations
are loaded here; accepted reviews still pass the original complete validators.
"""
import copy
import json
import re
from typing import Annotated, Literal
from pydantic import Field, ValidationError, model_validator
from . import generation_demands as dc
from .generation_schema import Strict
from .generation_direct import Issue

VERSION = 'inventory-correction-v2'
LIMIT = 2


def enabled(job):
    return dc.enabled(job) and job.get('inventory_correction_version') in ('inventory-correction-v1',VERSION)


class ValueRule(dc.Rule):
    op: Literal['eq', 'length_eq', 'count_eq', 'last_eq']


class RelationRule(dc.Rule):
    op: Literal['distinct', 'equal']
    minimum: int = Field(default=2, ge=2, le=200)


class Demand(dc.Demand):
    rules: list[Annotated[ValueRule | RelationRule, Field(discriminator='op')]] = Field(min_length=1, max_length=6)


class SpecReview(dc.SpecReview):
    requirements: list[Demand] = Field(default_factory=list, max_length=12)


class RestoreID(Strict):
    candidate_id: str = Field(pattern='^[a-z][a-z0-9_]{0,40}$')
    accepted_id: str = Field(pattern='^[a-z][a-z0-9_]{0,40}$')


class RenameInvalidID(Strict):
    candidate_id: str = Field(min_length=1,max_length=1000)
    new_id: str = Field(pattern='^[a-z][a-z0-9_]{0,40}$')


class Envelope(Strict):
    binding: dict[str, str]
    decision: Literal['patch', 'revise']
    reason: str = Field(min_length=10, max_length=1800)
    replacements: list[dict] = Field(default_factory=list, max_length=12)
    rename_invalid_ids: list[RenameInvalidID] = Field(default_factory=list, max_length=12)
    restore_ids: list[RestoreID] = Field(default_factory=list, max_length=12)
    issues: list[Issue] = Field(default_factory=list, max_length=8)
    interface_gap: bool = False

    @model_validator(mode='after')
    def coherent(self):
        if self.decision == 'patch':
            if (not self.replacements and not self.rename_invalid_ids) or self.issues or self.interface_gap:
                raise ValueError('patch只替换清单项；规范或接口缺项应revise')
        elif self.replacements or self.restore_ids or self.rename_invalid_ids or not self.issues:
            raise ValueError('revise须说明具体规范字段问题，不夹带清单替换')
        return self


class Correction(Envelope):
    replacements: list[Demand] = Field(default_factory=list, max_length=12)

class SupplyEnvelope(Strict):
    binding: dict[str,str]
    decision: Literal['supply_inventory']
    reason: str = Field(min_length=10,max_length=1800)
    requirements: list[dict] = Field(min_length=1,max_length=12)

class SupplyCorrection(SupplyEnvelope):
    requirements: list[Demand] = Field(min_length=1,max_length=12)


def correction_mode(job):
    raw=candidate(job)
    if job.get('inventory_correction_version')==VERSION and ('requirements' not in raw or raw['requirements']==[]):
        return 'supply_inventory'
    items=raw.get('requirements')
    if not isinstance(items,list):return 'invalid_structure'
    ids=[x.get('id') for x in items if isinstance(x,dict)]
    if len(ids)!=len(items) or any(not isinstance(i,str) or not i for i in ids) or len(set(ids))!=len(ids):return 'invalid_structure'
    return 'patch'


def correction_schema(job):return SupplyCorrection if correction_mode(job)=='supply_inventory' else Correction


POLICY = '''需求证据规则：path始终是字符串列表，数组下标写"0"，*展开数组。
eq要求非空匹配且每个值等于value；length_eq要求非空匹配且每个选中值自身的长度等于value：数组元素数、对象键数或字符串Unicode码点数，不是匹配结果的个数。
例如tickets/*/id选中两个数字，不能用length_eq计数；tickets选中一个数组，length_eq=2检查该数组含两个元素。没有自动的选择结果计数操作符。
distinct要求选中至少minimum个值且全部互异；equal要求至少minimum个值且全部相等，两者minimum至少2且路径需*展开。
count_eq要求等于value的匹配值数量至少为minimum，不是恰好相等；last_eq检查最后一个匹配值。空匹配不构成证据。
每项行为需求必须有expected输出规则；shared_state专指同一scenario内不同实体的关系，须input distinct。单实体重试可用sequence及副作用输出证据，不能强造不同实体。
运行环境约束、排除范围保留在公开constraints/exclusions，不硬编成输出行为规则，也不因它们未列入requirements而填行为遗漏omissions。此阶段未见实现和执行，不宣称已验证代码只用标准库或实际持久化路径；具体文件读写、关闭重开等原始行为仍须测试证据，不能改成环境约束来逃避检查。
接口无法表达原需求时revise并明确缺少的能力，interface_gap=true；只缺公开取值规则则interface_gap=false。不能编造覆盖、修改正确期望或减少原始要求。'''


def fail(reason, **feedback):
    from .generation_fault_first import Invalid
    return Invalid(reason, feedback, category='inventory_correction_invalid')


def inspect_review(job, raw):
    """Collect independent inventory errors; do not guess past broken paths."""
    if not enabled(job) or not isinstance(raw, dict) or raw.get('decision') != 'approve':
        return
    from . import generation_direct as direct
    # Only enter inventory correction after the public review envelope and output
    # rules are valid. It must never turn an unrelated bad review into approval.
    base = {k: v for k, v in raw.items() if k not in ('requirements', 'interface_gap')}
    direct.validate_review({**job, 'demand_coverage_version': None}, base)
    items = raw.get('requirements')
    errors = []
    if not isinstance(items, list) or not 1 <= len(items) <= 12:
        errors.append({'field': 'requirements', 'message': '清单须包含1至12项', 'actual_type':type(items).__name__,'field_present':'requirements' in raw,'unchecked': '无法逐项检查'})
    else:
        ids = [x.get('id') for x in items if isinstance(x, dict)]
        if len(ids) != len(items) or len(set(str(x) for x in ids)) != len(ids):
            errors.append({'field': 'requirements', 'message': '需求ID缺失或重复，不能局部替换'})
        for i, item in enumerate(items):
            prefix = f'requirements.{i}'
            rid = item.get('id') if isinstance(item, dict) else None
            def add(message, field='', **detail):
                errors.append({'field': prefix + ('.' + field if field else ''), 'requirement_id': rid,
                               'message': message, **detail})
            try:
                Demand.model_validate(item)
            except ValidationError as exc:
                for e in exc.errors(include_url=False, include_context=False):
                    add(e['msg'], '.'.join(map(str, e['loc'])), actual_type=type(e.get('input')).__name__)
            if not isinstance(item, dict) or not isinstance(item.get('rules'), list):
                continue
            rules = item['rules']
            # Report independent missing relations even if a different rule has
            # a bad field type. No claim that the remaining semantics passed.
            if not any(isinstance(r, dict) and r.get('source') == 'expected' for r in rules):
                add('缺少expected输出证据；输入或覆盖标签不能证明行为成立', 'rules')
            if item.get('kind') == 'shared_state' and not any(isinstance(r, dict) and r.get('source') == 'input' and r.get('op') == 'distinct' for r in rules):
                add('跨实体关系缺少同一input多个不同实体的distinct条件；请判断规则遗漏还是接口缺项', 'rules')
            for n, rule in enumerate(rules):
                try:
                    parsed = dc.Rule.model_validate(rule)
                except ValidationError:
                    continue
                try:
                    node = dc.schema_node(job['contract']['input_schema' if parsed.source == 'input' else 'output_schema'], parsed.path)
                except ValueError as exc:
                    add(str(exc), f'rules.{n}.path', rule=rule, unchecked='路径无效，未检查该规则的值类型')
                    continue
                if parsed.op == 'length_eq' and (node['type'] not in ('array', 'object', 'string') or type(parsed.value) is not int or parsed.value < 0):
                    add('length_eq检查每个选中值自身长度；选中类型或长度值不适用。检查数组路径或选择符合意图的规则，程序不自动替换',
                        f'rules.{n}', rule=rule, selected_type=node['type'])
            try:
                dc.validate_inventory({**job, 'demand_previous_requirements': []}, [item])
            except ValueError as exc:
                if not isinstance(exc, ValidationError):
                    add(str(exc), detail=getattr(exc, 'feedback', {}))
        from .generation import digest
        if job.get('demand_previous_source_hash') == digest(dc.sources(job)):
            by_id = {x['id']: x for x in items if isinstance(x, dict) and isinstance(x.get('id'), str)}
            for previous in job.get('demand_previous_requirements', []):
                current = by_id.get(previous['id'])
                if current is None or any(current.get(k) != previous[k] for k in ('source', 'quote', 'kind')):
                    errors.append({'field': 'requirements', 'requirement_id': previous['id'],
                                   'message': '须恢复此前已接受的需求ID、原文和类别，被拒候选不是保护基准',
                                   'required': {k: previous[k] for k in ('id', 'source', 'quote', 'kind')}})
    if errors:
        from .generation_fault_first import Invalid
        raise Invalid('需求清单未通过：' + '；'.join(e['field'] + '：' + e['message'] for e in errors[:3]),
                      {'kind': 'inventory_invalid', 'contract_hash': job['contract_hash'], 'errors': errors[:36],
                       'omitted_count': max(0, len(errors) - 36), 'execution_performed': False,
                       'instruction': '依据原候选局部纠正；只报告已确定问题，不代表未列项语义已通过。缺接口或公开规则时revise，不猜期望。'},
                      category='inventory_invalid')


def pending(job):
    return enabled(job) and bool(job.get('inventory_correction'))


def binding(job):
    from . import generation as g
    state = job['inventory_correction']
    if state['contract_hash'] != job['contract_hash'] or state['source_hash'] != g.digest(dc.sources(job)):
        raise fail('清单纠正依据已变化，禁止合并旧候选；须按新规范重新审核')
    return {k: state[k] for k in ('contract_hash', 'source_hash', 'candidate_hash')}


def candidate(job):
    from . import generation as g
    binding(job)
    raw = json.loads((g.root(job) / job['inventory_correction']['asset'] / 'inventory-candidate.json').read_text())
    if g.digest(raw) != job['inventory_correction']['candidate_hash']:
        raise fail('被拒清单资产摘要不匹配，禁止纠正')
    return raw


def context(job):
    from . import generation_budget as budget
    from .generation_safeguards import rejection_context
    result={'operation': ('supply_inventory：原候选整个requirements缺失或为空，只提交完整requirements。程序复制已通过的公开审核和output_rules，禁止改规范、代码或其他字段；逐项引用requirement_sources的原文，并保留previous_requirements已有ID/原文/类别。' if correction_mode(job)=='supply_inventory' else '局部纠正被拒需求清单；patch只返回指定ID的完整替换项，不重写其他清单或output_rules。候选改名导致旧义务ID缺失时，用restore_ids明确candidate_id到accepted_id，并在replacements提供恢复后的完整项；只允许恢复已接受且当前缺失的ID，不猜映射、不新增或删除义务。revise说明具体公开规范缺项。'),
            'correction_mode':correction_mode(job),'binding': binding(job), 'contract': job['contract'], 'requirement_sources': dc.sources(job),
            'rejected_review': candidate(job), 'validation_feedback': job['inventory_correction']['feedback'],
            'previous_requirements': (job.get('demand_previous_requirements', [])
                                      if job.get('demand_previous_source_hash') == job['inventory_correction']['source_hash'] else []),
            'previous_attempts': job.get('inventory_correction_history', []),
            'corrections_remaining': max(0, LIMIT - job.get('inventory_correction_calls', 0)),
            'budget_remaining_seconds': budget.remaining(job), 'execution_performed': False,
            'rule_policy': POLICY}
    from .generation_corrections import enabled as executable
    if executable(job):
        result['operation']+=' 例外：当前未批准且格式非法的ID允许通过rename_invalid_ids一对一改名，其余内容由服务端复制；此操作不使用restore_ids。'
        result['invalid_id_renames']={'instruction':'仅未批准且格式非法的ID可通过rename_invalid_ids(candidate_id,new_id)一对一改名。只改ID时replacements=[]；服务端复制条目其余内容。已批准或合法ID不能改名，不新增/合并/删除义务。',
                                    'eligible_ids':[i['id'] for i in candidate(job).get('requirements',[]) if isinstance(i,dict) and isinstance(i.get('id'),str) and not re.fullmatch(r'[a-z][a-z0-9_]{0,40}',i['id'])]}
    if rejection_context(job):result['adoption_rejection']=rejection_context(job)
    return result


def merge(job, raw):
    from . import generation as g
    if job.get('fault_freeze'):raise fail('冻结后禁止补齐或修改清单，须显式新版本')
    base=candidate(job)
    if not isinstance(raw,dict):raise fail('纠正响应必须是JSON对象',field='$',actual_type=type(raw).__name__)
    if raw.get('decision')=='supply_inventory':
        if correction_mode(job)!='supply_inventory':
            raise fail('完整补齐仅限新协议下缺失或空清单；已有清单必须局部纠正',field='requirements',action='使用当前提供的纠正schema，不能整包替换已有清单')
        supply=SupplyEnvelope.model_validate(raw).model_dump()
        if supply['binding']!=binding(job):raise fail('完整清单补齐绑定过期',expected=binding(job),received=supply['binding'])
        # The existing inspector and complete typed schema run before adoption.
        # Keep raw items until then so it can return all independent field errors.
        return {**base,'requirements':copy.deepcopy(supply['requirements'])}
    if correction_mode(job)=='supply_inventory':
        raise fail('整个清单缺失或为空，没有可替换ID；请使用supply_inventory补齐完整requirements',field='requirements',required_decision='supply_inventory')
    patch = Envelope.model_validate(raw).model_dump()
    if patch['binding'] != binding(job):
        raise fail('纠正响应未绑定当前被拒清单', expected=binding(job), received=patch['binding'])
    if patch['decision'] == 'revise':
        return {**base, 'decision': 'revise', 'reason': patch['reason'], 'requirements': [], 'output_rules': [],
                'issues': patch['issues'], 'questions': [], 'interface_gap': patch['interface_gap']}
    items = base.get('requirements')
    if not isinstance(items,list):raise fail('清单结构不是列表，不能局部替换',field='requirements',actual_type=type(items).__name__,action='先重新规范审核；不能把结构损坏当成空清单整包覆盖')
    replacements = patch['replacements']
    original_sources = dc.sources(job)
    from .generation import digest
    previous = ({x['id']: x for x in job.get('demand_previous_requirements', [])}
                if job.get('demand_previous_source_hash') == digest(original_sources) else {})
    renaming = patch['rename_invalid_ids']
    if renaming:
        from .generation_corrections import enabled as executable
        if not executable(job):raise fail('旧协议不允许未批准ID改名')
        ids_before=[x.get('id') for x in items if isinstance(x,dict)]
        mapping={x['candidate_id']:x['new_id'] for x in renaming}
        if (len(mapping)!=len(renaming) or len(set(mapping.values()))!=len(mapping)
                or any(old not in ids_before or old in previous or re.fullmatch(r'[a-z][a-z0-9_]{0,40}',old)
                       or new in ids_before or new in previous for old,new in mapping.items())):
            raise fail('仅允许当前未批准非法ID一对一改为无冲突合法ID',accepted_ids=list(previous),existing_ids=ids_before)
        items=[{**x,'id':mapping.get(x['id'],x['id'])} for x in items]
    ids = [x.get('id') for x in items if isinstance(x, dict)]
    changed = [x.get('id') for x in replacements]
    if (len(ids) != len(items) or not all(isinstance(x, str) for x in ids + changed)
            or len(set(ids)) != len(ids) or len(set(changed)) != len(changed)):
        raise fail('需求ID必须唯一，不能合并含重复或缺失ID的候选', existing_ids=ids, replacement_ids=changed)
    renames = {x['candidate_id']: x['accepted_id'] for x in patch['restore_ids']}
    if (len(renames) != len(patch['restore_ids']) or len(set(renames.values())) != len(renames)
            or any(old not in ids or old in previous or new not in previous or new in ids or new not in changed
                   for old, new in renames.items())):
        raise fail('ID映射只允许将未接受的候选ID恢复为已接受但当前缺失的ID，须同时提供完整替换项',
                   existing_ids=ids, accepted_ids=list(previous), restore_ids=patch['restore_ids'])
    items = [{**x, 'id': renames.get(x['id'], x['id'])} for x in items]
    ids = [x['id'] for x in items]
    if not set(changed) <= set(ids):
        raise fail('只能替换存在的需求ID；恢复被改名旧义务须显式提供restore_ids，不得新增或删除需求',
                   existing_ids=ids, accepted_ids=list(previous), replacement_ids=changed,
                   action='仅缺失的已接受ID可通过restore_ids恢复，candidate_id与accepted_id由模型明确选择，并返回完整替换项')
    by_id = {x['id']: x for x in items}
    for replacement in replacements:
        old = by_id[replacement['id']]
        protected = previous.get(old['id'], old)
        fields = ('source', 'quote', 'kind') if old['id'] in previous else ('source', 'quote')
        if (isinstance(protected.get('source'), str) and protected['source'] in original_sources
                and isinstance(protected.get('quote'), str) and 4 <= len(protected['quote']) <= 1200
                and protected['quote'] in original_sources[protected['source']]):
            if any(replacement.get(k) != protected.get(k) for k in fields):
                raise fail('局部纠正须保留或恢复有效的原需求引用，优先使用此前已接受义务',
                           requirement_id=old['id'], required={k: protected[k] for k in fields})
        by_id[replacement['id']] = replacement
    result = {**base, 'requirements': [by_id[x['id']] for x in items]}
    if digest(result) == digest(base):
        raise fail('清单候选没有变化，原校验结果仍有效；请提出实际修改或revise说明接口缺项')
    return result


def rejected(job, review, directory, reason):
    """Persist the latest rejected candidate, including partially fixed ones."""
    if not enabled(job):
        return
    starting = reason['category'] == 'inventory_invalid'
    if not starting and not pending(job):
        return
    from . import generation as g
    prior = job.get('inventory_correction')
    if review is not None and starting:
        (directory / 'inventory-candidate.json').write_text(json.dumps(review, ensure_ascii=False))
        job['inventory_correction'] = {'asset': directory.name, 'contract_hash': job['contract_hash'],
            'source_hash': g.digest(dc.sources(job)), 'candidate_hash': g.digest(review), 'feedback': reason.get('validation_feedback', reason)}
    elif prior:
        prior['feedback'] = {'latest_error': reason, 'inventory_errors': prior['feedback']}
    job.setdefault('inventory_correction_history', []).append({'asset': directory.name,
        'candidate_hash': (job.get('inventory_correction') or {}).get('candidate_hash'), 'error': copy.deepcopy(reason)})


def check_allowance(job):
    binding(job)
    if job.get('inventory_correction_version')==VERSION and correction_mode(job)=='invalid_structure':
        from .generation_fault_recovery import Stop
        raise Stop('清单结构损坏或ID缺失/重复，不能进行条目替换；需重新规范审核，具体字段错误已保留','inventory_correction_invalid')
    if job.get('inventory_correction_calls', 0) >= LIMIT:
        from .generation_fault_recovery import Stop
        raise Stop('清单局部纠正已达两次上限；具体未解决项与候选已保存', 'inventory_invalid')


def reserve(job):
    check_allowance(job)
    job['inventory_correction_calls'] = job.get('inventory_correction_calls', 0) + 1


def recover(job):
    from .generation_fault_recovery import Stop
    try:
        binding(job)
    except ValueError as exc:
        raise Stop(str(exc), 'inventory_correction_invalid') from exc
    if job.get('inventory_correction_calls', 0) >= LIMIT:
        raise Stop('清单局部纠正已达两次上限；具体未解决项与候选已保存', 'inventory_invalid')
    return 'spec_review'
