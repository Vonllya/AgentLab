import React,{useState} from 'react';

export function DeleteButton({label,confirmText,onDelete,variant='icon'}:{label:string;confirmText:string;onDelete:()=>Promise<void>;variant?:'icon'|'text'}){
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 const remove=async()=>{if(busy||!window.confirm(confirmText))return;setBusy(true);setError('');try{await onDelete()}catch(e){setError(String(e))}finally{setBusy(false)}};
 return <span className={'delete-control '+variant}><button className="delete-action" type="button" aria-label={variant==='icon'?'删除 '+label:undefined} title={'删除 '+label} disabled={busy} onClick={()=>void remove()}><svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 10v7M14 10v7"/></svg><span>{busy?'删除中…':'删除'}</span></button>{error&&<small role="alert">{error}</small>}</span>;
}
