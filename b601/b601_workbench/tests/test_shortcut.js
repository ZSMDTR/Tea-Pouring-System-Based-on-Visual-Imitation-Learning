// Executes the page controller against a tiny DOM stub, never real hardware.
const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const nodes=new Map(),listeners={};
function node(id){if(!nodes.has(id))nodes.set(id,{value:'',textContent:'',disabled:false,hidden:false,setAttribute(){},removeAttribute(){},getAttribute(){return null;},pause(){},load(){},replaceChildren(){},append(){},classList:{toggle(){}}});return nodes.get(id);}
const ctx=vm.createContext({document:{getElementById:node,querySelectorAll:()=>[],addEventListener:(name,handler)=>listeners[name]=handler,createElement:()=>node(Symbol())},localStorage:{getItem:()=>null,setItem(){}},window:{scrollTo(){}},setTimeout(){},console});
vm.runInContext(fs.readFileSync(__dirname+'/../web/ui.js','utf8'),ctx);
let stops=0;node('capture-stop').onclick=()=>stops++;
function press(code,overrides={}){let prevented=false;listeners.keydown({code,repeat:false,isComposing:false,target:{closest:()=>null},preventDefault(){prevented=true;},...overrides});return prevented;}
vm.runInContext("recordingJob={id:'one',active:true,state:'recording'}",ctx);
node('capture-shortcut').value='Space';assert.equal(press('Space'),true);assert.equal(stops,1);
for(const change of [{repeat:true},{isComposing:true},{ctrlKey:true},{altKey:true},{metaKey:true},{shiftKey:true},{target:{closest:()=>({})}}])assert.equal(press('Space',change),false);
assert.equal(stops,1);assert.equal(press('Enter'),false);
node('capture-shortcut').value='Enter';assert.equal(press('Enter'),true);assert.equal(stops,2);
for(const state of ['idle','starting','connecting','saving','returning','completed']){vm.runInContext(`recordingJob={id:'one',active:true,state:'${state}'}`,ctx);assert.equal(press('Enter'),false);}
node('capture-shortcut').value='none';vm.runInContext("recordingJob={active:true,state:'recording'}",ctx);assert.equal(press('Space'),false);assert.equal(stops,2);
console.log('Shortcut lifecycle, input-focus, modifier and repeat checks passed.');
