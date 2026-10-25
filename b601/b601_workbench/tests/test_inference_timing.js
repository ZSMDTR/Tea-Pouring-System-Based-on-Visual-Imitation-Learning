// Native-only UI transport, capability gates, and truthful historical records.
// No network, camera, or motor calls.
const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const html=fs.readFileSync(__dirname+'/../web/index.html','utf8'),nodes=new Map(),rendered=[];
for(const id of ['infer-timed','infer-policy-hz','infer-timing-options'])assert.ok(!html.includes(`id="${id}"`));
function n(id){
 assert.ok(id==='.infer-stop'||html.includes(`id="${id}"`),`Missing HTML control: ${id}`);
 if(!nodes.has(id))nodes.set(id,{id,value:'',checked:false,hidden:false,dataset:{},classList:{toggle(){}},append(){},replaceChildren(){}});
 return nodes.get(id);
}
const ctx=vm.createContext({$:n,snapshot:{csrf_token:'test'},recordingJob:{active:false},
 document:{querySelector:n,visibilityState:'visible'},location:{hash:''},
 api:()=>new Promise(()=>{}),el:(tag,text)=>{rendered.push({tag,text});return {append(){}}},setTimeout(){},Date,Number,JSON});
vm.runInContext(fs.readFileSync(__dirname+'/../web/inference_ui.js','utf8'),ctx);
n('infer-model').value='model';n('infer-hz').value='50';n('infer-startup-pose').value='horizontal';
vm.runInContext("inferenceState={limits:{command_hz:250},model:{active:true,state:'ready',rtc_ready:true,message:''},session:{active:true,state:'preview',supports_rtc:true,message:''},history:[]};drawInference()",ctx);
assert.equal(n('infer-execute').disabled,true);
assert.match(n('infer-timing-status').textContent,/关闭预览/);
vm.runInContext("inferenceState.session.state='starting';drawInference()",ctx);
assert.match(n('infer-timing-status').textContent,/等待执行程序就绪/);
vm.runInContext("inferenceState.session.state='preview';inferenceState.session.supports_native_actions=true;drawInference()",ctx);
assert.equal(n('infer-execute').disabled,false);
assert.equal(n('infer-hz').min,1);assert.equal(n('infer-hz').max,250);
assert.match(n('infer-timing-status').textContent,/原生动作.*50 Hz/);
n('infer-hz').value='100';n('infer-hz').onchange();
assert.equal(n('infer-execute').disabled,true);
assert.match(n('infer-timing-status').textContent,/高频下发适配/);
vm.runInContext('inferenceState.session.max_command_hz=250;drawInference()',ctx);
assert.equal(n('infer-execute').disabled,false);
assert.match(n('infer-rate-hint').textContent,/10.0 ms/);
assert.equal(n('infer-hz').value,'100');
assert.equal(n('infer-chunk').disabled,false);
assert.equal(n('infer-rtc-options').hidden,true);
// Native mode applies with either RTC or synchronous chunk generation.
n('infer-rtc').checked=true;n('infer-rtc').onchange();
assert.equal(n('infer-chunk').disabled,true);
assert.equal(n('infer-rtc-options').hidden,false);
assert.match(n('infer-rtc-status').textContent,/前台直接逐条执行/);
vm.runInContext('inferenceState.model.rtc_ready=false;drawInference()',ctx);
assert.equal(n('infer-execute').disabled,true);
vm.runInContext('inferenceState.model.rtc_ready=true;drawInference()',ctx);
assert.equal(n('infer-execute').disabled,false);
n('infer-prompt').value='task';n('infer-prepared').checked=true;
n('infer-seconds').value=30;n('infer-chunk').value=10;n('infer-limit').value=25;n('infer-gripper-limit').value=150;
vm.runInContext('inferenceAction=(action,body)=>{submitted={action,body}}',ctx);
n('rtc-paper').onclick();assert.equal(n('infer-hz').value,'100');
n('infer-form').onsubmit({preventDefault(){}});
assert.equal(ctx.submitted.body.timing_mode,'native');
assert.equal(ctx.submitted.body.hz,100);
assert.equal('policy_hz' in ctx.submitted.body,false);
assert.equal(ctx.submitted.body.rtc.enabled,true);
assert.equal(ctx.submitted.body.rtc.s_min,25);
assert.equal(ctx.submitted.body.startup_pose,'horizontal');
// New motion is explicit, and an older camera worker cannot silently skip it.
n('infer-startup-pose').value='official_center_ready';n('infer-startup-pose').onchange();
assert.equal(n('infer-execute').disabled,true);assert.equal(n('infer-prepared').checked,false);
assert.match(n('infer-startup-status').textContent,/重新启动/);
vm.runInContext('inferenceState.session.supports_working_pose=true;drawInference()',ctx);
assert.equal(n('infer-execute').disabled,true); // v1 preview must restart for the fix.
vm.runInContext('inferenceState.session.working_pose_version=2;drawInference()',ctx);
assert.equal(n('infer-execute').disabled,false);
n('infer-form').onsubmit({preventDefault(){}});
assert.equal(ctx.submitted.body.startup_pose,'official_center_ready');
n('infer-hz').value='250';n('infer-rtc').checked=false;n('infer-rtc').onchange();
n('infer-form').onsubmit({preventDefault(){}});
assert.equal(ctx.submitted.body.timing_mode,'native');
assert.equal(ctx.submitted.body.hz,250);
assert.equal(ctx.submitted.body.rtc.enabled,false);
assert.equal(n('infer-chunk').disabled,false);
assert.equal(n('infer-rtc-options').hidden,true);
// Running task owns controls. Viewing an older interpolated task cannot alter
// its execution or make the next UI request use interpolation.
vm.runInContext("inferenceState.session={active:true,state:'executing',supports_native_actions:true,supports_rtc:true,max_command_hz:250,timing_mode:'native',target_command_hz:100,rtc_enabled:true,rtc_config:{s_min:15},elapsed:1,infer_ms:85,control_hz:99.5,max_action_gap_ms:17,late_interval_percent:1.2};drawInference()",ctx);
assert.equal(n('infer-hz').disabled,true);assert.equal(n('infer-hz').value,100);
assert.equal(n('infer-rtc').disabled,true);assert.equal(n('infer-rtc').checked,true);
assert.match(n('infer-rtc-metrics').textContent,/原生动作.*长间隔 1.2%/);
vm.runInContext("inferenceState.session.state='positioning';inferenceState.session.startup_pose='official_center_ready';inferenceState.session.startup_progress={progress:50,max_error_deg:10};drawInference()",ctx);
assert.equal(n('infer-startup-pose').disabled,true);
assert.equal(n('infer-stop').disabled,false);assert.equal(n('infer-finish').disabled,true);
assert.match(n('infer-startup-status').textContent,/轨迹 50%.*10°/);
assert.equal(n('infer-phase').textContent,'进入工作待命位');
vm.runInContext("inferenceState.session.state='executing';drawInference()",ctx);
vm.runInContext("inferenceState.session.timing_mode='timed';inferenceState.session.policy_hz=30;drawInference()",ctx);
assert.match(n('infer-timing-status').textContent,/此前的插值模式.*30 Hz/);
assert.match(n('infer-rtc-metrics').textContent,/插值.*30 Hz/);
vm.runInContext("inferenceState.session.state='preview';drawInference()",ctx);
n('infer-form').onsubmit({preventDefault(){}});
assert.equal(ctx.submitted.body.timing_mode,'native');
assert.match(n('infer-timing-status').textContent,/原生动作/);
vm.runInContext("inferenceState.history=['timed','native'].map(timing_mode=>({timing_mode,policy_hz:30,hz:100,finished_at:1,prompt:'task',message:'done',steps:10,elapsed:1,relative_limit:5,errors:[]}));drawInference()",ctx);
assert.ok(rendered.some(x=>x.tag==='small'&&x.text.includes('插值 / 动作 30 Hz')));
assert.ok(rendered.some(x=>x.tag==='small'&&x.text.includes('原生动作 / 目标 100 Hz')));
console.log('Native-only UI, RTC/sync transport, rate gates, running controls, and historical modes pass.');
