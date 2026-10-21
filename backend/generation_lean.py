"""Lean public review/coverage policy. Old job protocols remain unchanged.

No coverage DSL, generated evaluator, or model assertions execute here. Reviews
are semantic opinions; objective evidence still comes from the Docker runner.
"""
import copy
import time
from typing import Literal
from pydantic import Field, model_validator
from .generation_schema import Strict
from .generation_direct import Issue
from .contract_revision import Question

VERSION = 'lean-generation-v1'


def enabled(job):
    return job.get('lean_version') == VERSION


def restore_validation_feedback(job, stage):
    """An unavailable provider must not erase an uncorrected asset rejection.

    Only contiguous attempts of the same stage/version qualify. A successful
    result or a different stage ends the pending correction episode.
    """
    if not enabled(job):return
    non_asset = {'provider_http', 'connection', 'timeout', 'authentication',
                 'permission', 'configuration', 'rate_limit', 'output_limit'}
    skipped = False
    for attempt in reversed(job.get('attempts', [])):
        if attempt.get('stage') != stage or attempt.get('contract_version') != job['contract_version']:
            return
        error = attempt.get('error') or {}
        if error.get('category') in non_asset:
            skipped = True
            continue
        if skipped and attempt.get('status') == 'failed' and error:
            job['format_error'] = {'stage': stage, 'reason': error['reason'],
                                  'recovered_from_asset': attempt.get('asset')}
            if error.get('validation_feedback'):
                job['format_error']['validation_feedback'] = copy.deepcopy(error['validation_feedback'])
        return


class SpecReview(Strict):
    decision: Literal['approve', 'revise', 'need_user', 'unsupported']
    reason: str = Field(min_length=10, max_length=1400)
    issues: list[Issue] = Field(default_factory=list, max_length=6)
    questions: list[Question] = Field(default_factory=list, max_length=3)

    @model_validator(mode='after')
    def coherent(self):
        if (self.decision == 'revise') != bool(self.issues):
            raise ValueError('revise须给具体规范问题；其他决定不夹带修改')
        if (self.decision == 'need_user') != bool(self.questions):
            raise ValueError('仅用户原始需求有歧义时提出具体问题')
        return self


class Coverage(Strict):
    decision: Literal['approve', 'add_tests', 'specification_issue']
    reason: str = Field(min_length=10, max_length=1600)
    missing_scenarios: list[str] = Field(default_factory=list, max_length=6)
    issues: list[Issue] = Field(default_factory=list, max_length=6)

    @model_validator(mode='after')
    def coherent(self):
        if (self.decision == 'add_tests') != bool(self.missing_scenarios):
            raise ValueError('add_tests须描述具体缺失场景；其他决定不夹带补测')
        if any(not 10 <= len(s) <= 1000 for s in self.missing_scenarios):
            raise ValueError('每个缺失场景须为10至1000字的具体行为说明')
        if (self.decision == 'specification_issue') != bool(self.issues):
            raise ValueError('specification_issue须定位具体公开规范字段')
        return self


SPEC_INSTRUCTION = (
    '简短审核用户原始需求与公开规范，不看实现、私有故障或测试。只阻塞：偏离核心需求、'
    '正常行为与故障症状混淆、无法构造或观察必要场景、导致正确期望无法确定的实质矛盾。'
    '已有schema描述或其他公开字段能说明行为时无需重复补写。不要因文风、可选边界、'
    '未要求的精确诊断文本而退回。approve只需简短依据，不生成需求清单、覆盖DSL、'
    '逐字段引用或哈希。revise定位Issue允许的真实字段，接口问题可指定/input_schema、'
    '/output_schema；仅用户原始目标真有歧义才need_user。故障症状不是正常验收标准。')

COVERAGE_INSTRUCTION = (
    '一次简短覆盖复查：对照用户原始需求、公开规范及当前全部测试的输入和正确expected。'
    '不要生成或审查覆盖DSL，不因标签齐全而批准。重点检查关键正常场景、主要症状场景、'
    '明确要求的中断/多操作/同次调用内多会话关系。固定输入配完整expected可以验证具体'
    '授权、包含和排除行为，不要求通用跨字段断言。实际缺项用add_tests描述具体输入条件'
    '与需观察行为，不指定迎合实现的expected。已有场景足够则approve，理由简短。'
    '只有现有输入不能构造或输出不能观察原始要求时才specification_issue，说明具体'
    '场景和缺少的能力；其余规范矛盾也定位具体字段。不要为增加质量扩展用户范围。'
    '当前pending_gaps是待核实意见，补测后据具体场景判断，错误旧判断可说明理由撤销。'
    '通过仅为模型覆盖意见，不证明项目已执行或全面正确。')


def context(job, stage):
    from . import generation as g
    base = {'lean_version': VERSION, 'requirement': job['request'],
            'requirement_answers': job.get('requirement_answers', []),
            'specification': job['contract'], 'execution_performed': False}
    if stage == 'coverage_review':
        base.update(evaluation=g.asset(job, 'evaluation'),
                    pending_gaps=job.get('lean_pending_gaps', []))
    if job.get('format_error', {}).get('stage') == stage:
        base['validation_feedback'] = job['format_error']
    return base


