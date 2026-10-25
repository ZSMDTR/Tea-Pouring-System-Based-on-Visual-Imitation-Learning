let libraryData={collections:[],members:[],mixtures:[]};
function option(value,label){const o=el('option',label);o.value=value;return o;}
function populateGroups(select,empty=false){const previous=select.value;select.replaceChildren();if(empty)select.append(option('','请先选择数据集'));for(const g of libraryData.collections)select.append(option(g.id,g.name));if(libraryData.collections.some(g=>g.id===previous))select.value=previous;}
async function libraryPost(action,body){return api('/api/library/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-Workbench-Token':snapshot.csrf_token},body:JSON.stringify(body)});}
async function loadLibrary(){
  try{
    libraryData=await api('/api/library');populateGroups($('capture-collection'),true);populateGroups($('library-group'));
    if(typeof renderProjectSummary==='function')renderProjectSummary();
    const prior=$('library-source').value;$('library-source').replaceChildren(option('','选择已有原始数据集'));
    for(const d of snapshot?.datasets||[])if(d.frames)$('library-source').append(option(d.name,`${d.name} · ${d.episodes} 条`));
    $('library-source').value=prior;renderMembers();renderMixtures();renderWorkspaceList();if(typeof workflowReady!=='undefined'&&workflowReady){drawWorkflow();renderActionList();}await loadRecycle();if(recordingJob)drawRecording(recordingJob);
  }catch(e){failure(e);}
}
function currentMembers(){return libraryData.members.filter(m=>m.collection===$('library-group').value&&!m.source_deleted);}
function renderMembers(){
  const all=currentMembers(), filter=$('library-filter').value;
  const rows=all.filter(m=>filter==='trash'?m.trashed:!m.trashed&&(filter==='active'||m.review===filter||filter==='replay_passed'&&m.replay?.status==='passed'||filter==='replay_pending'&&!['passed','failed'].includes(m.replay?.status)));
  if(typeof renderMemberWorkbench==='function'){renderMemberWorkbench(rows,all);$('library-summary').textContent=`在库 ${all.filter(m=>!m.trashed).length} 条 · 筛选后 ${rows.length} 条`;return;}
  $('library-summary').textContent=`在库 ${all.filter(m=>!m.trashed).length} 条 · 保留 ${all.filter(m=>!m.trashed&&m.review==='keep').length} 条 · 回收站 ${all.filter(m=>m.trashed).length} 条`;
  $('library-members').replaceChildren();
  if(!rows.length)$('library-members').append(el('p','这里还没有示范。可开始采集，或纳入此前的数据。','hint'));
  for(const m of rows){
    const card=el('article','','dataset'),buttons=el('div','','capture-actions');
    card.append(el('h3',`示范 ${m.episode+1}`),el('p',m.task||'未记录任务'),el('p',`${m.frames} 帧 · ${new Date(m.created*1000).toLocaleString()} 纳入`)); const fileInfo=el('details');fileInfo.append(el('summary','文件信息'),el('p',m.dataset,'path'));card.append(fileInfo);
    card.append(el('p',`${({unreviewed:'未检查',keep:'保留',reject:'需重录'})[m.review]||'未检查'} · ${({completed:'保存完成',legacy:'历史记录',pending:'等待保存',unavailable:'源文件不可用',error:'采集异常'})[m.capture_state]||'待检查'} · ${typeof replayLabel==='function'?replayLabel(m.replay?.status):'Replay 未验收'}`));
    const review=el('select');review.setAttribute('aria-label','结果标记');for(const [v,l] of [['unreviewed','未检查'],['keep','保留（不等于 Replay 通过）'],['reject','需重录']])review.append(option(v,l));review.value=m.review;
    const note=el('textarea');note.placeholder='备注，例如：抓取时滑落';note.setAttribute('aria-label','示范备注');note.maxLength=2000;note.value=m.note;
    const move=el('select');move.setAttribute('aria-label','移动到数据集');for(const g of libraryData.collections)move.append(option(g.id,g.name));move.value=m.collection;
    const edit=el('details','','member-edit');edit.append(el('summary','整理标记、备注与归属'),review,note,el('label','归属数据集'),move);card.append(edit);
    const operation=(label,action)=>{const b=el('button',label);b.onclick=async()=>{b.disabled=true;try{await action();}catch(e){failure(e);}finally{b.disabled=false;}};buttons.append(b);return b;};
    edit.append(operation('保存整理结果',async()=>{await libraryPost('update',{dataset:m.dataset,episode:m.episode,review:review.value,note:note.value,collection:move.value});$('library-notice').textContent='已保存整理结果。';await loadLibrary();}));
    operation('查看视频',async()=>{await refresh();const d=snapshot.datasets.find(d=>d.name===m.dataset);if(!d)throw Error('原始数据尚未保存或不可用。');await showSource(d,m.collection);const button=[...document.querySelectorAll('.episode')].find(b=>Number(b.dataset.episodeIndex)===m.episode);if(button)button.click();});
    if(!m.trashed&&['completed','legacy'].includes(m.capture_state))operation('实机 Replay／验收',()=>openReplay(m));
    if(!m.trashed)operation('沿用任务重录',async()=>{if(recordingJob?.active)throw Error('请先结束当前采集。');selectWorkspaceGroup(m.collection);$('capture-task').value=m.task;$('capture-prepared').checked=false;page('capture');if(recordingJob)drawRecording(recordingJob);});
    operation(m.trashed?'恢复':'删除',async()=>{await libraryPost('update',{dataset:m.dataset,episode:m.episode,trashed:!m.trashed});$('library-notice').textContent=m.trashed?'已恢复。':'已移入统一回收站，可恢复或彻底删除。';await syncAfterDelete();});
    if(recordingJob?.active && recordingJob.dataset===m.dataset)for(const b of [...buttons.children,...edit.querySelectorAll('button')])b.disabled=true;
    card.append(buttons);$('library-members').append(card);
  }
}
function renderMixtures(){
  const chosen=new Map([...$('mixture-choices').querySelectorAll('[data-group]')].map(row=>[row.dataset.group,{checked:row.querySelector('input[type=checkbox]').checked,weight:row.querySelector('input[type=number]').value}]));
  $('mixture-choices').replaceChildren();
  for(const g of libraryData.collections){const row=el('label','','mixture-choice');row.dataset.group=g.id;const check=el('input');check.type='checkbox';check.checked=chosen.get(g.id)?.checked||false;const count=libraryData.members.filter(m=>m.collection===g.id&&!m.trashed&&m.review==='keep').length;const weight=el('input');weight.type='number';weight.min='.001';weight.step='any';weight.value=chosen.get(g.id)?.weight||1;weight.setAttribute('aria-label',g.name+'采样权重');row.append(check,el('span',`${g.name} · ${count} 条保留示范`),el('span','权重'),weight);$('mixture-choices').append(row);}
  if(!libraryData.collections.length)$('mixture-choices').append(el('p','先创建数据集并整理示范。','hint'));
  $('mixture-list').replaceChildren();
  for(const mix of libraryData.mixtures){const card=el('article','','dataset');card.append(el('h3',mix.name),el('p',`${mix.episodes} 条 · ${new Date(mix.created*1000).toLocaleString()} · 固定清单`),el('p','可按原始帧数合并导出。草稿采样权重仍待训练加载器接入。'));if(mix.unavailable_sources?.length)card.append(el('p',`不可用：${mix.unavailable_sources.length} 条引用的数据已删除。恢复后自动恢复可用性；彻底删除后需重建组合。`,'deletion-warning'));for(const s of mix.sources)card.append(el('p',`${s.name}：${s.episodes.length} 条 · 草稿权重 ${(s.probability*100).toFixed(1)}%`));const detail=el('details'),summary=el('summary','查看固定示范清单');detail.append(summary);for(const s of mix.sources)for(const m of s.episodes)detail.append(el('p',`${s.name} / ${m.dataset} / 示范 ${m.episode+1}`,'path'));card.append(detail);if(typeof previewMixtureExport==='function'){const merge=el('button','预览合并导出');merge.disabled=!!mix.unavailable_sources?.length;merge.onclick=()=>previewMixtureExport(mix);card.append(merge);}$('mixture-list').append(card);}
}
$('capture-new-group').onclick=()=>page('workspace');
$('capture-collection').onchange=()=>{const group=libraryData.collections.find(g=>g.id===$('capture-collection').value);if(group)$('capture-task').value=group.task;if(recordingJob)drawRecording(recordingJob);};
$('library-group').onchange=renderMembers;$('library-filter').onchange=renderMembers;
$('library-refresh').onclick=async()=>{await refresh();await loadLibrary();};
$('group-form').onsubmit=async event=>{event.preventDefault();const button=event.submitter;button.disabled=true;try{const group=await libraryPost('create',{name:$('group-name').value,task:$('group-task').value});await loadLibrary();$('library-group').value=group.id;$('capture-collection').value=group.id;$('capture-task').value=group.task;renderMembers();if(recordingJob)drawRecording(recordingJob);$('library-notice').textContent='已创建数据集。';$('group-form').reset();await finishWorkspaceCreate(group);}catch(e){failure(e);}finally{button.disabled=false;}};
$('library-import').onclick=async()=>{const b=$('library-import');b.disabled=true;try{const r=await libraryPost('import',{collection:$('library-group').value,dataset:$('library-source').value});$('library-notice').textContent=`已纳入 ${r.added} 条；已归属的示范不会重复导入。`;await loadLibrary();}catch(e){failure(e);}finally{b.disabled=false;}};
$('mixture-form').onsubmit=async event=>{event.preventDefault();const b=event.submitter;b.disabled=true;try{const selections=[...$('mixture-choices').children].filter(row=>row.querySelector('input[type=checkbox]')?.checked).map(row=>({id:row.dataset.group,weight:Number(row.querySelector('input[type=number]').value)}));await libraryPost('mixture',{name:$('mixture-name').value,selections});await loadLibrary();$('mixture-name').value='';}catch(e){failure(e);}finally{b.disabled=false;}};
