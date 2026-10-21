"""Public-specification amendments. Immutable assets are copied by the server.

Fault-model and case-impact jobs opt in; persisted old protocols remain readable.
"""
import copy
import json
from typing import Any, Literal
from pydantic import Field, model_validator
from .generation_schema import Strict, Contract
from .generation_handoff import NeedsReview
from . import generation_fault_model as fm


def enabled(job):
    from .generation_fault_obligations import enabled as impacts_enabled
    return bool((fm.enabled(job) or impacts_enabled(job)) and job.get('installed_bundle') and job.get('spec_review_feedback') and not job.get('design_rebuild'))


class Change(Strict):
    path: str = Field(max_length=150)
    before: Any
    after: Any
    reason: str = Field(min_length=10,max_length=800)


class ConflictEvidence(Strict):
    path: str = Field(max_length=150)
    quote: str = Field(min_length=4,max_length=1000)


class Amendment(Strict):
    contract_hash: str = Field(pattern='^[a-f0-9]{64}$')
    bundle_hash: str = Field(pattern='^[a-f0-9]{64}$')
    decision: Literal['patch','conflict']
    changes: list[Change] = Field(max_length=8)
    conflict_evidence: list[ConflictEvidence] = Field(max_length=4)
    explanation: str = Field(min_length=10,max_length=1400)
    @model_validator(mode='after')
    def coherent(self):
        if self.decision=='patch' and (not self.changes or self.conflict_evidence):
            raise ValueError('patch必须有字段补丁且不夹带冲突裁决')
        if self.decision=='conflict' and (self.changes or len(self.conflict_evidence)<2):
            raise ValueError('conflict不得修改资产，必须引用至少两个冲突来源')
        return self


class PatchError(ValueError):
    category='spec_patch_invalid'
    def __init__(self,path,reason,expected=None):
        self.feedback={'path':path,'reason':reason,'expected':expected}
        super().__init__(f'规范补丁被拒绝：{path}：{reason}')


def pointer(obj,path):
    if not path.startswith('/'):raise PatchError(path,'必须为绝对JSON指针')
    value=obj
    for part in path[1:].split('/'):
        part=part.replace('~1','/').replace('~0','~')
        if isinstance(value,dict) and part in value:value=value[part]
        elif isinstance(value,list) and part.isdigit() and str(int(part))==part and int(part)<len(value):value=value[int(part)]
        else:raise PatchError(path,'路径不存在')
    return value


def targets(contract, feedback):
    """Compile review issues into executable replacements without widening scope."""
    from .generation_direct import Issue
    issues=[Issue.model_validate(i).model_dump() for i in feedback['issues']]
    schema=Contract.model_json_schema()['properties']
    paths=set();missing=set()
    for issue in issues:
        path=issue['path']
        try:pointer(contract,path)
        except PatchError:
            # Missing behavior rules can be added through their existing map.
            # Never authorize arbitrary ancestors or private asset paths.
            parent=path.rsplit('/',1)[0]
            if parent!='/behaviors' or not isinstance(pointer(contract,parent),dict):
                raise PatchError(path,'审核目标不存在且没有可替换的公开父字段')
            missing.add(path)
            path=parent
        paths.add(path)
    capacity=schema['behaviors']['maxProperties']-len(contract['behaviors'])
    if len(missing)>capacity:
        raise PatchError('/behaviors','新增规则超过契约字段上限；请重新审核授权已有规则字段，不要继续提交新增子路径',
                         {'missing_paths':sorted(missing),'remaining_slots':capacity,
                          'existing_paths':['/behaviors/'+k for k in sorted(contract['behaviors'])]+['/simulation'],
                          'action':'保留原审核问题，明确选择已有行为规则或/simulation补充说明；不得删除既有行为ID或放宽上限'})
    paths=sorted(p for p in paths if not any(p.startswith(a+'/') for a in paths if a!=p))
    return [{'path':p,'before':copy.deepcopy(pointer(contract,p)),
             'value_schema':copy.deepcopy(schema[p.split('/')[1]] if p.count('/')==1
                                          else next(iter(schema['behaviors']['patternProperties'].values()))),
             'issues':[{'requested_path':i['path'],'reason':i['reason']} for i in issues
                       if i['path']==p or i['path'].startswith(p+'/')]}
            for p in paths]


def changed_paths(before, after, path=''):
    if isinstance(before,dict) and isinstance(after,dict):
        for key in sorted(set(before)|set(after)):
            child=path+'/'+key.replace('~','~0').replace('/','~1')
            if key not in before or key not in after:yield child
            else:yield from changed_paths(before[key],after[key],child)
    elif before!=after:
        yield path