def messages(job, stage):
    import json
    from . import generation_roles as roles
    schema = SpecReview if stage == 'spec_review' else Coverage
    instruction = SPEC_INSTRUCTION if stage == 'spec_review' else COVERAGE_INSTRUCTION
    return [{'role': 'system', 'content': roles.ROLE_INSTRUCTIONS['evaluator'] + '\n' + instruction +
             '\n仅返回JSON：' + json.dumps(schema.model_json_schema(), ensure_ascii=False)},
            {'role': 'user', 'content': json.dumps(context(job, stage), ensure_ascii=False)}]


def binding(job):
    from . import generation as g
    return {'contract_hash': job['contract_hash'],
            'source_hash': g.digest({'request': job['request'], 'answers': job.get('requirement_answers', [])}),
            'evaluation_hash': g.digest(g.asset(job, 'evaluation'))}


def validate_spec(job, raw):
    from . import generation_spec_patch as sp
    from .generation_fault_first import Invalid
    if 'contract_hash' in raw and raw['contract_hash'] != job['contract_hash']:
        raise Invalid('已保存的规范审核属于旧版本，不能重新绑定到当前规范',
                      {'received': raw['contract_hash'], 'expected': job['contract_hash']},
                      'specification_conflict')
    # Parsed replies stored with server-owned metadata can be revalidated.
    value = SpecReview.model_validate({k: v for k, v in raw.items()
                                      if k not in ('contract_hash', 'output_rules')}).model_dump()
    if value['decision'] == 'revise':
        sp.targets(job['contract'], value)
    return {**value, 'contract_hash': job['contract_hash'], 'output_rules': []}


def validate_coverage(job, raw):
    from . import generation_spec_patch as sp
    value = Coverage.model_validate(raw).model_dump()
    if value['decision'] == 'specification_issue':
        sp.targets(job['contract'], value)
    return value


def reserve_repair(job, kind):
    from .generation_fault_recovery import Stop
    counts = job.setdefault('lean_repairs', {})
    if counts.get(kind, 0) >= 2:
        raise Stop('精简流程该类修改已达两次上限，具体缺项保留：' + kind, 'lean_repair_limit')
    counts[kind] = counts.get(kind, 0) + 1


def request_spec(job, value):
    from . import generation as g
    from .generation_fault_recovery import Stop
    if job.get('fault_freeze'):
        raise Stop('故障基线已冻结；规范修改须创建显式新版本', 'specification_conflict')
    reserve_repair(job, 'specification')
    job['spec_review_feedback'] = {'reason': value['reason'], 'issues': copy.deepcopy(value['issues'])}
    # Interface changes require rebuilding the project, not just changing its schema.
    if any(i['path'] in ('/input_schema', '/output_schema') for i in value['issues']):
        job['design_rebuild'] = {'issue': copy.deepcopy(value), 'previous_contract_hash': job['contract_hash']}
    job['checkpoint'] = 'project_build'
    g.put(job)
    return 'project_build'


def apply_coverage(job):
    from . import generation as g
    from .generation_fault_recovery import Stop
    asset = job['assets']['coverage_review']
    if job.get('lean_applied_review') == asset:
        return job['checkpoint']
    value = validate_coverage(job, g.asset(job, 'coverage_review'))
    basis = binding(job)
    job.setdefault('lean_coverage_reviews', []).append(
        {'asset': asset, 'binding': basis, 'result': copy.deepcopy(value),
         'trust': '模型覆盖复查意见，非执行证明', 'time': time.time()})
    if value['decision'] == 'approve':
        job['lean_coverage_approval'] = {'binding': basis, 'asset': asset}
        job.pop('lean_pending_gaps', None)
        stage = 'fingerprint'
    elif value['decision'] == 'specification_issue':
        stage = request_spec(job, value)
    else:
        if job.get('fault_freeze'):
            raise Stop('已冻结测试不能静默追加，需显式新版本', 'specification_conflict')
        reserve_repair(job, 'tests')
        base = copy.deepcopy(g.asset(job, 'evaluation'))
        if len(base['cases']) >= 10:
            raise Stop('测试容量已满，缺失场景已保留；不得删除旧测试腾位置', 'coverage_incomplete')
        job['lean_pending_gaps'] = value['missing_scenarios']
        job['demand_test_repair'] = {'contract_hash': job['contract_hash'], 'evaluation': base,
                                   'missing_scenarios': value['missing_scenarios']}
        job['evaluation_review_guided'] = True
        stage = 'evaluation'
    job['lean_applied_review'] = asset
    job['checkpoint'] = stage
    g.put(job)
    return stage


def require_approved(job):
    from . import generation as g
    from .generation_fault_first import Invalid
    approval = job.get('lean_coverage_approval', {})
    if approval.get('binding') != binding(job) or approval.get('asset') != job['assets'].get('coverage_review'):
        raise Invalid('当前测试尚未完成覆盖复查或依据已变化', {}, 'coverage_incomplete')
    if validate_coverage(job, g.asset(job, 'coverage_review'))['decision'] != 'approve':
        raise Invalid('覆盖复查仍有具体缺项', {}, 'coverage_incomplete')


def mock_response(stage):
    if stage == 'spec_review':
        return {'decision': 'approve', 'reason': 'MOCK仅验证精简流程，求和接口及正常行为已明确。'}
    return {'decision': 'approve', 'reason': 'MOCK仅验证编排，固定正向、负数及空输入测试供执行器验证。'}
