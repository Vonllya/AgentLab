import {test,expect} from '@playwright/test';

test('根路径进入生成创建页且深链接保持',async({page,request})=>{
 await page.addInitScript(()=>localStorage.setItem('session','f'.repeat(32)));
 await page.goto('/');
 await expect(page).toHaveURL('/generate');
 await expect(page.getByLabel('训练需求')).toBeVisible();
 const session=await (await request.post('/api/sessions',{data:{task_id:'rag'}})).json();
 await page.goto('/training/'+session.id);
 await expect(page).toHaveURL('/training/'+session.id);
 await page.reload();await expect(page.getByRole('tab',{name:/任务说明\.md/})).toBeVisible();
});

test('生成记录删除支持取消、成功、重复请求与直接链接',async({page,request})=>{
 const requirement='浏览器删除专用生成记录 '+Date.now();
 const job=await (await request.post('/api/generation/jobs',{data:{requirement}})).json();
 await page.goto('/generate');
 await expect(page.locator('.managed-records')).not.toHaveAttribute('open');
 await page.locator('.managed-records > summary').click();
 const row=page.locator('.record-row').filter({hasText:requirement});
 page.once('dialog',dialog=>dialog.dismiss());await row.getByRole('button',{name:/删除 /}).click();
 await expect(row).toBeVisible();
 page.once('dialog',dialog=>{expect(dialog.message()).toContain('已发布题目');return dialog.accept()});
 await row.getByRole('button',{name:/删除 /}).click();await expect(row).toHaveCount(0);
 expect((await request.delete('/api/generation/jobs/'+job.id)).ok()).toBe(true);
 await page.goto('/generate/'+job.id);await expect(page.getByRole('heading',{name:'记录已删除或不可用'})).toBeVisible();
});

test('训练记录删除清理浏览器状态且不影响其他会话',async({page,request})=>{
 const one=await (await request.post('/api/sessions',{data:{task_id:'rag'}})).json();
 const other=await (await request.post('/api/sessions',{data:{task_id:'rag'}})).json();
 await page.goto('/library');
 await page.locator('.training-history > summary').click();
 await page.evaluate(id=>{localStorage.setItem('session',id);localStorage.setItem('draft:'+id,'{}');localStorage.setItem('workspace:tabs:'+id,'{}')},one.id);
 const row=page.locator('.training-record').filter({hasText:one.id.slice(0,12)});
 page.once('dialog',dialog=>{expect(dialog.message()).toContain('共享快照不会删除');return dialog.accept()});
 await row.getByRole('button',{name:/删除 /}).click();await expect(row).toHaveCount(0);
 expect(await page.evaluate(id=>[localStorage.getItem('session'),localStorage.getItem('draft:'+id),localStorage.getItem('workspace:tabs:'+id)],one.id)).toEqual([null,null,null]);
 expect((await request.get('/api/sessions/'+other.id)).ok()).toBe(true);
 await page.goto('/training/'+one.id);await expect(page.getByRole('heading',{name:'记录已删除或不可用'})).toBeVisible();
});

test('移除题目刷新后不再出现且保留已有训练',async({page,request})=>{
 const existing=await (await request.post('/api/sessions',{data:{task_id:'resume'}})).json();
 await page.goto('/library');
 const row=page.locator('.cards article').filter({hasText:'Agent 中断恢复后重复执行步骤'}).first();
 page.once('dialog',dialog=>{expect(dialog.message()).toContain('不会删除已有训练记录');return dialog.accept()});
 await row.getByRole('button',{name:'删除',exact:true}).click();await page.reload();
 await expect(page.locator('.cards')).not.toContainText('Agent 中断恢复后重复执行步骤');
 expect((await request.get('/api/sessions/'+existing.id)).ok()).toBe(true);
 await page.goto('/library/resume');await expect(page.getByRole('heading',{name:'记录已删除或不可用'})).toBeVisible();
});

test('多文件标签打开切换关闭并按会话刷新恢复',async({page,request})=>{
 const session=await (await request.post('/api/sessions',{data:{task_id:'rag_versioning'}})).json();
 await page.goto('/training/'+session.id);
 await page.getByRole('button',{name:'打开文件 pipeline.py'}).click();
 await page.getByRole('button',{name:'打开文件 retrieval.py'}).click();
 await expect(page.getByRole('tab',{name:/retrieval.py/})).toHaveAttribute('aria-selected','true');
 let editor=page.locator('.monaco-editor textarea').first();await editor.focus();await page.keyboard.press('ControlOrMeta+End');await page.keyboard.insertText('\n# undo-across-tabs');
 await page.getByRole('button',{name:'切换到 pipeline.py'}).click();await page.getByRole('button',{name:'切换到 retrieval.py'}).click();
 await expect(page.locator('.editor .panelhead')).toContainText('retrieval.py');editor=page.locator('.monaco-editor textarea').first();
 await editor.focus();await page.keyboard.press('ControlOrMeta+Z');await expect(page.locator('.view-lines')).not.toContainText('undo-across-tabs');
 await page.keyboard.press('ControlOrMeta+End');await page.keyboard.insertText('\n# closed-tab-save-all');
 await expect(page.locator('.view-lines')).toContainText('closed-tab-save-all');
 await page.getByRole('button',{name:'关闭标签 retrieval.py'}).click();
 await expect(page.getByRole('tab',{name:/pipeline.py/})).toHaveAttribute('aria-selected','true');
 await page.getByRole('button',{name:'保存 Ctrl S',exact:true}).click();
 await expect(page.locator('.notice')).toContainText('代码与诊断已保存');
 expect((await (await request.get('/api/sessions/'+session.id)).json()).files['retrieval.py']).toContain('# closed-tab-save-all');
 await page.reload();
 await expect(page.getByRole('tab',{name:/pipeline.py/})).toBeVisible();
 await expect(page.getByRole('tab',{name:/retrieval.py/})).toHaveCount(0);
 const closes=page.getByRole('button',{name:/关闭标签/});while(await closes.count())await closes.first().click();
 await expect(page.getByRole('heading',{name:'没有打开的文件'})).toBeVisible();
 await page.getByRole('button',{name:'打开文件 retrieval.py'}).click();
 await expect(page.getByRole('tab',{name:/retrieval.py/})).toBeVisible();
 await page.screenshot({path:'../data/usability-verification/workspace-tabs.png',fullPage:true});
});
