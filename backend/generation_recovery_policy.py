"""Versioned recovery routing for new jobs; no execution or acceptance changes."""
import copy
import json
import re

VERSION = 'bounded-recovery-v1'


def enabled(job):
    return job.get('recovery_policy_version') == VERSION


WITNESS_POLICY = ('requirements.rules用于判断某个测试输入及正确expected是否构成覆盖见证，'
    '不是所有合法输入必须满足的通用约束。distinct至少两个实体不排除单实体合法输入；'
    '完整执行样例的记录数量不限制中断样例。仍须判断规则是否足以支撑objective，'
    '数据关系匹配不等于语义覆盖或实现通过。')


def quoted_field(value, quote):
    """An exact excerpt of one decoded string field, never concatenated fields."""
    pending=[value]
    while pending:
        current=pending.pop()
        if isinstance(current,str) and quote in current:return True
        if isinstance(current,dict):pending.extend(current.values())
        elif isinstance(current,list):pending.extend(current)
    return False


def reserve_spec_revision(job, review):
    from . import generation as g
    from .generation_fault_recovery import Stop
    state = job.setdefault('public_spec_recovery', {'count': 0, 'scopes': {}, 'events': []})
    paths = sorted({i['path'] for i in review['issues']})
    key = g.digest(paths)  # Changing prose or contract hashes cannot reset scope limits.
    signature = g.digest({'contract': job['contract_hash'], 'issues': review['issues']})
    event = {'asset': job['assets']['spec_review'], 'paths': paths, 'signature': signature}
    if any(e['asset'] == event['asset'] for e in state['events']):return
    if (any(e['signature'] == signature for e in state['events'])
            or state['scopes'].get(key, 0) >= 2 or state['count'] >= 4):
        g.put(job)
        raise Stop('公开规范修订达到同范围两次或本批四次上限，具体问题与原资产已保留', 'public_rule_missing')
    state['count'] += 1
    state['scopes'][key] = state['scopes'].get(key, 0) + 1
    state['events'].append(event)
    g.put(job)


def rule_issue_targets(job, raw):
    """The server advertises concrete editable paths, never arbitrary model routes."""
    targets = {}
    demands = job['demand_inventory']['requirements']
    questioned = {c['requirement_id'] for c in job['inventory_dispute']['challenges']}
    for i, decision in enumerate(raw['decisions']):
        targets[f'/decisions/{i}/replacement_rules'] = 'rules'
    for i, demand in enumerate(demands):
        if demand['id'] in questioned:
            targets[f'/current_requirements/{i}/objective'] = 'requirement'
    for name in ('input_schema', 'output_schema'):
        targets['/' + name] = 'interface'
    return targets


def issue_owner(job, raw, issues):
    from .generation_safeguards import invalid
    targets = rule_issue_targets(job, raw)
    owners = set()
    for issue in issues:
        path = issue['path']
        # Normalize public candidate notation; no eval/JSONPath execution.
        path = re.sub(r'\[(\d+)\]', r'/\1', path.removeprefix('candidate.'))
        path = '/' + path.lstrip('/').replace('.', '/')
        matches = [owner for prefix, owner in targets.items()
                   if path == prefix or path.startswith(prefix + '/')]
        if not matches:
            raise invalid('拒绝位置不属于当前可纠正资产', received=issue['path'],
                          allowed_issue_paths=targets, action='定位实际规则、目标或接口字段；程序按位置选择返回阶段')
        owners.update(matches)
    return 'interface' if 'interface' in owners else 'requirement' if 'requirement' in owners else 'rules'


def route_inventory_rejection(job, pending, value):
    from . import generation as g, generation_demands as dc
    raw = json.loads((g.root(job)/pending['asset']/'output.json').read_text())
    owner = issue_owner(job, raw, value['issues'])
    job['standard_rejection']['repair_owner'] = owner
    job.pop('standard_approval', None)
    job.pop('demand_approval', None)
    if owner in ('rules', 'interface'):
        # The creator must identify the affected requirement in its proposal;
        # never fabricate interface gaps for every challenged requirement.
        job.pop('applied_inventory_review', None)
        return 'inventory_review'
    job['demand_previous_requirements'] = copy.deepcopy(job['demand_inventory']['requirements'])
    job['demand_previous_source_hash'] = g.digest(dc.sources(job))
    job.pop('inventory_dispute', None)
    job['standard_rejection']['inventory_scope'] = {'owner': 'spec_review',
        'preserve': ['id', 'source', 'quote', 'kind'], 'may_correct': ['objective', 'rules']}
    return 'spec_review'


def output_key(job, stage):
    from . import generation as g
    return stage + ':' + g.digest({'contract': job.get('contract_hash'), 'assets': job.get('assets'),
        'candidate': job.get('standard_candidate'), 'dispute': job.get('inventory_dispute'),
        'interface': job.get('interface_proposal'), 'correction': job.get('inventory_correction')})


def recover_output(job, stage, reason):
    from . import generation as g
    from .generation_fault_recovery import Stop
    key = output_key(job, stage)
    states = job.setdefault('output_recoveries', {})
    state = states.setdefault(key, {'retries': 0, 'failures': []})
    attempt = next(a for a in reversed(job['attempts']) if a['stage'] == stage)
    state['failures'].append({'asset': attempt['asset'], 'metadata': copy.deepcopy(attempt.get('metadata', {})),
                              'error': copy.deepcopy(reason)})
    if state['retries'] >= 1:
        g.put(job)
        raise Stop('当前阶段受控输出恢复后仍耗尽；未形成有效审查结果，不能判断项目错误', 'output_limit')
    state['retries'] += 1
    job['output_recovery'] = {'stage': stage, 'key': key, 'previous_asset': attempt['asset']}
    g.put(job)
    return stage


def recovering_output(job, stage):
    item = job.get('output_recovery')
    return enabled(job) and item and item['stage'] == stage and item['key'] == output_key(job, stage)


def compact_output_messages(job, stage, messages):
    if not recovering_output(job, stage):return messages
    result = copy.deepcopy(messages)
    # Contexts are rebuilt from source assets. Remove only duplicate historical
    # diagnostics, never source requirements, contract, inventory, cases or gaps.
    context = json.loads(result[-1]['content'])
    for key in ('previous_error', 'error', 'previous_attempts'):
        context.pop(key, None)
    context['output_recovery'] = {'previous_asset': job['output_recovery']['previous_asset'],
        'instruction': '上次响应因输出预算耗尽未被采用。只返回当前schema所需完整JSON，理由简短；不复述规范、日志或历史，不省略需求和检查，不改变标准。'}
    result[-1]['content'] = json.dumps(context, ensure_ascii=False)
    return result
