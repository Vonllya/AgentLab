import time
from backend import storage as s, workspace as w, agent


def test_generation_delete_is_soft_idempotent_and_blocks_active(client):
    created=client.post('/api/generation/jobs',json={'requirement':'删除接口专用生成记录测试'}).json()
    published=s.get('generation',created['id']);published.update(status='published',published_task='retry');s.put('generation',created['id'],published)
    assert client.delete('/api/generation/jobs/'+created['id']).json()['effect']=='record_hidden_assets_retained'
    assert client.delete('/api/generation/jobs/'+created['id']).json()['deleted'] is True
    assert all(x['id']!=created['id'] for x in client.get('/api/generation/jobs').json())
    assert client.get('/api/generation/jobs/'+created['id']).status_code==410
    assert any(task['id']=='retry' for task in client.get('/api/tasks').json())
    active=client.post('/api/generation/jobs',json={'requirement':'运行状态不能删除的专用记录'}).json()
    raw=s.get('generation',active['id']);raw['status']='building';s.put('generation',active['id'],raw)
    denied=client.delete('/api/generation/jobs/'+active['id'])
    assert denied.status_code==400 and '仍在运行' in denied.json()['detail']


def test_task_removal_persists_and_keeps_existing_training(client):
    session=w.create('retry')
    assert client.delete('/api/tasks/retry').json()['effect']=='removed_from_library'
    assert client.delete('/api/tasks/retry').json()['deleted'] is True
    assert all(x['id']!='retry' for x in client.get('/api/tasks').json())
    assert client.post('/api/sessions',json={'task_id':'retry'}).status_code==400
    assert client.get('/api/sessions/'+session['id']).status_code==200
    s.init()
    assert all(x['id']!='retry' for x in client.get('/api/tasks').json())


def test_session_delete_hides_children_and_preserves_other_session(client):
    one=w.create('rag');other=w.create('rag')
    event=s.event(one['id'],'user','专用删除测试消息')
    run={'id':s.ident(),'session':one['id'],'status':'passed','snapshot':'0'*64}
    s.put('run',run['id'],run,one['id'])
    report={'id':s.ident(),'session':one['id'],'feedback_status':'generated'}
    s.put('report',report['id'],report,one['id'])
    assert client.delete('/api/sessions/'+one['id']).json()['effect']=='removed_with_training_records'
    assert client.delete('/api/sessions/'+one['id']).json()['deleted'] is True
    assert client.get('/api/sessions/'+one['id']).status_code==410
    assert client.get('/api/runs/'+run['id']).status_code==410
    assert client.get('/api/reports/'+report['id']).status_code==410
    assert all(x['id']!=one['id'] for x in client.get('/api/sessions').json())
    assert client.get('/api/sessions/'+other['id']).status_code==200
    assert s.get('event',event['id'])['content']=='专用删除测试消息'


def test_session_delete_rejects_background_activity(client):
    session=w.create('rag')
    run={'id':s.ident(),'session':session['id'],'status':'running'}
    s.put('run',run['id'],run,session['id'])
    assert '评测运行' in client.delete('/api/sessions/'+session['id']).json()['detail']
    run['status']='passed';s.put('run',run['id'],run,session['id'])
    report={'id':s.ident(),'session':session['id'],'feedback_status':'generating'}
    s.put('report',report['id'],report,session['id'])
    assert '反馈仍在生成' in client.delete('/api/sessions/'+session['id']).json()['detail']
    report['feedback_status']='failed';s.put('report',report['id'],report,session['id'])
    agent.ACTIVE.add(session['id'])
    try:assert 'Agent 正在处理' in client.delete('/api/sessions/'+session['id']).json()['detail']
    finally:agent.ACTIVE.discard(session['id'])
