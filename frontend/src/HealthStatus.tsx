import React,{useEffect,useState} from 'react';
import {api} from './router';

type Health={agent_mode:'mock'|'real';docker_available:boolean;reason:string};

export function HealthStatus(){
 const [health,setHealth]=useState<Health|null>(null),[failed,setFailed]=useState(false),[open,setOpen]=useState(false),[checking,setChecking]=useState(false);
 const check=async()=>{setChecking(true);setFailed(false);try{setHealth(await api<Health>('/health'))}catch{setHealth(null);setFailed(true)}finally{setChecking(false)}};
 useEffect(()=>{void check()},[]);
 return <div className="health-wrap">
  <button className="health-trigger" aria-expanded={open} aria-label="查看系统状态" onClick={()=>setOpen(v=>!v)}>
   <span className={'health-dot '+(health?'ok':'unknown')} aria-hidden="true"/>
   <span>{failed?'状态未知':checking?'正在检查':health?.agent_mode==='mock'?'MOCK · 确定性辅导':'真实模型'}</span>
   <span aria-hidden="true">·</span>
   <span>{failed?'检查失败':health?health.docker_available?'执行环境可用':'执行环境不可用':'状态未知'}</span>
  </button>
  {open&&<section className="health-popover" aria-label="系统状态详情">
   <h2>系统状态</h2>
   <dl><div><dt>模型模式</dt><dd>{failed||!health?'状态未知':health.agent_mode==='mock'?'MOCK 确定性模式':'真实模型模式'}</dd></div><div><dt>执行环境</dt><dd>{failed||!health?'状态未知':health.docker_available?'Docker 可用':'不可用'}</dd></div></dl>
   {health?.reason&&<p>{health.reason}</p>}{failed&&<p role="alert">健康检查失败，请确认后端服务是否可访问。</p>}
   <button disabled={checking} onClick={()=>void check()}>{checking?'检查中…':'重新检查'}</button>
  </section>}
 </div>;
}
