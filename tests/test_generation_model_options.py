import json

import httpx
import pytest

from backend import agent


@pytest.fixture
def deepseek(monkeypatch):
    monkeypatch.setenv('MODEL_BASE_URL','https://api.deepseek.com/v1')
    monkeypatch.setenv('MODEL_NAME','deepseek-v4-pro')
    monkeypatch.setenv('MODEL_API_KEY','test-secret')
    monkeypatch.setenv('GENERATION_THINKING','enabled')
    monkeypatch.setenv('GENERATION_REASONING_EFFORT','high')
    monkeypatch.setenv('GENERATION_OUTPUT_MULTIPLIER','1')


def test_generation_reasoning_keeps_report_policy(deepseek):
    generation=agent.request_options('generation')
    assert generation['thinking']=={'type':'enabled'}
    assert generation['reasoning_effort']=='high'
    assert generation['max_tokens']==12000
    for retry,cap in [(False,1200),(True,1800)]:
        report=agent.request_options('report',retry)
        assert report['thinking']=={'type':'disabled'}
        assert 'reasoning_effort' not in report and report['max_tokens']==cap


@pytest.mark.parametrize('model',['deepseek-v4-pro','deepseek-flash'])
def test_provider_metadata_and_output_cap(deepseek,monkeypatch,model):
    monkeypatch.setenv('MODEL_NAME',model)
    def post(self,url,**kwargs):
        body=kwargs['json']
        assert body['model']==model and body['max_tokens']==3000
        assert body['thinking']=={'type':'enabled'} and body['reasoning_effort']=='high'
        return httpx.Response(200,request=httpx.Request('POST',url),json={
            'model':'provider-model-id',
            'choices':[{'finish_reason':'stop','message':{'content':'{}','reasoning_content':'PRIVATE_REASONING'}}],
            'usage':{'prompt_tokens':10,'completion_tokens':25,'total_tokens':35,
                     'completion_tokens_details':{'reasoning_tokens':20}}})
    monkeypatch.setattr(httpx.Client,'post',post)
    result=agent.completion([{'role':'user','content':'JSON'}],purpose='generation',output_limit=3000)
    meta=result['_meta']
    assert meta['response_model']=='provider-model-id' and meta['model']==model
    assert meta['reasoning_effort']=='high' and meta['usage']['reasoning_tokens']==20
    # Generation persists content and metadata, never the provider's hidden reasoning.
    assert 'PRIVATE_REASONING' not in json.dumps({'content':result['content'],'metadata':meta})
    assert 'test-secret' not in json.dumps(meta)


def test_default_remains_disabled_and_other_provider_unchanged(deepseek,monkeypatch):
    monkeypatch.delenv('GENERATION_THINKING')
    assert agent.request_options('generation')['thinking']=={'type':'disabled'}
    monkeypatch.setenv('MODEL_BASE_URL','https://other.example/v1')
    options=agent.request_options('generation')
    assert 'thinking' not in options and 'reasoning_effort' not in options


@pytest.mark.parametrize('name,value',[('GENERATION_THINKING','maybe'),('GENERATION_REASONING_EFFORT','unbounded')])
def test_invalid_configuration_rejected(deepseek,monkeypatch,name,value):
    monkeypatch.setenv(name,value)
    with pytest.raises(agent.ModelFailure) as error:agent.request_options('generation')
    assert error.value.category=='configuration'


@pytest.mark.parametrize('value',['0','301','abc'])
def test_generation_timeout_is_bounded(monkeypatch,value):
    monkeypatch.setenv('GENERATION_REQUEST_TIMEOUT',value)
    with pytest.raises(agent.ModelFailure):agent.generation_timeout()


def test_generation_timeout_default_and_override(monkeypatch):
    monkeypatch.delenv('GENERATION_REQUEST_TIMEOUT',raising=False)
    assert agent.generation_timeout()==90
    monkeypatch.setenv('GENERATION_REQUEST_TIMEOUT','300')
    assert agent.generation_timeout()==300


def test_coordinator_clamps_request_to_total_remaining_time(client,deepseek,monkeypatch):
    from backend import generation as g, generation_budget as budget, generation_flow as flow, generation_roles as roles
    from backend.generation_schema import Request
    from backend.generation_mock import role_response
    job=g.create(Request(requirement='仅用于超时预算回归测试'),direct_build=True)
    output=role_response('project_build',roles.context(job,'project_build'))
    job['mode']='real'
    job['budget']=budget.initialize(budget.BudgetPolicy(seconds=10))
    g.put(job)
    monkeypatch.setenv('GENERATION_REQUEST_TIMEOUT','300')
    captured=[]
    def complete(messages,**kwargs):
        captured.append(kwargs)
        return {'content':json.dumps(output),'reasoning_content':'PRIVATE_REASONING',
                '_meta':{'usage':{'completion_tokens':10}}}
    monkeypatch.setattr(agent,'completion',complete)
    flow.call(job,'project_build')
    assert len(captured)==1 and 0<captured[0]['timeout']<=10
    assert captured[0]['output_limit']==12000
    assert all('PRIVATE_REASONING' not in p.read_text() for p in g.root(job).rglob('*') if p.is_file())
    assert 'PRIVATE_REASONING' not in json.dumps(g.get(job['id']))


def test_larger_budget_is_persisted_and_legacy_caps_unchanged(deepseek,monkeypatch):
    from backend import generation_budget as budget
    old=budget.initialize()
    old['policy'].pop('output_multiplier')
    monkeypatch.setenv('GENERATION_OUTPUT_MULTIPLIER','4')
    newer=budget.initialize()
    assert newer['policy']['output_tokens']==384000
    assert budget.reserve({'budget':old},'project_build',1)==12000
    monkeypatch.setenv('GENERATION_OUTPUT_MULTIPLIER','1')
    assert budget.reserve({'budget':newer},'project_build',1)==48000
    assert budget.reserve({'budget':newer},'fingerprint',1)==12000
    assert budget.reserve({'budget':newer,'fault_impact_version':'case-impact-v1'},'fingerprint',1)==24000
    assert agent.request_options('report')['max_tokens']==1200


@pytest.mark.parametrize('value',['0','5','abc'])
def test_multiplier_bounded(monkeypatch,value):
    from backend import generation_budget as budget
    monkeypatch.setenv('GENERATION_OUTPUT_MULTIPLIER',value)
    with pytest.raises(ValueError):budget.output_multiplier()
    with pytest.raises(agent.ModelFailure):agent.request_options('generation')


def test_persisted_large_cap_accepted_even_if_default_changes(deepseek,monkeypatch):
    def post(self,url,**kwargs):
        assert kwargs['json']['max_tokens']==48000
        return httpx.Response(200,request=httpx.Request('POST',url),json={'choices':[{'message':{'content':'{}'}}]})
    monkeypatch.setattr(httpx.Client,'post',post)
    agent.completion([{'role':'user','content':'JSON'}],purpose='generation',output_limit=48000)
    with pytest.raises(ValueError):agent.completion([],purpose='generation',output_limit=48001)
