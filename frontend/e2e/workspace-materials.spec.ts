import {test,expect} from '@playwright/test';

test('统一任务文档、公开测试标签与全部保存及刷新恢复',async({page,request},info)=>{
 const session=await (await request.post('/api/sessions',{data:{task_id:'rag'}})).json();
 await page.goto('/training/'+session.id);
 await expect(page.getByRole('tab')).toHaveCount(1);
 await expect(page.getByLabel('任务文档',{exact:true})).toBeVisible();
 await expect(page.locator('.brand')).not.toHaveRole('button');
 const beforeBrand=page.url();await page.locator('.brand').click();await expect(page).toHaveURL(beforeBrand);
 await page.screenshot({path:info.outputPath('task-document.png'),fullPage:true});
 for(const name of ['任务说明','环境与约束','诊断说明'])await expect(page.getByRole('heading',{name,exact:true})).toBeVisible();
 await expect(page.getByRole('button',{name:'打开文件 环境与约束.md',exact:true})).toHaveCount(0);
 await page.getByLabel('诊断说明',{exact:true}).fill('统一文档诊断：先检查引用映射，再验证过滤边界。');
 await page.getByRole('button',{name:'打开文件 solution.py',exact:true}).click();
 await page.locator('.monaco-editor textarea').first().focus();await page.keyboard.press('ControlOrMeta+End');await page.keyboard.insertText('\n# public-tab-save-all');
 await page.getByRole('button',{name:'公开测试与固定数据',exact:true}).click();
 await expect(page.getByRole('tab',{name:/公开测试与固定数据.md/})).toHaveAttribute('aria-selected','true');
 await expect(page.locator('.editor .panelhead')).toContainText('只读');
 await expect(page.locator('.view-lines')).toContainText('只读参考资料');
 await expect(page.locator('.evidence').getByRole('button',{name:'公开测试',exact:true})).toHaveCount(0);
 await page.getByRole('button',{name:'保存 Ctrl S',exact:true}).click();
 await expect(page.locator('.notice')).toContainText('代码与诊断已保存');
 const saved=await (await request.get('/api/sessions/'+session.id)).json();
 expect(saved.code).toContain('# public-tab-save-all');
 expect(saved.diagnosis).toContain('统一文档诊断');
 expect(Object.keys(saved.files)).toEqual(['solution.py']);
 await page.reload();
 await expect(page.getByRole('tab',{name:/公开测试与固定数据.md/})).toHaveAttribute('aria-selected','true');
 await expect(page.locator('.view-lines')).toContainText('只读参考资料');
 await page.getByRole('button',{name:'打开文件 任务说明.md',exact:true}).click();
 await expect(page.getByLabel('诊断说明',{exact:true})).toHaveValue(saved.diagnosis);
 // Old tab preferences migrate to the unified document without losing diagnosis or code tabs.
 await page.evaluate(id=>localStorage.setItem('workspace:tabs:'+id,JSON.stringify({open:['任务说明.md','环境与约束.md','诊断说明.md','solution.py'],active:'诊断说明.md'})),session.id);
 await page.reload();
 await expect(page.getByRole('tab')).toHaveCount(2);
 await expect(page.getByLabel('诊断说明',{exact:true})).toHaveValue(saved.diagnosis);
 await expect(page.getByRole('tab',{name:/任务说明.md/})).toHaveAttribute('aria-selected','true');
});

