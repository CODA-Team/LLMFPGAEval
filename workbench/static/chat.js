"use strict";
const $ = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
function richText(value){
  const inline=text=>esc(text).replace(/`([^`\n]+)`/g,'<code>$1</code>').replace(/\*\*([^\n]+?)\*\*/g,'<strong>$1</strong>');
  return String(value).split(/(```[\s\S]*?(?:```|$))/g).map(part=>{
    if(part.startsWith('```'))return '<pre><code>'+esc(part.replace(/^```[^\n]*\n?/, '').replace(/```$/, ''))+'</code></pre>';
    const lines=part.split('\n'),output=[];
    const cells=line=>line.trim().replace(/^\||\|$/g,'').split('|').map(x=>x.trim());
    for(let i=0;i<lines.length;i++){
      if(lines[i].includes('|')&&i+1<lines.length&&/^\s*\|?\s*:?-{3,}/.test(lines[i+1])&&cells(lines[i+1]).every(x=>/^:?-{3,}:?$/.test(x))){
        const head=cells(lines[i]);i+=2;const rows=[];
        while(i<lines.length&&lines[i].includes('|')){rows.push(cells(lines[i]));i++;}i--;
        output.push('<div class="table-scroll"><table><thead><tr>'+head.map(x=>'<th>'+inline(x)+'</th>').join('')+'</tr></thead><tbody>'+rows.map(row=>'<tr>'+row.map(x=>'<td>'+inline(x)+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>');
      }else output.push(inline(lines[i]).replace(/^#{1,4}\s+(.+)$/, '<h3>$1</h3>'));
    }
    return output.join('\n');
  }).join('');
}
let state = null, selected = null, experiment = null, polling = false, signature = "", fileSignature = "", flowSignature = "", currentCode = "";
let cleanupState = null, pendingDelete = null, deleteInProgress = false;
const pendingSends = new Set();
function selectExperiment(id){
  if(selected!==id){
    for(const name of ['programConfirm','boardPassed','boardProcedure','boardObserved'])$(name).value='';
    $('boardConfirmed').checked=false;
    signature=fileSignature=flowSignature='';
  }
  selected=id;
}
function activeExperimentIds(){return Array.isArray(state?.active_ids)?state.active_ids:state?.active_id?[state.active_id]:[];}
function experimentBusy(id){return !!id&&(activeExperimentIds().includes(id)||state?.maintenance_ids?.includes(id)||state?.sessions?.some(s=>s.id===id&&s.status==='running'));}
function sendBlocked(){const limit=state?.settings?.max_parallel_experiments;return experimentBusy(selected)||pendingSends.has(selected)||(limit!=null&&activeExperimentIds().length>=limit);}
const labels = {ready:"等待输入",running:"执行中",passed:"功能仿真通过",needs_test:"等待复测",failed:"运行失败",interrupted:"已中断",cancelled:"已停止",timeout:"已超时"};
const runLabels = {running:"执行中",passed:"通过",failed:"失败",compile_failed:"编译失败",inconclusive:"缺少有效自检",timeout:"超时",cancelled:"已停止",tool_error:"工具错误",output_limit:"输出超限",license_error:"许可证不可用",stale:"运行期间版本改变"};
function notice(message, error=false){$("notice").hidden=!message;$("notice").textContent=message;$("notice").className=error?"bad":"";}
async function api(path, body){
  const options=body===undefined?{}:{method:"POST",headers:{"Content-Type":"application/json","X-Workbench-Token":state?.token||""},body:JSON.stringify(body)};
  const response=await fetch(path,options);const value=await response.json();
  if(!response.ok)throw new Error(value.error||response.statusText);return value;
}
function timeLabel(value){return new Date(value).toLocaleString("zh-CN",{month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"});}
function evidenceURL(run,file){return "/api/teaching/evidence?id="+selected+"&run="+encodeURIComponent(run)+"&file="+encodeURIComponent(file);}
function renderState(){
  const active=activeExperimentIds(),busy=experimentBusy(selected);
  $("modelLabel").textContent=state.settings.model;
  $('providerLabel').textContent=(state.settings.provider_name||'DeepSeek')+' · 模型推理';
  $("keyDot").className="status-dot"+(state.settings.key_present?" ready":"");
  $("sessionCount").textContent=state.sessions.length;
  $("sessions").innerHTML=state.sessions.map(s=>'<div class="session-row"><button class="session-item '+(s.id===selected?'selected':'')+'" data-id="'+s.id+'"><span>'+esc(s.title)+'</span><small>'+esc(timeLabel(s.updated_at))+' · '+esc(labels[s.status]||s.status)+'</small></button><button class="session-delete" data-delete="'+s.id+'" aria-label="删除实验 '+esc(s.title)+'" title="删除整个实验" '+(experimentBusy(s.id)?'disabled':'')+'>删除</button></div>').join("");
  $('confirmDeleteButton').disabled=deleteInProgress||experimentBusy(pendingDelete?.id);
  $('cleanupFiles').disabled=!experiment||busy;
  if(cleanupState&&cleanupState.data.busy!==experimentBusy(cleanupState.data.id)){cleanupState.data.busy=experimentBusy(cleanupState.data.id);renderCleanup();}
  $("sendButton").disabled=$("startFlow").disabled=sendBlocked();
  $("settingsButton").disabled=active.length>0||!!state.maintenance_ids?.length;
  $("toolSettingsButton").disabled=active.length>0||!!state.maintenance_ids?.length;
  $("stopButton").hidden=!active.includes(selected);
  $("working").hidden=!active.includes(selected);
  if($('runningExperiments')){$('runningExperiments').hidden=!active.length;$('runningExperiments').textContent=active.length+' 个实验运行中';}
}
function renderExperiment(){
  const e=experiment, sim=e?.simulation, latest=sim?.latest;
  $("experimentTitle").textContent=e?.title||"AI 设计实验";
  $('renameExperiment').disabled=!e||state?.maintenance_ids?.includes(e.id);
  $('cleanupFiles').disabled=!e||experimentBusy(e.id);
  $('openSourceFolder').disabled=$('copySourcePath').disabled=!e?.source_path;
  $('sourcePath').textContent=e?.source_path||'新建实验后显示源码目录。';
  $('experimentId').textContent=e?.id?e.id.slice(0,8):'';
  $('experimentId').title=e?.id||'';
  $("sessionStatus").textContent=labels[e?.status]||"准备开始";
  $("sessionStatus").className="badge "+(e?.status==="passed"?"good":e?.status==="running"?"running":e?.status==="failed"?"bad":"");
  $("fileCount").textContent=(e?.files.length||0)+" 个文件";
  $("runCount").textContent=sim?.runs.length||0;
  $("welcome").hidden=!!e?.messages.length;
  const nextSignature=JSON.stringify([e?.id,e?.messages,e?.events,e?.status,sim?.source_sha256]);
  if(nextSignature!==signature){
    signature=nextSignature;
    renderMessages();
    renderRuns();
    const changes=(e?.events||[]).filter(x=>['file_changed','testbench_unlocked','testbench_policy_updated'].includes(x.type));
    $("changes").innerHTML=changes.length?changes.slice().reverse().map(x=>x.type!=='file_changed'?'<p class="empty">'+esc(timeLabel(x.time))+' · '+(x.type==='testbench_unlocked'?'历史记录：允许更新 Testbench':'已取消旧版 Testbench 自动锁定，允许根据日志继续修复')+'</p>':'<details class="change"><summary>'+esc(x.path)+'<small>'+esc(timeLabel(x.time))+'</small></summary><pre>'+esc(x.diff)+'</pre></details>').join(""):'<p class="empty">每次代码修改都会留下差异记录。</p>';
  }
  const fresh=sim?.verified_current;
  $("verificationCard").className="verification-card "+(fresh?'good':latest&&!latest.ok?'bad':'');
  $("verificationTitle").textContent=fresh?'当前版本功能仿真通过':latest?.ok?(latest.evidence_valid===false?'上次功能仿真通过 · 证据需核对':'上次功能仿真通过 · 输入已修改'):latest?runLabels[latest.status]||latest.status:'等待生成';
  $("verificationDetail").textContent=fresh?((latest.engine==='modelsim'?'ModelSim':latest.engine==='icarus'?'Icarus':'仿真器')+' · '+latest.tests_passed.length+' 项检查通过 · 测试修改后需复测'):latest?'保留上次运行结果和下载文件；重跑 02 只更新功能仿真，不清空其他阶段。':'代码生成后，仿真器将给出真实结果。';
  const usage=e?.usage;
  $("tokenCount").textContent=usage?.input!==undefined?((usage.input||0)+(usage.output||0)+(usage.reasoning||0)+(usage.cache_read||0)).toLocaleString():"—";
  $("costValue").textContent=usage?.reported_cost_usd!=null?'$'+usage.reported_cost_usd.toFixed(5):"未报告";
  const nextFiles=JSON.stringify([e?.id,e?.files,sim?.source_sha256]);
  if(nextFiles!==fileSignature){fileSignature=nextFiles;const old=$("fileSelect").value;$("fileSelect").innerHTML=e?.files.length?Object.entries({rtl:'RTL 源码',tb:'Testbench',constraints:'引脚与时序约束',scripts:'工具运行脚本'}).map(([group,label])=>'<optgroup label="'+label+'">'+e.files.filter(f=>f.path.startsWith(group+'/')).map(f=>'<option value="'+esc(f.path)+'">'+esc(f.path)+'</option>').join('')+'</optgroup>').join(''):'<option value="">暂无文件</option>';if(e?.files.some(f=>f.path===old))$("fileSelect").value=old;loadCode().catch(error=>notice(error.message,true));}
  if(e?.error)notice(e.error,true);
  renderFlow();
}
function flowURL(stage,run,file){return '/api/teaching/flow-file?id='+selected+'&stage='+stage+'&run='+encodeURIComponent(run)+'&file='+encodeURIComponent(file);}
function renderFlow(){
  const flow=experiment?.flow,fresh=experiment?.id===selected;
  const sig=JSON.stringify([selected,flow,experiment?.stage_states,experiment?.stage_results,experiment?.simulation?.source_sha256,experiment?.simulation?.latest]);
  if(sig!==flowSignature){
    flowSignature=sig;renderStageTree();
    const observation=flow?.hardware_observation;
    $('observationRecord').innerHTML=observation?'<p class="hint">人工记录 · '+(flow.observation_current?'对应当前下载':'旧版本记录')+' · '+(observation.passed?'通过':'失败')+'</p><pre>'+esc(observation.procedure)+'\n'+esc(observation.observed)+'</pre>':'';
  }
  $('programGate').textContent=flow?.ready_to_program?'当前版本已通过软件流程；连接并核对板卡后可确认本次下载。':'历史文件可在各阶段下载。上板确认需对应当前输入的功能、实现与后仿真证据；仅改 TB 可复用 TD 实现，按需复测 02 / 07。';
  $('programBoard').disabled=experimentBusy(selected)||!fresh||!flow?.ready_to_program;
  $('saveObservation').disabled=experimentBusy(selected)||!fresh||!flow?.programmed_current;
}
async function refresh(){
  if(polling)return;polling=true;
  try{
    state=await api('/api/teaching');
    if($('notice').textContent.startsWith('连接失败：'))notice('');
    if(!state.sessions.some(s=>s.id===selected))selectExperiment(state.sessions[0]?.id||null);
    renderState();
    if(selected&&!state.maintenance_ids?.includes(selected)){
      const id=selected,value=await api('/api/teaching/session?id='+id);
      if(selected===id){experiment=value;renderExperiment();}
    }else if(!selected||experiment?.id!==selected){experiment=null;renderExperiment();}
  }catch(error){if(!error.message.includes('文件正在清理'))notice('连接失败：'+error.message,true);}
  finally{polling=false;}
}
async function loadCode(){
  const path=$("fileSelect").value,id=selected;$("downloadCode").disabled=!path;
  if(!id||!path){currentCode='';$("codeView").textContent='// 生成的 RTL 和 Testbench 会显示在这里';return;}
  const result=await api('/api/teaching/file?id='+id+'&path='+encodeURIComponent(path));
  if(id===selected&&path===$("fileSelect").value){currentCode=result.content;$("codeView").textContent=result.content;}
}
const runLimitFields={max_agent_steps:'agentStepLimit',turn_timeout_minutes:'turnTimeLimit',max_functional_runs:'functionalRunLimit',max_implementation_runs:'implementationRunLimit',max_post_route_runs:'postRouteRunLimit',max_query_runs:'queryRunLimit',max_continuations:'continuationLimit',max_message_chars:'messageCharLimit',max_parallel_experiments:'parallelExperimentLimit'};
let credentialReference='';
function readModelConfig(){
  let value;
  try{value=JSON.parse($('connectionJson').value);}catch(error){throw new Error('JSON 格式错误：'+error.message);}
  if(!value||typeof value!=='object'||Array.isArray(value)||!Object.keys(value).length)throw new Error('请填写“模型名称 → 配置对象”的 JSON。');
  for(const [alias,entry] of Object.entries(value)){
    if(!entry||typeof entry!=='object'||Array.isArray(entry))throw new Error(alias+' 的配置必须是对象。');
    if('api_key' in entry||'apiKey' in entry)throw new Error('JSON 中请用 api_key_env 引用密钥，实际密钥填下方输入框。');
  }
  return value;
}
function updateModelSummary(){
  const models=readModelConfig(),entry=models[$('modelProfile').value];
  if(!entry)return;
  const ref=entry.api_key_env||'';
  if(ref!==credentialReference){
    $('apiKey').value='';
    $('rememberKey').checked=state?.settings.profile_credentials?.[ref]?.saved||false;
    credentialReference=ref;
  }
  $('apiKeyLabel').textContent=(ref||'当前模型')+' 的密钥（可选）';
  const status=state?.settings.profile_credentials?.[ref];
  $('credentialState').textContent=status?.present?({saved:'已在本机加密保存，留空保留。',memory:'已在本次服务运行中配置，留空保留。',environment:'已从后端环境变量读取，留空即可。'}[status.source]||'已有可用密钥，留空保留。'):'保存时读取后端环境变量 '+ref+'；未设置时可在此输入密钥。';
  $('modelProfileSummary').textContent=(entry.model_id||'未填写 model_id')+' · '+(entry.base_url||'未填写 base_url');
}
function syncModelProfiles(preferred){
  try{
    const models=readModelConfig(),aliases=Object.keys(models),selectedAlias=preferred||$('modelProfile').value;
    $('modelProfile').innerHTML=aliases.map(alias=>'<option value="'+esc(alias)+'">'+esc(alias)+'</option>').join('');
    $('modelProfile').value=aliases.includes(selectedAlias)?selectedAlias:aliases[0];
    updateModelSummary();
    $('connectionJsonNotice').textContent='包含 '+aliases.length+' 个模型配置。编辑后点击“保存设置”应用。';
  }catch(error){$('connectionJsonNotice').textContent=error.message;}
}
async function copyText(value){
  try{await navigator.clipboard.writeText(value);}
  catch{throw new Error('浏览器未允许复制，请从页面选中并复制文字。');}
}
function openSettings(){
  $('connectionJson').value=JSON.stringify(state.settings.model_config,null,2);
  credentialReference='';$('apiKey').value='';
  syncModelProfiles(state.settings.active_profile);
  $('executableInput').value=state.settings.executable;
  for(const [key,id] of Object.entries(runLimitFields))$(id).value=state.settings[key]??'';
  $('settingsNotice').textContent='';$('settingsDialog').showModal();
}
$('connectionJson').addEventListener('input',()=>syncModelProfiles());
$('modelProfile').addEventListener('change',()=>{try{updateModelSummary();}catch(error){$('connectionJsonNotice').textContent=error.message;}});
$('connectionJsonFile').addEventListener('change',async event=>{
  try{const file=event.target.files?.[0];if(file){$('connectionJson').value=await file.text();syncModelProfiles();}}
  catch(error){$('connectionJsonNotice').textContent=error.message;}
  finally{event.target.value='';}
});
$('formatModelJson').addEventListener('click',()=>{try{$('connectionJson').value=JSON.stringify(readModelConfig(),null,2);syncModelProfiles();}catch(error){$('connectionJsonNotice').textContent=error.message;}});
$('copyConnectionJson').addEventListener('click',async()=>{try{await copyText(JSON.stringify(readModelConfig(),null,2));$('connectionJsonNotice').textContent='模型 JSON 已复制，密钥仍由 api_key_env 引用。';}catch(error){$('connectionJsonNotice').textContent=error.message;}});
$('openSourceFolder').addEventListener('click',async()=>{try{if(!selected)return;await api('/api/teaching/open-source',{id:selected});notice('已在服务器所在电脑打开当前实验的源码目录。');}catch(error){notice(error.message,true);}});
$('copySourcePath').addEventListener('click',async()=>{try{if(!experiment?.source_path)return;await copyText(experiment.source_path);notice('已复制源码目录路径。');}catch(error){notice(error.message,true);}});
$('renameExperiment').addEventListener('click',()=>{if(!experiment)return;$('renameExperimentName').value=experiment.title;$('renameExperimentNotice').textContent='';$('renameExperimentDialog').showModal();$('renameExperimentName').focus();});
$('cancelRenameExperiment').addEventListener('click',()=>$('renameExperimentDialog').close());
$('renameExperimentForm').addEventListener('submit',async event=>{event.preventDefault();try{await api('/api/teaching/rename',{id:selected,title:$('renameExperimentName').value.trim()});$('renameExperimentDialog').close();await refresh();}catch(error){$('renameExperimentNotice').textContent=error.message;}});
$("newSession").addEventListener('click',()=>{$('experimentName').value='';$('newExperimentNotice').textContent='';$('newExperimentDialog').showModal();$('experimentName').focus();});
$('cancelNewExperiment').addEventListener('click',()=>$('newExperimentDialog').close());
$('newExperimentForm').addEventListener('submit',async event=>{
  event.preventDefault();const button=$('createExperiment');if(button.disabled)return;
  button.disabled=true;$('newExperimentNotice').textContent='';
  try{const value=await api('/api/teaching/create',{title:$('experimentName').value.trim()||'新实验'});selectExperiment(value.id);experiment=value;$('newExperimentDialog').close();notice('');await refresh();$('prompt').focus();}
  catch(error){$('newExperimentNotice').textContent=error.message;}
  finally{button.disabled=false;}
});
$("sessions").addEventListener('click',async event=>{const removal=event.target.closest('[data-delete]');if(removal){if(!removal.disabled)await openExperimentDelete(removal.dataset.delete);return;}const item=event.target.closest('[data-id]');if(item){selectExperiment(item.dataset.id);notice('');await refresh();}});
document.querySelectorAll('[data-example]').forEach(button=>button.addEventListener('click',()=>{$("prompt").value=button.dataset.example;$("prompt").focus();}));
async function sendMessage(fullFlow=false){
  const draft=$('prompt').value,original=selected;
  let target=selected,message=draft.trim();if(sendBlocked())return;
  if(fullFlow){
    if(!message&&experiment?.files?.length)message='继续当前实验，根据已有文件和日志完成尚未通过的软件阶段。';
    if(!message){notice('先输入电路需求，或点击上方示例填入需求。');$('prompt').focus();return;}
    message+='\n请启动完整软件流程：编写或修正 RTL、Testbench、引脚与时钟约束、TD Tcl 和 ModelSim DO 脚本，调用实际工具完成功能仿真、综合、布局布线、网表/比特流生成及 SDF 后仿真。按真实日志修复并重测，汇报各阶段结果与警告，最后给出板上验证步骤。';
  }
  if(!message)return;if(!state.settings.key_present){openSettings();return;}
  $('sendButton').disabled=$('startFlow').disabled=true;
  pendingSends.add(original);
  try{
    if(!target){const value=await api('/api/teaching/create',{title:message.slice(0,28)});target=value.id;if(selected===original)selectExperiment(target);}
    pendingSends.add(target);
    await api('/api/teaching/send',{id:target,message});
    if(selected===target){if($('prompt').value===draft)$('prompt').value='';notice('');if(fullFlow)selectPanel('flow');}
    await refresh();
  }catch(error){if(selected===target)notice(error.message,true);}
  finally{pendingSends.delete(original);pendingSends.delete(target);renderState();}
}
$('chatForm').addEventListener('submit',event=>{event.preventDefault();sendMessage();});
$('startFlow').addEventListener('click',()=>sendMessage(true));
$("prompt").addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();$("chatForm").requestSubmit();}});
$("stopButton").addEventListener('click',async()=>{const id=selected;try{await api('/api/teaching/stop',{id});if(selected===id)notice('停止请求已发送，正在收尾当前工具。');await refresh();}catch(error){if(selected===id)notice(error.message,true);}});
$("settingsButton").addEventListener('click',openSettings);$("closeSettings").addEventListener('click',()=>$("settingsDialog").close());
$("settingsForm").addEventListener('submit',async event=>{
  event.preventDefault();
  const optionalNumber=id=>$(id).value.trim()===''?null:Number($(id).value);
  try{
    const models=readModelConfig(),alias=$('modelProfile').value;
    if(!models[alias])throw new Error('请在 JSON 中选择当前模型。');
    await api('/api/teaching/config',{model_config:models,active_profile:alias,api_key:$('apiKey').value,
      executable:$('executableInput').value.trim(),remember_key:$('rememberKey').checked,
      ...Object.fromEntries(Object.entries(runLimitFields).map(([key,id])=>[key,optionalNumber(id)]))});
    $("apiKey").value='';$("settingsDialog").close();notice('设置已保存，可以开始设计实验。');await refresh();
  }catch(error){$("settingsNotice").textContent=error.message;}
});
$("checkConnection").addEventListener('click',async()=>{const button=$("checkConnection");button.disabled=true;$("settingsNotice").textContent='检查已保存的连接…';try{const result=await api('/api/teaching/check',{});$("settingsNotice").textContent=(result.provider_name||'API')+' 模型列表可访问：'+result.models.join('、')+(result.selected_available?'':'\n当前模型不在返回列表中，请核对标识。')+'\n此检查不调用模型生成；完整工具调用在实验对话中验证。';}catch(error){$("settingsNotice").textContent=error.message;}finally{button.disabled=false;}});
$("clearKey").addEventListener('click',async()=>{try{
  const alias=$('modelProfile').value,entry=readModelConfig()[alias],saved=state.settings.model_config?.[alias];
  if(!saved||entry.api_key_env!==saved.api_key_env||alias!==state.settings.active_profile)throw new Error('请先保存当前模型选择，再清除该模型的本机密钥。');
  state.settings=await api('/api/teaching/config',{clear_key:true});$('apiKey').value='';$('rememberKey').checked=false;
  updateModelSummary();await refresh();$('settingsNotice').textContent='已清除当前环境变量名对应的本机密钥。';
}catch(error){$('settingsNotice').textContent=error.message;}});
document.querySelectorAll('[data-tab]').forEach(button=>button.addEventListener('click',()=>{for(const tab of document.querySelectorAll('[data-tab]')){const active=tab===button;tab.classList.toggle('active',active);tab.setAttribute('aria-selected',String(active));$(tab.dataset.tab+'Panel').hidden=!active;}}));
$("fileSelect").addEventListener('change',()=>loadCode().catch(error=>notice(error.message,true)));
$("downloadCode").addEventListener('click',()=>{const url=URL.createObjectURL(new Blob([currentCode],{type:'text/plain;charset=utf-8'})),link=document.createElement('a');link.href=url;link.download=$("fileSelect").value.split('/').pop();link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
const toolFields={simulation_backend:'simulationBackend',td_root:'tdRoot',modelsim_root:'modelsimRoot',modelsim_license:'modelsimLicense',iverilog:'iverilogPath',vvp:'vvpPath'};
const toolLimitFields={simulation_timeout_s:'simulationTimeLimit',implementation_timeout_s:'implementationTimeLimit',programming_timeout_s:'programmingTimeLimit',max_source_files:'sourceFileLimit',max_source_file_kib:'sourceSizeLimit',log_limit_mib:'logSizeLimit',waveform_limit_mib:'waveSizeLimit'};
$('toolSettingsButton').addEventListener('click',()=>{for(const [key,id] of Object.entries({...toolFields,...toolLimitFields}))$(id).value=state.settings.tools[key]??'';$('toolSettingsNotice').textContent='';$('toolSettingsDialog').showModal();});
$('closeToolSettings').addEventListener('click',()=>$('toolSettingsDialog').close());
$('toolSettingsForm').addEventListener('submit',async event=>{event.preventDefault();try{const body={...Object.fromEntries(Object.entries(toolFields).map(([key,id])=>[key,$(id).value.trim()])),...Object.fromEntries(Object.entries(toolLimitFields).map(([key,id])=>[key,$(id).value.trim()===''?null:Number($(id).value)]))};await api('/api/teaching/tools',body);$('toolSettingsDialog').close();notice('工具与运行设置已保存，下一轮使用新配置。');await refresh();}catch(error){$('toolSettingsNotice').textContent=error.message;}});
$('programForm').addEventListener('submit',async event=>{
  event.preventDefault();if(!selected||experiment?.id!==selected||experimentBusy(selected))return;
  const id=selected;$('programBoard').disabled=true;
  try{
    const result=await api('/api/teaching/program',{id,confirmation:$('programConfirm').value,board_confirmed:$('boardConfirmed').checked});
    if(selected===id){$('programConfirm').value='';$('boardConfirmed').checked=false;notice(result.ok?'SRAM 下载成功。请实际操作板卡并记录观察。':'下载失败：'+(result.log_tail||result.status),!result.ok);}
    await refresh();
  }catch(error){if(selected===id)notice(error.message,true);await refresh();}
});
$('observationForm').addEventListener('submit',async event=>{
  event.preventDefault();if(!selected||experiment?.id!==selected||experimentBusy(selected))return;
  const id=selected;
  try{
    if(!$('boardPassed').value)throw new Error('请选择实际观察结论');
    await api('/api/teaching/observation',{id,passed:$('boardPassed').value==='true',procedure:$('boardProcedure').value,observed:$('boardObserved').value});
    if(selected===id)notice('人工观察已绑定本次下载的 bit 文件。');await refresh();
  }catch(error){if(selected===id)notice(error.message,true);}
});
function storageSize(bytes){
  if(bytes==null)return '未统计';
  const units=['B','KiB','MiB','GiB','TiB'];let value=bytes,i=0;
  while(value>=1024&&i<units.length-1){value/=1024;i++;}
  return (i?value.toFixed(value<10?2:1):value)+' '+units[i];
}
function visibleCleanupFiles(){
  const query=$('cleanupSearch').value.trim().toLowerCase(),kind=$('cleanupKind').value;
  return (cleanupState?.data.files||[]).filter(f=>(kind==='all'||f.kind===kind)&&f.path.toLowerCase().includes(query));
}
function renderCleanup(){
  if(!cleanupState)return;
  const labels={waveform:'波形',log:'日志/报告',source:'当前源码',evidence:'运行产物/快照',other:'其他'};
  const data=cleanupState.data,chosen=data.files.filter(f=>cleanupState.chosen.has(f.path)),visible=visibleCleanupFiles();
  $('cleanupHeading').textContent='清理文件 · '+data.title;
  $('cleanupSummary').textContent=data.file_count+' 个文件，共 '+storageSize(data.bytes)+'。只会删除你勾选并确认的文件。';
  $('cleanupSelected').textContent='已选 '+chosen.length+' 个 · '+storageSize(chosen.reduce((n,f)=>n+f.bytes,0));
  $('reviewCleanup').disabled=!chosen.length||data.busy||deleteInProgress;
  $('cleanupList').innerHTML=visible.length?visible.map(f=>'<label class="cleanup-file '+(!f.deletable?'protected':'')+'"><input type="checkbox" data-cleanup-path="'+esc(f.path)+'" '+(cleanupState.chosen.has(f.path)?'checked ':'')+(!f.deletable||data.busy?'disabled':'')+'><span class="cleanup-file-name">'+esc(f.path)+'<small>'+esc(f.deletable?labels[f.kind]:f.reason)+'</small></span><span class="cleanup-file-size">'+storageSize(f.bytes)+'</span></label>').join(''):'<p class="empty">没有匹配的文件。</p>';
}
async function openCleanup(id){
  if(!id)return;
  try{
    const data=await api('/api/teaching/storage?id='+encodeURIComponent(id));
    cleanupState={id,data,chosen:new Set()};$('cleanupSearch').value='';
    $('cleanupKind').value=data.files.some(f=>f.kind==='waveform')?'waveform':'all';
    $('cleanupNotice').textContent=data.busy?'有实验正在运行，结束后才能清理。':'';
    renderCleanup();if(!$('cleanupDialog').open)$('cleanupDialog').showModal();
  }catch(error){notice(error.message,true);}
}
function showDeleteConfirmation(preview){
  pendingDelete=preview;
  $('deleteConfirmHeading').textContent=preview.whole?'删除整个实验？':'删除所选文件？';
  $('deleteConfirmSummary').textContent='实验「'+preview.title+'」 · '+preview.file_count+' 个文件 · '+storageSize(preview.bytes);
  $('deleteConfirmDirectory').textContent=preview.directory;
  $('deleteConfirmImpact').textContent=preview.whole?'将删除整个实验目录，包括 RTL、Testbench、约束、脚本、对话、波形和全部运行结果，同时删除对应的 OpenCode 会话。':preview.impacts.length?'所选文件包含源码或运行证据，删除后可能需要重新生成、实现或仿真验证。':'删除后将无法再查看所选波形，源码与其他运行记录保留。';
  $('deleteConfirmCount').textContent='查看 '+preview.file_count+' 个文件的清单';
  $('deleteConfirmFiles').innerHTML=preview.paths.map(name=>'<li>'+esc(name)+'</li>').join('');
  $('deleteConfirmNotice').textContent='';$('confirmDeleteButton').disabled=false;
  if(!$('deleteConfirmDialog').open)$('deleteConfirmDialog').showModal();$('cancelDelete').focus();
}
async function openExperimentDelete(id){
  try{showDeleteConfirmation(await api('/api/teaching/delete-preview',{id}));}
  catch(error){notice(error.message,true);}
}
$('cleanupFiles').addEventListener('click',()=>openCleanup(selected));
for(const id of ['closeCleanup','cancelCleanup'])$(id).addEventListener('click',()=>$('cleanupDialog').close());
$('cleanupSearch').addEventListener('input',renderCleanup);$('cleanupKind').addEventListener('change',renderCleanup);
$('cleanupList').addEventListener('change',event=>{const input=event.target.closest('[data-cleanup-path]');if(!input||input.disabled||!cleanupState)return;if(input.checked)cleanupState.chosen.add(input.dataset.cleanupPath);else cleanupState.chosen.delete(input.dataset.cleanupPath);renderCleanup();});
$('selectVisibleFiles').addEventListener('click',()=>{if(!cleanupState||cleanupState.data.busy)return;for(const f of visibleCleanupFiles())if(f.deletable)cleanupState.chosen.add(f.path);renderCleanup();});
$('clearCleanupSelection').addEventListener('click',()=>{cleanupState?.chosen.clear();renderCleanup();});
$('reviewCleanup').addEventListener('click',async()=>{if(!cleanupState)return;try{showDeleteConfirmation(await api('/api/teaching/cleanup-preview',{id:cleanupState.id,paths:[...cleanupState.chosen]}));}catch(error){$('cleanupNotice').textContent=error.message;}});
$('cancelDelete').addEventListener('click',()=>{pendingDelete=null;$('deleteConfirmDialog').close();});
$('deleteConfirmDialog').addEventListener('cancel',event=>{if(deleteInProgress)event.preventDefault();else pendingDelete=null;});
$('deleteConfirmForm').addEventListener('submit',async event=>{
  event.preventDefault();if(!pendingDelete||deleteInProgress)return;
  const preview=pendingDelete;deleteInProgress=true;$('confirmDeleteButton').disabled=true;$('cancelDelete').disabled=true;
  $('deleteConfirmNotice').textContent='正在删除，请稍候…';
  try{
    const result=await api(preview.whole?'/api/teaching/delete':'/api/teaching/cleanup',{id:preview.id,token:preview.token});
    pendingDelete=null;$('deleteConfirmDialog').close();signature=fileSignature=flowSignature='';
    const problems=result.errors.map(e=>e.path+'：'+e.error).join('\n');
    const message=result.deleted_experiment?'已删除实验「'+preview.title+'」。':'已删除 '+result.deleted_files+' 个文件'+(result.bytes!=null?'，释放 '+storageSize(result.bytes):'')+'。';
    if(result.deleted_experiment){
      if(cleanupState?.id===preview.id){cleanupState=null;$('cleanupDialog').close();}
      if(selected===preview.id){selectExperiment(null);experiment=null;}
    }else if(cleanupState?.id===preview.id){
      cleanupState.data=await api('/api/teaching/storage?id='+encodeURIComponent(preview.id));cleanupState.chosen.clear();
      deleteInProgress=false;renderCleanup();$('cleanupNotice').textContent=message+(problems?'\n以下文件未删除，可关闭占用程序后重试：\n'+problems:'');
    }
    await refresh();notice(message+(problems?'\n部分文件未删除：\n'+problems:''),!!problems);
  }catch(error){$('deleteConfirmNotice').textContent=error.message+'\n请取消后重新预览，核对当前文件再重试。';}
  finally{deleteInProgress=false;$('confirmDeleteButton').disabled=!pendingDelete;$('cancelDelete').disabled=false;}
});
initEdaView();refresh();setInterval(refresh,1200);
