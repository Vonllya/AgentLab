"""Typed, persisted recovery for case-impact jobs. No free-text action routing."""
import ast
import copy
import json
import time
from typing import Literal
from pydantic import Field, model_validator
from .generation_schema import Strict
from .generation_fault_obligations import Issue


class Conflict(Strict):
    left_ref: str = Field(max_length=100)
    right_ref: str = Field(max_length=100)
    explanation: str = Field(min_length=15, max_length=1000)


class Review(Strict):
    code_hash: str = Field(pattern='^[a-f0-9]{64}$')
    decision: Literal['code', 'impact', 'specification', 'design', 'need_evidence']
    requirement_refs: list[str] = Field(max_length=20)
    issue: Issue | None = None
    next_change: str = Field(min_length=15, max_length=1600)
    @model_validator(mode='after')
    def coherent(self):
        if (self.decision in ('impact', 'specification', 'design')) != (self.issue is not None):
            raise ValueError('只有影响/规范/设计修订需要结构化issue，代码或缺证据不得夹带修改标准')
        if self.issue and self.issue.kind != {'impact':'classification_or_impact_error', 'specification':'public_rule_missing', 'design':'design_indistinguishable'}[self.decision]:
            raise ValueError('复核动作与问题分类不一致')
        if self.decision == 'code' and not self.requirement_refs:
            raise ValueError('代码纠正必须引用未满足要求，不能泛泛要求重做')
        return self


class TriggerCoverage(Strict):
    source: str = Field(max_length=100)
    quote: str = Field(min_length=4,max_length=1200)
    behavior_ids: list[str] = Field(min_length=1,max_length=10)
    insufficient_cases: list[str] = Field(min_length=1,max_length=10)
    visibility: Literal['public','hidden']
    trigger_condition: str = Field(min_length=15,max_length=1000)


class ExecutableReview(Review):
    decision: Literal['code','impact','specification','design','need_evidence','add_coverage']
    coverage_request: TriggerCoverage | None = None
    @model_validator(mode='after')
    def coverage_action(self):
        if (self.decision=='add_coverage') != (self.coverage_request is not None):
            raise ValueError('仅add_coverage提供结构化coverage_request，不夹带代码或正确期望')
        return self


def review_schema(job):
    from .generation_corrections import enabled
    return ExecutableReview if enabled(job) else Review


class Stop(ValueError):
    def __init__(self, reason, category='candidate_no_progress'):
        self.category = category
        super().__init__(reason)


class Revision(ValueError):
    def __init__(self, issue):
        self.issue = issue
        self.category = issue['kind']
        self.feedback = {'issue': issue, 'action': '按结构化问题返回未冻结设计、规范或影响审核，不自动修改正确期望'}
        super().__init__(issue['explanation'])


def state(job):
    return job.setdefault('fault_recovery', {'design_revisions': 0, 'reviews': {}, 'format_retries': {}, 'candidates': {}, 'corrections': {}, 'events': []})


def scope(job):
    return 'fixed' if job.get('fault_freeze') else 'faulty'


def ast_hash(files):
    from . import generation as g
    return g.digest({p: ast.dump(ast.parse(code), include_attributes=False) for p, code in sorted(files.items())})


def remember_candidate(job, phase, base, result):
    from .generation_fault_first import Invalid
    seen = state(job)['candidates'].setdefault(phase, [])
    old = ast_hash(base); new = ast_hash(result)
    if not seen: seen.append(old)
    if new in seen:
        raise Invalid('候选只有已见代码，未形成新修改（含A→B→A回摆）',
                      {'phase': phase, 'candidate_ast_hash': new, 'action': '进入一次要求复核，不重复提交旧候选'}, category='candidate_no_progress')
    seen.append(new)


def validate_issue(job, issue):
    from . import generation as g
    from .generation_fault_obligations import Issue, validate_sources
    value = Issue.model_validate(issue).model_dump()
    cases = {c['id'] for c in g.asset(job, 'evaluation')['cases']}
    if not set(value['case_ids']) <= cases:
        raise ValueError('修订问题引用了不存在的场景')
    validate_sources(job, value['source_refs'])
    required_source = 'private_fault_requirements' if value['kind'] != 'public_rule_missing' else 'contract'
    if not any(r['source'].startswith(required_source) for r in value['source_refs']):
        raise ValueError('修订缺少对应设计或公开规则的原文依据')
    return value


