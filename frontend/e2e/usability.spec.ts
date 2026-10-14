import {test,expect} from '@playwright/test';

test('健康检查失败不会误报模型或执行环境正常',async({page})=>{
 await page.route('**/api/health',route=>route.abort());
 await page.goto('/library');
 await expect(page.getByRole('button',{name:'查看系统状态'})).toContainText('状态未知');
 await page.getByRole('button',{name:'查看系统状态'}).click();
 await expect(page.getByLabel('系统状态详情').getByRole('alert')).toContainText('健康检查失败');
 await expect(page.getByLabel('系统状态详情')).not.toContainText('真实模型模式');
 await expect(page.getByLabel('系统状态详情')).not.toContainText('Docker 可用');
});

test('题库筛选写入 URL 并可恢复',async({page})=>{
 await page.goto('/library');
 await page.getByLabel('搜索题库').fill('RAG');
 await expect(page).toHaveURL(/q=RAG/);
 await page.reload();
 await expect(page.getByLabel('搜索题库')).toHaveValue('RAG');
});

test('示例不会无提示覆盖草稿，窄屏使用明确工作区切换',async({page})=>{
 await page.goto('/generate');
 await page.getByLabel('训练需求').fill('这是用户尚未提交的有效需求草稿');
 page.once('dialog',dialog=>dialog.dismiss());
 await page.getByRole('button',{name:/工具重试/}).click();
 await expect(page.getByLabel('训练需求')).toHaveValue('这是用户尚未提交的有效需求草稿');
 await page.goto('/library');
 await page.getByRole('button',{name:'开始训练 ↗'}).first().click();
 await page.setViewportSize({width:390,height:844});
 await expect(page.getByRole('navigation',{name:'工作区面板'})).toBeVisible();
 await page.getByRole('button',{name:'对话',exact:true}).click();
 await expect(page.getByLabel('给 Agent 的消息')).toBeVisible();
 await page.getByRole('button',{name:'代码',exact:true}).click();
 await expect(page.locator('.monaco-editor')).toBeVisible();
});
