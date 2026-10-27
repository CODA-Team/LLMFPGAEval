const assert=require('node:assert/strict');
const fs=require('node:fs'), vm=require('node:vm');
const nodes=new Map(),requests=[];
function element(id){
  if(!nodes.has(id))nodes.set(id,{value:'',textContent:'',innerHTML:'',hidden:false,disabled:false,open:false,handlers:{},
    classList:{toggle(){}},setAttribute(){},focus(){},addEventListener(n,h){this.handlers[n]=h;},showModal(){this.open=true;},close(){this.open=false;}});
  return nodes.get(id);
}
const catalog={'GLM-5.3-Flash':{provider_id:'micuapi',model_id:'glm-5.3-flash',base_url:'https://www.micuapi.ai/v1',api_key_env:'MICUAPI_API_KEY',npm:'@ai-sdk/openai-compatible',usage_adapter:'openai-chat-v1',user_agent:'Mozilla/5.0',settings:{}},
  'DeepSeek-Flash':{provider_id:'deepseek-official',model_id:'deepseek-flash',base_url:'https://api.deepseek.com',api_key_env:'DEEPSEEK_API_KEY',npm:'@ai-sdk/openai-compatible',usage_adapter:'deepseek-v1',settings:{reasoningEffort:'low',max_tokens:4096}}};
const settings={model:'deepseek-flash',provider_name:'DeepSeek',executable:'opencode.exe',model_config:catalog,active_profile:'DeepSeek-Flash',
  profile_credentials:{DEEPSEEK_API_KEY:{present:true,saved:true,source:'saved'}},tools:{}};
let copied='';
const context=vm.createContext({console,Map,Date,JSON,setInterval(){},
  navigator:{clipboard:{writeText:async v=>{copied=v;}}},document:{getElementById:element,querySelectorAll:()=>[]},
  initEdaView(){},renderStageTree(){},renderMessages(){},renderRuns(){},
  fetch:async(path,options)=>{const body=options?.body?JSON.parse(options.body):undefined;requests.push({path,body});
    return {ok:true,json:async()=>path.endsWith('/open-source')?{opened:true}:path.endsWith('/rename')?{id:'abc',title:body.title}:
      {settings,sessions:[],active_id:null,token:'test-token'}};}});
vm.runInContext(fs.readFileSync('workbench/static/chat.js','utf8'),context);
const event={preventDefault(){}};
(async()=>{
  await new Promise(resolve=>setImmediate(resolve));vm.runInContext('refresh=async()=>{};',context);
  await element('settingsButton').handlers.click();
  assert.deepEqual(JSON.parse(element('connectionJson').value),catalog);
  assert.equal(element('modelProfile').value,'DeepSeek-Flash');
  assert.match(element('apiKeyLabel').textContent,/DEEPSEEK_API_KEY/);
  element('modelProfile').value='GLM-5.3-Flash';await element('modelProfile').handlers.change();
  assert.equal(element('rememberKey').checked,false);
  assert.match(element('apiKeyLabel').textContent,/MICUAPI_API_KEY/);
  element('apiKey').value='temporary-browser-key';element('rememberKey').checked=true;
  await element('settingsForm').handlers.submit(event);
  assert.equal(requests.at(-1).path,'/api/teaching/config');
  assert.deepEqual(requests.at(-1).body.model_config,catalog);
  assert.equal(requests.at(-1).body.active_profile,'GLM-5.3-Flash');
  assert.equal(requests.at(-1).body.api_key,'temporary-browser-key');
  assert.equal(requests.at(-1).body.max_output_tokens,undefined,'token cap belongs to each JSON entry');
  assert.equal(requests.at(-1).body.max_agent_steps,null);
  await element('copyConnectionJson').handlers.click();
  assert.equal(copied.includes('temporary-browser-key'),false);
  assert.equal(JSON.parse(copied)['DeepSeek-Flash'].settings.max_tokens,4096);
  element('apiKey').value='must-clear-on-key-reference-switch';
  element('modelProfile').value='DeepSeek-Flash';await element('modelProfile').handlers.change();
  assert.equal(element('apiKey').value,'');
  element('connectionJson').value='{invalid';const before=requests.length;
  await element('settingsForm').handlers.submit(event);
  assert.equal(requests.length,before);assert.match(element('settingsNotice').textContent,/JSON/);
  const fileInput={files:[{text:async()=>JSON.stringify(catalog)}],value:'models.json'};
  await element('connectionJsonFile').handlers.change({target:fileInput});
  assert.equal(fileInput.value,'');assert.deepEqual(JSON.parse(element('connectionJson').value),catalog);
  const reduced={'DeepSeek-Flash':catalog['DeepSeek-Flash']};element('connectionJson').value=JSON.stringify(reduced);
  await element('formatModelJson').handlers.click();
  assert.equal(element('modelProfile').value,'DeepSeek-Flash');
  vm.runInContext("selected='abc';experiment={id:'abc',title:'旧实验',source_path:'D:/lab/source'};",context);
  await element('openSourceFolder').handlers.click();
  assert.deepEqual(requests.at(-1),{path:'/api/teaching/open-source',body:{id:'abc'}});
  await element('renameExperiment').handlers.click();element('renameExperimentName').value='计算器';
  await element('renameExperimentForm').handlers.submit(event);
  assert.deepEqual(requests.at(-1),{path:'/api/teaching/rename',body:{id:'abc',title:'计算器'}});
  console.log('JSON-first settings: exact schema, save, model selection, environment reference, file import/copy and navigation passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
