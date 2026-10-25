// Source preview and human acceptance are separate from motor execution.
let replaySelection=null,replayPlan=null,replayJob=null,replayBusy=false,replayRequest=0,replayFinishedId=null;
function replayLabel(status){return ({passed:'Replay 验收通过',failed:'Replay 未成功 · 需重录',pending:'Replay 已结束 · 待人工验收',running:'Replay 进行中',stopped:'Replay 已中断 · 未验收',error:'Replay 异常 · 未验收',stale:'源文件已变化 · 需重新 Replay'})[status]||'Replay 未验收';}
async function replayPost(action,body){return api('/api/replay/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-Workbench-Token':snapshot.csrf_token},body:JSON.stringify(body)});}
function sameReplay(a,b){return a&&b&&a.dataset===b.dataset&&a.episode===b.episode;}
function mountReplayPanel(host){
  const inline=host==='member';
  const target=$(inline?'member-replay-slot':'action-player');
  if(target)target.append($('replay-panel'));
  if(typeof workflowReady!=='undefined'&&workflowReady){$('actions').classList.toggle('replay-selected',!inline&&!$('replay-panel').hidden);$('action-close').hidden=inline||$('replay-panel').hidden;}
}
function selectedReplayMember(){return libraryData.members.find(m=>sameReplay(m,replaySelection));}
function replayControls(){
  const active=!!recordingJob?.active,matching=sameReplay(replayJob,replaySelection);
  $('replay-start').disabled=replayBusy||active||!replayPlan||!$('replay-prepared').checked||(replayPlan.legacy&&!$('replay-legacy').checked);
  $('replay-prepared').disabled=active||replayBusy;$('replay-legacy').disabled=active||replayBusy;
  $('replay-stop').disabled=!active||recordingJob?.mode!=='replay'||stopRequested===recordingJob.id;
  const member=selectedReplayMember(),verdict=member?.replay?.status;
  const complete=matching?replayJob.state==='completed'&&replayJob.return_completed:verdict==='pending'||verdict==='passed'||verdict==='failed';
  $('replay-review').hidden=!complete;
  for(const id of ['replay-pass','replay-fail'])$(id).disabled=active||replayBusy||!complete;
  $('replay-inspect').disabled=replayBusy||active;
}
async function inspectReplay(){
  if(!replaySelection||replayBusy)return;
  const version=++replayRequest;replayBusy=true;replayPlan=null;$('replay-prepared').checked=false;$('replay-legacy').checked=false;
  $('replay-plan').textContent='正在只读检查这一条的关节数据、时间记录和配置…';replayControls();
  try{
    const result=await replayPost('plan',{dataset:replaySelection.dataset,episode:replaySelection.episode});
    if(version!==replayRequest)return;replayPlan=result;
    $('replay-plan').textContent=`${result.frames} 帧 · 约 ${result.duration_s.toFixed(1)} 秒 · ${result.clock_source}。先低速进入起始动作目标，再逐条回放；结束回水平位并失能。`;
    $('replay-warnings').replaceChildren();for(const warning of result.warnings)$('replay-warnings').append(el('p',warning,'hint'));
    $('replay-legacy-label').hidden=!result.legacy;
    $('replay-pose').textContent=Object.entries(result.start_motor_degrees).map(([n,v])=>`${n}: ${v.toFixed(1)}°`).join(' · ');
  }catch(e){$('replay-plan').textContent=e.message;$('replay-warnings').replaceChildren();$('replay-pose').textContent='';}
  finally{replayBusy=false;replayControls();}
}
async function openReplay(member,{inline=false}={}){
  if(replayBusy)return;
  if(recordingJob?.active&&recordingJob.mode==='replay'&&!sameReplay(member,recordingJob))throw Error('请先结束当前 Replay。');
  replaySelection={dataset:member.dataset,episode:member.episode};replayPlan=null;
  selectWorkspaceGroup(member.collection);page(inline?'library':'actions');$('replay-panel').hidden=false;mountReplayPanel(inline?'member':'actions');if(inline&&typeof setMemberTab==='function')setMemberTab('replay');
  $('replay-title').textContent=`示范 ${member.episode+1} · ${member.task||member.dataset}`;
  $('replay-message').textContent=member.replay?replayLabel(member.replay.status):'尚未进行实机 Replay。预览检查不会连接电机。';
  $('replay-note').value=member.replay?.note||'';$('replay-image').hidden=true;$('replay-count').textContent='';
  if(typeof loadReplaySourceVideo==='function')loadReplaySourceVideo(member);
  if(recordingJob?.mode==='replay'&&sameReplay(recordingJob,member))drawReplay(recordingJob);
  else replayJob=null;
  $('replay-panel').scrollIntoView({behavior:'smooth',block:'start'});
  if(!(recordingJob?.active&&recordingJob.mode==='replay')){
    await inspectReplay();
    if(recordingJob?.active)$('replay-message').textContent='只读检查完成。设备正在使用中，请先结束采集、机械臂模式或关闭推理相机预览，再启动 Replay。';
  }else replayControls();
}
function drawReplay(job){
  $('replay-running-bar').hidden=!(job.active&&job.mode==='replay');
  if(job.mode==='replay'){
    replayJob=job;
    $('replay-running-message').textContent=`Replay · ${job.frames||0} / ${job.total_frames||0} 帧 · ${job.message}`;
    $('replay-running-stop').disabled=stopRequested===job.id||job.state==='returning';
    if(job.active&&!replaySelection){
      replaySelection={dataset:job.dataset,episode:job.episode};if(typeof workflowReady!=='undefined'&&workflowReady){$('actions').classList.add('replay-selected');$('action-close').hidden=false;}$('replay-panel').hidden=false;
      $('replay-title').textContent=`示范 ${job.episode+1} · ${job.dataset}`;
    }
    if(sameReplay(job,replaySelection)){
      const accepted=selectedReplayMember()?.replay;
      $('replay-message').textContent=job.state==='completed'&&accepted?.id===job.id&&['passed','failed','stale'].includes(accepted.status)?replayLabel(accepted.status):job.message;
      $('replay-count').textContent=`${job.frames||0} / ${job.total_frames||0} 帧 · ${Number(job.elapsed||0).toFixed(1)} 秒`;
      if(typeof syncReplaySourceVideo==='function')syncReplaySourceVideo(job);
      if(job.active&&['positioning','replaying','returning'].includes(job.state)){$('replay-image').hidden=false;$('replay-image').src='/api/record/preview?t='+Date.now();}
      if(!job.active&&replayFinishedId!==job.id){replayFinishedId=job.id;loadLibrary();}
    }
  }
  if(replaySelection)replayControls();
}
async function stopReplay(){
  if(!recordingJob?.active||recordingJob.mode!=='replay'||stopRequested===recordingJob.id)return;
  stopRequested=recordingJob.id;replayControls();$('replay-running-stop').disabled=true;
  try{await recordPost('stop',{id:recordingJob.id});$('replay-message').textContent='已请求停止，请等待回水平位并失能。';}
  catch(e){stopRequested=null;failure(e);replayControls();}
}
$('replay-start').onclick=async()=>{
  if($('replay-start').disabled)return;
  replayBusy=true;replayControls();
  try{
    const job=await replayPost('start',{...replaySelection,digest:replayPlan.digest,prepared:$('replay-prepared').checked,legacy_confirmed:$('replay-legacy').checked});
    $('replay-prepared').checked=false;replayFinishedId=null;drawRecording(job);
  }catch(e){$('replay-message').textContent=e.message;}
  finally{replayBusy=false;replayControls();}
};
async function acceptReplay(verdict){
  const attempt=sameReplay(replayJob,replaySelection)?replayJob.id:selectedReplayMember()?.replay?.id;
  if(!attempt||replayBusy)return;replayBusy=true;replayControls();
  try{const result=await replayPost('review',{...replaySelection,id:attempt,verdict,note:$('replay-note').value});await loadLibrary();$('replay-message').textContent=result.message;}
  catch(e){$('replay-message').textContent=e.message;}
  finally{replayBusy=false;replayControls();}
}
$('replay-pass').onclick=()=>acceptReplay('passed');$('replay-fail').onclick=()=>acceptReplay('failed');
$('replay-stop').onclick=stopReplay;$('replay-running-stop').onclick=stopReplay;$('replay-inspect').onclick=inspectReplay;
$('replay-prepared').onchange=replayControls;$('replay-legacy').onchange=replayControls;
$('replay-running-open').onclick=()=>{const m=libraryData.members.find(m=>sameReplay(m,recordingJob));if(m)openReplay(m).catch(failure);};
$('replay-image').onerror=()=>{$('replay-image').hidden=true;};
