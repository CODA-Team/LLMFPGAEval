const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const nodes=new Map(), requests=[];
function element(id){
  if(!nodes.has(id))nodes.set(id,{value:'',textContent:'',innerHTML:'',hidden:false,disabled:false,open:false,
    handlers:{},classList:{toggle(){}},setAttribute(){},focus(){this.focused=true;},
    addEventListener(name,handler){this.handlers[name]=handler;},
    showModal(){this.open=true;},close(){this.open=false;}});
  return nodes.get(id);
}
const settings={model:'deepseek-flash',executable:'opencode.exe',max_output_tokens:null,
  max_agent_steps:null,turn_timeout_minutes:null,
  max_functional_runs:null,max_implementation_runs:null,max_post_route_runs:null,
  tools:{log_limit_mib:1024,waveform_limit_mib:1024},
  limits:{model_max_output_tokens:393216,agent_steps:null,turn_timeout_s:null}};
settings.model_config={DeepSeek:{provider_id:'deepseek',model_id:'deepseek-flash',base_url:'https://api.deepseek.com',api_key_env:'DEEPSEEK_API_KEY',npm:'@ai-sdk/deepseek',settings:{}}};
settings.active_profile='DeepSeek';
const context=vm.createContext({console,Map,Date,JSON,setInterval(){},
  document:{getElementById:element,querySelectorAll:()=>[]},
  initEdaView(){},renderStageTree(){},renderMessages(){},renderRuns(){},
  fetch:async(path,options)=>{
    const body=options?.body?JSON.parse(options.body):undefined;
    requests.push({path,body});
    return {ok:true,json:async()=>path.endsWith('/create')?{id:'created',title:body.title}:
      {settings,sessions:[],active_id:null,token:'unit-test-token'}};
  }});
vm.runInContext(fs.readFileSync('workbench/static/chat.js','utf8'),context);
const event={preventDefault(){}};
(async()=>{
  await new Promise(resolve=>setImmediate(resolve));
  vm.runInContext('refresh=async()=>{};',context);
  await element('newSession').handlers.click();
  assert.equal(element('newExperimentDialog').open,true);
  assert.equal(requests.filter(r=>r.path.endsWith('/create')).length,0);
  element('experimentName').value='取消的实验';
  await element('cancelNewExperiment').handlers.click();
  assert.equal(element('newExperimentDialog').open,false);
  assert.equal(requests.filter(r=>r.path.endsWith('/create')).length,0);
  await element('newSession').handlers.click();
  element('experimentName').value='  四位计算器实验  ';
  await element('newExperimentForm').handlers.submit(event);
  assert.equal(requests.at(-1).body.title,'四位计算器实验');
  assert.equal(element('newExperimentDialog').open,false);
  await element('newSession').handlers.click();
  element('experimentName').value='  ';
  await element('newExperimentForm').handlers.submit(event);
  assert.equal(requests.at(-1).body.title,'新实验');
  element('createExperiment').disabled=true;
  const count=requests.length;
  await element('newExperimentForm').handlers.submit(event);
  assert.equal(requests.length,count,'double submission must not create duplicate experiments');
  await element('settingsButton').handlers.click();
  assert.equal(element('agentStepLimit').value,'');
  assert.equal(element('turnTimeLimit').value,'');
  for(const id of ['functionalRunLimit','implementationRunLimit','postRouteRunLimit'])assert.equal(element(id).value,'');
  await element('settingsForm').handlers.submit(event);
  assert.equal(requests.at(-1).body.max_agent_steps,null);
  assert.equal(requests.at(-1).body.turn_timeout_minutes,null);
  for(const key of ['max_functional_runs','max_implementation_runs','max_post_route_runs'])assert.equal(requests.at(-1).body[key],null);
  element('agentStepLimit').value='60';
  element('turnTimeLimit').value='45';
  element('functionalRunLimit').value='8';element('implementationRunLimit').value='6';element('postRouteRunLimit').value='4';
  await element('settingsForm').handlers.submit(event);
  assert.equal(requests.at(-1).body.max_agent_steps,60);
  assert.equal(requests.at(-1).body.turn_timeout_minutes,45);
  assert.equal(requests.at(-1).body.max_functional_runs,8);
  assert.equal(requests.at(-1).body.max_implementation_runs,6);
  assert.equal(requests.at(-1).body.max_post_route_runs,4);
  element('agentStepLimit').value='';element('turnTimeLimit').value='';
  for(const id of ['functionalRunLimit','implementationRunLimit','postRouteRunLimit'])element(id).value='';
  await element('settingsForm').handlers.submit(event);
  assert.equal(requests.at(-1).body.max_agent_steps,null);
  assert.equal(requests.at(-1).body.turn_timeout_minutes,null);
  for(const key of ['max_functional_runs','max_implementation_runs','max_post_route_runs'])assert.equal(requests.at(-1).body[key],null);
  for(const id of ['queryRunLimit','continuationLimit','messageCharLimit'])assert.equal(element(id).value,'');
  for(const key of ['max_query_runs','max_continuations','max_message_chars'])assert.equal(requests.at(-1).body[key],null);
  await element('toolSettingsButton').handlers.click();
  assert.equal(element('logSizeLimit').value,1024);
  assert.equal(element('waveSizeLimit').value,1024);
  // Browser inputs stringify numbers; emulate that in the lightweight DOM.
  element('logSizeLimit').value='1024';element('waveSizeLimit').value='';
  element('simulationTimeLimit').value='900';
  await element('toolSettingsForm').handlers.submit(event);
  assert.equal(requests.at(-1).body.log_limit_mib,1024);
  assert.equal(requests.at(-1).body.waveform_limit_mib,null);
  assert.equal(requests.at(-1).body.simulation_timeout_s,900);
  assert.equal(requests.at(-1).body.implementation_timeout_s,null);
  console.log('Experiment naming/cancel/default/duplicate guard and optional run budgets: passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
