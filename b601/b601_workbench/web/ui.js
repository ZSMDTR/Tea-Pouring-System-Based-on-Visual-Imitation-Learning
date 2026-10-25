const $ = id => document.getElementById(id);
let snapshot, currentDataset, requestId = 0, clipStart = 0, clipEnd = Infinity;
let canPolling = false;
function el(tag, text, cls) { const n = document.createElement(tag); n.textContent = text; if (cls) n.className = cls; return n; }
async function api(url, options) { const r = await fetch(url, options); const d = await r.json(); if (!r.ok) throw Error(d.error || '读取失败'); return d; }
function failure(e) { $('error').hidden = false; $('error').textContent = e.message; }
let activePage='overview', setupStage='connections', trainingStage='train', workflowReady=false;
function page(name) {
  if(name==='modes'){name='setup';setupStage='motion';}
  const inside=['workspace','sources','library','capture','data','mixtures','recycle'].includes(name);
  const target=inside?'workspace':name;
  if(!['overview','setup','actions','workspace','experiments','models','inference','roadmap','management'].includes(target))return;
  // Keep the existing inference page heartbeat contract: an executing robot
  // remains on its monitoring page until normal stop/return has completed.
  if(workflowReady && target!=='inference' && inferenceRunning()){
    failure(Error('请先结束执行并等待回位，再切换页面。'));return;
  }
  window.scrollTo(0,0);$('player').pause();activePage=target;
  document.querySelectorAll('.page').forEach(n=>n.hidden=n.id!==target);
  document.querySelectorAll('nav button').forEach(n=>{const selected=n.dataset.page===target;n.classList.toggle('active',selected);n.setAttribute('aria-current',selected?'page':'false');});
  const meta={overview:['项目概览','从设备准备到模型运行，按步骤推进你的实验。','PROJECT OVERVIEW'],setup:['设备设置','检查连接、验证遥操作，确认相机视角。','01 / DEVICE SETUP'],actions:['动作回放','命名动作、录制示教，并使用实机回放验收。','02 / ACTION REPLAY'],workspace:['数据集','按任务采集、整理和管理你的示范数据。','03 / DATASETS'],experiments:['训练','导出保留示范，检查数据，再开始本地训练。','04 / TRAINING'],models:['模型','查看训练结果与完整存档，选择模型进入运行。','05 / MODELS'],inference:['运行','选择模型与相机，用语言指令执行任务。','06 / RUN'],roadmap:['功能与开发进度','查看已接入能力和仍待完成的工作。','PROJECT NOTES'],management:['项目与管理','整理项目、查看空间、备份配置与交换数据。','WORKSPACE MANAGEMENT']}[target];
  $('title').textContent=meta[0];$('subtitle').textContent=meta[1];
  if($('page-crumb'))$('page-crumb').textContent=meta[0];
  if($('page-step'))$('page-step').textContent=meta[2];
  if(inside)workspaceView(name);
  if(target==='setup'){setupView(setupStage);if(typeof refreshDeviceSetup==='function')refreshDeviceSetup();}
  if(target==='experiments')trainingView(trainingStage);
  if(target==='actions'&&workflowReady)renderActionList();
  if(target==='inference'&&workflowReady)drawInference();
  if(typeof operationsPage==='function')operationsPage(target);
  if(typeof history!=='undefined' && typeof location!=='undefined')history.replaceState(null,'','#'+target);
}
document.querySelectorAll('nav button').forEach(b => b.onclick = () => page(b.dataset.page));
$('browse').onclick = () => page('workspace');
function renderData() { if(typeof renderWorkspaceList==='function')renderWorkspaceList(); }
async function openDataset(d) {
  const id = ++requestId; currentDataset = d.name; $('player').pause(); $('player').removeAttribute('src'); $('player').load();
  $('detail').hidden = false; $('datasets').hidden = true; $('detail-title').textContent = d.name; $('detail-path').textContent = d.path;
  $('episodes').replaceChildren(el('p','正在读取示范边界…')); $('task').textContent = ''; $('preview-label').textContent = '选择一条示范查看'; $('video-error').textContent = '';
  try {
    const eps = await api('/api/episodes?dataset='+encodeURIComponent(d.name)); if (id !== requestId) return;
    sourceEpisodes=eps;
    $('episodes').replaceChildren();
    if (!eps.length) $('episodes').append(el('p','没有示范元信息。'));
    for (const ep of eps) { const b = el('button',`示范 ${ep.index + 1} · 编号 ${ep.index}`,'episode'); b.append(el('span',`${ep.frames} 帧 · ${(ep.frames/d.fps).toFixed(1)} 秒 · 待验收`)); b.dataset.episodeIndex=ep.index; b.onclick = () => preview(ep,b); $('episodes').append(b); }
  } catch(e) { if (id === requestId) { $('episodes').replaceChildren(el('p','读取失败，可返回后重试。')); failure(e); } }
}
function preview(ep, button) {
  document.querySelectorAll('.episode').forEach(b => b.classList.toggle('selected',b === button));
  $('player').pause(); $('video-error').textContent = ''; $('task').textContent = Array.isArray(ep.tasks) ? ep.tasks.join(' / ') : ep.tasks;
  const v = ep.videos.find(v => v.camera.includes('wrist_rgb')) || ep.videos.find(v => !v.camera.includes('depth')) || ep.videos[0];
  if (!v) { $('player').removeAttribute('src'); $('player').load(); $('video-error').textContent = '本条没有视频元信息。'; return; }
  clipStart = v.start; clipEnd = v.end;
  $('preview-label').textContent = `示范 ${ep.index+1} · ${v.camera} · 文件时间 ${v.start.toFixed(1)}–${v.end.toFixed(1)} 秒`;
  $('player').src = '/video?dataset='+encodeURIComponent(currentDataset)+'&path='+encodeURIComponent(v.path)+`#t=${clipStart},${clipEnd}`;
}
$('player').onloadedmetadata = () => { $('player').currentTime = clipStart; };
$('player').ontimeupdate = () => { if ($('player').currentTime >= clipEnd) $('player').pause(); };
$('player').onseeking = () => { const p = $('player'); if (p.currentTime < clipStart-.05) p.currentTime = clipStart; if (p.currentTime > clipEnd+.05) p.currentTime = clipEnd; };
$('player').onplay = () => { if ($('player').currentTime >= clipEnd-.05) $('player').currentTime = clipStart; };
$('player').onerror = () => { if ($('player').getAttribute('src')) $('video-error').textContent = '视频无法播放：可能是浏览器不支持该编码，或文件无法读取。这不等于示范验收失败。'; };
$('back').onclick = () => returnFromPreview();
$('search').oninput = renderData; $('hide-empty').onchange = renderData;
async function refresh() {
  $('refresh').disabled = true; $('refresh').textContent = '检查中…'; $('error').hidden = true;
  try {
    snapshot = await api('/api/status'); $('checks').replaceChildren();
    for (const c of snapshot.checks) { const card = el('article','','check'); card.append(el('h3',c.title.includes('相机')?'相机 USB 识别':c.title),el('div',c.status,'status'+(c.ok?'':' warn')),el('p',c.detail)); if (c.id === 'can') { const b = el('button',snapshot.can_job.running ? '等待启动完成…' : (c.ok ? '检查 CAN' : '启动 CAN'),'primary can-start'); b.id = 'start-can'; b.disabled = snapshot.can_job.running; b.onclick = startCan; card.append(b); card.append(el('p','仅启用通信接口；需要时会弹出系统授权窗口。')); const message = el('p',snapshot.can_job.message || '', 'can-result'); message.id='can-result'; message.setAttribute('role','status'); card.append(message); } $('checks').append(card); }
    const originals = snapshot.datasets.filter(d => d.version?.startsWith('v3') && d.frames);
    $('stats').replaceChildren();
    for (const [label,num,note] of [['原始示范',originals.reduce((a,d)=>a+d.episodes,0),'含历史测试示范；不代表已通过验收'],['数据集目录',snapshot.datasets.length,'包含原始、转换版与空记录'],['主要模型目录',snapshot.models.length,'基础权重、最终参数与完整存档']]) { const c=el('article','','stat'); c.append(el('div',label),el('strong',String(num)),el('span',note)); $('stats').append(c); }
    renderData(); $('model-list').replaceChildren();
    for (const m of snapshot.models) { const c=el('article','','model'); c.append(el('h3',m.name),el('p',`${m.step ? '保存步数 '+m.step+' · ' : ''}${m.params_present ? '参数目录存在' : '参数目录缺失'} · ${m.resume}`),el('p',m.path,'path'),el('p','归一化资产：'+(m.assets.join('；') || '未找到 norm_stats.json'),'path')); $('model-list').append(c); }
    $('updated').textContent = '最近检查 '+new Date().toLocaleTimeString();
    if(workflowReady)drawWorkflow();
    if(typeof refreshDeviceSetup==='function')refreshDeviceSetup();
    if (snapshot.can_job.running && !canPolling) pollCan();
  } catch(e) { failure(e); } finally { $('refresh').disabled=false; $('refresh').textContent='↻ 刷新状态'; }
}
function showCanJob(job) {
  if ($('can-result')) $('can-result').textContent = job.message;
  if ($('start-can')) { $('start-can').disabled = job.running; $('start-can').textContent = job.running ? '等待启动完成…' : '启动 CAN'; }
}
async function startCan() {
  $('start-can').disabled = true;
  try {
    const job = await api('/api/can/start', {method:'POST', headers:{'X-Workbench-Token': snapshot.csrf_token}});
    showCanJob(job); if (!canPolling) pollCan();
  } catch(e) { failure(e); $('start-can').disabled = false; }
}
async function pollCan() {
  canPolling = true;
  try {
    const job = await api('/api/can/status'); showCanJob(job);
    if (job.running) { setTimeout(pollCan,700); return; }
    await refresh();
  } catch(e) { failure(e); if ($('start-can')) $('start-can').disabled = false; }
  canPolling = false;
}
$('refresh').onclick = async()=>{await refresh();await loadLibrary();};

