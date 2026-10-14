import React,{useState} from 'react';

export function DeleteButton({label,confirmText,onDelete,variant='icon'}:{label:string;confirmText:string;onDelete:()=>Promise<void>;variant?:'icon'|'text'}){
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 const remove=async()=>{if(busy||!window.confirm(confirmText))return;setBusy(true);setError('');try{await onDelete()}catch(e){setError(String(e))}finally{setBusy(false)}};
 return <span className={'delete-control '+variant}>{variant==='icon'?<button className="delete-icon" type="button" aria-label={'删除 '+label} title={'删除 '+label} disabled={busy} onClick={()=>void remove()}>×</button>:<button className="delete-text" type="button" disabled={busy} onClick={()=>void remove()}>{busy?'删除中…':'删除'}</button>}{error&&<small role="alert">{error}</small>}</span>;
}
