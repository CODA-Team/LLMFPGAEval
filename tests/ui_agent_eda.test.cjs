const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const context=vm.createContext({console,Map});
vm.runInContext(fs.readFileSync('workbench/static/eda-view.js','utf8'),context);
vm.runInContext(fs.readFileSync('workbench/static/chat-conversation.js','utf8'),context);
vm.runInContext(`
let selected='test';
let experiment;
let views={};
const esc=v=>String(v??'');
const timeLabel=v=>v;
const evidenceURL=()=>'';
const flowURL=(stage,run,file)=>'/artifact/'+run+'/'+file;
paint=(id,html)=>views[id]=html;
function renderFixture(statuses){
  views={};
  const build={id:'20261023T000000_aaaaaaaa',time:'now',status:'passed',ok:true,
    source_sha256:'source',project_sha256:'old-project',
    steps:['optimize_rtl','optimize_gate','place','route','write_verilog','bitgen'].map(name=>({name,args:[],status:'passed'}))};
  experiment={stage_states:statuses,simulation:{source_sha256:'source',verified_current:false,latest:{id:'functional',time:'now',source_sha256:'source',project_sha256:'old-project',ok:true,status:'passed'}},
    flow:{project:{top:'lab_top',device:'EG4',clock_period_ns:20,pins:{}},board:{pins:{}},
      current_project_sha256:'old-project',implementation_evidence_valid:false,
      implementation_ok:false,implementation:build,runs:{}}};
  renderStageTree();
  return views.flowStages;
}
`,context);
for(const statuses of [['running',...Array(6).fill('pending')],['prepared','failed',...Array(5).fill('pending')],
                       ['passed','passed','stale','stale','stale','stale','pending']]){
  const html=vm.runInContext(`renderFixture(${JSON.stringify(statuses)})`,context);
  const rows=[...html.matchAll(/<details class="stage-node"[^]*?<summary>([^]*?)<\/summary>/g)];
  assert.equal(rows.length,7);
  statuses.forEach((status,i)=>{
    const labels={running:'运行中',pending:'未运行',prepared:'已准备',failed:'失败',passed:'通过',stale:'证据需核对'};
    assert.ok(rows[i][1].includes('>'+labels[status]+'</span>'),`stage ${i+1}: ${status}`);
    if(status!=='passed')assert.ok(!rows[i][1].includes('badge good'));
  });
  if(statuses[1]==='pending')assert.ok(!html.includes('TEST_PASS'));
}
console.log('UI stage rollback, preparation and evidence status rendering: passed');
const retained=vm.runInContext(`
renderFixture(['passed','running','failed','passed','passed','passed','passed']);
const oldBuild={...experiment.flow.implementation,id:'original-build',inputs_current:false,
  changed_inputs:['rtl/top.v'],artifacts:{'design.bit':'hash','route.sdf':'hash','post_route.v':'hash'}};
const newBuild={...oldBuild,id:'failed-retry',ok:false,status:'failed',steps:[{name:'optimize_rtl',args:[],status:'failed'}]};
const oldPost={id:'original-post',status:'passed',ok:true,implementation_id:'original-build',
  inputs_current:false,changed_inputs:['tb/tb.sv'],implementation_current:false,waveform:true,tests_passed:['kept_test']};
experiment.stage_results=[{run:null},{run:experiment.simulation.latest},{run:newBuild},
  {run:oldBuild},{run:oldBuild},{run:oldBuild},{run:oldPost}];
renderStageTree();views.flowStages;
`,context);
assert.match(retained,/original-build\/design.bit/);
assert.match(retained,/original-post\/wave.vcd/);
assert.match(retained,/输入已修改/);
assert.match(retained,/tb\/tb.sv/);
assert.match(retained,/RTL 功能仿真/);
assert.match(retained,/kept_test/);
assert.doesNotMatch(retained,/failed-retry\/design.bit/);
assert.doesNotMatch(retained,/修改后重新验证/);
console.log('Retained stages use their own run, version notes and downloadable artifacts: passed');
vm.runInContext(`
const scroll={scrollHeight:100,scrollTop:0,clientHeight:100,querySelectorAll:()=>[]};
const $=()=>scroll;
function messageFixture(message,status='running'){
  experiment={messages:[message],status};renderMessages();return views.messages;
}
`,context);
const thinking=vm.runInContext("messageFixture({role:'assistant',text:'',tools:[],activity:'reasoning',completed:false})",context);
assert.match(thinking,/正在思考/);
const truncated=vm.runInContext("messageFixture({role:'assistant',text:'',tools:[],finish:'length',completed:true},'failed')",context);
assert.match(truncated,/长度上限/);
const empty=vm.runInContext("messageFixture({role:'assistant',text:'',tools:[]},'ready')",context);
assert.match(empty,/没有正文/);
const automatic=vm.runInContext("messageFixture({role:'user',origin:'platform',text:'自动续跑',tools:[]})",context);
assert.match(automatic,/平台/);
console.log('UI thinking, truncation, historical empty reply and automatic continuation rendering: passed');