let recordingJob = null, recordingPending = false, stopRequested = null;
const phaseNames = {idle:'未开始',starting:'加载环境',connecting:'连接设备',recording:'正在采集',returning:'回零与失能',saving:'保存中',completed:'已保存 · 待验收',error:'需要检查'};
function drawRecording(job) {
  const finished = recordingJob?.active && !job.active && !job.mode;
  recordingJob = job;
  if(typeof updateWorkspaceRecording==='function')updateWorkspaceRecording(job.mode&&job.mode!=='replay'?{active:false}:job);
  if(typeof drawMode==='function')drawMode(job);
  if(typeof drawReplay==='function')drawReplay(job);
  if(finished) { refresh().then(loadLibrary).then(()=>{if(workspaceGroup===job.collection && !$('workspace').hidden && !$('capture').hidden)page('library');}); }
  if (job.active && !job.mode) { $('capture-task').value=job.task; $('capture-seconds').value=job.seconds; if(job.collection) $('capture-collection').value=job.collection; }
  $('capture-phase').textContent = phaseNames[job.state] || job.state;
  $('capture-message').textContent = job.message;
  $('capture-count').textContent = `${job.frames || 0} 帧 · ${Number(job.elapsed || 0).toFixed(1)} 秒`;
  $('capture-path').textContent = job.root ? '数据位置：'+job.root : '';
  $('capture-start').disabled = job.active || recordingPending || !snapshot || !$('capture-collection').value;
  $('capture-stop').disabled = stopRequested === job.id || !job.active || !['starting','connecting','recording','teleop','gravity','resting'].includes(job.state);
  for (const id of ['capture-task','capture-seconds','capture-prepared','capture-collection']) $(id).disabled = job.active || recordingPending;
  $('capture-view').hidden = !job.saved;
  if (job.state === 'recording' || job.frames > 0) {
    $('capture-image').hidden = false;
    if (job.state === 'recording' || !$('capture-image').getAttribute('src')) $('capture-image').src = '/api/record/preview?t='+Date.now();
    const dual=(job.camera_views||[]).includes('head_rgb');$('capture-head-view').hidden=!dual;
    if(dual&&(job.state==='recording'||!$('capture-head-image').getAttribute('src')))$('capture-head-image').src='/api/record/preview?role=head&t='+Date.now();
    $('capture-image-note').textContent = job.state === 'recording' ? '采集中 · 最新画面' : '采集已结束 · 最后一帧';
  } else { $('capture-image').hidden=true; $('capture-image').removeAttribute('src');$('capture-head-view').hidden=true;$('capture-head-image').removeAttribute('src'); }
  if(workflowReady)updateWorkflowActivity();
  if(typeof drawMemberControls==='function')drawMemberControls();
  if(typeof drawDeviceSetupControls==='function')drawDeviceSetupControls();
  if(typeof drawManagementActivity==='function')drawManagementActivity(job);
}
async function recordPost(action, body) {
  return api('/api/record/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-Workbench-Token':snapshot.csrf_token},body:JSON.stringify(body)});
}
async function pollRecording() {
  try {
    const job = await api('/api/record/status');
    if (job.active && job.mode!=='inference' && snapshot) await recordPost('heartbeat',{id:job.id});
    drawRecording(job);
  } catch(e) { $('capture-message').textContent='无法读取采集状态：'+e.message; }
  setTimeout(pollRecording,1000);
}
$('capture-form').onsubmit = async event => {
  event.preventDefault(); if (recordingPending || recordingJob?.active) return;
  recordingPending=true; $('capture-start').disabled=true;
  $('capture-image').removeAttribute('src');
  try {
    const extra=$('capture-series')?{series_count:Number($('capture-series').value),rest_seconds:Number($('capture-rest').value),auto_convert:$('capture-auto-convert').checked}:{};
    const job=await recordPost('start',{task:$('capture-task').value,seconds:Number($('capture-seconds').value),prepared:$('capture-prepared').checked,collection:$('capture-collection').value,...extra});
    $('capture-prepared').checked=false; drawRecording(job);
  } catch(e) { failure(e); }
  finally { recordingPending=false; if (recordingJob) drawRecording(recordingJob); }
};
$('capture-stop').onclick = async () => {
  if(!recordingJob?.active || stopRequested===recordingJob.id) return;
  stopRequested=recordingJob.id; $('capture-stop').disabled=true;
  try { await recordPost('stop',{id:recordingJob.id}); $('capture-message').textContent='已请求结束，请等待回零与保存完成。'; }
  catch(e) { stopRequested=null; failure(e); }
};
$('capture-view').onclick=async()=>{await refresh(); const d=snapshot.datasets.find(d=>d.name===recordingJob.dataset);if(d)await showSource(d,recordingJob.collection||null);};

