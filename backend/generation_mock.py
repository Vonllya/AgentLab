"""Explicit offline fixture. Never described as demand-driven real model generation."""
def response(stage,payload):
    contract={'title':'MOCK 离线累加器故障样例','scenario':'用于离线验证生成、审核和训练机制的固定样例，不代表真实需求生成。','symptom':'包含负数时结果比正常累加偏大，普通正数输入正常。','capabilities':['MOCK','边界验证'],'minutes':20,'input_schema':{'type':'object','properties':{'values':{'type':'array','items':{'type':'integer'}}},'required':['values'],'additionalProperties':False},'output_schema':{'type':'integer'},'behaviors':{'sum':'返回所有整数的算术和','empty':'空列表返回0'},'constraints':['保留对所有整数的处理能力'],'input_domain':'values为最多20个绝对值小于100的整数；空列表合法','exclusions':['并发','非法输入'],'files':['app.py','calculator.py'],'simulation':'仅离线固定样例，不验证真实模型生成能力'}
    if stage=='design':return {'assessment':'simulation','rationale':'MOCK 模式只能使用明确标记的固定离线样例，不能按需求生成。','questions':[],'contract':contract,'private_fault_requirements':'过滤负数导致累加错误'}
    if stage=='build':
        def files(expr):return {'app.py':'from calculator import total\ndef scenario(data):\n    return total(data["values"])\n','calculator.py':'def total(values):\n    return '+expr+'\n'}
        return {'normal':files('sum(values)'),'faulty':files('sum(v for v in values if v >= 0)'),'reference':files('sum(values)'),'evasions':{'absolute':files('sum(abs(v) for v in values)'),'first':files('values[0] if values else 0')},'fault_explanation':'负数被过滤，导致输出比数学和偏大。','evasion_explanations':{'absolute':'负数取绝对值','first':'只处理第一项'}}
    if stage=='evaluation':return {'cases':[
        {'id':'normal','group':'regression','visibility':'public','covers':['sum'],'input':{'values':[2]},'expected':2},
        {'id':'negative','group':'target','visibility':'public','covers':['sum'],'input':{'values':[-1,3]},'expected':2,'faulty_expected':3},
        {'id':'multiple','group':'target','visibility':'hidden','covers':['sum'],'input':{'values':[2,3]},'expected':5},
        {'id':'empty','group':'regression','visibility':'hidden','covers':['empty'],'input':{'values':[]},'expected':0}], 'evasion_checks':['负数取绝对值不满足算术和','只计算第一项不满足多元素输入']}
    return {'brief':'MOCK 离线固定训练：数字列表累加时，带负数的结果偏大。请比较不同合法输入下的输出，修复并说明验证依据。本样例仅验证平台流程，不代表模型生成能力。','hints':['比较正数与负数输入。','检查每项是否均参与计算。','思考过滤与累加的区别。'],'followups':['空列表是什么结果？','如何覆盖多元素？'],'consistency_notes':'题面和检查均围绕整数算术和，未新增要求；须人工审核。'}


