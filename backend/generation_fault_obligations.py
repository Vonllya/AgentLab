"""Versioned, data-only fault impact review and shared execution obligations.

Independent correctness expectations are never changed here. This module does
not import or execute project code; arrays are atomic assertion values.
"""
import copy
from typing import Literal
from pydantic import Field, model_validator
from .generation_schema import CASE_ID_MAX_LENGTH, Strict
from .generation_protocol import Assertion, matches

VERSION = 'case-impact-v1'


def enabled(job):
    return job.get('fault_impact_version') == VERSION


class Source(Strict):
    source: str = Field(max_length=160)


class Impact(Strict):
    case_id: str = Field(max_length=CASE_ID_MAX_LENGTH)
    affected_paths: list[list[str]] = Field(max_length=12)
    fault_assertions: list[Assertion] = Field(max_length=12,description='预设故障版会返回的错误值，每项必须不同于对应正确expected；不是修复后应有的值')
    source_refs: list[Source] = Field(min_length=1, max_length=4)
    reason: str = Field(min_length=10, max_length=600)


class Issue(Strict):
    kind: Literal['design_indistinguishable', 'public_rule_missing', 'classification_or_impact_error']
    case_ids: list[str] = Field(min_length=1, max_length=10)
    source_refs: list[Source] = Field(min_length=1, max_length=4)
    explanation: str = Field(min_length=15, max_length=1400)


def invalid(reason, **feedback):
    from .generation_fault_first import Invalid
    return Invalid(reason, feedback, category='classification_or_impact_error')


def sources(job):
    from .generation_direct import evidence_sources
    return {'private_fault_requirements': job['private_fault_requirements'],
            **{'contract' + k: v for k, v in evidence_sources(job['contract']).items()}}


def validate_sources(job, refs):
    available = sources(job)
    for ref in refs:
        if ref['source'] not in available:
            raise invalid('影响审核来源引用不存在', received=ref,
                          allowed_sources=available, action='只选择所列source ID；原文由服务端依据冻结摘要定位，不要求模型抄写')


def leaves(value, path=()):
    if isinstance(value, dict) and value:
        for key, item in value.items():
            yield from leaves(item, (*path, key))
    else:
        yield list(path), value


def schema_leaves(schema, path=()):
    """Literal object leaves; an entire array is one assertion value."""
    if schema.get('type')=='object' and schema.get('properties'):
        for key,node in schema['properties'].items():
            yield from schema_leaves(node,(*path,key))
    else:
        yield list(path),schema


def validate_symptoms(schema, paths):
    allowed=[p for p,_ in schema_leaves(schema)]
    if not paths or len(set(map(tuple,paths)))!=len(paths) or any(p not in allowed for p in paths):
        from .generation_fault_first import Invalid
        raise Invalid('症状路径与数组整体规则不兼容；合法路径='+str(allowed),
                      {'received_paths':paths,'allowed_symptom_paths':allowed,
                       'action':'只从合法路径选择主要症状；数组必须整体引用，不进入下标、通配符或非空对象。旧资产须显式新版本修订并重新验证，不自动截短路径。'},
                      category='path_protocol_incompatible')
    return paths


def path_policy(job):
    from . import generation as g
    schema=job['contract']['output_schema']
    symptoms=g.asset(job,'project_build')['symptom_paths']
    validate_symptoms(schema,symptoms)
    allowed=[p for p,_ in schema_leaves(schema)]
    cases=[]
    for c in g.asset(job,'evaluation')['cases']:
        actual=[p for p,_ in leaves(c['expected']) if p in allowed]
        cases.append({'case_id':c['id'],'allowed_impact_paths':actual,
                      'allowed_primary_paths':[p for p in actual if p in symptoms]})
    return {'semantics':'对象逐叶字段；数组是整体叶值，不能进入数组下标或通配符；根数组/标量使用[]。',
            'schema_leaf_paths':allowed,'symptom_paths':symptoms,'cases':cases}


def value_at(value, path):
    # Arrays remain atomic: no index, wildcard, expression or parent exemptions.
    allowed=[p for p,_ in leaves(value)]
    for key in path:
        if not isinstance(value, dict) or key not in value:
            raise invalid('影响字段不存在或试图进入数组', path=path,
                          allowed_paths=allowed,
                          action='只能引用正确结果中实际存在的叶字段；数组作为整体')
        value = value[key]
    if isinstance(value, dict) and value:
        raise invalid('不能豁免整个非空对象', path=path, allowed_paths=allowed,action='明确列出实际发生差异的叶字段')
    return value


def validate_impacts(job, impacts):
    from . import generation as g
    policy=path_policy(job)
    nodes={tuple(p):node for p,node in schema_leaves(job['contract']['output_schema'])}
    cases = {c['id']: c for c in g.asset(job, 'evaluation')['cases']}
    if len(impacts) != len(cases) or {x['case_id'] for x in impacts} != set(cases):
        raise invalid('影响审核必须恰好覆盖全部场景', required_case_ids=sorted(cases),
                      received_case_ids=[x['case_id'] for x in impacts])
    for item in impacts:
        validate_sources(job, item['source_refs'])
        # The private design hash is bound to the whole review by the server.
        # Source IDs add explanatory references, not a second copy of that hash.
        paths = item['affected_paths']; assertions = item['fault_assertions']
        if len({tuple(p) for p in paths}) != len(paths) or sorted(map(tuple, paths)) != sorted(tuple(a['path']) for a in assertions):
            raise invalid('每个影响字段须对应一条具体错误断言，不能重复或仅列豁免', case_id=item['case_id'])
        for a in assertions:
            expected = value_at(cases[item['case_id']]['expected'], a['path'])
            from .generation_schema import conforms
            if a['path'] not in policy['schema_leaf_paths'] or not conforms(a['equals'], nodes[tuple(a['path'])]):
                raise invalid('错误断言不符合公开输出类型',case_id=item['case_id'],path=a['path'])
            if g.digest(expected) == g.digest(a['equals']):
                raise invalid('与正确值相同的字段不是本场景预期差异', case_id=item['case_id'], path=a['path'],
                              action='该字段应保留正确值；控制流可能受影响不等于输出必须错误')
    if not any(not i['affected_paths'] for i in impacts):
        raise invalid('缺少完整保留的正常场景', action='独立评测补充确实不受故障影响的合法场景，不能伪造回归')
    return impacts


