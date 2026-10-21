"""New-job recovery policy. Corrections never authorize weaker acceptance rules."""
import copy
import time

VERSION = 'stage-recovery-v1'
LIMIT = 6
STAGES = {'coverage_review', 'evaluation', 'spec_review', 'contract_review', 'contract_check', 'inventory_review'}


def correction_context(job, stage):
    from . import generation as g
    item = job.get('stage_correction')
    if not enabled(job) or not item or item['stage'] != stage:
        return None
    if item['binding'].get('contract_hash') != job.get('contract_hash'):
        return None
    if stage == 'coverage_review':
        from . import generation_lean as lean
        from .generation_demands import binding
        if item['binding'] != (lean.binding(job) if lean.enabled(job) else binding(job)):return None
    if stage == 'inventory_review':
        from .generation_inventory_review import binding
        if item['binding'] != binding(job):return None
    result = copy.deepcopy(item)
    path = g.root(job) / item['asset'] / 'response.txt' if item.get('asset') else None
    if path and path.exists():
        result['rejected_response'] = path.read_text()[:80000]
    result['instruction'] = '根据具体错误修正原候选，保留已满足的要求；仅当前阶段可修改资产，不降低验收标准。'
    return result


def enabled(job):
    return job.get('stage_recovery_version') == VERSION


def recover(job, stage, reason):
    from . import generation as g, generation_fault_recovery as recovery
    binding = {'contract_hash': job.get('contract_hash')}
    if stage == 'coverage_review':
        from . import generation_lean as lean
        from .generation_demands import binding as coverage_binding
        binding = lean.binding(job) if lean.enabled(job) else coverage_binding(job)
    if stage == 'inventory_review':
        from .generation_inventory_review import binding as review_binding
        binding = review_binding(job)
    key = stage + ':' + g.digest(binding)
    state = job.setdefault('stage_corrections', {}).setdefault(key, {'calls': 0, 'failures': []})
    attempt = next((a for a in reversed(job.get('attempts', [])) if a['stage'] == stage), {})
    folder = g.root(job) / attempt['asset'] if attempt.get('asset') else None
    response = folder / 'response.txt' if folder else None
    # Same category alone is not stagnation: corrected candidates can expose
    # different field errors. Stop only identical candidate + exact feedback.
    signature = g.digest({'error': reason, 'response': response.read_text() if response and response.exists() else attempt.get('output_hash')})
    repeated = sum(f['signature'] == signature for f in state['failures'])
    state['failures'].append({'signature': signature, 'error': copy.deepcopy(reason),
                              'asset': attempt.get('asset'), 'time': time.time()})
    g.put(job)
    if repeated >= 1:
        raise recovery.Stop('同一候选与同一校验错误连续重复，停止无进展调用：' + reason['reason'], reason['category'])
    if state['calls'] >= LIMIT:
        raise recovery.Stop(f'本阶段已使用{LIMIT}次纠正，保留具体错误：' + reason['reason'], reason['category'])
    state['calls'] += 1
    job['stage_correction'] = {'stage': stage, 'binding': binding, 'asset': attempt.get('asset'),
                               'error': copy.deepcopy(reason), 'attempt': state['calls'], 'limit': LIMIT}
    g.put(job)
    return stage


def begin_clarification(job, review, asset):
    from . import generation as g
    job['clarification_guard'] = {'contract_hash': job['contract_hash'], 'review_asset': asset,
                                  'reason': review['reason'], 'questions': review['questions']}
    job['contract_issue'] = {'proposed_ambiguity': copy.deepcopy(job['clarification_guard']),
                            'instruction': '区分原始需求歧义与生成规范错误。明确需求与规范矛盾应correction；故障表现不能作为正常验收标准。'}
    for k in ('contract_candidate', 'contract_decision', 'contract_review_feedback'):
        job.pop(k, None)
    g.put(job)
    return 'contract_review'


def apply_clarification(job):
    from . import generation as g, contract_revision as contracts
    from .generation_direct import Issue
    from .generation_spec_patch import targets
    proposal = job['contract_candidate']['proposal']
    checked = contracts.validate_check(job, job['contract_decision']['result'])
    guard = job['clarification_guard']
    if guard['contract_hash'] != job['contract_hash']:
        raise ValueError('澄清复核所绑定的规范已改变，禁止应用')
    audit = {'guard': copy.deepcopy(guard), 'proposal': copy.deepcopy(job['contract_candidate']),
             'check': copy.deepcopy(job['contract_decision']), 'time': time.time()}
    job.setdefault('clarification_reviews', []).append(audit)
    if checked['verdict'] == 'revise':
        job['contract_review_feedback'] = checked['reason']; g.put(job)
        return 'contract_review'
    if proposal['decision'] == 'correction':
        issues = []
        for change in proposal['changes']:
            path = change['path']
            if not path.startswith('/behaviors/'):
                path = '/' + path.split('/')[1]
            issues.append(Issue(path=path, reason=change['reason']).model_dump())
        feedback = {'reason': proposal['reason'], 'issues': issues,
                    'reviewed_correction': copy.deepcopy(proposal['changes'])}
        targets(job['contract'], feedback)
        # Builder remains responsible for public spec changes; no hidden direct
        # patch, no implementation/expected output enters this independent check.
        job['spec_review_feedback'] = feedback
        stage = 'project_build'
    elif proposal['decision'] == 'need_user':
        job.update(status='awaiting_requirement', error=None,
                   requirement_question={'id': job['contract_candidate']['asset'],
                                         'contract_hash': job['contract_hash'],
                                         'questions': proposal['questions'], 'reason': proposal['reason']})
        stage = None
    else:
        job['clarification_resolution'] = {'contract_hash': job['contract_hash'], 'reason': proposal['reason']}
        stage = 'spec_review'
    for k in ('clarification_guard', 'contract_issue', 'contract_candidate', 'contract_decision', 'contract_review_feedback'):
        job.pop(k, None)
    job.pop('format_error', None)
    g.put(job)
    return stage
