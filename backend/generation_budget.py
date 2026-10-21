"""Shared conservative accounting for all generation roles (never training chat/report)."""
import os
import time
from pydantic import Field
from .generation_schema import Strict

POLICY='roles-v1'
ROLE_LIMITS={'fault_correct':6000,'fix_patch':6000,'evasions':10000,'project_build':12000,'spec_review':4000,'design':4000,'build':12000,'evaluation':6000,'fingerprint':3000,'diagnosis':3000,'repair_build':12000,'teaching':3000,'contract_review':4000,'contract_check':3000}

def output_multiplier():
    try:value=int(os.getenv('GENERATION_OUTPUT_MULTIPLIER','1'))
    except ValueError:raise ValueError('生成输出倍率必须是1至4整数') from None
    if not 1<=value<=4:raise ValueError('生成输出倍率必须是1至4整数')
    return value

class BudgetPolicy(Strict):
    requests:int=Field(default=24,ge=1,le=64)
    output_tokens:int=Field(default_factory=lambda:96000*output_multiplier(),ge=512,le=1536000)
    output_multiplier:int=Field(default_factory=output_multiplier,ge=1,le=4)
    input_bytes:int=Field(default=600000,ge=1000,le=2400000)
    seconds:int=Field(default=1200,ge=10,le=7200)
    validations:int=Field(default=10,ge=1,le=30)

    @classmethod
    def development(cls):
        """Explicit opt-in for isolated probes; normal defaults unchanged."""
        return cls(requests=64,output_tokens=1536000,input_bytes=2400000,
                   seconds=7200,validations=30,output_multiplier=4)
class Exhausted(ValueError):pass
ROLE_LIMITS['fault_review']=4000
ROLE_LIMITS['coverage_review']=4000
ROLE_LIMITS['inventory_review']=4000
ROLE_LIMITS['standard_review']=4000

def initialize(policy=None):
    return {'policy':(policy or BudgetPolicy()).model_dump(),'requests':0,'output_charged':0,'input_bytes':0,'validations':0,'active_seconds':0,'unknown_usage':0}

def remaining(job):
    b=job['budget'];active=time.time()-b['active_started'] if b.get('active_started') else 0
    return max(0,b['policy']['seconds']-b['active_seconds']-active)

def begin(job):job['budget']['active_started']=time.time()
def end(job):
    b=job['budget'];start=b.pop('active_started',None)
    if start is not None:b['active_seconds']+=max(0,time.time()-start)

def reserve(job,role,input_bytes):
    b=job['budget'];p=b['policy'];left=p['output_tokens']-b['output_charged']
    if b['requests']>=p['requests']:raise Exhausted('本批模型请求次数预算耗尽')
    if left<512:raise Exhausted('本批剩余输出额度不足512 Token，预算耗尽')
    if b['input_bytes']+input_bytes>p['input_bytes']:raise Exhausted(f'本批输入字节预算不足：已用{b["input_bytes"]}，下一次需要{input_bytes}，上限{p["input_bytes"]}')
    if remaining(job)<=0:raise Exhausted('本批活动执行时间预算耗尽')
    limit=6000 if role=='fingerprint' and job.get('fault_impact_version')=='case-impact-v1' else ROLE_LIMITS[role]
    # New spec reviews also emit the bounded demand inventory, unlike legacy reviews.
    if role=='spec_review' and job.get('demand_coverage_version')=='demand-coverage-v1':limit=6000
    # Persist the selected profile. Old batches without this field retain 1x,
    # even after the operator changes environment configuration or restarts.
    from . import generation_recovery_policy as rp
    base=limit*p.get('output_multiplier',1)
    if rp.recovering_output(job,role):base=min(48000,base*2)
    cap=min(base,left)
    b['requests']+=1;b['input_bytes']+=input_bytes;b['output_charged']+=cap
    return cap

def settle(job,attempt,metadata):
    b=job['budget'];usage=(metadata or {}).get('usage') or {};used=usage.get('completion_tokens')
    if type(used) is int and 0<=used<=attempt['reserved_tokens']:
        b['output_charged']-=attempt['reserved_tokens']-used;attempt['charged_tokens']=used
    else:
        b['unknown_usage']+=1;attempt['charged_tokens']=attempt['reserved_tokens'];attempt['usage_unknown']=True

def validation(job):
    b=job['budget']
    if b['validations']>=b['policy']['validations'] or remaining(job)<=0:raise Exhausted('本批实际验证次数或执行时间预算耗尽')
    b['validations']+=1
