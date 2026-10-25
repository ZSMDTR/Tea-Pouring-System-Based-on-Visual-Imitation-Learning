// Device discovery reads OS metadata and saved configuration, without opening devices.
let deviceSetupState=null,deviceSetupPending=false;
function setupStatusLine(label,value,ok){const row=el('div','','setup-status-line');row.append(el('span',label),el('strong',value,ok?'status-good':'status-pending'));return row;}
async function refreshDeviceSetup(){
 if(deviceSetupPending)return;deviceSetupPending=true;$('setup-scan').disabled=true;
 try{deviceSetupState=await api('/api/setup/status');renderDeviceSetup();}
 catch(e){deviceSetupState=null;$('setup-check-summary').textContent='设备信息读取失败：'+e.message;drawDeviceSetupControls();}
 finally{deviceSetupPending=false;$('setup-scan').disabled=false;}
}
function renderDeviceSetup(){
 const d=deviceSetupState;if(!d)return;
 const p=d.profile,can=d.can,cal=d.calibrations.every(c=>c.exists);
 $('setup-check-summary').textContent=d.session.active?'设备正在使用中：'+d.session.message:`${can.ready?'CAN 已启用':'CAN 待启用'} · ${d.leader_present?'引导臂端口存在':'引导臂端口未找到'} · ${cal?'已找到两份标定文件':'标定文件需检查'}。读取连接信息不会使能电机。`;
 const list=$('setup-bindings');list.replaceChildren();
 const leader=el('article','','preview');leader.append(el('span','LEADER / 引导臂','eyebrow'),el('h3','reBot Arm 102'),setupStatusLine('当前端口',p.leader_port,d.leader_present),el('p','候选端口：'+(d.ports.join('、')||'未发现'),'hint'));
 const follower=el('article','','preview');follower.append(el('span','FOLLOWER / 从臂','eyebrow'),el('h3','B601 RS'),setupStatusLine('通信接口',p.can_port,can.ready),el('p',can.ready?'通信接口已启用；实际跟随请在下一步观察。':can.error||'启用 CAN 后再开始遥操作或采集。','hint'));
 const start=el('button',can.ready?'重新检查 CAN':'启动 CAN','primary');start.id='setup-start-can';start.onclick=async()=>{start.disabled=true;await startCan();drawDeviceSetupControls();};follower.append(start);
 list.append(leader,follower);$('setup-calibrations').replaceChildren();
 for(const c of d.calibrations){const row=el('article','','calibration-card');row.append(setupStatusLine(c.role,c.exists?'标定文件已找到':'缺少标定文件',c.exists),el('p',c.id,'hint'));if(c.exists)row.append(el('small','文件更新：'+new Date(c.modified_at*1000).toLocaleString()));const info=el('details');info.append(el('summary','文件位置'),el('p',c.path,'path'));row.append(info);$('setup-calibrations').append(row);}
 const cameraCards=$('setup-camera-cards');cameraCards.replaceChildren();
 const wrist=el('article','','preview camera-config'),cfg=p.camera;
 wrist.append(el('span',d.wrist?'USB 已识别':'当前相机未找到',d.wrist?'pill':'pill muted-pill'),el('h3','腕部相机 · '+(d.wrist?.name||'已配置相机')),el('p',cfg.serial_number_or_name,'path'));
 const values=el('dl');for(const [key,value] of [['相机输出',`${cfg.width} × ${cfg.height} / ${cfg.fps} FPS`],['保存画面',`${cfg.output_width||cfg.width} × ${cfg.output_height||cfg.height}`],['缩放方式',cfg.resize_mode==='letterbox'?'等比补边':'沿用采集配置']])values.append(el('dt',key),el('dd',value));wrist.append(values);
 const preview=el('button','去运行页预览相机 →');preview.onclick=()=>page('inference');wrist.append(preview);cameraCards.append(wrist);
 for(const camera of d.cameras.filter(c=>c!==d.wrist && c.serial!==d.wrist?.serial)){
  const enabled=!!p.secondary_enabled&&camera.serial===p.secondary_camera?.serial_number_or_name;
  const extra=el('article','','preview camera-config');extra.append(el('span',enabled?'第三视角 · 新采集已启用':'已发现 · 未启用第三视角采集','pill muted-pill'),el('h3',camera.name),el('p',camera.serial||'没有序列号','path'),el('p',enabled?'新示范记录两路视角，新训练会共同使用；旧模型仍使用腕部画面。':'可在下方配置角色并打开预览，确认画面后再启用新采集。','hint'));cameraCards.append(extra);
 }
 $('setup-camera-usb').textContent='检查时间：'+new Date(d.checked_at*1000).toLocaleTimeString()+' · USB 识别不代表视频流已验证。';
 drawDeviceSetupControls();
 if(typeof renderDeviceEditor==='function')renderDeviceEditor(d);
}
function drawDeviceSetupControls(){
 const d=deviceSetupState;if(!d)return;
 const active=!!recordingJob?.active;
 const ready=d.can.ready&&d.leader_present&&d.calibrations.every(c=>c.exists);
 if(!ready&&!active)$('mode-teleop').disabled=true;
 if((!d.can.ready||!d.calibrations.some(c=>c.role==='从臂'&&c.exists))&&!active)$('mode-gravity').disabled=true;
 const message=active?recordingJob.message:!d.can.ready?'请先在“设备与连接”启用 CAN。':!d.leader_present?'当前引导臂端口不存在，请检查 USB 连接后重新检查。':!d.calibrations.every(c=>c.exists)?'缺少标定文件；请先检查原有标定配置。':'连接与标定文件检查完成。开始遥操作后，小幅移动引导臂，人工观察方向和跟随情况。';
 $('setup-motion-readiness').textContent=message;
 const button=$('setup-start-can');if(button)button.disabled=!!$('start-can')?.disabled||active;
}
$('setup-scan').onclick=async()=>{await refresh();await refreshDeviceSetup();};
document.addEventListener('DOMContentLoaded',refreshDeviceSetup);
