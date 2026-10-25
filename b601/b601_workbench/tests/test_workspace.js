// Navigation regression: no browser, no devices, no capture requests.
const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const nodes=new Map();
function n(id){if(!nodes.has(id))nodes.set(id,{id,value:'',dataset:{},hidden:false,children:[],classList:{toggle(){}},append(){},replaceChildren(){},setAttribute(){},pause(){},focus(){}});return nodes.get(id);}
const panels=['capture','library','data','mixtures','recycle'].map(n);
const ctx=vm.createContext({$:n,snapshot:null,libraryData:{collections:[{id:'a',name:'A',task:'Task A'},{id:'b',name:'B',task:'Task B'}],members:[]},recordingJob:{active:true,collection:'a'},renderMembers(){},drawRecording(){},document:{querySelectorAll:s=>s==='.workspace-panel'?panels:[],querySelector:n},refresh:()=>new Promise(()=>{}),loadLibrary(){},pollRecording(){},phaseNames:{},stopRequested:null,requestId:0});
vm.runInContext(fs.readFileSync(__dirname+'/../web/workspace_ui.js','utf8'),ctx);
n('capture-collection').value='a';n('capture-task').value='Task A';
vm.runInContext("selectWorkspaceGroup('b');workspaceView('capture')",ctx);
assert.equal(n('capture').hidden,true);assert.equal(n('library').hidden,false);
assert.equal(n('capture-collection').value,'a'); // Running job retains ownership.
ctx.recordingJob={active:false,collection:'a'};n('capture-prepared').checked=true;
vm.runInContext("workspaceView('capture')",ctx);
assert.equal(n('capture').hidden,false);assert.equal(n('capture-collection').value,'b');
assert.equal(n('capture-task').value,'Task B');assert.equal(n('capture-prepared').checked,false);
n('capture-task').value='Custom B';vm.runInContext("workspaceView('library');workspaceView('capture')",ctx);
assert.equal(n('capture-task').value,'Custom B');
vm.runInContext("selectWorkspaceGroup('a');workspaceView('capture')",ctx);
assert.equal(n('capture-collection').value,'a');assert.equal(n('capture-task').value,'Task A');
console.log('Workspace navigation preserves active ownership and binds the selected dataset after completion.');
