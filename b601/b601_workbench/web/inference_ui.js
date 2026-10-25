let inferenceState=null,inferencePending=false,inferenceHistory='',inferenceStream='',inferenceHeadStream='';
function inferencePreview(s,fresh){
 const image=$('infer-image');
 const visible=fresh&&!$('inference').hidden&&document.visibilityState==='visible';
 if(visible){
  image.hidden=false;$('infer-placeholder').hidden=true;
  if(s.preview_stream){
   // Keep one connection across status polls; changing src would restart it.
   const url='/api/inference/stream?id='+encodeURIComponent(s.id);
   if(inferenceStream!==url){image.src=url;inferenceStream=url;}
   image.onerror=()=>{inferenceStream='';image.removeAttribute('src');image.hidden=true;$('infer-placeholder').hidden=false;};
  }else{image.src='/api/inference/preview?t='+Date.now();inferenceStream='';}
 }else{
  image.hidden=true;$('infer-placeholder').hidden=false;
  if(inferenceStream){image.removeAttribute('src');inferenceStream='';}
 }
 const head=$('infer-head-image'),hasHead=(s.camera_views||[]).includes('head_rgb');
 const headFresh=s.active&&s.head_preview?.image_at&&Date.now()/1000-s.head_preview.image_at<3;
 $('infer-head-view').hidden=!hasHead;
 const showHead=hasHead&&headFresh&&!$('inference').hidden&&document.visibilityState==='visible';
 $('infer-head-placeholder').hidden=!!showHead;head.hidden=!showHead;
 $('infer-head-label').textContent=headFresh?`● LIVE · ${Number(s.head_preview.preview_fps||0).toFixed(1)} FPS`:'等待新画面';
 if(showHead){const url='/api/inference/stream?id='+encodeURIComponent(s.id)+'&role=head';if(inferenceHeadStream!==url){head.src=url;inferenceHeadStream=url;}head.onerror=()=>{inferenceHeadStream='';head.removeAttribute('src');head.hidden=true;$('infer-head-placeholder').hidden=false;};}
 else if(inferenceHeadStream){head.removeAttribute('src');inferenceHeadStream='';}
}
const inferencePhases={off:'未启动',starting:'加载相机',preview:'等待指令',queued:'接收指令',connecting:'连接从臂',positioning:'进入工作待命位',executing:'执行中',returning:'回零与失能',completed:'已关闭',error:'需要检查'};
async function inferPost(action,body={}){return api('/api/inference/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-Workbench-Token':snapshot.csrf_token},body:JSON.stringify(body)});}
function drawInference(){
 if(!inferenceState)return;
 const m=inferenceState.model,s=inferenceState.session,run=['queued','connecting','positioning','executing','returning'].includes(s.state)&&s.active;
 document.querySelector('.infer-stop').classList.toggle('running',run);
 const disabled=inferencePending||!snapshot;
 $('infer-load').disabled=disabled||m.active||!$('infer-model').value;
 $('infer-unload').disabled=disabled||!m.active||run;
 $('infer-model').disabled=disabled||m.active;
 $('infer-preview').disabled=disabled||s.active||recordingJob?.active;
 $('infer-close').disabled=disabled||!s.active||run;
 $('infer-execute').disabled=disabled||!s.active||s.state!=='preview'||!m.active||m.state!=='ready';
 $('infer-finish').disabled=disabled||s.state!=='executing'||!s.active||!s.steps;
 $('infer-stop').disabled=disabled||!run||s.state==='returning';
 for(const id of ['infer-prompt','infer-continuous','infer-seconds','infer-hz','infer-chunk','infer-limit','infer-gripper-limit','infer-rtc','rtc-s-min','rtc-delay','rtc-beta','rtc-denoise','rtc-buffer','rtc-paper','infer-startup-pose','infer-prepared','infer-preset'])$(id).disabled=run||inferencePending;
 if(run)$('infer-startup-pose').value=s.startup_pose??'horizontal';
 const workingPose=$('infer-startup-pose').value==='official_center_ready';
 const poseUnsupported=workingPose&&s.active&&(!s.supports_working_pose||Number(s.working_pose_version??0)<Number(inferenceState.limits?.working_pose_version??2));
 if(poseUnsupported)$('infer-execute').disabled=true;
 $('infer-startup-status').textContent=s.state==='positioning'?`正在低速进入待命位 · 轨迹 ${s.startup_progress?.progress??0}% · 最大关节误差 ${s.startup_progress?.max_error_deg??'—'}°；${s.startup_progress?.settle_status??'等待稳定到位后模型接管'}${s.startup_progress?.window_motion_deg==null?'':`（窗口内变化 ${s.startup_progress.window_motion_deg}°）`}。`:poseUnsupported?'请关闭相机预览后重新启动，以启用更新后的工作待命位；已加载模型无需重载。':workingPose?'官方待命位：末端前方约 30 cm、高约 30 cm，向下俯视。启动移动约 8 秒，以实际位置连续稳定确认到位后再启动模型；夹爪保持当前位置。正常结束回水平位再失能。':'沿用原有水平位直接启动模型；正常结束回水平位再失能。';
 if(run){$('infer-continuous').checked=!!s.continuous;$('infer-rtc').checked=!!s.rtc_enabled;if(s.target_command_hz!=null)$('infer-hz').value=s.target_command_hz;for(const [id,key] of [['rtc-s-min','s_min'],['rtc-delay','delay_steps'],['rtc-beta','beta'],['rtc-denoise','denoise_steps'],['rtc-buffer','delay_buffer']])if(s.rtc_config?.[key]!=null)$(id).value=s.rtc_config[key];}
 $('infer-hz').min=1;
 $('infer-hz').max=inferenceState.limits?.command_hz??250;
 const highUnsupported=s.active&&Number($('infer-hz').value)>Number(s.max_command_hz??50);
 const nativeUnsupported=s.active&&!s.supports_native_actions;
 if(highUnsupported)$('infer-execute').disabled=true;
 if(nativeUnsupported)$('infer-execute').disabled=true;
 $('infer-timing-status').textContent=run&&s.timing_mode==='timed'?`当前任务沿用此前的插值模式，模型动作 ${s.policy_hz} Hz · 目标下发 ${s.target_command_hz} Hz。`:run&&s.timing_mode==='legacy'?'当前任务沿用旧执行方式：等待新相机帧。':nativeUnsupported?(s.state==='starting'?'正在启动相机，等待执行程序就绪。':'当前预览使用旧程序，请关闭预览后重新启动。'):highUnsupported?(s.state==='starting'?'正在启动相机，等待高频下发适配就绪。':'当前预览尚未启用高频下发适配，请关闭预览后重新启动。'):`原生动作：每次下发直接取一个模型预测动作，目标 ${$('infer-hz').value} Hz；相机独立更新最新画面。`;
 const commandHz=Number($('infer-hz').value);
 $('infer-rate-hint').textContent=`可设置 1–${$('infer-hz').max} Hz。${commandHz>0?`当前每 ${(1000/commandHz).toFixed(1)} ms 下发一次。`:''}提高频率会改变动作时间尺度，先按 30 → 50 → 100 Hz 比较任务效果。高频连续执行建议启用 RTC；设置值不保证实机达到。画面超过 250 ms 未更新会停止。`;
 const rtcEnabled=$('infer-rtc').checked;
 $('infer-rtc-options').hidden=!rtcEnabled;
 for(const id of ['rtc-s-min','rtc-delay','rtc-beta','rtc-denoise','rtc-buffer','rtc-paper'])$(id).disabled=run||inferencePending||!rtcEnabled;
 $('infer-chunk').disabled=run||inferencePending||rtcEnabled;
 if(rtcEnabled&&(!m.rtc_ready||!s.supports_rtc))$('infer-execute').disabled=true;
 $('infer-rtc-status').textContent=!rtcEnabled?'RTC 关闭：同步生成动作段，按“每次执行动作数”逐条执行。':!m.rtc_ready?'RTC 尚未就绪，请重新加载模型并等待预热。'+(m.rtc_error||''):!s.supports_rtc?(s.state==='starting'?'RTC 模型已预热，等待相机预览就绪。':'RTC 模型已预热，请重新启动相机预览。'):run&&s.timing_mode==='timed'?'当前任务使用 RTC 与此前的插值执行模式。':'RTC 已预热；后台异步生成动作段，前台直接逐条执行预测动作。';
 $('infer-rtc-metrics').textContent=(s.control_hz==null?'':`${s.timing_mode==='timed'?`插值 · 模型动作 ${s.policy_hz} Hz · `:s.timing_mode==='native'?'原生动作 · ':''}实际下发 ${s.control_hz} Hz · 最大间隔 ${s.max_action_gap_ms} ms · ${s.late_interval_percent==null?'':`长间隔 ${s.late_interval_percent}% · `}`)+(s.rtc_enabled?`RTC · 已更新 ${s.rtc_replans??0} 段 · 最近延迟 ${s.rtc_delay_steps??0} 步 · 预计延迟 ${s.rtc_delay_estimate??'—'} 步 · 剩余 ${s.rtc_remaining??'—'} 步${s.timing_mode==='timed'?'（插值前模型步）':'（直接动作步）'}`:'同步推理');
 const labels={observe:'关节读取',frame:'腕部缓存',head_frame:'第三视角缓存',policy:'动作获取',send:'电机下发',preview:'预览转换'};
 const timing=Object.entries(s.step_timing_ms||{}).map(([key,v])=>`${labels[key]||key}：平均 ${v.mean} / 最大 ${v.max} ms`);
 $('infer-timing-metrics').textContent=(s.camera_frame_age_ms==null?'':`画面年龄 ${s.camera_frame_age_ms} ms · 本轮最大 ${s.camera_max_frame_age_ms} ms。 `)+(timing.join('；')||'执行后显示各环节的平均与最大耗时。')+'。长间隔指两次下发之间超过目标周期的 1.5 倍；不计首次推理等待与结束后的回零。';
 $('infer-seconds').disabled=run||inferencePending||$('infer-continuous').checked;
 $('infer-mode-hint').textContent=$('infer-continuous').checked?'持续模式：时长限制不生效；点击结束执行或任务完成后回水平位并失能。':'定时模式：到达设定时长后回水平位并失能。';
 $('infer-model-status').textContent=m.message;
 const required=m.camera_contract?.views||['wrist_rgb'],dual=required.includes('head_rgb');
 const missingView=s.active&&required.some(view=>!(s.camera_views||['wrist_rgb']).includes(view));
 if(missingView)$('infer-execute').disabled=true;
 $('infer-camera-inputs').textContent=(dual?'当前模型输入：腕部 + 第三视角。':'当前模型输入：仅腕部；第三视角可预览，新采双视角数据训练后的模型才会使用两路。')+(missingView?' 请启用第三视角并重新打开预览。':'');
 $('infer-session-message').textContent=s.message;
 $('infer-phase').textContent=s.state==='executing'&&s.continuous?'持续执行中':inferencePhases[s.state]||s.state;
 $('infer-elapsed').textContent=s.elapsed==null?'—':s.elapsed.toFixed(1)+' s';
 $('infer-steps').textContent=s.steps??'—';
 $('infer-latency').textContent=s.infer_ms==null?'—':s.infer_ms.toFixed(0)+' ms';
 $('infer-log').textContent=m.log||'尚无模型运行日志。';
 const fresh=s.active&&s.image_at&&Date.now()/1000-s.image_at<3;
 $('infer-camera-label').textContent=fresh?`● LIVE · WRIST RGB${s.preview_stream?' · '+Number(s.preview_fps||0).toFixed(1)+' FPS':''}`:'CAMERA OFF / 等待新画面';
 inferencePreview(s,fresh);
 const history=JSON.stringify(inferenceState.history);
 if(history!==inferenceHistory){inferenceHistory=history;$('infer-history').replaceChildren();
  for(const r of [...inferenceState.history].reverse()){const card=el('article','',`infer-result ${r.outcome}`);card.append(el('strong',r.message),el('p',r.prompt),el('small',`${new Date(r.finished_at*1000).toLocaleTimeString()} · ${r.continuous?'持续模式':'定时模式'} · ${r.startup_pose==='official_center_ready'?'官方待命位启动':'水平位启动'} · ${r.rtc?.enabled?`RTC / n=${r.rtc.denoise_steps} / s=${r.rtc.s_min} / β=${r.rtc.beta}`:'同步推理'} · ${r.timing_mode==='timed'?`插值 / 动作 ${r.policy_hz} Hz / 目标 ${r.hz} Hz · `:r.timing_mode==='native'?`原生动作 / 目标 ${r.hz} Hz · `:'旧执行方式 · '}${r.control_hz==null?'':'实际 '+r.control_hz+' Hz · '}${r.steps} 次下发 · ${r.elapsed} s · 关节 ${r.relative_limit}° / 夹爪 ${r.gripper_relative_limit??r.relative_limit}°`));if(r.startup_pose_result?.verified)card.append(el('p',`待命位已确认 · 启动移动 ${r.startup_pose_result.elapsed} s · 最大关节误差 ${r.startup_pose_result.max_error_deg}°`,'hint'));if(r.errors.length)card.append(el('p',r.errors.join('；'),'deletion-warning'));$('infer-history').append(card);}
  if(!inferenceState.history.length)$('infer-history').append(el('p','执行结束后，在这里查看结果。','hint'));
 }
 if(typeof workflowReady!=='undefined'&&workflowReady)drawWorkflow();
}
async function inferenceAction(action,body={}){
 if(inferencePending)return;inferencePending=true;$('infer-notice').textContent='';drawInference();
 try{await inferPost(action,body);if(action==='execute')$('infer-prepared').checked=false;await fetchInference();}
 catch(e){failure(e);$('infer-notice').textContent=e.message;}
 finally{inferencePending=false;drawInference();}
}
async function fetchInference(){
 inferenceState=await api('/api/inference/status');
 const select=$('infer-model'),old=select.value,items=inferenceState.models;
 if(select.dataset.items!==JSON.stringify(items)){select.replaceChildren();for(const item of items){const o=el('option',item.label);o.value=item.id;select.append(o);}if(items.some(i=>i.id===old))select.value=old;select.dataset.items=JSON.stringify(items);}
 drawInference();
}
$('infer-load').onclick=()=>inferenceAction('load',{model:$('infer-model').value});
$('infer-unload').onclick=()=>inferenceAction('unload',{model_id:inferenceState?.model.id});
$('infer-preview').onclick=()=>inferenceAction('preview');
$('infer-close').onclick=()=>inferenceAction('close',{id:inferenceState?.session.id});
$('infer-preset').onclick=()=>{$('infer-prompt').value=inferenceState?.default_prompt||'';};
$('infer-rtc').onchange=()=>drawInference();
$('infer-hz').onchange=()=>drawInference();
$('infer-startup-pose').onchange=()=>{$('infer-prepared').checked=false;drawInference();};
$('rtc-paper').onclick=()=>{$('rtc-s-min').value=25;$('rtc-delay').value=5;$('rtc-beta').value=5;$('rtc-denoise').value=5;$('rtc-buffer').value=10;};
function rtcRequest(){return $('infer-rtc').checked?{enabled:true,s_min:Number($('rtc-s-min').value),delay_steps:Number($('rtc-delay').value),beta:Number($('rtc-beta').value),denoise_steps:Number($('rtc-denoise').value),delay_buffer:Number($('rtc-buffer').value)}:{enabled:false};}
$('infer-continuous').onchange=()=>drawInference();
$('infer-form').onsubmit=e=>{e.preventDefault();inferenceAction('execute',{id:inferenceState?.session.id,prompt:$('infer-prompt').value,continuous:$('infer-continuous').checked,seconds:$('infer-continuous').checked?30:Number($('infer-seconds').value),hz:Number($('infer-hz').value),timing_mode:'native',startup_pose:$('infer-startup-pose').value,chunk:Number($('infer-chunk').value),relative_limit:Number($('infer-limit').value),gripper_relative_limit:Number($('infer-gripper-limit').value),rtc:rtcRequest(),prepared:$('infer-prepared').checked});};
for(const [id,action] of [['infer-finish','finish'],['infer-stop','stop_task']])$(id).onclick=()=>inferenceAction(action,{id:inferenceState?.session.id,task_id:inferenceState?.session.task_id});
async function pollInference(){
 try{await fetchInference();if(inferenceState.session.active&&snapshot&&!$('inference').hidden&&document.visibilityState==='visible')await recordPost('heartbeat',{id:inferenceState.session.id});}
 catch(e){$('infer-session-message').textContent='连接工作台失败：'+e.message;}
 setTimeout(pollInference,700);
}
pollInference();

if(location.hash==='#inference')page('inference');