test('生成历史默认收起，删除控件统一并保留明确确认',async({page,request},info)=>{
 const session=await (await request.post('/api/sessions',{data:{task_id:'rag'}})).json();
 const requirement='历史折叠与删除样式验证 '+Date.now();
 await request.post('/api/generation/jobs',{data:{requirement}});
 await page.goto('/generate');
 const history=page.locator('.managed-records');
 await expect(history).not.toHaveAttribute('open');
 const row=history.locator('.record-row').filter({hasText:requirement});
 await expect(row).not.toBeVisible();
 await history.locator(':scope > summary').click();await expect(row).toBeVisible();
 await row.locator('.record-main').hover();
 expect(await row.locator('.record-main').evaluate(el=>[getComputedStyle(el).borderTopRightRadius,getComputedStyle(el).borderBottomRightRadius])).toEqual(['0px','0px']);
 expect(await row.evaluate(el=>Math.abs(el.querySelector('.record-main')!.getBoundingClientRect().right-el.querySelector('.delete-control')!.getBoundingClientRect().left)<=1)).toBe(true);
 await page.screenshot({path:info.outputPath('generation-history-hover.png'),fullPage:true});
 const button=row.getByRole('button',{name:/删除 /});
 await expect(button.locator('svg')).toHaveCount(1);await expect(button).toHaveText('删除');
 page.once('dialog',dialog=>dialog.dismiss());await button.click();await expect(row).toBeVisible();
 await page.getByRole('link',{name:'题库',exact:true}).click();
 await expect(page.locator('.cards article').first().locator('.delete-action svg')).toHaveCount(1);
 const training=page.locator('.training-history');await expect(training).not.toHaveAttribute('open');
 await training.locator(':scope > summary').click();
 const trainingRow=training.locator('.training-record').filter({hasText:session.id.slice(0,12)});
 await trainingRow.locator('.record-main').hover();
 expect(await trainingRow.locator('.record-main').evaluate(el=>[getComputedStyle(el).borderTopRightRadius,getComputedStyle(el).borderBottomRightRadius])).toEqual(['0px','0px']);
 expect(await trainingRow.evaluate(el=>Math.abs(el.querySelector('.record-main')!.getBoundingClientRect().right-el.querySelector('.delete-control')!.getBoundingClientRect().left)<=1)).toBe(true);
 await page.screenshot({path:info.outputPath('training-history-hover.png'),fullPage:true});
 await page.getByRole('link',{name:'故障题目生成',exact:true}).click();
 await expect(page.locator('.managed-records')).not.toHaveAttribute('open');
});

// Layout and error-display fixture only; actual publish behavior is covered by generation.spec.ts with Docker.
test('审核矩阵长文字不溢出，作者明确选择入库且失败保留表单',async({page},info)=>{
 const id='c'.repeat(32),long='long_behavior_identifier_'.repeat(8);
 const matrix={passed:true,checks:[{id:'e'.repeat(32),version:long,case:long,status:'passed',expected:{message:long.repeat(4)},actual:{message:long.repeat(4)}}]};
 const job={id,status:'awaiting_review',request:{requirement:'作者审核布局测试'},contract:{title:'作者审核布局测试',scenario:'核对结构化证据',symptom:'测试现象',behaviors:{example:'公开行为'},files:['app.py','tool.py']},matrix};
 await page.route('**/api/generation/jobs/'+id,route=>route.fulfill({json:job}));
 await page.route('**/api/author/generation/'+id,route=>route.fulfill({json:{review_digest:'f'.repeat(64),matrix,summary:{},assets:{}}}));
 await page.route('**/api/author/generation/'+id+'/publish',route=>route.fulfill({status:400,json:{detail:'审核后资产已变化'}}));
 await page.goto('/generate/'+id+'/review');
 await page.getByRole('button',{name:'验证结果',exact:true}).click();
 for(const width of [1280,390]){
  await page.setViewportSize({width,height:900});
  const cell=page.locator('.matrix-cell').first();
  expect(await cell.evaluate(el=>el.scrollWidth<=el.clientWidth+1)).toBe(true);
  await cell.click();await expect(page.getByLabel('矩阵证据')).toBeVisible();
  expect(await page.getByLabel('矩阵证据').evaluate(el=>el.scrollWidth<=el.clientWidth+1)).toBe(true);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
  await page.screenshot({path:info.outputPath('review-'+width+'.png'),fullPage:true});
 }
 const add=page.getByRole('button',{name:'添加至题库',exact:true});
 const alignment=await add.evaluate(el=>({button:el.getBoundingClientRect().right,group:el.parentElement!.getBoundingClientRect().right}));
 expect(Math.abs(alignment.button-alignment.group)).toBeLessThanOrEqual(1);
 await expect(add).toBeEnabled();
 await add.click();await expect(page.getByRole('alert')).toContainText('请填写至少 10 个字符');
 await page.getByLabel('审核依据').fill('仅用于界面回归，核对长字段、确认流程和失败表单保留。');
 await add.click();await expect(page.getByRole('alert')).toContainText('请勾选审核确认');
 await expect(page.getByLabel('我已审核当前资产及验证证据，确认可以发布')).toBeFocused();
 await page.getByLabel('我已审核当前资产及验证证据，确认可以发布').check();
 await expect(add).toBeEnabled();
 await expect(page.getByRole('button',{name:'暂不添加，返回详情'})).toBeVisible();
 await add.click();
 await expect(page.getByRole('alert')).toContainText('审核后资产已变化');
 await expect(page.getByLabel('审核依据')).toHaveValue('仅用于界面回归，核对长字段、确认流程和失败表单保留。');
 await expect(page.getByRole('button',{name:'添加至题库',exact:true})).toBeVisible();
});
