import React,{useEffect} from 'react';
import {createRoot} from 'react-dom/client';
import {Training} from './Training';
import {Generation} from './Generation';
import {GenerationReview} from './GenerationReview';
import {HealthStatus} from './HealthStatus';
import {navigate,usePath} from './router';
import './style.css';
import './enhancements.css';
import './workbench.css';
function Shell(){
 const path=usePath();
 useEffect(()=>{if(location.pathname==='/')navigate('/generate',true)},[]);
 const section=path.startsWith('/generate')?'generate':path==='/projects'?'projects':'library';
 const training=path.match(/^\/training\/([a-f0-9]{32})$/),task=path.match(/^\/library\/([a-z][a-z0-9_]+)$/),review=path.match(/^\/generate\/([a-f0-9]{32})\/review$/),job=path.match(/^\/generate\/([a-f0-9]{32})$/);
 return <><header className="app-header"><div className="brand" aria-label="AgentLab">◈ AgentLab <span>V0.5</span></div><nav aria-label="主导航">{[['generate','故障题目生成','/generate'],['library','题库','/library'],['projects','个人工程项目训练','/projects']].map(([key,title,to])=><a key={key} href={to} aria-current={section===key?'page':undefined} onClick={e=>{e.preventDefault();navigate(to)}}>{title}</a>)}</nav><HealthStatus/></header>
 {path==='/projects'?<main className="landing compact-page"><p className="eyebrow">即将推出</p><h1>个人工程项目训练</h1><div className="empty-state"><h2>尚未开放</h2><p>未来将支持围绕用户自己的工程项目开展训练。本版不提供上传、仓库导入或真实项目训练。</p></div></main>:review?<GenerationReview id={review[1]}/>:path==='/generate'||job?<Generation key={job?.[1]||'new'} id={job?.[1]}/>:path==='/library'||task||training?<Training key={training?.[1]||task?.[1]||'library'} sessionId={training?.[1]} taskId={task?.[1]}/>:path==='/'?<p>正在恢复入口…</p>:<main className="landing"><h1>页面不存在</h1><a href="/library">返回题库</a></main>}
 </>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><Shell/></React.StrictMode>);