def available_evidence(job, phase):
    """An invalid review asset is missing evidence, never a passed obligation.

    Keep the precise validation error in the next actual model request and the
    terminal author asset. Reporting a failure must not fail on the same asset.
    """
    from . import generation_fault_first as ff
    if 'fingerprint' not in job['assets']:
        return {'requirements':[], 'matrix_id':None, 'code_hash':None,
                'asset_issue':{'category':'classification_or_impact_error','reason':'缺少已审核的逐场景影响资产，不能编译代码修改要求'}}
    try:return ff.repair_requirements(job,phase)
    except (ValueError,KeyError,TypeError) as exc:
        return {'requirements':[],'matrix_id':(job.get('matrix') or {}).get('id'),'code_hash':None,
                'asset_issue':{'category':getattr(exc,'category','classification_or_impact_error'),
                               'reason':str(exc),'validation_feedback':getattr(exc,'feedback',None)}}


def review_context(job):
    from . import generation as g, generation_fault_first as ff, generation_fault_obligations as obligations
    phase = scope(job)
    code = g.asset(job, 'fixed_candidate')['files'] if phase == 'fixed' and 'fixed_candidate' in job['assets'] else ff.files(job)
    evidence=available_evidence(job,phase)
    allowed=['need_evidence']
    if any(r['state']=='unmet' and r.get('execution_observed') for r in evidence['requirements']):allowed.append('code')
    if not job.get('fault_freeze'):allowed.extend(['impact','specification','design'])
    from .generation_corrections import enabled as executable
    if executable(job) and not job.get('fault_freeze') and len(g.asset(job,'evaluation')['cases'])<10:
        allowed.append('add_coverage')
    result = {'workflow': ff.VERSION, 'requirement': job['request'], 'requirement_answers': job.get('requirement_answers', []),
            'contract': job['contract'], 'private_fault_requirements': job['private_fault_requirements'],
            'phase': phase, 'code_hash': g.digest(code), 'files': code,
            'execution_evidence':evidence,'allowed_decisions':allowed,
            'failure': job.get('format_error'), 'returned_conflict': job.get('returned_conflict'),
            'evaluation': g.asset(job, 'evaluation'), 'fault_review': g.asset(job, 'fingerprint') if 'fingerprint' in job['assets'] else None,
            'sources': obligations.sources(job),'path_policy':obligations.path_policy(job)}
    if executable(job):
        from .generation_demands import sources
        result['coverage_sources']={**sources(job),'contract':json.dumps(job['contract'],ensure_ascii=False)}
        result['coverage_slots_remaining']=max(0,10-len(result['evaluation']['cases']))
        result['prior_review']=copy.deepcopy(state(job)['reviews'].get(phase,{}).get('result'))
        result['prior_review_policy']='此前复核结论是待核对的提案，不是执行事实；结合当前校验错误重新选择动作，不遗漏其中具体补测条件。'
    return result


def validate_review(job, value):
    ctx = review_context(job)
    from .generation_fault_first import Invalid
    if value['decision'] not in ctx['allowed_decisions']:
        raise Invalid('当前证据不允许此复核动作',{'received':value['decision'],'allowed_decisions':ctx['allowed_decisions'],
                      'requirements':ctx['execution_evidence']['requirements'],'action':'没有已审核的代码要求时不能要求改代码；先解决影响/规范/设计或明确缺证据'},category='patch_invalid')
    if value['code_hash'] != ctx['code_hash']: raise ValueError('复核代码摘要过期')
    requirements = {r['requirement_id']: r for r in ctx['execution_evidence']['requirements']}
    allowed_refs=set(requirements) if value['decision']=='code' else set(requirements)|set(ctx['sources'])
    if not set(value['requirement_refs']) <= allowed_refs:
        raise Invalid('复核引用了不存在的要求',{'received':value['requirement_refs'],'available_requirements':requirements,
                      'action':'requirement_refs只选这里的requirement_id，不能填behavior_id或case_id'},category='patch_invalid')
    if value['decision'] == 'code' and not any(requirements[k]['state'] == 'unmet' for k in value['requirement_refs']):
        # No execution can justify a claim that already satisfied behavior is broken.
        raise ValueError('代码复核必须针对至少一个未满足要求，不能修掉已保留故障')
    if value['issue']:validate_issue(job, value['issue'])
    if value['decision']=='add_coverage':
        request=TriggerCoverage.model_validate(value['coverage_request']).model_dump()
        from .generation_demands import sources
        public={**sources(job),'contract':json.dumps(job['contract'],ensure_ascii=False)}
        cases={c['id'] for c in ctx['evaluation']['cases']}
        if (request['quote'] not in public.get(request['source'],'')
                or not set(request['behavior_ids']) <= set(job['contract']['behaviors'])
                or not set(request['insufficient_cases']) <= cases):
            raise Invalid('补测依据不合法：核对source/quote、behavior_ids和insufficient_cases',
                {'received':request,'public_sources':public,'available_behavior_ids':sorted(job['contract']['behaviors']),
                 'available_case_ids':sorted(cases),'action':'从public_sources复制连续原文，行为和场景只选提供的ID；不提供实现或实际错误输出'},category='patch_invalid')
    return value


