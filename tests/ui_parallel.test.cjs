const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const nodes=new Map(),requests=[];
const node=id=>{if(!nodes.has(id))nodes.set(id,{value:'',textContent:'',innerHTML:'',hidden:false,disabled:false,handlers:{},
  addEventListener(t,f){this.handlers[t]=f;},setAttribute(){},classList:{toggle(){}},focus(){},close(){}});return nodes.get(id);};
const context=vm.createContext({console,Date,Map,JSON,setInterval(){},document:{getElementById:node,querySelectorAll:()=>[]},
  initEdaView(){},renderMessages(){},renderRuns(){},renderStageTree(){},fetch:async()=>({ok:true,json:async()=>({settings:{model:'fixture'},sessions:[],active_ids:[]})})});
vm.runInContext(fs.readFileSync('workbench/static/chat.js','utf8'),context);
(async()=>{
  await new Promise(r=>setImmediate(r));
  vm.runInContext(`refresh=async()=>{}; state={settings:{key_present:true,model:'fixture',max_parallel_experiments:null},
    sessions:[{id:'a',title:'A',status:'running'},{id:'b',title:'B',status:'ready'}],active_id:'a',active_ids:['a']};
    selected='b';experiment={id:'b',status:'ready'};renderState();`,context);
  assert.equal(node('sendButton').disabled,false,'busy A must not disable send in B');
  assert.equal(node('stopButton').hidden,true);
  assert.equal(node('cleanupFiles').disabled,false);
  vm.runInContext(`selected='a';experiment={id:'a',status:'running'};renderState();`,context);
  assert.equal(node('sendButton').disabled,true);
  assert.equal(node('stopButton').hidden,false);
  assert.equal(node('cleanupFiles').disabled,true);
  context.requests=requests;
  vm.runInContext(`selected='b';experiment={id:'b',status:'ready'};api=async(path,body)=>{requests.push({path,body});
    selected='a';$('prompt').value='draft for A';return {};};`,context);
  node('prompt').value='B task';
  await vm.runInContext('sendMessage()',context);
  assert.equal(requests[0].body.id,'b');
  assert.equal(node('prompt').value,'draft for A','finishing B send must not clear a different experiment draft');
  vm.runInContext(`selected='b';state.settings.max_parallel_experiments=1;renderState();`,context);
  assert.equal(node('sendButton').disabled,true,'honor optional concurrent job cap');
  vm.runInContext(`selected='a';experiment={id:'a'};state.active_ids=[];state.sessions=[];state.settings.max_parallel_experiments=null;
    api=async(path,body)=>{requests.push({path,body});selected='b';$('programConfirm').value='B confirmation';
    $('boardConfirmed').checked=true;$('notice').textContent='B notice';return {ok:true};};`,context);
  await node('programForm').handlers.submit({preventDefault(){}});
  assert.equal(requests.at(-1).body.id,'a');
  assert.equal(node('programConfirm').value,'B confirmation','A response must not clear B confirmation');
  assert.equal(node('boardConfirmed').checked,true);
  assert.equal(node('notice').textContent,'B notice','never show A hardware result as B result');
  vm.runInContext(`selected='a';state.active_ids=['a'];`,context);
  await node('stopButton').handlers.click();
  assert.equal(requests.at(-1).body.id,'a');
  assert.equal(node('notice').textContent,'B notice','A stop response must not change B notice');
  vm.runInContext(`selectExperiment('c');`,context);
  assert.equal(node('programConfirm').value,'','switching experiments clears hardware confirmation');
  assert.equal(node('boardConfirmed').checked,false);
  console.log('Parallel send/stop/cleanup state, optional cap and switch-during-request isolation: passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
