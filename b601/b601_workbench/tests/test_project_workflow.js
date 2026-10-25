// Navigation composes existing controls; it cannot start hardware or training.
const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const nodes=new Map(),listeners={};
function node(id){
 if(!nodes.has(id)){const classes=new Set();nodes.set(id,{id,dataset:{},value:'',checked:false,disabled:false,hidden:false,textContent:'',children:[],classList:{toggle(k,on){if(on)classes.add(k);else classes.delete(k)},add(k){classes.add(k)},remove(k){classes.delete(k)},contains(k){return classes.has(k)}},setAttribute(){},removeAttribute(){},getAttribute(){return null},pause(){},load(){},click(){if(!this.disabled)this.onclick?.()},replaceChildren(...n){this.children=n},append(...n){this.children.push(...n)}});}return nodes.get(id);
}
const pages=['overview','setup','actions','workspace','experiments','models','inference','roadmap'].map(node);
const nav=pages.map(p=>({...node('nav-'+p.id),dataset:{page:p.id}}));
const ctx=vm.createContext({document:{getElementById:node,querySelectorAll:s=>s==='.page'?pages:s==='nav button'?nav:[],addEventListener:(name,handler)=>listeners[name]=handler,createElement:()=>node(Symbol()),body:node('body')},localStorage:{getItem:()=>null,setItem(){}},window:{scrollTo(){}},setTimeout(){},inferenceState:{session:{active:false},models:[]},experimentJob:null,formatReport:null,drawInference(){},workspaceView(name){ctx.workspaceTarget=name},libraryData:{members:[],collections:[]},console});
vm.runInContext(fs.readFileSync(__dirname+'/../web/ui.js','utf8'),ctx);
vm.runInContext("workflowReady=true;page('inference')",ctx);
ctx.inferenceState.session={active:true,state:'executing'};
vm.runInContext("page('workspace')",ctx);
assert.equal(node('inference').hidden,false);assert.equal(node('workspace').hidden,true);
assert.match(node('error').textContent,/等待回位/);
ctx.inferenceState.session.state='returning';vm.runInContext("page('setup')",ctx);assert.equal(node('inference').hidden,false);
ctx.inferenceState.session.state='preview';vm.runInContext("page('modes')",ctx);
assert.equal(node('setup').hidden,false);assert.equal(node('setup-motion').hidden,false);assert.equal(node('setup-connections').hidden,true);
vm.runInContext("page('sources')",ctx);assert.equal(node('workspace').hidden,false);assert.equal(ctx.workspaceTarget,'sources');
node('train-batch').value='32';node('infer-hz').value='120';node('infer-rtc').checked=true;
vm.runInContext("trainingView('export');page('overview');page('experiments')",ctx);
assert.equal(node('training-export').hidden,false);assert.equal(node('train-form').hidden,true);assert.equal(node('training-source').hidden,true);
vm.runInContext("trainingView('train')",ctx);assert.equal(node('train-form').hidden,false);assert.equal(node('training-source').hidden,false);
assert.equal(node('train-batch').value,'32');assert.equal(node('infer-hz').value,'120');assert.equal(node('infer-rtc').checked,true);
ctx.formatReport={name:'raw',compatible:true,needs_conversion:true};node('experiment-source').value='raw';
vm.runInContext('trainingGuide()',ctx);assert.equal(node('training-go-convert').hidden,false);
ctx.formatReport=null;vm.runInContext('trainingGuide()',ctx);assert.equal(node('training-go-convert').hidden,true);
let stops=0;node('capture-stop').disabled=false;node('capture-stop').onclick=()=>stops++;
vm.runInContext("recordingJob={active:true,id:'record',collection:'a',message:'Recording'};updateWorkflowActivity()",ctx);
assert.equal(node('workflow-activity').hidden,false);assert.equal(node('activity-stop').hidden,false);node('activity-stop').click();assert.equal(stops,1);
node('capture-stop').disabled=true;vm.runInContext('updateWorkflowActivity()',ctx);assert.equal(node('activity-stop').disabled,true);node('activity-stop').click();assert.equal(stops,1);
vm.runInContext("recordingJob={active:false};updateWorkflowActivity()",ctx);assert.equal(node('workflow-activity').hidden,true);
console.log('Project routing, inference monitoring guard, stage visibility, conversion handoff parameter preservation and delegated stop controls pass.');
