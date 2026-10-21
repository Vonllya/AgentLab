import {expect,type Page} from '@playwright/test';
export async function fillDiagnosis(page:Page,text:string){
 const previous=await page.locator('.file-tab[aria-selected="true"] .tab-select').getAttribute('aria-label');
 await page.getByRole('button',{name:'打开文件 诊断说明.md',exact:true}).click();
 const editor=page.locator('.monaco-editor textarea').first();await editor.focus();await page.keyboard.press('ControlOrMeta+A');await page.keyboard.insertText(text);
 await expect(page.locator('.view-lines')).toContainText(text);
 if(previous&&previous!=='切换到 诊断说明.md')await page.getByRole('button',{name:previous,exact:true}).click();
}
export async function expectDiagnosis(page:Page,text:string){
 const previous=await page.locator('.file-tab[aria-selected="true"] .tab-select').getAttribute('aria-label');
 await page.getByRole('button',{name:'打开文件 诊断说明.md',exact:true}).click();
 if(text)await expect(page.locator('.view-lines')).toContainText(text);
 else await expect(page.locator('.view-lines')).not.toContainText('导航切换保留本会话草稿');
 if(previous&&previous!=='切换到 诊断说明.md')await page.getByRole('button',{name:previous,exact:true}).click();
}
