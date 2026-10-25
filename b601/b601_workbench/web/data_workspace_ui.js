// One selected demonstration owns the preview, editable draft and inspection view.
let memberSelection=null,memberRows=[],memberTab='video',memberGeneration=0,memberBusy=false;
let memberClip={start:0,end:Infinity},memberVideos=[],memberReport=null;
const memberDrafts=new Map(),memberEpisodeCache=new Map(),memberReportCache=new Map();
const memberKey=m=>m?`${m.dataset}:${m.episode}`:'';
const activeMember=()=>libraryData.members.find(m=>memberKey(m)===memberKey(memberSelection)&&!m.trashed&&!m.source_deleted);
function saveMemberDraft(){
 const m=activeMember();if(!m||!$('member-review'))return;
 const draft={review:$('member-review').value,note:$('member-note').value,collection:$('member-move').value};
 if(draft.review!==m.review||draft.note!==m.note||draft.collection!==m.collection)memberDrafts.set(memberKey(m),draft);else memberDrafts.delete(memberKey(m));
 $('member-draft-status').textContent=memberDrafts.has(memberKey(m))?'有未保存的修改；切换示范会暂存，点击保存后才写入。':'整理结果已保存。';
}
function renderMemberWorkbench(rows,all){
 saveMemberDraft();
 const query=$('member-search').value.trim().toLowerCase();
 memberRows=rows.filter(m=>`${m.episode+1} ${m.task} ${m.note} ${m.dataset}`.toLowerCase().includes(query));
 const kpis=$('library-kpis');kpis.replaceChildren();
 for(const [value,label] of [[all.filter(m=>!m.trashed).length,'在库示范'],[all.filter(m=>!m.trashed&&m.review==='keep').length,'标记保留'],[all.filter(m=>!m.trashed&&m.replay?.status==='passed').length,'回放验收通过'],[all.filter(m=>!m.trashed&&m.review==='reject').length,'需要重录']]){const item=el('div');item.append(el('strong',String(value)),el('span',label));kpis.append(item);}
 const list=$('library-members');list.replaceChildren();
 for(const m of memberRows){
  const button=el('button','','member-row');button.type='button';button.dataset.memberKey=memberKey(m);
  const top=el('div');top.append(el('strong',`示范 ${m.episode+1}`),el('span',({keep:'保留',reject:'需重录',unreviewed:'未检查'})[m.review]||'未检查','member-review-badge'));
  const fps=snapshot?.datasets.find(d=>d.name===m.dataset)?.fps;
  button.append(top,el('p',m.task||'未记录任务'),el('small',`${m.frames} 帧${fps?' · 约 '+(m.frames/fps).toFixed(1)+' 秒':''}`),el('small',replayLabel(m.replay?.status),'member-replay-badge'));
  button.onclick=()=>selectLibraryMember(m);
  if(typeof memberBatchCheckbox==='function'){const row=el('div','','member-select-wrap');row.append(memberBatchCheckbox(m),button);list.append(row);}else list.append(button);
 }
 if(!memberRows.length)list.append(el('p','没有符合筛选条件的示范。','hint'));
 const current=memberRows.find(m=>memberKey(m)===memberKey(memberSelection));
 if(!current){if(memberRows.length)selectLibraryMember(memberRows[0]);else clearMemberSelection();}
 else{memberSelection=current;fillMemberEditor(current);drawMemberSelection();}
 if(typeof renderBatchToolbar==='function')renderBatchToolbar();
}
function clearMemberSelection(){
 memberGeneration++;memberSelection=null;memberReport=null;$('member-player').pause();$('member-player').removeAttribute('src');$('member-player').load();$('member-workbench').hidden=true;$('member-empty').hidden=false;
 if(typeof mountReplayPanel==='function')mountReplayPanel('actions');
}
function fillMemberEditor(m){
 const draft=memberDrafts.get(memberKey(m));
 $('member-review').value=draft?.review??m.review;$('member-note').value=draft?.note??m.note??'';
 $('member-move').replaceChildren();for(const group of libraryData.collections)$('member-move').append(option(group.id,group.name));$('member-move').value=draft?.collection??m.collection;
 $('member-draft-status').textContent=draft?'有未保存的修改；点击保存后才写入。':'整理结果已保存。';
}
function drawMemberSelection(){
 const m=activeMember();if(!m)return;
 $('member-workbench').hidden=false;$('member-empty').hidden=true;
 $('member-title').textContent=`示范 ${m.episode+1} · ${m.frames} 帧`;$('member-task').textContent=m.task||'未记录任务';$('member-source').textContent=m.dataset;
 $('member-verdict').textContent=replayLabel(m.replay?.status);
 document.querySelectorAll('#library-members .member-row').forEach(b=>{const selected=b.dataset.memberKey===memberKey(m);b.classList.toggle('selected',selected);b.setAttribute('aria-current',selected?'true':'false');});
 drawMemberControls();
}
function drawMemberControls(){
 const m=activeMember();const locked=memberBusy||!!recordingJob?.active||!m;
 for(const id of ['member-save','member-trash','member-rerecord','member-review','member-note','member-move','member-discard'])$(id).disabled=locked;
 if(m&&recordingJob?.active)$('member-feedback').textContent='设备任务正在进行；可以看视频和检查已保存的数据，整理操作请等任务结束。';
}
async function selectLibraryMember(member){
 saveMemberDraft();
 const changed=memberKey(memberSelection)!==memberKey(member);memberSelection=member;const generation=++memberGeneration;
 if(changed){memberTab='video';memberReport=null;$('member-feedback').textContent='';if(typeof mountReplayPanel==='function')mountReplayPanel('actions');}
 fillMemberEditor(member);drawMemberSelection();setMemberTab(memberTab);
 const video=$('member-player');video.pause();video.removeAttribute('src');video.load();memberVideos=[];$('member-camera').replaceChildren();$('member-video-status').textContent='正在读取这条示范的视频边界…';$('member-video-meta').textContent='';
 try{
  let episodes=memberEpisodeCache.get(member.dataset);
  if(!episodes){episodes=await api('/api/episodes?dataset='+encodeURIComponent(member.dataset));memberEpisodeCache.set(member.dataset,episodes);}
  if(generation!==memberGeneration||memberKey(memberSelection)!==memberKey(member))return;
  const episode=episodes.find(e=>e.index===member.episode);if(!episode)throw Error('这条示范不存在或已删除。');
  memberVideos=episode.videos||[];
  for(let i=0;i<memberVideos.length;i++)$('member-camera').append(option(String(i),memberVideos[i].camera));
  const preferred=memberVideos.findIndex(v=>v.camera.includes('wrist_rgb'));$('member-camera').value=String(Math.max(0,preferred));loadMemberVideo();
 }catch(e){if(generation===memberGeneration)$('member-video-status').textContent=e.message;}
}
function loadMemberVideo(){
 const m=activeMember(),video=$('member-player'),source=memberVideos[Number($('member-camera').value)];
 video.pause();video.removeAttribute('src');
 if(!m||!source){video.load();$('member-video-status').textContent='这条示范没有可预览的视频。';return;}
 memberClip={start:source.start,end:source.end};video.dataset.memberKey=memberKey(m);
 video.src='/video?dataset='+encodeURIComponent(m.dataset)+'&path='+encodeURIComponent(source.path)+`#t=${source.start},${source.end}`;
 $('member-video-meta').textContent=`片段 ${source.start.toFixed(1)}–${source.end.toFixed(1)} 秒`;
 $('member-video-status').textContent='点击播放查看；切换示范会停止上一段。';
}
function setMemberTab(tab){
 memberTab=tab;
 document.querySelectorAll('[data-member-tab]').forEach(b=>{const active=b.dataset.memberTab===tab;b.classList.toggle('active',active);b.setAttribute('aria-current',active?'page':'false');});
 for(const name of ['video','checks','edit','replay'])$('member-'+name+'-pane').hidden=name!==tab;
 if(tab!=='video')$('member-player').pause();
 if(tab==='checks')loadMemberInspection();
}
async function openMemberReplay(){
 const m=activeMember();if(!m)return;
 saveMemberDraft();try{await openReplay(m,{inline:true});}catch(e){$('member-feedback').textContent=e.message;}
}
async function loadMemberInspection(force=false){
 const m=activeMember();if(!m)return;const key=memberKey(m),generation=memberGeneration;
 memberReport=null;$('member-check-status').textContent='正在只读检查帧数、数值和文件…';$('member-checks').replaceChildren();$('member-plot').replaceChildren();$('member-check-note').textContent='';$('member-check-again').disabled=true;
 try{
  let report=force?null:memberReportCache.get(key);
  if(!report){report=await api('/api/episode/inspect?dataset='+encodeURIComponent(m.dataset)+'&episode='+m.episode);memberReportCache.set(key,report);}
  if(generation!==memberGeneration||key!==memberKey(memberSelection))return;
  memberReport=report;$('member-check-status').textContent=report.status==='error'?'发现结构或文件问题，请先检查后再决定是否保留。':report.status==='warning'?'基础结构已检查；仍有信息需要人工核对。':'基础结构检查通过；任务结果仍需视频与回放验收。';
  for(const c of report.checks){const row=el('div','','member-check '+c.status);row.append(el('span',c.status==='ok'?'✓':c.status==='error'?'!':'○','check-symbol'),el('strong',c.label),el('p',c.detail));$('member-checks').append(row);}
  $('member-check-note').textContent=report.note;$('member-joint').replaceChildren();report.plot.action.names.forEach((name,i)=>$('member-joint').append(option(String(i),name)));drawMemberPlot();
 }catch(e){if(generation===memberGeneration)$('member-check-status').textContent='检查未完成：'+e.message;}
 finally{if(generation===memberGeneration)$('member-check-again').disabled=false;}
}
function drawMemberPlot(){
 const report=memberReport;if(!report)return;
 const index=Number($('member-joint').value),times=report.plot.times;
 const series=['action','observation.state'].map(key=>report.plot[key].values.map(row=>row[index]));
 const finite=series.flat().filter(v=>typeof v==='number'&&Number.isFinite(v));
 const plot=$('member-plot');plot.replaceChildren();if(!finite.length){plot.append(el('p','这一维没有可绘制的有效值。','hint'));return;}
 const lo=Math.min(...finite),hi=Math.max(...finite),span=Math.max(1,hi-lo),maxTime=Math.max(.01,...times);
 const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 640 220');svg.setAttribute('role','img');svg.setAttribute('aria-label','所选关节的动作与状态曲线');
 const make=(tag,attributes)=>{const n=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [k,v]of Object.entries(attributes))n.setAttribute(k,String(v));svg.append(n);return n;};
 for(let i=0;i<4;i++){const y=20+i*54;make('line',{x1:50,x2:625,y1:y,y2:y,stroke:'#e4ebe7'});make('text',{x:4,y:y+4,fill:'#778a7e','font-size':11}).textContent=(hi-i*span/3).toFixed(1);}
 series.forEach((values,j)=>{let segment=[];const flush=()=>{if(segment.length)make('polyline',{points:segment.join(' '),fill:'none',stroke:j?'#5183ae':'#2e8a61','stroke-width':1.8});segment=[];};values.forEach((value,i)=>{if(value==null||!Number.isFinite(value)){flush();return;}segment.push(`${50+575*Math.max(0,times[i])/maxTime},${20+162*(hi-value)/span}`);});flush();});
 make('text',{x:50,y:209,fill:'#778a7e','font-size':11}).textContent='0 s';make('text',{x:565,y:209,fill:'#778a7e','font-size':11}).textContent=maxTime.toFixed(1)+' s';plot.append(svg);
}
async function mutateMember(action){
 const m=activeMember();if(!m||memberBusy||recordingJob?.active)return;
 memberBusy=true;drawMemberControls();
 try{await action(m);}
 catch(e){$('member-feedback').textContent=e.message;}
 finally{memberBusy=false;drawMemberControls();}
}
$('member-edit-form').onsubmit=event=>{event.preventDefault();saveMemberDraft();const draft=memberDrafts.get(memberKey(activeMember()));if(!draft)return;mutateMember(async m=>{await libraryPost('update',{dataset:m.dataset,episode:m.episode,...draft});memberDrafts.delete(memberKey(m));await loadLibrary();$('library-notice').textContent='整理结果已保存。';$('member-feedback').textContent='整理结果已保存。';});};
$('member-discard').onclick=()=>{const m=activeMember();if(!m)return;memberDrafts.delete(memberKey(m));fillMemberEditor(m);};
$('member-trash').onclick=()=>mutateMember(async m=>{await libraryPost('update',{dataset:m.dataset,episode:m.episode,trashed:true});memberDrafts.delete(memberKey(m));memberReportCache.delete(memberKey(m));memberSelection=null;await syncAfterDelete();$('library-notice').textContent='已移入统一回收站，可恢复。';});
$('member-rerecord').onclick=()=>{const m=activeMember();if(!m||recordingJob?.active)return;saveMemberDraft();selectWorkspaceGroup(m.collection);$('capture-task').value=m.task;$('capture-prepared').checked=false;page('capture');};
$('member-camera').onchange=loadMemberVideo;$('member-search').oninput=renderMembers;
for(const id of ['member-review','member-note','member-move'])$(id).oninput=saveMemberDraft;
$('member-check-again').onclick=()=>loadMemberInspection(true);$('member-joint').onchange=drawMemberPlot;
$('member-player').onloadedmetadata=()=>{if($('member-player').dataset.memberKey===memberKey(memberSelection))$('member-player').currentTime=memberClip.start;};
$('member-player').ontimeupdate=()=>{if($('member-player').currentTime>=memberClip.end)$('member-player').pause();};
$('member-player').onseeking=()=>{const v=$('member-player');if(v.currentTime<memberClip.start-.05)v.currentTime=memberClip.start;if(v.currentTime>memberClip.end+.05)v.currentTime=memberClip.end;};
$('member-player').onplay=()=>{const v=$('member-player');if(v.currentTime>=memberClip.end-.05)v.currentTime=memberClip.start;};
$('member-player').onerror=()=>{if($('member-player').getAttribute('src'))$('member-video-status').textContent='视频暂时无法播放，请检查文件或浏览器编码支持；这不代表实机回放失败。';};
document.querySelectorAll('[data-member-tab]').forEach(b=>b.onclick=()=>b.dataset.memberTab==='replay'?openMemberReplay():setMemberTab(b.dataset.memberTab));
$('library-refresh').onclick=async()=>{saveMemberDraft();memberEpisodeCache.clear();memberReportCache.clear();await refresh();await loadLibrary();if(activeMember())await selectLibraryMember(activeMember());};

document.addEventListener('DOMContentLoaded',()=>{$('library').append($('library-import-panel'));});