def request_review(job, origin, category):
    from . import generation as g
    phase = scope(job); st = state(job)
    if st['reviews'].get(phase):
        from . import generation_fault_first as ff
        evidence=available_evidence(job,phase)
        unmet=[r for r in evidence['requirements'] if r['state']=='unmet']
        failed=[k for k,v in (job.get('matrix') or {}).get('gates',{}).items() if not v]
        details={'phase':phase,'origin':origin,'cause':category,'matrix_id':(job.get('matrix') or {}).get('id'),
                 'failed_gates':failed,'unmet_requirements':unmet,'asset_issue':evidence.get('asset_issue'),'review':st['reviews'][phase],
                 'action':'先对照required_assertions与actual核对目标方向和影响审核；不能以正常正确输出替换preserve_specified_fault，也不能按失败结果修改正确期望。新修改须明确授权新批次。'}
        st['last_stop']=details
        ff.save_asset(job,'recovery_stop',details)
        refs=', '.join(r['requirement_id'] for r in unmet[:6]) or '尚无可执行要求（审核资产未成立）'
        raise Stop('复核后候选仍失败；门禁：'+(', '.join(failed) or '资产校验')+'；未满足：'+refs+
                   '。具体输入、字段目标、实际值和复核指令见作者资产 recovery_stop；本批停止重复。', 'candidate_no_progress')
    st['reviews'][phase] = {'origin': origin, 'category': category, 'started': time.time()}
    st['events'].append({'action':'review', 'phase':phase, 'origin':origin, 'category':category})
    g.put(job)
    return 'fault_review'


def apply_revision(job, issue):
    from . import generation as g
    from .generation_fault_obligations import sources
    if job.get('fault_freeze'):
        raise Stop('故障基线已冻结，修订设计/规范/影响必须创建新版本，旧资产不改写', issue['kind'])
    issue = validate_issue(job, issue); st = state(job)
    # One revision episode per kind, persisted across restart. A new explicitly
    # authorized budget batch may reset counters; parent evidence is immutable.
    revisions = st.setdefault('revision_counts', {})
    kind = issue['kind']
    if revisions.get(kind, 0) >= 1:
        raise Stop('同类修订已执行一次仍不成立；保留新旧证据等待复核', kind)
    revisions[kind] = revisions.get(kind, 0) + 1
    st['events'].append({'action':'revision', 'issue':issue, 'contract_hash':job['contract_hash']})
    job['revision_issue'] = issue
    if kind == 'classification_or_impact_error':
        # Same correct expectations, independently re-review effects, not output.
        for key in ('fingerprint', 'fault_acceptance', 'fixed_candidate', 'fix_patch', 'fix_acceptance', 'build', 'teaching'):
            archive(job, key)
        for key in ('fix_acceptance', 'review_digest', 'phase_feedback'):
            job.pop(key, None)
        if job.get('matrix'):job.setdefault('validation_history', []).append(copy.deepcopy(job['matrix']))
        job['matrix'] = None; job.pop('format_error', None); g.put(job)
        return 'fingerprint'
    if kind == 'public_rule_missing':
        # The independent public reviewer gets only public sources, not actual
        # output, code, private fault text, hidden case IDs or proposed expected.
        job['public_rule_issue'] = {'source_refs':[{'source':r['source'],'quote':sources(job)[r['source']]} for r in issue['source_refs'] if r['source'].startswith('contract')]}
        g.put(job); return 'spec_review'
    st['design_revisions'] += 1
    job['design_rebuild'] = {'issue': issue, 'previous_contract_hash': job['contract_hash']}
    job.pop('format_correction', None); job.pop('format_error', None)
    g.put(job); return 'project_build'


def archive(job, key):
    from . import generation as g
    if key in job['assets']:
        job.setdefault('asset_revisions', []).append({'stage': key, 'asset': job['assets'][key], 'hash':g.digest(g.asset(job, key)), 'replaced_at':time.time()})
        del job['assets'][key]


