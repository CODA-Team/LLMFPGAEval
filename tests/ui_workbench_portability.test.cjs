const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
async function check(history,configured){
  const nodes=new Map();
  const node=id=>{if(!nodes.has(id))nodes.set(id,{value:'',options:[],checked:id==='useAsic',innerHTML:'',
    addEventListener(){},textContent:'',hidden:false});return nodes.get(id);};
  const input={config:{ssh_host:configured?'eda-server':'',orfs_root:configured?'/home/student/orfs':'',
    remote_work_root:configured?'/home/student/runs':''},projects:[],jobs:history?[{id:'old',project_id:'example'}]:[]};
  const context=vm.createContext({console,document:{getElementById:node,querySelectorAll:()=>[]},setInterval(){},
    fetch:async()=>({ok:true,json:async()=>input})});
  vm.runInContext(fs.readFileSync('workbench/static/app.js','utf8'),context);
  vm.runInContext('render=()=>{};',context);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(node('useAsic').checked,configured,'remote selection must follow configuration even with existing history');
}
(async()=>{
  await check(false,false);await check(true,false);await check(true,true);
  console.log('FPGA-only first load and reload with existing history: passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
