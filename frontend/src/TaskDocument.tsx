import {Markdown} from './Markdown';

type Props={task?:{title:string;brief:string;environment:string};diagnosis:string;onDiagnosisChange:(value:string)=>void};
export function TaskDocument({task,diagnosis,onDiagnosisChange}:Props){
 return <article className="task-document" aria-label="任务文档">
  <header><h2>{task?.title||'正在加载任务…'}</h2><p>阅读题面与约束，在本页记录你的诊断。</p></header>
  {/* 任务说明来自固定任务版本，只读；不作为可编辑业务代码保存。 */}
  <section><h3>任务说明</h3><p className="document-note">只读 · 描述已知现象、接口与验收要求。</p><Markdown text={task?.brief||'正在加载任务说明…'}/></section>
  {/* 环境与约束同样只读，运行始终使用任务白名单与 Docker。 */}
  <section><h3>环境与约束</h3><p className="document-note">只读 · 请在题面允许的范围内修改实现。</p><p>{task?.environment}</p><ul><li>仅允许修改资源管理器中标为可编辑的业务文件。</li><li>保存、运行与提交都会保存全部可编辑文件。</li><li>代码在隔离 Docker 环境执行，测试结果绑定实际运行快照。</li></ul></section>
  {/* 诊断说明可编辑，使用原有独立字段和草稿机制，随本次提交冻结。 */}
  <section><h3>诊断说明</h3><p className="document-note">可编辑 · 与代码一起保存；报告使用提交时的说明。</p><label htmlFor="training-diagnosis">记录现象、定位依据、修复取舍与验证范围</label><textarea id="training-diagnosis" aria-label="诊断说明" maxLength={10000} rows={7} value={diagnosis} onChange={e=>onDiagnosisChange(e.target.value)} placeholder="观察到什么？依据哪些代码或测试定位？为什么这样修复？还有哪些场景未验证？"/></section>
 </article>;
}
