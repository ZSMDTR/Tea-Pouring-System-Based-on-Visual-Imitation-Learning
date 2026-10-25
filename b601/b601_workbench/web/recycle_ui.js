async function recyclePost(action,body){return api('/api/recycle/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-Workbench-Token':snapshot.csrf_token},body:JSON.stringify(body)});}
function bytesLabel(n){return n>=1073741824?(n/1073741824).toFixed(2)+' GiB':(n/1048576).toFixed(2)+' MiB';}
async function syncAfterDelete(broadcast=true){
  requestId++;$('player').pause();$('player').removeAttribute('src');$('player').load();$('detail').hidden=true;$('datasets').hidden=false;
  await refresh();await loadLibrary();if(!$('data').hidden)returnFromPreview();drawRecording(await api('/api/record/status'));
  if(broadcast)localStorage.setItem('b601.libraryChanged',String(Date.now()));
}
async function trashDataset(d){
  const name=d.name||d.dataset;
  const count=d.source_episodes??d.episodes;
  if(!window.confirm(`将原始数据集“${name}”的全部 ${count??'未知数量的'} 条示范移入统一回收站？\n其他列表会同步隐藏，相关训练组合会标记不可用。可以恢复。`))return;
  try{await recyclePost('trash',{dataset:name});await syncAfterDelete();$('recycle-notice').textContent='已移入回收站，可恢复或彻底删除。';}catch(e){failure(e);}
}
async function loadRecycle(){
  const items=await api('/api/recycle');$('recycle-list').replaceChildren();
  if(!items.length)$('recycle-list').append(el('p','回收站为空。','hint'));
  for(const item of items){const card=el('article','','dataset'),buttons=el('div','','capture-actions');
    card.append(el('h3',item.dataset+(item.kind==='episode'?` / 示范 ${item.episode+1}`:'')));
    card.append(el('p',item.kind==='dataset'?`${item.episodes} 条 · 占用约 ${bytesLabel(item.allocated)} · ${item.state==='purging'?'彻底删除中断，可重新确认后重试':'已移入回收站'}`:`单条示范 · 所在原始数据集共 ${item.source_episodes} 条`));
    if(item.references.length)card.append(el('p','受影响的训练组合：'+item.references.map(r=>`${r.name}（${r.episodes} 条）`).join('、'),'deletion-warning'));
    const button=(label,run,cls)=>{const b=el('button',label,cls);b.onclick=async()=>{b.disabled=true;try{await run();}catch(e){failure(e);}finally{b.disabled=false;}};buttons.append(b);return b;};
    const restore=button('恢复',async()=>{await recyclePost('restore',{dataset:item.dataset,...(item.kind==='episode'?{episode:item.episode}:{})});await syncAfterDelete();$('recycle-notice').textContent='已恢复，其他列表已同步。';});
    restore.disabled=item.state!=='trashed';
    if(item.kind==='dataset'||item.whole_source_removed)button('彻底删除…',async()=>{
      const plan=await recyclePost('plan',{dataset:item.dataset});
      const refs=plan.references.length?'\n受影响组合：'+plan.references.map(r=>r.name).join('、'):'';
      const answer=window.prompt(`彻底删除 ${plan.episodes} 条示范，预计释放 ${bytesLabel(plan.allocated)}。\n${plan.message}${refs}\n\n请输入完整数据集名称确认：\n${plan.dataset}`);
      if(answer===null){await syncAfterDelete();return;}
      if(answer!==plan.dataset)throw Error('名称不一致，未删除。');
      await recyclePost('purge',{dataset:plan.dataset,token:plan.token,confirmation:answer});await syncAfterDelete();$('recycle-notice').textContent='已彻底删除原始数据集文件。';
    },'danger');
    else {card.append(el('p','此示范与同一原始数据集的其他示范共享文件。本版不会截断或重写共享文件；彻底释放空间需要将整组移入回收站。','hint'));button('将整个原始数据集移入回收站…',()=>trashDataset(item),'danger');}
    card.append(buttons);$('recycle-list').append(card);
  }
}
$('delete-detail').onclick=()=>{const d=snapshot.datasets.find(d=>d.name===currentDataset);if(d)trashDataset(d);};
$('recycle-refresh').onclick=async()=>{try{await syncAfterDelete();}catch(e){failure(e);}};
window.addEventListener('storage',event=>{if(event.key==='b601.libraryChanged')syncAfterDelete(false).catch(failure);});
