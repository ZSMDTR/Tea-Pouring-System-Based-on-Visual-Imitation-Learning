const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const nodes=new Map();let writes=0,pauses=0;
function n(id){if(!nodes.has(id))nodes.set(id,{id,value:'',textContent:'',dataset:{},hidden:false,disabled:false,currentTime:0,children:[],classList:{toggle(){}},setAttribute(){},getAttribute(k){return this[k]||null},removeAttribute(k){delete this[k]},replaceChildren(...x){this.children=x},append(...x){this.children.push(...x)},pause(){pauses++},load(){}});return nodes.get(id)}
const a={dataset:'raw-a',episode:0,collection:'g',task:'A',frames:30,review:'unreviewed',note:''},b={...a,dataset:'raw-b',task:'B'};
const requests={};const ctx=vm.createContext({$:n,document:{querySelectorAll:()=>[],addEventListener(){}},libraryData:{members:[a,b],collections:[{id:'g',name:'G'}]},snapshot:{datasets:[{name:'raw-a',fps:30},{name:'raw-b',fps:30}]},recordingJob:{active:false},el:(tag,text)=>({...n(Symbol()),textContent:text}),option:(v,t)=>({value:v,textContent:t}),replayLabel:()=>'',api:url=>new Promise(resolve=>requests[url]=resolve),renderMembers(){},libraryPost:async(action,body)=>{writes++;const target=[a,b].find(m=>m.dataset===body.dataset);Object.assign(target,body)},loadLibrary:async()=>{},a,b,console});
vm.runInContext(fs.readFileSync(__dirname+'/../web/data_workspace_ui.js','utf8'),ctx);
(async()=>{
 const first=vm.runInContext('selectLibraryMember(a)',ctx),second=vm.runInContext('selectLibraryMember(b)',ctx);
 requests['/api/episodes?dataset=raw-b']([{index:0,videos:[{camera:'wrist_rgb',path:'b.mp4',start:10,end:20}]}]);await second;
 assert.match(n('member-player').src,/dataset=raw-b/);
 requests['/api/episodes?dataset=raw-a']([{index:0,videos:[{camera:'wrist_rgb',path:'a.mp4',start:0,end:1}]}]);await first;
 assert.match(n('member-player').src,/dataset=raw-b/); // Late A must not replace B.
 n('member-player').currentTime=20;n('member-player').onplay();assert.equal(n('member-player').currentTime,10);
 n('member-player').currentTime=2;n('member-player').onseeking();assert.equal(n('member-player').currentTime,10);
 n('member-player').currentTime=25;n('member-player').onseeking();assert.equal(n('member-player').currentTime,20);
 const before=pauses;n('member-player').ontimeupdate();assert.equal(pauses,before+1);
 n('member-note').value='unsaved B';vm.runInContext('saveMemberDraft()',ctx);
 await vm.runInContext('selectLibraryMember(a)',ctx);await vm.runInContext('selectLibraryMember(b)',ctx);assert.equal(n('member-note').value,'unsaved B');
 await n('member-edit-form').onsubmit({preventDefault(){}});await new Promise(resolve=>setImmediate(resolve));assert.equal(vm.runInContext('memberSelection.dataset',ctx),'raw-b');assert.equal(b.note,'unsaved B');assert.equal(writes,1);
 ctx.recordingJob.active=true;vm.runInContext('drawMemberControls()',ctx);assert.equal(n('member-save').disabled,true);assert.equal(n('member-trash').disabled,true);
 await vm.runInContext('mutateMember(()=>libraryPost())',ctx);assert.equal(writes,1);
 console.log('Member selection race, video bounds, draft retention and busy-device mutation gates pass.');
})().catch(e=>{console.error(e);process.exitCode=1});
