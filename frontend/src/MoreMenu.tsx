import React,{useRef,useState} from 'react';

export function MoreMenu({label,confirmText,onDelete}:{label:string;confirmText:string;onDelete:()=>Promise<void>}){
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 const details=useRef<HTMLDetailsElement>(null);
 const remove=async()=>{if(!window.confirm(confirmText))return;setBusy(true);setError('');try{await onDelete();if(details.current)details.current.open=false}catch(e){setError(String(e))}finally{setBusy(false)}};
 return <details className="more-menu" ref={details}><summary aria-label={'更多操作：'+label}>更多</summary><div className="more-popover"><button className="danger-action" disabled={busy} onClick={()=>void remove()}>{busy?'删除中…':'删除'}</button>{error&&<p role="alert">删除失败：{error}</p>}</div></details>;
}
