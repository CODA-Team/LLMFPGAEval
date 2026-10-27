"use strict";
// Rendering keeps expansion state scoped to the selected experiment.
const foldStates=new Map(), logPages=new Map();
function paint(id,html){
  const host=$(id);
  for(const el of host.querySelectorAll('details[data-fold]'))foldStates.set(host.dataset.session+':'+el.dataset.fold,el.open);
  host.innerHTML=html;host.dataset.session=selected||'';
  for(const el of host.querySelectorAll('details[data-fold]')){
    const key=(selected||'')+':'+el.dataset.fold;if(foldStates.has(key))el.open=foldStates.get(key);
  }
}
function selectPanel(name){
  for(const tab of document.querySelectorAll('[data-tab]')){const active=tab.dataset.tab===name;tab.classList.toggle('active',active);tab.setAttribute('aria-selected',String(active));$(tab.dataset.tab+'Panel').hidden=!active;}
}
function stageBadge(status){return '<span class="badge '+({passed:'good',failed:'bad',running:'running'}[status]||'')+'">'+esc({passed:'通过',prepared:'已准备',ready:'待继续',failed:'失败',running:'运行中',stale:'证据需核对',unknown:'阶段未记录',pending:'未运行',cancelled:'已停止',manual:'待实测'}[status]||status)+'</span>';}
function commandTable(steps){
  if(!steps?.length)return '<p class="hint">此历史运行没有单独的命令记录，可查看原始日志。</p>';
  return '<div class="table-scroll"><table class="command-table"><thead><tr><th>实际工具命令</th><th>结果</th></tr></thead><tbody>'+steps.map(s=>'<tr><td><code>'+esc(s.name+' '+s.args.map(a=>/\s/.test(a)?'{'+a+'}':a).join(' '))+'</code></td><td>'+stageBadge(s.status)+'</td></tr>').join('')+'</tbody></table></div>';
}
function runURL(stage,id,file){return stage==='functional'?evidenceURL(id,file):flowURL(stage,id,file);}
function logKey(stage,run,file){return [selected,stage,run,file].join('|');}
function logBody(key){
  const page=logPages.get(key);
  if(!page)return '<button class="button load-log" data-key="'+esc(key)+'">查看原始日志</button>';
  return '<p class="hint">第 1–'+(page.next_line-1)+' 行 / 共 '+page.total_lines+' 行</p><pre class="raw-log">'+esc(page.content)+'</pre>'+(page.has_more?'<button class="button load-log" data-key="'+esc(key)+'">继续读取</button>':'');
}
function inputNote(r){
  if(!r)return '';
  let notes=[];
  if(r.inputs_current===false)notes.push('输入已修改：'+(r.changed_inputs||[]).join('、')+'。这里保留上次运行结果与文件，重跑本阶段后更新。');
  if(r.implementation_current===false)notes.push('使用实现 '+r.implementation_id+' 的网表和 SDF；不是最近一次实现。');
  if(r.evidence_valid===false)notes.push('部分运行证据已清理或改变，请核对已有文件；不代表工具当时运行失败。');
  return notes.map(note=>'<p class="input-note">'+esc(note)+'</p>').join('');
}
function runContent(stage,r){
  const log=stage==='implementation'||stage==='query'&&r.engine==='td'?'td.log':stage==='programming'?'program.log':'simulation.log';
  const key=logKey(stage,r.id,log);
  let files=['result.json',log];
  if(r.executor==='agent_script')files.push('commands.jsonl','source/'+r.script);
  else if(stage==='functional'||stage==='post_route')files.push('compile.log');
  if(stage==='implementation')files.push(...['timing.rpt','clock_summary.rpt','post_route.v','route.sdf','design.bit'].filter(n=>r.artifacts?.[n]));
  if(r.waveform)files.push('wave.vcd');
  return '<p class="run-meta">'+esc(r.id)+' · '+esc(r.duration_s??'—')+' s · '+esc(r.script||'历史流程')+'</p>'+
    inputNote(r)+(r.explanation?'<p class="settings-notice">'+esc(r.explanation)+'</p>':'')+
    (r.tests_passed?.length?'<div class="checks">'+r.tests_passed.map(t=>'<span>✓ '+esc(t)+'</span>').join('')+'</div>':'')+
    (r.timing?'<div class="metric-grid"><span>WNS<b>'+esc(r.timing.wns_ns??'—')+' ns</b></span><span>WHS<b>'+esc(r.timing.whs_ns??'—')+' ns</b></span><span>LUT / FF<b>'+esc(r.resources?.lut??'—')+' / '+esc(r.resources?.ff??'—')+'</b></span></div><p class="hint">时序检查：'+(r.timing_ok?'通过':'未通过 / 信息不足')+'；范围以本次约束与报告为准。</p>':'')+
    (stage==='post_route'?'<p class="hint">SDF：'+(r.sdf_annotated?'已标注':'未确认')+' · 警告 '+esc(r.warning_count??r.warnings?.length??0)+(r.warning_count===undefined&&r.warnings?.length===100?'（历史摘要上限，完整数量见日志）':'')+'</p>':'')+
    (r.board_compatible===false?'<p class="board-mismatch">上板核对未通过：本次脚本的引脚、时钟或顶层与工程记录不一致。</p>':'')+
    '<details data-fold="cmd-'+stage+'-'+r.id+'"><summary>实际命令与返回状态</summary>'+commandTable(r.steps)+'</details>'+
    '<details data-fold="log-'+stage+'-'+r.id+'"><summary>原始日志</summary><div data-log-key="'+esc(key)+'">'+logBody(key)+'</div></details>'+
    '<div class="run-links">'+files.map(file=>'<a href="'+runURL(stage,r.id,file)+'">'+esc(file.split('/').pop())+' ↓</a>').join('')+'</div>';
}
function runCard(stage,r){return '<details class="flow-run" data-fold="run-'+stage+'-'+r.id+'"><summary>'+esc(timeLabel(r.time))+' · '+esc(r.id.slice(-8))+' '+stageBadge(r.status)+'</summary>'+runContent(stage,r)+'</details>';}
function renderRuns(){paint('runs',experiment?.simulation?.runs?.length?experiment.simulation.runs.slice().reverse().map(r=>runCard('functional',r)).join(''):'<p class="empty">尚未运行功能仿真。</p>');}
function renderStageTree(){
  const flow=experiment?.flow,sim=experiment?.simulation;
  const rows=experiment?.stage_results;
  const runAt=i=>rows?rows[i]?.run:(i===1?sim?.latest:i===6?flow?.post_route:flow?.implementation);
  const status=experiment?.stage_states||Array(7).fill('pending');
  const tdContent=(index,names)=>{
    const b=runAt(index);
    return b?inputNote(b)+(b.executor==='fixed_template'&&!b.steps?.length&&!b.ok&&b.status!=='running'?'<p class="hint">这次历史 TD 运行未完成，旧版未记录失败位置，请查看完整日志。</p>':'')+commandTable(b.steps?.filter(s=>names.includes(s.name)))+
      '<p class="hint">'+esc(b.id)+' · '+esc(timeLabel(b.time))+'</p><div class="run-links">'+
      ['td.log','result.json',...(index===4?['timing.rpt','clock_summary.rpt']:[])].map(n=>'<a href="'+flowURL('implementation',b.id,n)+'">'+n+' ↓</a>').join('')+'</div>':
      '<p class="hint">尚未运行此阶段。</p>';
  };
  const preparation=status[0]==='running'?'助手正在编写或修复 RTL、测试、约束和脚本；已有结果保留，重跑时更新对应阶段。':
    flow?.project?flow.project.top+' · '+flow.project.device+' · '+flow.project.clock_period_ns+' ns':
    status[0]==='prepared'?'文件已用于功能仿真；TD 顶层和板卡引脚尚未登记，助手需在实现前补齐。':
    status[0]==='ready'?'已有工程文件，等待继续编辑或启动验证。':'等待助手确定接口、编写约束和记录工程。';
  const b=runAt(5),routing=runAt(4),f=runAt(1),p=runAt(6);
  const stages=[
    ['01','工程与约束','<p class="hint">'+esc(preparation)+'</p><button class="text-button" data-panel="code">查看 RTL、测试、约束和运行脚本 →</button>'],
    ['02','RTL 功能仿真',f?runContent('functional',f):''],
    ['03','综合与逻辑优化',tdContent(2,['import_device','read_verilog','read_sdc','optimize_rtl','optimize_gate'])],
    ['04','布局',tdContent(3,['place'])],
    ['05','布线与时序',tdContent(4,['route','report_timing','report_clock_summary'])+(routing?'<p class="hint">时序检查：'+(routing.timing_ok?'通过':routing.status==='running'?'等待报告':'未通过 / 信息不足')+' · WNS '+esc(routing.timing?.wns_ns??'—')+' ns</p>':'')],
    ['06','网表与比特流',b?tdContent(5,['write_verilog','bitgen'])+'<div class="run-links">'+['post_route.v','route.sdf','design.bit'].filter(n=>b.artifacts?.[n]).map(n=>'<a href="'+flowURL('implementation',b.id,n)+'">'+n+' ↓</a>').join('')+'</div>':''],
    ['07','SDF 后仿真',p?runContent('post_route',p):'']
  ];
  paint('flowStages','<p class="flow-caption">各阶段最近一次运行 · 仅重跑的阶段更新 · 历史文件保留</p><div class="stage-tree">'+stages.map(([id,title,body],i)=>{
    const run=i?runAt(i):null;
    const changed=run?.inputs_current===false||run?.implementation_current===false;
    return '<details class="stage-node" data-fold="stage-'+id+'"><summary><span class="stage-number">'+id+'</span><span>'+esc(title)+'</span>'+
      (changed?'<span class="input-changed">'+(run.inputs_current===false?'输入已修改':'实现版本不同')+'</span>':'')+stageBadge(status[i])+'</summary><div class="stage-body">'+
      (body||'<p class="hint">尚未运行此阶段。</p>')+'</div></details>';
  }).join('')+'</div>');
  const project=flow?.project;
  paint('flowProject',project?'<details class="flow-project" data-fold="pins"><summary>板卡引脚 · '+Object.keys(project.pins).length+' 个绑定</summary><table><thead><tr><th>端口</th><th>资源</th><th>引脚</th></tr></thead><tbody>'+Object.entries(project.pins).map(([port,res])=>'<tr><td>'+esc(port)+'</td><td>'+esc(res)+'</td><td>'+esc(flow.board.pins[res].pin)+'</td></tr>').join('')+'</tbody></table><p class="hint">约束脚本由助手编写；上板前核对实际板卡和接线。</p></details>':'');
  const history=['implementation','post_route','query','programming'].map(stage=>{
    const runs=flow?.runs?.[stage]||[];
    return runs.length?'<details class="history-group" data-fold="history-'+stage+'"><summary>'+esc({implementation:'TD 实现',post_route:'后仿真',query:'工具查询',programming:'下载'}[stage])+'记录 · '+runs.length+' 次</summary>'+runs.slice().reverse().map(r=>runCard(stage,r)).join('')+'</details>':'';
  }).join('');
  paint('flowEvidence',history);
}
function initEdaView(){
  for(const [formId,title] of [['programForm','08 · 下载到板卡 SRAM'],['observationForm','09 · 板上功能验证']]){
    const form=$(formId),wrapper=document.createElement('details'),summary=document.createElement('summary');
    wrapper.className='hardware-section';summary.textContent=title+' · 人工确认';form.before(wrapper);wrapper.append(summary,form);
  }
  document.addEventListener('click',async event=>{
    const panel=event.target.closest('[data-panel]');if(panel)selectPanel(panel.dataset.panel);
    const button=event.target.closest('.load-log');if(!button)return;
    const key=button.dataset.key,[id,stage,run,file]=key.split('|'),old=logPages.get(key);button.disabled=true;
    try{
      const page=await api('/api/teaching/read-run?'+new URLSearchParams({id,stage,run,file,start_line:old?.next_line||1,max_lines:120}));
      logPages.set(key,{...page,content:(old?old.content+'\n':'')+page.content});
      for(const node of document.querySelectorAll('[data-log-key]'))if(node.dataset.logKey===key)node.innerHTML=logBody(key);
    }catch(error){notice(error.message,true);button.disabled=false;}
  });
}
