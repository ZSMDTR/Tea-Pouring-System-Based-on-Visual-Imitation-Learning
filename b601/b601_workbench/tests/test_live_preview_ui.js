// A status poll must not reconnect the video; hidden or stale views release it.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const nodes=new Map();let assignments=0;
function node(id){
 if(!nodes.has(id)){
  const n={hidden:false,attrs:{},removeAttribute(k){delete this.attrs[k];}};
  Object.defineProperty(n,'src',{set(v){this.attrs.src=v;assignments++;},get(){return this.attrs.src;}});
  nodes.set(id,n);
 }
 return nodes.get(id);
}
const ctx=vm.createContext({$:node,document:{visibilityState:'visible'},encodeURIComponent,Date});
const source=fs.readFileSync(__dirname+'/../web/inference_ui.js','utf8').split('const inferencePhases=')[0];
vm.runInContext(source,ctx);
ctx.session={id:'one',preview_stream:true};
for(let i=0;i<10;i++)vm.runInContext('inferencePreview(session,true)',ctx);
assert.equal(assignments,1);assert.equal(node('infer-image').src,'/api/inference/stream?id=one');
node('inference').hidden=true;vm.runInContext('inferencePreview(session,true)',ctx);
assert.equal(node('infer-image').src,undefined);assert.equal(node('infer-image').hidden,true);
node('inference').hidden=false;vm.runInContext('inferencePreview(session,true)',ctx);assert.equal(assignments,2);
ctx.session.id='two';vm.runInContext('inferencePreview(session,true)',ctx);
assert.equal(node('infer-image').src,'/api/inference/stream?id=two');
node('infer-image').onerror();assert.equal(node('infer-image').src,undefined);
vm.runInContext('inferencePreview(session,true)',ctx);assert.equal(assignments,4);
ctx.document.visibilityState='hidden';vm.runInContext('inferencePreview(session,true)',ctx);
assert.equal(node('infer-image').src,undefined);
ctx.document.visibilityState='visible';vm.runInContext('inferencePreview(session,false)',ctx);
assert.equal(node('infer-image').hidden,true);
ctx.session={id:'dual',active:true,preview_stream:true,camera_views:['wrist_rgb','head_rgb'],head_preview:{image_at:Date.now()/1000,preview_fps:30}};
vm.runInContext('inferencePreview(session,true)',ctx);
assert.equal(node('infer-head-image').src,'/api/inference/stream?id=dual&role=head');
assert.equal(node('infer-head-image').hidden,false);assert.equal(node('infer-head-view').hidden,false);
const dualAssignments=assignments;
vm.runInContext('inferencePreview(session,true)',ctx);assert.equal(assignments,dualAssignments);
ctx.session.head_preview.image_at-=10;
vm.runInContext('inferencePreview(session,true)',ctx);
assert.equal(node('infer-head-image').src,undefined);assert.equal(node('infer-head-image').hidden,true);
assert.equal(node('infer-image').hidden,false);
ctx.session.camera_views=['wrist_rgb'];vm.runInContext('inferencePreview(session,true)',ctx);
assert.equal(node('infer-head-view').hidden,true);
console.log('Stable stream connection, session changes, retry, hidden page and stale image checks pass.');
