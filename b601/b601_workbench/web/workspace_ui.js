// Navigation only: existing recording and deletion APIs retain their semantics.
let workspaceGroup=null, previewReturnGroup=null, sourceEpisodes=[], organizeSource=null;
const groupName=id=>libraryData.collections.find(g=>g.id===id);
function selectWorkspaceGroup(id){
  const group=groupName(id);if(!group)return false;
  const changed=workspaceGroup!==id;workspaceGroup=id;
  $('library-group').value=id;
  if(!recordingJob?.active){const captureChanged=$('capture-collection').value!==id;$('capture-collection').value=id;if(changed||captureChanged){$('capture-task').value=group.task;$('capture-prepared').checked=false;}}
  if(changed){$('library-filter').value='active';if($('member-search'))$('member-search').value='';if(typeof clearBatchSelection==='function')clearBatchSelection();}
  renderMembers();if(recordingJob)drawRecording(recordingJob);return true;
}
function workspaceView(name){
  const section=name==='workspace'?'datasets':name;
  $('workspace').classList.toggle('with-member',section==='library');
  if(section!=='library'&&$('member-player'))$('member-player').pause();
  const home=['datasets','sources'].includes(section), group=groupName(workspaceGroup);
  const sources=section==='sources';
  if(home)$('workspace-home').dataset.view=sources?'sources':'datasets';
  $('workspace-list-title').textContent=sources?'原始与转换目录':'我的数据集';
  $('workspace-list-hint').textContent=sources?'查看磁盘上的原始数据与训练副本，可预览、整理或移入回收站。':'按任务管理示范，打开数据集可继续采集。';
  $('workspace-new').hidden=sources;$('workspace-create').hidden=sources;
  if(['library','capture'].includes(section)&&!group){workspaceView('workspace');return;}
  if(section==='capture'){
    if(recordingJob?.active && recordingJob.collection!==group.id){workspaceView('library');return;}
    if(!recordingJob?.active && $('capture-collection').value!==group.id){
      $('capture-collection').value=group.id;$('capture-task').value=group.task;
      $('capture-prepared').checked=false;
    }
    if(recordingJob)drawRecording(recordingJob);
  }
  $('workspace-home').hidden=!home;
  document.querySelectorAll('.workspace-panel').forEach(panel=>panel.hidden=panel.id!==section);
  $('workspace-context').hidden=!['library','capture','data'].includes(section);
  const tab=['sources','mixtures','recycle'].includes(section)?section:'datasets';
  document.querySelectorAll('[data-workspace]').forEach(b=>{b.classList.toggle('active',b.dataset.workspace===tab);b.setAttribute('aria-current',b.dataset.workspace===tab?'page':'false');});
  if(group&&['library','capture'].includes(section)){
    $('workspace-dataset-title').textContent=group.name;$('workspace-dataset-task').textContent=group.task;
    $('workspace-group-tabs').hidden=false;
  }else if(section==='data'){
    $('workspace-dataset-title').textContent=previewReturnGroup?groupName(previewReturnGroup)?.name||'示范预览':'已有数据';
    $('workspace-dataset-task').textContent=previewReturnGroup?'查看本数据集的示范视频；返回后可继续整理。':'可直接查看和删除，也可整理为数据集后继续采集。';
    $('workspace-group-tabs').hidden=true;
  }
  document.querySelectorAll('[data-group-view]').forEach(b=>b.classList.toggle('active',b.dataset.groupView===section));
  $('workspace-organize-source').hidden=!!previewReturnGroup;
  if(home)renderWorkspaceList();
}
function renderWorkspaceList(){
  if(!snapshot)return;
  const list=$('workspace-list'),query=$('search').value.trim().toLowerCase(),sources=$('workspace-home').dataset.view==='sources';list.replaceChildren();
  for(const group of sources?[]:libraryData.collections){
    const members=libraryData.members.filter(m=>m.collection===group.id&&!m.trashed&&!m.source_deleted);
    if(!`${group.name} ${group.task}`.toLowerCase().includes(query))continue;
    const row=el('article','','dataset'),top=el('div','','dataset-top');top.append(el('h3',group.name));
    const open=el('button','打开数据集 →','primary');open.onclick=()=>{selectWorkspaceGroup(group.id);page('library');};top.append(open);row.append(top,el('p',group.task),el('p',`${members.length} 条示范 · ${members.filter(m=>m.review==='keep').length} 条保留 · 可继续采集`));list.append(row);
  }
  const assigned=new Map();for(const m of libraryData.members)if(!m.trashed&&!m.source_deleted)assigned.set(m.dataset,(assigned.get(m.dataset)||0)+1);
  for(const d of sources?snapshot.datasets:[]){
    const remaining=d.episodes-(d.removed_episodes||0);
    if(($('hide-empty').checked&&!d.frames&&!d.error)||!d.name.toLowerCase().includes(query))continue;
    const row=el('article','','dataset'),top=el('div','','dataset-top');top.append(el('h3',d.name));
    const open=el('button','查看与整理 →');open.disabled=!d.frames;open.onclick=()=>showSource(d).catch(failure);
    const remove=el('button','删除','danger');remove.onclick=()=>trashDataset(d);top.append(open,remove);row.append(top);
    row.append(el('p',d.error||`${d.simulation?'[软件设备测试] ':''}${remaining} 条在库 · ${d.version||'未知格式'} · ${assigned.get(d.name)||0} 条已纳入数据集`));
    const details=el('details');details.append(el('summary','文件信息'),el('p',d.path||'','path'),el('p',`${d.version||''} · ${d.fps||0} FPS · ${d.frames||0} 帧`,'path'));row.append(details);list.append(row);
  }
  if(!list.children.length)list.append(el('p','没有符合条件的数据集。可新建数据集，或调整搜索条件。','hint'));
}
async function showSource(d,group=null){
  previewReturnGroup=group;if(group)selectWorkspaceGroup(group);sourceEpisodes=[];
  page('data');await openDataset(d);
}
function returnFromPreview(){
  requestId++;$('player').pause();$('detail').hidden=true;
  if(previewReturnGroup&&selectWorkspaceGroup(previewReturnGroup))page('library');else page('workspace');
}
function updateWorkspaceRecording(job){
  const replay=job.mode==='replay';
  $('workspace-recording').hidden=!job.active;
  $('workspace-recording-label').textContent=`${replay?'Replay':groupName(job.collection)?.name||'当前任务'} · ${phaseNames[job.state]||job.state} · ${job.frames||0} 帧`;
  $('workspace-return-capture').textContent=replay?'返回 Replay':'返回当前采集';
  $('workspace-stop').textContent=replay?'结束 Replay 并回位':'结束并保存';
  $('workspace-stop').disabled=!job.active||stopRequested===job.id||!['starting','connecting','recording','positioning','replaying','resting'].includes(job.state);
  // Never allow switching the capture form into a different selected dataset
  // while a job owns the devices. The persistent status strip stays accessible.
  const captureTab=document.querySelector('[data-group-view="capture"]');
  if(captureTab)captureTab.disabled=!!job.active&&workspaceGroup!==job.collection;
}
async function finishWorkspaceCreate(group){
  const source=organizeSource;organizeSource=null;$('workspace-create').open=false;
  selectWorkspaceGroup(group.id);page('library');
  if(source){
    try{const result=await libraryPost('import',{collection:group.id,dataset:source});await loadLibrary();selectWorkspaceGroup(group.id);$('library-notice').textContent=`已整理 ${result.added} 条示范。可以继续采集。`;}
    catch(e){$('library-source').value=source;$('library-notice').textContent='数据集已创建，纳入旧示范未完成；可在下方重试。';throw e;}
  }
}
function newWorkspaceDataset(){organizeSource=null;page('workspace');$('workspace-create').open=true;$('group-name').focus();}
// Move existing controls rather than duplicate forms or recording state.
for(const id of ['capture','data','library','mixtures','recycle'])$('workspace-panels').append($(id));
$('workspace-create-form').append($('group-form'));
$('workspace-search').append(document.querySelector('#data .toolbar'));
$('datasets').hidden=true;
// Import remains available as an advanced operation inside a dataset.
$('workspace-new').onclick=newWorkspaceDataset;
$('workspace-back').onclick=()=>page('workspace');
$('workspace-return-capture').onclick=()=>{if(recordingJob?.mode==='replay'){if(selectWorkspaceGroup(recordingJob.collection))page('actions');$('replay-panel').hidden=false;$('replay-panel').scrollIntoView({block:'start'});}else if(selectWorkspaceGroup(recordingJob?.collection))page('capture');};
$('workspace-stop').onclick=()=>recordingJob?.mode==='replay'?stopReplay():$('capture-stop').onclick();
$('workspace-organize-source').onclick=()=>{
  const d=snapshot.datasets.find(d=>d.name===currentDataset);if(!d)return;
  organizeSource=d.name;page('workspace');$('workspace-create').open=true;$('group-name').value=d.name.slice(0,80);
  const tasks=sourceEpisodes[0]?.tasks;$('group-task').value=Array.isArray(tasks)?tasks[0]||'':tasks||'';$('group-name').focus();
};
$('workspace-add-mixture').onclick=()=>{page('mixtures');const row=[...$('mixture-choices').children].find(n=>n.dataset.group===workspaceGroup);if(row)row.querySelector('input[type=checkbox]').checked=true;};
document.querySelectorAll('[data-workspace]').forEach(b=>b.onclick=()=>page(b.dataset.workspace==='datasets'?'workspace':b.dataset.workspace));
document.querySelectorAll('[data-group-view]').forEach(b=>b.onclick=()=>page(b.dataset.groupView));
refresh().then(loadLibrary).then(pollRecording);