def role_response(stage,payload):
    from . import generation_lean as lean
    if payload.get('lean_version')==lean.VERSION and stage in ('spec_review','coverage_review'):
        return lean.mock_response(stage)
    if stage=='standard_review':
        source,original=next(iter(payload['requirement_sources'].items()))
        result={'decision':'accept','reason':'MOCK固定采纳只验证状态与资产绑定，不代表真实语义审核。',
                'evidence':[{'source':source,'quote':original[:1000]}]}
        if 'runtime_facts' not in payload:result.update(binding=payload['binding'],correction='')
        return result
    if stage=='inventory_review' and payload.get('review_type')=='interface-review-v2':
        return {'binding':payload['binding'],'decisions':[{'requirement_id':p['id'],'action':'keep_rules',
            'reason':'MOCK固定保留仅验证结构化对应，不代表语义已验证。','replacement_rules':[],'allowed_paths':[],
            'rule_assessments':[{'rule_index':i,'judgment':'valid','resolution':'preserve',
                'reason':'MOCK固定规则判断，只用于离线协议回归。','unresolved_reason':''} for i in range(len(p['rules']))],
            'missing_scenarios':[]} for p in payload['requirements']]}
    if stage=='inventory_review' and payload.get('review_type') in ('interface-review-v1','interface-review-v2'):
        return {'binding':payload['binding'],'decisions':[{'requirement_id':p['requirement_id'],'action':'add_tests',
            'reason':'MOCK固定补测试结论仅验证路由，不代表真实接口语义复核。','replacement_rules':[],'allowed_paths':[]} for p in payload['proposals']]}
    if stage=='project_build' and payload.get('authorization'):
        source,quote=next(iter(payload['base_bundle']['contract']['behaviors'].items()))
        return {'binding':payload['binding'],'decision':'return_review','contract_changes':[],'code_changes':[],
            'reason':'MOCK固定退回工单仅验证权限与反馈路径，不代表语义判断。',
            'public_evidence':[{'source':'contract.behaviors.'+source,'quote':quote}]}
    if stage=='inventory_review':
        return {'binding':payload['binding'],'decisions':[{'requirement_id':c['requirement_id'],
            'verdict':'keep_rules','replacement_rules':[],
            'reason':'MOCK固定拒绝用于离线验证反馈通路，不代表独立语义判断或真实模型纠正。'} for c in payload['challenges']]}
    if stage=='coverage_review':
        return {'binding':payload['binding'],'findings':[{'requirement_id':d['id'],'verdict':'covered' if any(x['matches'] for x in payload['data_evidence'][d['id']]) else 'test_gap',
                 'witnesses':[x['case_id'] for x in payload['data_evidence'][d['id']] if x['matches']],
                 'reason':'MOCK仅验证证据引用与状态机，不证明原需求语义被完整覆盖。'} for d in payload['requirements']], 'omissions':[]}
    if payload.get('workflow')=='fault-first-v1':
        project=response('build',{})
        if stage=='project_build':
            design=response('design',{})
            if payload.get('previous_specification'):design['contract']=payload['previous_specification']
            return {**design,'faulty':project['faulty'],'symptom_paths':[[]]}
        if stage=='fingerprint':
            cases=payload['candidate_cases']
            result={'checks':[{'case_id':c['id'],'behavior_id':'sum','reason':'MOCK负数过滤导致总和偏大，只验证工程机制。','assertions':[{'path':[],'equals':sum(x for x in c['input']['values'] if x>=0)}]} for c in cases if sum(x for x in c['input']['values'] if x>=0)!=c['expected']]}
            if payload.get('fault_impact_version'):
                for c in result['checks']:c['paths']=[a['path'] for a in c.pop('assertions')]
                result['case_impacts']=[]
                for c in cases:
                    actual=sum(x for x in c['input']['values'] if x>=0);different=actual!=c['expected']
                    result['case_impacts'].append({'case_id':c['id'],'affected_paths':[[]] if different else [],
                        'fault_assertions':[{'path':[],'equals':actual}] if different else [],
                        'source_refs':[{'source':'private_fault_requirements'}],
                        'reason':'MOCK按固定负数过滤机制核对有限输入，不是模型语义审核证明。'})
            return result
        if stage=='fault_review':
            return {'code_hash':payload['code_hash'],'decision':'need_evidence','requirement_refs':[],'issue':None,
                    'next_change':'MOCK固定模式不能推理实际冲突，保存当前证据并停止，不冒充真实复核成功。'}
        if stage in ('fix_patch','fault_correct'):
            return {'base_hash':payload['base_hash'],'decision':'patch','changes':[{'path':'calculator.py','before':'sum(v for v in values if v >= 0)','after':'sum(values)','reason':'MOCK补丁恢复完整整数累加，验证补丁绑定及执行。'}],'explanation':'MOCK仅测试流程，不代表需求驱动真实生成。'}
        if stage=='evasions':return {k:project[k] for k in ('evasions','evasion_explanations')}
    if stage=='project_build' and payload.get('allowed_paths'):
        from .generation_spec_patch import pointer
        path=payload['allowed_paths'][0];before=pointer(payload['public_specification'],path)
        return {'contract_hash':payload['contract_hash'],'bundle_hash':payload['bundle_hash'],'decision':'patch',
                'changes':[{'path':path,'before':before,'after':before+'（MOCK局部澄清夹具）' if isinstance(before,str) else before,'reason':'MOCK仅验证局部字段补齐，不代表语义审核。'}],
                'conflict_evidence':[],'explanation':'MOCK程序复制冻结资产，补丁只修改获准字段。'}
    if stage=='project_build':
        design=response('design',{});project=response('build',{})
        if payload.get('previous_specification'):design['contract']=payload['previous_specification']
        bundle={**design,'project':project}
        if payload.get('fault_model_policy'):
            bundle['fault_model']={'trigger':{'any_of':[[{'path':['values'],'quantifier':'any','operator':'lt','value':0}]]},'affected_paths':[[]],'preservation':'不含负数的输入维持完整的正确算术和行为。'}
        return bundle
    if stage=='spec_review' and payload.get('correction_mode')=='supply_inventory':
        return {'binding':payload['binding'],'decision':'supply_inventory','reason':'MOCK固定整数样例补齐清单，仅用于验证恢复机制，不代表真实生成能力。',
            'requirements':[{'id':'mock_mechanics','source':'request.requirement','quote':payload['requirement_sources']['request.requirement'][:1200],
                'objective':'MOCK只验证整数样例空列表输出，不能作为自由需求语义证据。','kind':'behavior',
                'rules':[{'source':'expected','path':[],'op':'eq','value':0,'minimum':2}]}]}
    if stage=='spec_review':
        contract=payload.get('specification')
        if not contract and payload.get('proposed_assessment')=='unsupported':
            return {'contract_hash':payload['contract_hash'],'decision':'unsupported','reason':'MOCK 平台范围审查暂不支持该需求，不伪造生成。','issues':[],'questions':[],'output_rules':[]}
        if not contract:
            return {'contract_hash':payload['contract_hash'],'decision':'need_user','reason':'MOCK需求澄清夹具，不证明真实语义判断。','issues':[],'questions':payload['proposed_questions'],'output_rules':[]}
        result={'contract_hash':payload['contract_hash'],'decision':'approve','reason':'MOCK 可验证性审查夹具，整数和规则明确，不证明真实模型审查质量。','issues':[],'questions':[], 'output_rules':[{'path':path,'quote':contract['behaviors']['sum'],'explanation':'MOCK 算术和规则定义该输出，空列表由empty规则定义为0。'} for path in payload['output_paths']]}
        if payload.get('demand_coverage_version'):
            result['requirements']=[{'id':'mock_mechanics','source':'request.requirement','quote':payload['requirement_sources']['request.requirement'][:1200],
                                     'objective':'MOCK只测试原文追踪及固定空列表场景，不代表自由需求语义覆盖。','kind':'behavior',
                                     'rules':[{'source':'expected','path':[],'op':'eq','value':0,'minimum':2}]}]
        return result
    if stage=='fingerprint':
        result={'checks':[{'case_id':'negative','design_quote':payload['private_fault_requirements'],'reason':'MOCK 冻结的过滤负数设计，在负数加正数输入中仅保留正数。','assertions':[{'path':[],'equals':3}]}]}
        if payload.get('frozen_fault_model'):
            result.update(review='agree',review_reason='MOCK规则与固定样例匹配，仅用于工程流程验证。')
            from .generation_fault_model import triggers
            result['impacts']=[{'case_id':c['id'],'triggers':triggers(payload['frozen_fault_model'],c['input']),
                'affected_paths':[[]] if triggers(payload['frozen_fault_model'],c['input']) else [],'rationale':'MOCK固定输入逐项核对冻结的负数过滤规则。'} for c in payload['impact_cases']]
        return result
    if stage=='evaluation' and payload.get('generation_protocol')=='separated-evidence-v2':
        if payload.get('repair_action'):
            action=payload['repair_action']
            cases={c['id']:c for c in payload['previous_evaluation']['cases']}
            changes=[];additions=[]
            if action=='classification':
                changes=[{'case_id':k,'field':'group','before':cases[k]['group'],'after':'target' if cases[k]['group']=='regression' else 'regression','reason':'MOCK 分类修复夹具；不能作为真实模型判断证据。'} for k in payload['target_cases']]
            patch={'evaluation_hash':payload['evaluation_hash'],'action':action,'changes':changes,'additions':additions}
            return {'decision':'patch','reason':'MOCK 受限补丁，仅验证平台协议，不证明语义正确。','patch':patch,'issues':[]} if payload.get('project_flow') else patch
        value=response(stage,payload)
        if payload.get('classification_policy')=='frozen_input_rules' or payload.get('fault_first_version')=='fault-first-v1':
            value['cases'][2].update(input={'values':[2,-3]},expected=-1)
        for c in value['cases']:
            c['faulty_expected']=None
            c['classification_reason']='MOCK 正整数或空列表不触发负数过滤，保留正常行为。' if c['group']=='regression' else 'MOCK 目标行为检查，覆盖负数和多元素的算术和。'
        return value
    if stage=='contract_review':
        answers={k:v for k,v in payload['requirement_sources'].items() if k.startswith('answer.')}
        if answers:
            key=list(answers)[-1];value=answers[key]
            return {'contract_hash':payload['contract_hash'],'decision':'correction','reason':'MOCK 离线局部修订夹具，追加用户已明确的说明。','changes':[{'path':'/scenario','op':'replace','before':payload['contract']['scenario'],'after':payload['contract']['scenario']+'需求说明：'+value,'source':key,'quote':value,'reason':'MOCK 仅将用户说明加入场景，不作为真实语义修复证明。'}],'questions':[]}
        return {'contract_hash':payload['contract_hash'],'decision':'need_user','reason':'MOCK 离线需求澄清流程，不能证明真实语义判断。','changes':[],'questions':[{'id':'intent','text':'请明确本次训练希望验证哪一种正常行为？','options':['保留已描述的正常行为','补充新的行为说明'],'impact':'回答用于局部澄清契约，不修改旧证据。'}]}
    if stage=='contract_check':
        return {'proposal_hash':payload['proposal_hash'],'verdict':'approve','reason':'MOCK 独立校核夹具，只验证工程流程。'}
    if stage=='diagnosis':
        matrix=payload['matrix'];project=payload['project']
        if payload.get('handoff_version')=='repair-handoff-v1':
            behavior=next(iter(payload['contract']['behaviors']))
            return {'action':{'kind':'edit_code','variants':['faulty'],'edits':[{'variant':'faulty','path':'calculator.py','location':'total(values)',
                'current_behavior':'MOCK夹具的故障版实际行为与正常版本等价，尚未触发指定负数过滤。',
                'intended_behavior':'恢复负数过滤的故障注入，使目标输入稳定出现偏大的错误和。',
                'must_preserve':'没有负数的正常输入维持正确算术和，其他版本与独立检查保持不变。'}]},
                'evidence':[{'check_ids':[c['id'] for c in matrix['checks'] if c['version']=='faulty'][:1],
                    'behavior_id':behavior,'quote':payload['contract']['behaviors'][behavior]}],
                'resolutions':[], 'evidence_responses':{}}
        return {'category':'implementation','failure_ids':[matrix['checks'][0]['id']], 'contract_behavior_ids':list(payload['contract']['behaviors']), 'target_variants':['faulty'], 'observed_facts':'MOCK 固定离线诊断，仅验证工程路由。', 'hypothesis':'离线样例故障版本未触发。','change_request':'恢复包含负数过滤故障的离线版本。','evaluation_action':'none','target_cases':[]}
    if stage=='repair_build':
        project=response('build',{})
        result={'variants':{k:project.get(k,project['evasions'].get(k)) for k in payload['target_variants']},'explanations':{k:'MOCK 固定角色修复，不代表真实模型能力。' for k in payload['target_variants']}}
        return {'result':{'kind':'modified',**result}} if payload.get('handoff_version')=='repair-handoff-v1' else result
    return response(stage,payload)
