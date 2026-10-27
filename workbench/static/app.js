"use strict";
const $ = (id) => document.getElementById(id);
let state = null, selected = null, previousArtifactKey = "", polling = false;
const labels = {completed:"已完成",failed:"存在失败项",running:"运行中",queued:"已排队",interrupted:"运行中断"};
const escapeHTML = (value) => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const numeric = (value, unit="") => value == null || !Number.isFinite(Number(value)) ? "—" : Number(value).toLocaleString("zh-CN",{maximumFractionDigits:4}) + (unit ? " " + unit : "");
const currentJob = () => state?.jobs.find(j => j.id === selected);
function notice(message, error=false) { $("notice").hidden=false; $("notice").textContent=message; $("notice").className=error?"bad":""; }
async function api(path, data) {
  const options = data === undefined ? {} : {method:"POST",headers:{"Content-Type":"application/json","X-Workbench-Token":state?.token || ""},body:JSON.stringify(data)};
  const response = await fetch(path, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || response.statusText);
  return body;
}
function badge(id, text, kind) { $(id).textContent=text; $(id).className="badge " + kind; }
function stageBadge(target) {
  const job = currentJob(), stage = job?.[target];
  if (stage) return badge(target+"Badge",stage.ok?"编译通过":"编译失败",stage.ok?"good":"bad");
  const active = job?.status === "running" && (target==="fpga"?job.phase.includes("TD"):job.phase.includes("ORFS"));
  badge(target+"Badge",active?"运行中":job?.targets.includes(target)?"待运行":"未选择",active?"running":"neutral");
}
function render() {
  const job = currentJob(), f = job?.fpga, a = job?.asic, sim = job?.simulation;
  $("historyCount").textContent=state.jobs.length;
  $("history").innerHTML=state.jobs.slice(0,20).map(j => '<button class="historyitem '+(selected===j.id?"selected":"")+'" data-job="'+escapeHTML(j.id)+'">'+escapeHTML(j.project_name)+'<small>'+escapeHTML(new Date(j.created_at).toLocaleString("zh-CN",{month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"}))+' · '+escapeHTML(labels[j.status]||j.status)+'</small></button>').join("");
  $("runButton").disabled=state.busy;
  $("runButton").textContent=state.busy?"任务运行中…":"运行对比 ↗";
  $("settingsButton").disabled=state.busy;
  $("programButton").disabled=state.busy || !f?.ok || !sim?.ok;
  $("powerButton").disabled=state.busy || !f?.ok;
  $("jobTitle").textContent=job?"运行概览 · "+job.project_name:"运行概览";
  badge("jobStatus",job?(labels[job.status]||job.status)+" · "+job.phase:"等待首次运行",job?.status==="completed"?"good":job?.status==="failed"?"bad":state.busy?"running":"neutral");
  $("simValue").textContent=sim?(sim.passed??0)+" / "+(sim.total??0):"—";
  $("simNote").textContent=sim?(sim.ok?"RTL 自检通过 · 非板上验证":"RTL 仿真未通过"):"Icarus 自检 Testbench";
  $("fpgaValue").textContent=numeric(f?.timing?.wns_ns);
  $("asicValue").textContent=numeric(a?.timing?.wns_ns);
  $("clockValue").textContent=numeric(job?1000/job.project.clock_period_ns:50,"MHz");
  $("deviceValue").textContent=(job?.project.device||"EG4S20BG256")+" / "+(a?.platform||state.config.platform);
  $("platformLabel").textContent=a?.platform||state.config.platform;
  $("fpgaDuration").textContent=f?"耗时 "+numeric(f.duration_s,"s")+(job.programming?" · "+(job.programming.ok?"SRAM 下载成功":"SRAM 下载失败"):""):"等待编译";
  $("asicDuration").textContent=a?"耗时 "+numeric(a.duration_s,"s"):"通过 SSH 执行";
  stageBadge("fpga");stageBadge("asic");
  const rtl = sim ? (sim.ok?"通过":"失败")+"（"+(sim.passed??0)+"/"+(sim.total??0)+"）" : "未验证";
  const fp = f?.power, ap = a?.power;
  const method = {td_estimator:"TD 估计",measured:"实测",vectorless_estimate:"活动率估计"};
  const ptext = p => p?.total_mw == null ? "未测得" : numeric(p.total_mw,"mW")+" · "+(method[p.method]||p.method);
  const rows = [
    ["共同 RTL 功能仿真",rtl,rtl],
    ["映射后功能验证","未验证","未验证"],
    ["实际硬件功能","未验证","未流片 / 未验证"],
    ["最差建立裕量 / WS",numeric(f?.timing?.wns_ns,"ns"),numeric(a?.timing?.wns_ns,"ns")],
    ["最差保持裕量 / WHS",numeric(f?.timing?.whs_ns,"ns"),numeric(a?.timing?.whs_ns,"ns")],
    ["Fmax · 工具时序估计",numeric(f?.timing?.fmax_mhz,"MHz"),numeric(a?.timing?.fmax_mhz,"MHz")],
    ["FPGA 资源",f?.resources?numeric(f.resources.lut)+" LUT / "+numeric(f.resources.ff)+" FF / "+numeric(f.resources.bram)+" BRAM":"—","不适用"],
    ["ASIC 标准单元数 / 面积","不适用",a?.resources?numeric(a.resources.cell_count)+" cells / "+numeric(a.resources.cell_area_um2,"μm²"):"—"],
    ["功耗",ptext(fp),ptext(ap)],
    ["功耗范围",fp?.scope==="whole_board"?"整块开发板":fp?.scope==="fpga_chip"?"FPGA 芯片":"未记录",ap?.total_mw==null?"未测得":"映射后标准单元"],
    ["RTL 校验值",f?.rtl_sha256?f.rtl_sha256.slice(0,16)+"…":"—",a?.rtl_sha256?a.rtl_sha256.slice(0,16)+"…":"—"]
  ];
  $("metricsBody").innerHTML=rows.map(r=>"<tr>"+r.map((v,i)=>"<td"+(i===0?' scope="row"':"")+">"+escapeHTML(v)+"</td>").join("")+"</tr>").join("");
  $("hashNote").textContent=job?"SHA-256  "+job.manifest.rtl_sha256:"每次任务生成独立源码快照与 SHA-256";
  $("resultLink").hidden=!job;
  if(job) $("resultLink").href=fileURL(job.id,"result.json");
  const key=job?[job.id,job.status,job.phase,!!f,!!a].join(":"):"";
  if(key!==previousArtifactKey){previousArtifactKey=key;loadArtifacts().catch(e=>notice(e.message,true));}
}
function fileURL(job,path){return "/api/file?job="+encodeURIComponent(job)+"&path="+encodeURIComponent(path);}
async function refresh() {
  if(polling)return;
  polling=true;
  try {
    state=await api("/api/state");
    const firstLoad=!$("project").options.length;
    if(firstLoad) $("project").innerHTML=state.projects.map(p=>'<option value="'+escapeHTML(p.id)+'">'+escapeHTML(p.name)+' · '+escapeHTML(p.top)+'</option>').join("");
    if(!selected && state.jobs.length)selected=state.jobs[0].id;
    if(firstLoad && currentJob()) $("project").value=currentJob().project_id;
    if(firstLoad) $("useAsic").checked=!!(state.config.ssh_host&&state.config.orfs_root&&state.config.remote_work_root);
    render();
  } catch(e){notice("工作台连接失败："+e.message,true);}
  finally{polling=false;}
}
async function loadArtifacts(){
  const job=currentJob();
  if(!job)return;
  const id=job.id;
  const files=await api("/api/artifacts?job="+encodeURIComponent(id));
  if(selected!==id)return;
  const old=$("logSelect").value;
  $("logSelect").innerHTML=files.map(f=>'<option value="'+escapeHTML(f.path)+'">'+escapeHTML(f.path)+' ('+numeric(f.size/1024)+' KB)</option>').join("");
  if(files.some(f=>f.path===old))$("logSelect").value=old;
  else $("logSelect").value=files.some(f=>f.path==="asic/orfs.log")?"asic/orfs.log":files.some(f=>f.path==="fpga/td.log")?"fpga/td.log":"result.json";
  await loadLog();
}
async function loadLog(){
  const job=currentJob(),path=$("logSelect").value;
  if(!job||!path)return;
  $("downloadLink").hidden=false;$("downloadLink").href=fileURL(job.id,path);
  if(!/\.(log|json|rpt|sdc|tcl|v)$/.test(path)){$("logView").textContent="这是二进制产物，可使用“打开 / 下载文件”保存。";return;}
  const response=await fetch(fileURL(job.id,path));
  const text=await response.text();
  $("logView").textContent=text.length>80000?"（仅显示最后 80,000 字符；完整内容可下载）\n"+text.slice(-80000):text;
}
$("history").addEventListener("click",e=>{const button=e.target.closest("[data-job]");if(button){selected=button.dataset.job;previousArtifactKey="";render();}});
$("logSelect").addEventListener("change",()=>loadLog().catch(e=>notice(e.message,true)));
$("runButton").addEventListener("click",async()=>{
  try{
    const targets=[];if($("useFpga").checked)targets.push("fpga");if($("useAsic").checked)targets.push("asic");
    if(!targets.length)throw new Error("至少选择一条流程");
    $("runButton").disabled=true;
    const job=await api("/api/jobs",{project:$("project").value,targets});selected=job.id;notice("任务已开始。共同 RTL 仿真后运行选定流程；不会自动烧录。");await refresh();
  }catch(e){notice(e.message,true);$("runButton").disabled=false;}
});
$("checkButton").addEventListener("click",async()=>{
  $("checkButton").disabled=true;
  try{const result=await api("/api/check",{});notice(Object.entries(result.local).map(([k,v])=>k+": "+(v?"已找到":"缺失")).join(" · ")+"\n远程 ORFS："+(result.remote.ok?"就绪":"未就绪")+"\n"+(result.remote.log||result.remote.error||""),!result.remote.ok);}
  catch(e){notice(e.message,true);}finally{$("checkButton").disabled=false;}
});
const fields={td_root:"Windows TD 安装目录",iverilog:"iverilog.exe",vvp:"vvp.exe",ssh_host:"SSH 主机 / 配置别名",ssh_port:"SSH 端口",ssh_identity:"私钥文件路径（可留空，使用现有 SSH 配置）",orfs_root:"服务器 ORFS 根目录",remote_work_root:"服务器专用任务目录",remote_library_path:"远程依赖库目录（冒号分隔，可留空）",platform:"ASIC 平台",threads:"ORFS 线程数",timeout_s:"单流程超时（秒，留空不限制）"};
$("settingsButton").addEventListener("click",()=>{
  $("configFields").innerHTML=Object.entries(fields).map(([k,v])=>'<label>'+escapeHTML(v)+(k==="platform"?'<select name="platform"><option>nangate45</option></select>':'<input name="'+k+'" value="'+escapeHTML(state.config[k])+'" '+(["ssh_port","threads","timeout_s"].includes(k)?'type="number"':'type="text"')+'>')+'</label>').join("");
  $("settingsDialog").showModal();
});
$("settingsForm").addEventListener("submit",async e=>{
  e.preventDefault();const values=Object.fromEntries(new FormData(e.target));["ssh_port","threads","timeout_s"].forEach(k=>values[k]=k==='timeout_s'&&values[k].trim()===''?null:Number(values[k]));
  try{await api("/api/config",values);$("settingsDialog").close();notice("配置已保存；已有任务保留原来的配置记录。");await refresh();}catch(error){alert(error.message);}
});
document.querySelectorAll("[data-close]").forEach(b=>b.addEventListener("click",()=>$(b.dataset.close).close()));
$("programButton").addEventListener("click",async()=>{
  const job=currentJob();
  if(!confirm("将任务 "+job.id+" 的位流写入当前连接的 EG4S20 开发板 SRAM（掉电丢失）。请确认板卡、下载器和引脚约束正确。继续烧录？"))return;
  $("programButton").disabled=true;
  try{const result=await api("/api/program",{job:job.id,confirmation:"PROGRAM SRAM"});notice(result.ok?"SRAM 下载成功。实际板上功能仍需观察验证。":"下载失败："+result.log_tail,!result.ok);await refresh();}catch(e){notice(e.message,true);}
});
$("powerButton").addEventListener("click",()=>{$("powerDialog").showModal();});
$("powerForm").addEventListener("submit",async e=>{
  e.preventDefault();const values=Object.fromEntries(new FormData(e.target));values.total_mw=Number(values.total_mw);values.frequency_mhz=Number(values.frequency_mhz);values.job=selected;
  try{await api("/api/power",values);$("powerDialog").close();notice("功耗记录与证据副本已保存。");await refresh();}catch(error){alert(error.message);}
});
refresh();setInterval(refresh,2500);
