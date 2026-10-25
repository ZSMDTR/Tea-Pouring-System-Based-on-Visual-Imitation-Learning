let formatReport=null,experimentJob=null,experimentPending=false;
async function experimentPost(action,body){return api('/api/experiment/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-Workbench-Token':snapshot.csrf_token},body:JSON.stringify(body)});}
function experimentButtons(){const busy=experimentPending||experimentJob?.active;const valid=formatReport?.name===$('experiment-source').value&&formatReport.compatible;$('experiment-convert').disabled=busy||!valid||!formatReport.needs_conversion;$('train-start').disabled=busy||!valid||formatReport.needs_conversion;$('experiment-inspect').disabled=experimentPending||!snapshot;$('experiment-source').disabled=experimentPending;exportButtons();if(typeof workflowReady!=='undefined'&&workflowReady)trainingGuide();}
async function experimentSources(){await refresh();await exportCollections();const previous=$('experiment-source').value;$('experiment-source').replaceChildren();for(const d of snapshot.datasets.filter(d=>!d.error&&d.frames)){const option=el('option',`${d.export_test?"[测试导出] ":""}${d.export_label?d.export_label+" — ":""}${d.name} · ${d.version} · ${d.episodes} 条`);option.value=d.name;$('experiment-source').append(option);}if([...$('experiment-source').options].some(o=>o.value===previous))$('experiment-source').value=previous;formatReport=null;$('experiment-report').textContent='请选择目录并检查格式。';experimentButtons();}
$('experiment-source').onchange=()=>{formatReport=null;$('experiment-report').textContent='选择已改变，请重新检查。';$('train-ack').checked=false;experimentButtons();trainingOptions();};
$('experiment-refresh').onclick=()=>experimentSources().catch(failure);
$('experiment-inspect').onclick=async()=>{experimentPending=true;experimentButtons();try{formatReport=await experimentPost('inspect',{dataset:$('experiment-source').value});$('experiment-report').textContent=[`${formatReport.version} · ${formatReport.episodes} 条 · ${formatReport.frames} 帧`,formatReport.camera_label||'','新训练自动使用数据中保存的视角；继续训练沿用存档的输入约定。',...formatReport.issues,...formatReport.warnings,formatReport.contract].join('\n');}catch(e){formatReport=null;failure(e);}finally{experimentPending=false;experimentButtons();trainingOptions();}};
async function beginExperiment(kind){experimentPending=true;experimentButtons();try{experimentJob=await experimentPost(kind,{dataset:$('experiment-source').value,steps:Number($('train-steps').value),batch:Number($('train-batch').value),acknowledged:$('train-ack').checked,save_mode:$('train-save-mode').value,save_interval:Number($('train-interval').value),resume_checkpoint:$('train-save-mode').value==='full'?$('train-resume').value:''});drawExperiment();}catch(e){failure(e);}finally{experimentPending=false;experimentButtons();}}
$('experiment-convert').onclick=()=>beginExperiment('convert');
$('train-form').onsubmit=event=>{event.preventDefault();beginExperiment('train');};
function drawExperiment(){const p=experimentJob.progress;$('experiment-progress').textContent=p?`${p.phase} · ${p.total?'已检查 '+p.step+' / '+p.total+' 条':p.frames?'已读取 '+p.frames+' 帧':'已完成 '+p.step+' 步'}${p.checkpoint?' · 最近完整存档：'+p.checkpoint:''}`:'';$('experiment-status').textContent=experimentJob.message;$('experiment-output').textContent=experimentJob.output||'';$('experiment-log').textContent=experimentJob.log||'';experimentButtons();if(typeof workflowReady!=='undefined'&&workflowReady)drawWorkflow();}
async function pollExperiments(){try{const previous=experimentJob;experimentJob=await api('/api/experiment/status');drawExperiment();if(previous?.active&&!experimentJob.active){await experimentSources();await refreshCheckpoints();if(experimentJob.dataset_output){$('experiment-source').value=experimentJob.dataset_output;$('experiment-inspect').click();}}}catch(e){$('experiment-status').textContent=e.message;}setTimeout(pollExperiments,2000);}
async function modePost(action,body){return api('/api/mode/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-Workbench-Token':snapshot.csrf_token},body:JSON.stringify(body)});}
let modePending=false;
function drawMode(job){$('mode-teleop').disabled=$('mode-gravity').disabled=modePending||job.active||!snapshot;$('mode-stop').disabled=!job.active||!job.mode||job.state==='returning';$('mode-save').disabled=!job.active||job.mode!=='gravity'||job.state!=='gravity'||job.lock_state!=='LOCKED';$('mode-status').textContent=job.mode?job.message:(job.active?'采集正在占用设备。':'尚未启动机械臂模式。');if(job.pose_message)$('mode-pose').textContent=job.pose_message;}
for(const mode of ['teleop','gravity'])$('mode-'+mode).onclick=async()=>{modePending=true;drawMode(recordingJob||{});try{drawRecording(await modePost('start',{mode,prepared:$('mode-prepared').checked}));$('mode-prepared').checked=false;}catch(e){failure(e);}finally{modePending=false;drawMode(recordingJob||{});}};
$('mode-stop').onclick=async()=>{try{await recordPost('stop',{id:recordingJob.id});$('mode-status').textContent='已请求退出，请等待回零结束。';}catch(e){failure(e);}};
$('mode-save').onclick=async()=>{try{const r=await modePost('save',{id:recordingJob.id});$('mode-pose').textContent=r.message;}catch(e){failure(e);}};
api('/api/mode/pose').then(p=>{if(p)$('mode-pose').textContent='已有候选姿态，尚未启用：'+Object.entries(p.motor_degrees).map(([k,v])=>`${k} ${v.toFixed(1)}°`).join('，');}).catch(failure);
let exportPlan=null,exportPending=false;
async function exportPost(action,body){return api('/api/export/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-Workbench-Token':snapshot.csrf_token},body:JSON.stringify(body)});}
function exportButtons(){$('export-start').disabled=exportPending||experimentPending||experimentJob?.active||!exportPlan?.episodes||exportPlan.collection!==$('export-collection').value;$('export-preview').disabled=exportPending||!snapshot||!$('export-collection').value;$('export-collection').disabled=exportPending;}
async function exportCollections(){const data=await api('/api/library');const selected=$('export-collection').value;$('export-collection').replaceChildren();for(const g of data.collections){const option=el('option',g.name);option.value=g.id;$('export-collection').append(option);}if(data.collections.some(g=>g.id===selected))$('export-collection').value=selected;exportPlan=null;exportButtons();}
$('export-collection').onchange=()=>{exportPlan=null;$('export-items').replaceChildren();$('export-summary').textContent='选择已改变，请重新预览清单。';exportButtons();};
$('export-preview').onclick=async()=>{exportPending=true;exportButtons();try{exportPlan=await exportPost('plan',{collection:$('export-collection').value});$('export-summary').textContent=`纳入 ${exportPlan.episodes} 条 · ${exportPlan.frames} 帧，排除 ${exportPlan.excluded} 条。`+(exportPlan.episodes?'':'请先在数据工作区将可用示范标记为保留。');$('export-items').replaceChildren();for(const item of exportPlan.items)$('export-items').append(el('li',`${item.dataset} / 示范 ${item.episode+1} · ${item.frames} 帧 · ${item.tasks.join(' / ')}`));}catch(e){exportPlan=null;failure(e);}finally{exportPending=false;exportButtons();}};
$('export-start').onclick=async()=>{if(!exportPlan||exportPending)return;exportPending=true;exportButtons();try{experimentJob=await exportPost('start',{collection:exportPlan.collection,digest:exportPlan.digest});drawExperiment();}catch(e){failure(e);}finally{exportPending=false;exportButtons();}};
$('workspace-export').onclick=async()=>{page('experiments');trainingView('export');await exportCollections();if(workspaceGroup)$('export-collection').value=workspaceGroup;$('export-preview').click();};
let fullCheckpoints=[];
function trainingOptions(){
 const full=$('train-save-mode').value==='full';$('train-full-options').hidden=!full;$('train-interval').disabled=!full;
 $('train-batch').disabled=full;if(full)$('train-batch').value=1;
 const saved=fullCheckpoints.find(c=>c.id===$('train-resume').value);
 $('train-start').textContent=full&&saved?'继续训练':'开始训练';
 const steps=Number($('train-steps').value),interval=Number($('train-interval').value),start=saved?.step||0;
 const saves=interval>0&&steps>start?Math.floor(steps/interval)-Math.floor(start/interval)+(steps%interval?1:0):0;
 $('train-save-summary').textContent=full?`${saved?'从第 '+start+' 步恢复':'从基础模型开始'}；本次预计保存 ${saves} 份，约 ${saves*50} GiB。目标总步数包含之前已完成的步数。中断后可回到最近完整存档。`:'仅在正常结束时保存推理参数；不支持恢复训练，中断会丢失本次进度。';
 $('train-save-summary').textContent+=' 输入视角：'+(saved?saved.camera_label||'单视角 · 腕部':formatReport?.camera_label||'检查所选数据格式后确定')+'。';
}
async function refreshCheckpoints(){
 const selected=$('train-resume').value;fullCheckpoints=await api('/api/experiment/checkpoints');
 $('train-resume').replaceChildren();const first=el('option','从基础模型开始');first.value='';$('train-resume').append(first);
 for(const c of fullCheckpoints){const o=el('option',`${c.dataset} · 第 ${c.step} 步 · ${c.camera_label||'单视角 · 腕部'} · ${c.id.split('/')[0]}`);o.value=c.id;$('train-resume').append(o);}
 if(fullCheckpoints.some(c=>c.id===selected))$('train-resume').value=selected;trainingOptions();
}
$('train-save-mode').onchange=()=>{trainingOptions();$('train-ack').checked=false;};
$('train-resume').onchange=()=>{const c=fullCheckpoints.find(c=>c.id===$('train-resume').value);if(c){$('experiment-source').value=c.dataset;$('experiment-source').onchange();$('train-steps').value=Math.max(Number($('train-steps').value),c.step+1);}trainingOptions();$('train-ack').checked=false;};
for(const id of ['train-steps','train-interval'])$(id).oninput=trainingOptions;
$('train-checkpoints-refresh').onclick=()=>refreshCheckpoints().catch(failure);
refreshCheckpoints().catch(failure);trainingOptions();
// Initial reads have no hardware side effects. Load selectors when requested.
$('experiment-refresh').click();pollExperiments();
