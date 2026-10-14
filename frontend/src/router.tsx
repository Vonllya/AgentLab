import {useEffect,useState} from 'react';
export function navigate(path:string,replace=false){if(replace)history.replaceState(null,'',path);else history.pushState(null,'',path);window.dispatchEvent(new PopStateEvent('popstate'));}
export function usePath(){const [path,setPath]=useState(location.pathname);useEffect(()=>{const fn=()=>setPath(location.pathname);window.addEventListener('popstate',fn);return()=>window.removeEventListener('popstate',fn)},[]);return path;}
export async function api<T=any>(path:string,method='GET',body?:unknown):Promise<T>{
 const res=await fetch('/api'+path,{method,headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
 const text=await res.text();
 let value:any;
 if(text){
  try{value=JSON.parse(text)}catch{
   if(!res.ok)throw Error(text.trim()||`请求失败（HTTP ${res.status}）`);
   throw Error(`服务器返回了无效的 JSON（HTTP ${res.status}）`);
  }
 }
 if(!res.ok){
  const detail=value?.detail;
  throw Error(typeof detail==='string'?detail:detail!==undefined?JSON.stringify(detail):`请求失败（HTTP ${res.status} ${res.statusText}）`.trim());
 }
 return value as T;
}