def normalize(job, raw):
    """Validate persisted assets as well as model responses; never trust flags alone."""
    from . import generation as g
    import json
    if len(json.dumps(raw,ensure_ascii=False).encode())>48000:
        raise invalid('逐场景影响审核资产超过48KiB',action='缩短说明与重复引用，保持场景覆盖和具体断言；不能扩大资产预算')
    impacts = [Impact.model_validate(x).model_dump() for x in raw.get('case_impacts', [])]
    validate_impacts(job, impacts)
    cases = {c['id']: c for c in g.asset(job, 'evaluation')['cases']}
    by_id = {i['case_id']: i for i in impacts}
    checks = raw['checks']; seen = set(); vis = set()
    paths = g.asset(job, 'project_build')['symptom_paths']
    from .generation_protocol import Fingerprint
    for c in checks:
        Fingerprint.model_validate(c)
        if c['case_id'] not in cases or c['case_id'] in seen:
            raise invalid('主要复现检查引用不存在或重复', case_id=c['case_id'])
        if c['design_quote'] not in job['private_fault_requirements']:
            raise invalid('主要复现依据不属于当前私有设计', case_id=c['case_id'])
        impact = by_id[c['case_id']]
        for a in c['assertions']:
            if a['path'] not in paths or a not in impact['fault_assertions']:
                raise invalid('主要断言必须来自已审核影响，且限于主要症状字段', case_id=c['case_id'], path=a['path'])
        if matches(cases[c['case_id']]['expected'], c):
            raise invalid('主要故障不能命中正确结果', case_id=c['case_id'])
        seen.add(c['case_id']); vis.add(cases[c['case_id']]['visibility'])
    if vis != {'public', 'hidden'}:
        raise invalid('主要故障必须具有公开与隐藏复现', received=sorted(vis))
    value = {'checks': checks, 'case_impacts': impacts, 'impact_version': VERSION}
    if raw.get('binding') != binding(job):
        raise invalid('影响审核所依据的规范、设计或独立期望已变化', required=binding(job), received=raw.get('binding'))
    return {**value, 'binding': raw['binding']}


def binding(job):
    from . import generation as g
    return {'contract_hash': job['contract_hash'], 'evaluation_hash': g.digest(g.asset(job, 'evaluation')),
            'design_hash': g.digest(job['private_fault_requirements'])}


def obligations(job, case, phase='faulty'):
    from . import generation as g
    if phase != 'faulty':
        return [{'id': case['id'] + ':correct', 'objective': 'correct_behavior',
                 'assertions': [{'path': [], 'equals': case['expected']}]}]
    raw = normalize(job, g.asset(job, 'fingerprint'))
    impact = next(i for i in raw['case_impacts'] if i['case_id'] == case['id'])
    affected = impact['affected_paths']
    preserve = [{'path': p, 'equals': copy.deepcopy(v)} for p, v in leaves(case['expected']) if p not in affected]
    result = []
    if impact['fault_assertions']:
        result.append({'id': case['id'] + ':fault', 'objective': 'preserve_specified_fault', 'assertions': impact['fault_assertions']})
    if preserve:
        result.append({'id': case['id'] + ':preserve', 'objective': 'correct_behavior', 'assertions': preserve})
    return result


def annotate(job, case, row):
    """Used by both phase and final matrix; status remains correct-output status."""
    from . import generation as g
    rules = obligations(job, case)
    safe = row.get('status') != 'error' and row.get('stable', row.get('repeat_consistent', False))
    outcomes = [{**r, 'satisfied': bool(safe and matches(row.get('actual'), {'assertions': r['assertions']}))} for r in rules]
    from .generation_fault_model import preserves_unaffected
    impact=next(i for i in g.asset(job,'fingerprint')['case_impacts'] if i['case_id']==case['id'])
    shape_preserved=bool(safe and preserves_unaffected(row.get('actual'),case['expected'],impact['affected_paths']))
    row.update(obligations=outcomes, obligations_passed=all(x['satisfied'] for x in outcomes) and shape_preserved,
               group='target' if any(x['objective'] == 'preserve_specified_fault' for x in rules) else 'regression',
               preservation_passed=shape_preserved and all(x['satisfied'] for x in outcomes if x['objective'] == 'correct_behavior'))
    return row


def gates(job, rows):
    from . import generation as g
    primary = {c['case_id'] for c in g.asset(job, 'fingerprint')['checks']}
    targets = [r for r in rows if r['case'] in primary]
    regressions = [r for r in rows if r['group'] == 'regression']
    return {'fault_trigger': len(targets) == len(primary) and all(r.get('fault_match') and r.get('stable') and r['status'] == 'failed' for r in targets),
            'fault_regression': bool(regressions) and all(r.get('obligations_passed') for r in regressions),
            'fault_preserved_fields': all(r.get('preservation_passed') for r in rows),
            'fault_behavior': all(r.get('obligations_passed') for r in rows)}