def apply_review(job, value):
    from . import generation as g
    phase = scope(job)
    state(job)['reviews'].setdefault(phase, {'origin':'explicit_checkpoint', 'started':time.time()})['result'] = copy.deepcopy(value)
    job.pop('returned_conflict', None)
    if value['decision']=='add_coverage':
        validate_review(job,value)
        count=job.get('trigger_coverage_repairs',0)
        if count>=2:raise Stop('故障触发补测已达两次上限，保留证据缺口','insufficient_evidence')
        job['trigger_coverage_repairs']=count+1
        base=copy.deepcopy(g.asset(job,'evaluation'))
        request=copy.deepcopy(value['coverage_request'])
        job.setdefault('trigger_supplement_history',[]).append({'review_asset':job['assets'].get('fault_review'),
            'evaluation_hash':g.digest(base),'request':request,'review':copy.deepcopy(value),'time':time.time()})
        job['demand_test_repair']={'contract_hash':job['contract_hash'],'evaluation':base,'findings':[],
            'trigger_coverage_request':request,
            'provenance':'code_aware_review_guided_supplement：仅传公开依据和触发条件，无源码、实际输出或建议expected；不是初次盲测。'}
        job['evaluation_review_guided']=True
        job.pop('demand_approval',None)
        archive(job,'fingerprint');archive(job,'fault_review')
        state(job)['reviews'].pop(phase,None)
        job.pop('format_error',None);job['checkpoint']='evaluation'
        g.put(job);return 'evaluation'
    if value['decision'] == 'need_evidence':
        raise Stop('复核仍缺少可执行依据：' + value['next_change'], 'insufficient_evidence')
    if value['decision'] == 'code':
        job['focused_correction'] = {'phase':phase, 'requirement_refs':value['requirement_refs'], 'next_change':value['next_change']}
        g.put(job); return 'fix_patch' if phase == 'fixed' else 'fault_correct'
    return apply_revision(job, value['issue'])


def handle(job, stage, exc, reason):
    from . import generation as g, generation_fault_first as ff
    from . import generation_inventory_repair as inventory
    from . import generation_interface_review as interface
    from . import generation_recovery_policy as rp
    if rp.enabled(job) and reason['category']=='output_limit':return rp.recover_output(job,stage,reason)
    if stage=='project_build' and interface.building(job) and reason['category']=='interface_return':return interface.returned(job,exc)
    if stage=='spec_review' and inventory.pending(job):return inventory.recover(job)
    if isinstance(exc, Revision):return apply_revision(job, exc.issue)
    st = state(job); category = reason['category']
    if category=='path_protocol_incompatible' and stage!='project_build':
        raise Stop('已有症状路径不符合当前审核协议，须显式新版本修订；不重复付费审核：'+reason['reason'],category)
    from . import generation_stage_recovery as stages
    from . import generation_lean as lean
    if lean.enabled(job) and stage in ('spec_review','coverage_review','evaluation'):
        from .generation_corrections import retry
        return retry(job,stage,reason)
    if stages.enabled(job) and stage in stages.STAGES:
        return stages.recover(job, stage, reason)
    if stage=='fault_review':
        from .generation_corrections import enabled as executable, retry
        if executable(job):return retry(job,stage,reason)
        # One logical review may repair its own wire format once. This does not
        # authorize a second semantic review or another series of code attempts.
        key='fault_review:'+scope(job)
        if st['format_retries'].get(key,0)>=1:raise Stop('受控复核格式已纠正一次仍不合法：'+reason['reason'],category)
        st['format_retries'][key]=1;g.put(job);return stage
    if stage in ('fault_correct', 'fix_patch'):
        # One format/location repair; a no-op or explicit conflict goes directly
        # to review. After review only one candidate call is permitted.
        if category in ('candidate_no_progress', 'requirements_conflict') or st['reviews'].get(scope(job)):
            return request_review(job, stage, category)
    if stage in ('fault_validation', 'fix_validation'):
        phase = 'faulty' if stage == 'fault_validation' else 'fixed'
        if category=='candidate_no_progress' or st['reviews'].get(phase) or st['corrections'].get(phase, 0) >= 2:
            return request_review(job, stage, category)
        return 'fault_correct' if phase == 'faulty' else 'fix_patch'
    from .generation_corrections import enabled as executable, retry
    if executable(job):return retry(job,stage,reason)
    key = stage + ':' + str(job.get('contract_hash'))
    retries = st['format_retries'].get(key, 0)
    if retries >= 1:
        if stage in ('fault_correct', 'fix_patch', 'fingerprint'):
            return request_review(job, stage, category)
        raise Stop('同阶段已纠正一次仍未通过：' + reason['reason'], category)
    st['format_retries'][key] = retries + 1; g.put(job)
    return stage
