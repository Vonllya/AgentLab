import {expect,type Page} from '@playwright/test';
export async function fillDiagnosis(page:Page,text:string){
 const previous=await page.locator('.file-tab[aria-selected="true"] .tab-select').getAttribute('aria-label');
 await page.getByRole('button',{name:'打开文件 任务说明.md',exact:true}).click();
 await page.getByLabel('诊断说明',{exact:true}).fill(text);
 if(previous&&previous!=='切换到 任务说明.md')await page.getByRole('button',{name:previous,exact:true}).click();
}
export async function expectDiagnosis(page:Page,text:string){
 const previous=await page.locator('.file-tab[aria-selected="true"] .tab-select').getAttribute('aria-label');
 await page.getByRole('button',{name:'打开文件 任务说明.md',exact:true}).click();
 await expect(page.getByLabel('诊断说明',{exact:true})).toHaveValue(text);
 if(previous&&previous!=='切换到 任务说明.md')await page.getByRole('button',{name:previous,exact:true}).click();
}
