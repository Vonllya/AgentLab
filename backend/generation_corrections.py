"""Opt-in executable corrections; server-owned scope and bounded work items."""
import copy
from . import generation as g

VERSION = 'executable-corrections-v1'

def enabled(job):
    return job.get('executable_corrections_version') == VERSION


def work_key(job, stage):
    """Rejected responses never create work items; only their server-owned basis does."""
    basis = {'stage': stage, 'contract': job.get('contract_hash')}
    if stage == 'standard_review':
        basis['candidate'] = job.get('standard_candidate')
    elif stage == 'project_build':
        basis.update(feedback=job.get('spec_review_feedback'),
                     rejection=job.get('standard_rejection'),
                     design=job.get('design_rebuild'), interface=job.get('interface_rebuild'))
    else:
        basis.update(evaluation=job.get('assets', {}).get('evaluation'),
                     review=job.get('fault_recovery', {}).get('focused_correction'))
    return stage + ':' + g.digest(basis)


def retry(job, stage, reason):
    from .generation_fault_recovery import Stop
    key = work_key(job, stage)
    item = job.setdefault('correction_work_items', {}).setdefault(key, {'retries': 0, 'failures': []})
    attempt = next((a for a in reversed(job['attempts']) if a['stage'] == stage), {})
    path = g.root(job) / attempt.get('asset', '') / 'response.txt'
    signature = g.digest({'response': path.read_text() if path.exists() else None, 'error': reason})
    repeated = any(f['signature'] == signature for f in item['failures'])
    item['failures'].append({'signature': signature, 'error': copy.deepcopy(reason), 'asset': attempt.get('asset')})
    if repeated or item['retries'] >= 2:
        g.put(job)
        if stage == 'fingerprint':
            from .generation_fault_recovery import request_review
            return request_review(job, stage, reason['category'])
        raise Stop('当前修复任务重复无进展或两次纠正已用尽：' + reason['reason'], reason['category'])
    item['retries'] += 1
    g.put(job)
    return stage