$('capture-shortcut').onchange=()=>{const value=$('capture-shortcut').value;localStorage.setItem('b601.stopKey',value);$('shortcut-hint').textContent=value==='none'?'通过页面按钮或定时结束。':`正在采集时按${value==='Space'?'空格':'回车'}结束；输入框内不会触发。`;};
$('capture-shortcut').value=localStorage.getItem('b601.stopKey')||'Space';$('capture-shortcut').onchange();
document.addEventListener('keydown',event=>{
  if(event.repeat||event.isComposing||event.ctrlKey||event.altKey||event.metaKey||event.shiftKey) return;
  if(event.target.closest('input,textarea,select,[contenteditable=true]')) return;
  if(recordingJob?.active && recordingJob.state==='recording' && event.code===$('capture-shortcut').value){event.preventDefault();$('capture-stop').onclick();}
});

// Project navigation composes existing controls. No hardware request is made
// by changing pages, tabs or selecting a model for deployment.
function inferenceRunning(){
  return !!inferenceState?.session?.active&&['queued','connecting','positioning','executing','returning'].includes(inferenceState.session.state);
}
function setupView(stage){
  setupStage=['connections','motion','cameras'].includes(stage)?stage:'connections';
  for(const id of ['connections','motion','cameras'])$('setup-'+id).hidden=id!==setupStage;
  document.querySelectorAll('[data-setup]').forEach(b=>{const active=b.dataset.setup===setupStage;b.classList.toggle('active',active);b.setAttribute('aria-current',active?'step':'false');});
}
function trainingView(stage){
  trainingStage=['train','convert','export'].includes(stage)?stage:'train';
  $('training-export').hidden=trainingStage!=='export';
  $('training-source').hidden=trainingStage==='export';
  $('train-form').hidden=trainingStage!=='train';
  $('training-conversion').hidden=trainingStage!=='convert';
  $('training-next').hidden=trainingStage!=='convert';
  $('training-next-hint').textContent=trainingStage==='convert'?'选择原始目录并检查格式；需要时生成 OpenPI v2.1 副本。':'先检查格式，通过后即可设置本次训练。';
  document.querySelectorAll('[data-training]').forEach(b=>{const active=b.dataset.training===trainingStage;b.classList.toggle('active',active);b.setAttribute('aria-current',active?'step':'false');});
  trainingGuide();
}
function trainingGuide(){
  if(!workflowReady)return;
  const checked=formatReport?.name===$('experiment-source').value;
  const conversion=checked&&formatReport.compatible&&formatReport.needs_conversion;
  $('training-go-convert').hidden=trainingStage!=='train'||!conversion;
  if(checked&&trainingStage==='train')$('training-next-hint').textContent=!formatReport.compatible?'当前数据检查未通过，请先处理格式报告中的问题。':conversion?'这个目录需要转换成 OpenPI v2.1；点击下方按钮进入转换。':'格式检查通过。确认任务与相机视角后，设置本次训练。';
}
let workflowSignature='', modelSignature='';
function drawWorkflow(){
  if(!workflowReady||!snapshot)return;
  const members=libraryData.members.filter(m=>!m.trashed&&!m.source_deleted);
  const can=snapshot.checks.find(c=>c.id==='can');
  const model=inferenceState?.model,session=inferenceState?.session;
  const signature=JSON.stringify([snapshot.checks,members.map(m=>[m.dataset,m.episode,m.review,m.capture_state,m.replay?.status]),libraryData.collections,snapshot.models,inferenceState?.models,model?.state,model?.active,session?.state,session?.active,experimentJob?.state,experimentJob?.active]);
  if(signature!==workflowSignature){
    workflowSignature=signature;
    $('project-device-state').textContent=can?.ok?'● CAN 已启用':'○ CAN 待检查';
    $('project-device-state').classList.toggle('connected',!!can?.ok);
    const summary=$('overview-device-summary');summary.replaceChildren();
    for(const c of snapshot.checks.filter(c=>c.id==='can'||/串口|相机/.test(c.title))){
      const item=el('div','','device-summary-item');item.append(el('span',c.ok?'●':'○',c.ok?'good':'pending'),el('strong',c.title.includes('相机')?'相机 USB':c.title),el('span',c.status));summary.append(item);
    }
    const groups=libraryData.collections.length,ready=members.filter(m=>m.review==='keep'&&['completed','legacy'].includes(m.capture_state)).length;
    const passed=members.filter(m=>m.replay?.status==='passed').length;
    const cards=[
      ['setup','01','设备设置',can?.ok?'CAN 已启用 · 继续检查设备':'从连接与 CAN 设置开始','连接主从臂，验证遥操作与相机。','检查设备'],
      ['actions','02','动作回放',`${passed} 条实机验收通过`,'从已保存示范中选择动作，回放验收。','查看动作'],
      ['workspace','03','数据集',`${groups} 个数据集 · ${ready} 条保留示范`,'单条采集，查看视频，整理与删除。','管理数据'],
      ['experiments','04','训练',experimentJob?.active?'本地任务进行中':experimentJob?.state==='completed'?'最近任务已完成':'准备本次训练','导出保留示范，检查格式，配置训练。','进入训练'],
      ['models','05','模型',`${snapshot.models.filter(m=>m.params_present).length} 份权重与存档`,'查看模型参数与可恢复的训练存档。','查看模型'],
      ['inference','06','运行',inferenceRunning()?'正在执行':model?.active?(model.state==='ready'?'模型已就绪':'模型准备中'):'选择模型开始','实时画面、语言指令与可选 RTC。','进入运行']
    ];
    $('workflow-cards').replaceChildren();
    for(const [dest,number,title,state,desc,label] of cards){
      const card=el('article','','workflow-card'),heading=el('div','','workflow-card-top');heading.append(el('span',number,'step-number'),el('span',state,'stage-state'));card.append(heading,el('h3',title),el('p',desc));
      const button=el('button',label+' →');button.onclick=()=>page(dest);card.append(button);$('workflow-cards').append(card);
    }

    renderDeployableModels();
  }
  updateWorkflowActivity();
}
function renderDeployableModels(){
  const items=inferenceState?.models||[];
  const signature=JSON.stringify(items);if(signature===modelSignature)return;modelSignature=signature;
  const list=$('deployable-models');list.replaceChildren();
  if(!items.length)list.append(el('p','尚无可部署模型。可展开下方查看全部权重与训练存档。','hint'));
  for(const item of items){
    const card=el('article','','deploy-model'),top=el('div','','dataset-top');top.append(el('span','π0.5','model-policy'),el('span','可部署','pill'));card.append(top,el('h3',item.label),el('p','使用配套归一化统计与本机 B601 部署配置。','hint'));
    const details=el('details');details.append(el('summary','模型位置'),el('p',item.path,'path'));card.append(details);
    const run=el('button','选择并进入运行 →','primary');run.onclick=()=>{
      if(inferenceRunning()&&inferenceState.model.model!==item.id){failure(Error('请先结束当前执行再选择模型。'));return;}
      if(inferenceState?.model.active&&inferenceState.model.model!==item.id){page('inference');failure(Error('已有模型正在加载或已加载；请先卸载当前模型，再选择其他模型。'));return;}
      page('inference');$('infer-model').value=item.id;drawInference();
    };card.append(run);list.append(card);
  }
}
function renderActionList(){
  if(!workflowReady)return;
  const query=$('action-search').value.trim().toLowerCase(),filter=$('action-filter').value;
  const all=libraryData.members.filter(m=>!m.trashed&&!m.source_deleted&&['completed','legacy'].includes(m.capture_state));
  const rows=all.filter(m=>{
    const group=groupName(m.collection);const status=m.replay?.status;
    return `${m.task} ${m.dataset} ${group?.name||''}`.toLowerCase().includes(query)&&(filter==='all'||(filter==='unreviewed'?!['passed','failed'].includes(status):status===filter));
  });
  $('action-count').textContent=`${rows.length} 条示范 · 仅列出保存完成或可检查的历史记录`;
  const list=$('action-list');list.replaceChildren();
  if(!rows.length){const empty=el('div','','empty-state');empty.append(el('span','↺','empty-symbol'),el('h3','暂无符合条件的示范'),el('p','在数据集中采集或纳入历史示范后，这里会同步显示。'));list.append(empty);}
  for(const m of rows){
    const card=el('article','','action-card'),top=el('div','','dataset-top');top.append(el('span',groupName(m.collection)?.name||'已有示范','action-source'),el('span',replayLabel(m.replay?.status),m.replay?.status==='passed'?'pill':'pill muted-pill'));
    card.append(top,el('h3',m.task||m.dataset),el('p',`示范 ${m.episode+1} · ${m.frames} 帧`,'hint'));
    const buttons=el('div','','capture-actions'),video=el('button','查看视频'),replay=el('button','检查与回放 →','primary');
    video.onclick=async()=>{try{if(typeof selectLibraryMember==='function'){selectWorkspaceGroup(m.collection);page('library');await selectLibraryMember(m);return;}const d=snapshot.datasets.find(d=>d.name===m.dataset);if(!d)throw Error('源文件不可用，请刷新数据。');await showSource(d,m.collection);const b=[...document.querySelectorAll('.episode')].find(b=>Number(b.dataset.episodeIndex)===m.episode);if(b)b.click();}catch(e){failure(e);}};
    replay.onclick=()=>openReplay(m).catch(failure);buttons.append(video,replay);card.append(buttons);list.append(card);
  }
}
function updateWorkflowActivity(){
  if(!workflowReady)return;
  const recording=recordingJob?.active&&recordingJob.mode!=='inference'?recordingJob:null;
  const running=inferenceRunning();
  const training=experimentJob?.active;
  const visible=running||!!recording&&recording.mode!=='replay'||training;
  $('workflow-activity').hidden=!visible;
  document.body.classList.toggle('has-activity',!!visible);
  if(!visible)return;
  const title=running?'模型正在执行':recording?recording.mode==='camera_preview'?'相机预览中':recording.mode==='teleop'?'遥操作中':recording.mode==='gravity'?'手扶调姿中':recording.state==='processing'?'采后转换中':'数据采集中':'本地任务进行中';
  $('activity-title').textContent=title;$('activity-message').textContent=running?inferenceState.session.message:recording?recording.message:experimentJob.message;
  const source=running?'infer-stop':recording?recording.mode==='camera_preview'?'camera-check-stop':recording.state==='processing'?'experiment-cancel':recording.mode?'mode-stop':'capture-stop':null;
  $('activity-stop').hidden=!source;
  if(source){$('activity-stop').disabled=$(source).disabled;$('activity-stop').textContent=recording?.mode==='camera_preview'?'关闭预览':recording?.state==='processing'?'停止转换':recording&&!recording.mode?'结束并保存':'结束并回位';$('activity-stop').onclick=()=>{if(!$(source).disabled)$(source).click();};}
  $('activity-open').onclick=()=>{if(running)page('inference');else if(recording?.mode==='camera_preview'){page('setup');setupView('cameras');}else if(recording?.state==='processing')page('experiments');else if(recording?.mode)page('modes');else if(recording&&selectWorkspaceGroup(recording.collection))page('capture');else page('experiments');};
  // A refreshed tab must show an already running inference task immediately.
  if(running&&activePage!=='inference')page('inference');
}
function initializeWorkflow(){
  $('setup-motion').append($('modes'));$('modes').classList.remove('page');$('modes').hidden=false;
  const footer=el('div','','step-footer'),back=el('button','← 设备与连接'),next=el('button','下一步：相机 →','primary');
  back.onclick=()=>setupView('connections');next.onclick=()=>setupView('cameras');footer.append(back,next);$('setup-motion').append(footer);
  mountReplayPanel('actions');
  $('action-close').onclick=()=>{$('replay-panel').hidden=true;$('action-close').hidden=true;$('actions').classList.remove('replay-selected');};
  document.querySelectorAll('[data-go]').forEach(b=>b.onclick=()=>page(b.dataset.go));
  document.querySelectorAll('[data-setup]').forEach(b=>b.onclick=()=>setupView(b.dataset.setup));
  document.querySelectorAll('[data-training]').forEach(b=>b.onclick=()=>trainingView(b.dataset.training));
  $('action-search').oninput=renderActionList;$('action-filter').onchange=renderActionList;
  $('action-refresh').onclick=async()=>{await refresh();await loadLibrary();};
  // Classify the existing read-only USB check without changing its backend.
  workflowReady=true;setupView('connections');trainingView('train');drawWorkflow();renderActionList();
  const requested=typeof location==='undefined'?'overview':location.hash.slice(1)||'overview';page(requested);
  if(typeof window.addEventListener==='function')window.addEventListener('hashchange',()=>page(location.hash.slice(1)||'overview'));
}
document.addEventListener('DOMContentLoaded',initializeWorkflow);
