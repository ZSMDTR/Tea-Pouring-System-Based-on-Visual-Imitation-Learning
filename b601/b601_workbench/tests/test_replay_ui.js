// Verify human acceptance and manual stop remain distinct from completion.
const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const nodes=new Map();
function n(id){if(!nodes.has(id))nodes.set(id,{id,hidden:false,checked:false,disabled:false,children:[],value:'',textContent:'',replaceChildren(){this.children=[]},append(v){this.children.push(v)},scrollIntoView(){}});return nodes.get(id);}
const ctx=vm.createContext({$:n,recordingJob:{active:false},libraryData:{members:[]},snapshot:{csrf_token:'token'},stopRequested:null,el:()=>({}),loadLibrary(){},recordPost:async()=>({}),failure(e){throw e;},api:async()=>({}),selectWorkspaceGroup(){},page(){},Date});
vm.runInContext(fs.readFileSync(__dirname+'/../web/replay_ui.js','utf8'),ctx);
vm.runInContext("replaySelection={dataset:'raw',episode:0};replayPlan={legacy:true};replayControls()",ctx);
assert.equal(n('replay-start').disabled,true);n('replay-prepared').checked=true;
vm.runInContext('replayControls()',ctx);assert.equal(n('replay-start').disabled,true);
n('replay-legacy').checked=true;vm.runInContext('replayControls()',ctx);assert.equal(n('replay-start').disabled,false);
ctx.recordingJob={id:'a',mode:'replay',dataset:'raw',episode:0,state:'replaying',active:true,frames:2,total_frames:4};
vm.runInContext('drawReplay(recordingJob)',ctx);assert.equal(n('replay-start').disabled,true);assert.equal(n('replay-stop').disabled,false);assert.equal(n('replay-running-bar').hidden,false);assert.equal(n('replay-review').hidden,true);
ctx.recordingJob={...ctx.recordingJob,state:'stopped',active:false,return_completed:true};vm.runInContext('drawReplay(recordingJob)',ctx);assert.equal(n('replay-review').hidden,true);
ctx.recordingJob={...ctx.recordingJob,state:'completed',frames:4};vm.runInContext('drawReplay(recordingJob)',ctx);assert.equal(n('replay-review').hidden,false);assert.equal(n('replay-pass').disabled,false);assert.equal(n('replay-running-bar').hidden,true);
console.log('Replay preparation, historical confirmation, persistent stop and human acceptance gates pass.');