def current_bundle(job):
    from . import generation as g
    bundle=g.asset(job,'project_build')
    from .generation_fault_obligations import enabled as impacts_enabled
    if impacts_enabled(job):
        from .generation_fault_first import files
        bundle['faulty']=files(job)
        return bundle
    # A later code repair may be newer than the initial bundle.
    bundle['project']=g.asset(job,'build')
    return bundle


def basis(job):
    from . import generation as g
    bundle=current_bundle(job)
    plan=targets(job['contract'],job['spec_review_feedback'])
    return {'requirement':job['request'],'requirement_answers':job.get('requirement_answers',[]),
            'author_note':job.get('repair_note',''),
            'contract_hash':job['contract_hash'],'bundle_hash':g.digest(bundle),
            'public_specification':job['contract'], 'review_feedback':job['spec_review_feedback'],
            'allowed_paths':[t['path'] for t in plan], 'patch_targets':plan,
            'frozen_assets':{k:bundle[k] for k in ('private_fault_requirements','fault_model','project','faulty','symptom_paths') if k in bundle},
            'instruction':'只返回Amendment，不重新返回Bundle或项目代码。patch只能替换allowed_paths中的完整字段，before逐项复制patch_targets原值，after必须符合value_schema（behaviors每项为字符串）。缺失规则通过已有父字段补充；只能修改issues.requested_path对应内容，必须原样保留其余兄弟字段，不能直接替换不存在的子路径。程序复制所有冻结资产。若要求与冻结设计/代码矛盾，返回conflict并用bundle JSON路径和原文引用双方，不能擅改故障设计或迎合代码改正确行为。'}


def merge(job,raw):
    from . import generation as g
    value=Amendment.model_validate(raw).model_dump()
    bundle=current_bundle(job)
    if value['contract_hash']!=job['contract_hash']:raise PatchError('/contract_hash','公开规范依据已过期',job['contract_hash'])
    if value['bundle_hash']!=g.digest(bundle):raise PatchError('/bundle_hash','冻结资产依据已过期',g.digest(bundle))
    if value['decision']=='conflict':
        if len({e['path'] for e in value['conflict_evidence']})<2:raise PatchError('/conflict_evidence','必须引用两个不同来源')
        for e in value['conflict_evidence']:
            source=pointer(bundle,e['path'])
            if not isinstance(source,str) or e['quote'] not in source:raise PatchError(e['path'],'冲突证据不是该字段的连续原文')
        job['spec_patch_conflict']={'contract_hash':job['contract_hash'],'bundle_hash':value['bundle_hash'],**value}
        g.put(job)
        exc=NeedsReview('规范补齐发现冻结资产矛盾：'+value['explanation']+'；原资产未修改，需核对引用证据。')
        exc.category='specification_conflict';raise exc
    plan=targets(job['contract'],job['spec_review_feedback'])
    result=copy.deepcopy(bundle);allowed={t['path'] for t in plan};seen=set()
    for change in value['changes']:
        path=change['path']
        if path not in allowed or path in seen:raise PatchError(path,'未授权字段或重复修改；按patch_targets替换现有字段',plan)
        seen.add(path);before=pointer(job['contract'],path)
        if g.digest(before)!=g.digest(change['before']):raise PatchError(path,'before不匹配当前字段',before)
        if g.digest(before)==g.digest(change['after']):raise PatchError(path,'补丁没有变化')
        parts=path[1:].split('/');target=result['contract']
        for part in parts[:-1]:target=target[part]
        target[parts[-1]]=copy.deepcopy(change['after'])
    requested=[i['path'] for i in job['spec_review_feedback']['issues']]
    for path in changed_paths(job['contract'],result['contract']):
        if not any(path==p or path.startswith(p+'/') for p in requested):
            raise PatchError(path,'父字段补丁修改了审核事项之外的内容；恢复该字段',
                             {'requested_paths':requested,'patch_targets':plan})
    Contract.model_validate(result['contract'])
    return result


def reserve(job,consume=True):
    """Persistent per-review attempt cap plus cross-review cycle cap per batch."""
    from . import generation as g
    key=g.digest({'contract':job['contract_hash'],'review':job['spec_review_feedback'],'batch':job.get('batch_id')})
    from .generation_corrections import enabled as executable, work_key
    if executable(job):
        key=work_key(job,'project_build')
        counts=job.setdefault('spec_patch_work_items',{})
    else:counts=job.setdefault('spec_patch_attempts',{})
    if counts.get(key,0)>=3 or sum(counts.values())>=6:
        exc=NeedsReview('规范补齐已达到有界尝试上限；原资产保持不变，请核对字段反馈或冻结资产冲突，不再整包重复生成。')
        exc.category='spec_patch_exhausted';raise exc
    if consume:counts[key]=counts.get(key,0)+1;g.put(job)
