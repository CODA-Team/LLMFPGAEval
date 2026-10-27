const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const views={},scroll={scrollHeight:100,scrollTop:0,clientHeight:100,querySelectorAll:()=>[]};
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const context=vm.createContext({console,Map,selected:'example',experiment:null,esc,richText:esc,$:id=>id==='chatScroll'?scroll:null});
vm.runInContext(fs.readFileSync('workbench/static/eda-view.js','utf8'),context);
if(fs.existsSync('workbench/static/chat-conversation.js'))
  vm.runInContext(fs.readFileSync('workbench/static/chat-conversation.js','utf8'),context);
context.paint=(id,html)=>views[id]=html;
function fixture(messages,status='passed'){context.experiment={messages,status};context.renderMessages();return views.messages;}
const messages=[
  {role:'user',text:'做一个计数器'},
  {role:'assistant',text:'先检查接口',finish:'tool-calls',completed:true,tools:[{name:'rtl_board_info',status:'completed',output:'board'}]},
  {role:'assistant',text:'正在修正 RTL',finish:'tool-calls',completed:true,tools:[]},
  {role:'user',origin:'platform',text:'自动续跑'},
  {role:'assistant',text:'计数器检查通过',finish:'stop',completed:true,tools:[]}
];
const html=fixture(messages);
assert.match(html,/class="process-group"/);
assert.equal((html.match(/class="chat-turn"/g)||[]).length,1);
assert.match(html,/data-answer/);
assert.ok(html.indexOf('计数器检查通过')>html.lastIndexOf('</details>'),'final answer must remain outside the process disclosure');
assert.doesNotMatch(html,/<details class="process-group"[^>]* open/);
assert.match(html,/平台 · 自动续跑/);
const turns=context.conversationTurns([...messages,{role:'user',text:'频率是多少？'},
  {role:'assistant',text:'25 MHz',finish:'stop',completed:true}]);
assert.equal(turns.length,2);
assert.equal(turns[0].process.length,3);
assert.equal(turns[1].answer.message.text,'25 MHz');
const active=fixture(messages.slice(0,3).concat({role:'assistant',text:'',activity:'reasoning',completed:false,tools:[]}),'running');
assert.match(active,/正在思考/);
assert.doesNotMatch(active,/data-answer/);
assert.match(fixture(messages.slice(0,3),'interrupted'),/没有完整的最终回复/);
assert.match(fixture([{role:'assistant',text:'未写完',finish:'length',completed:true}],'failed'),/长度上限/);
const legacy=fixture([{role:'user',text:'旧实验'},{role:'assistant',text:'旧版回复',tools:[]}]);
assert.match(legacy,/data-answer/);
assert.match(legacy,/旧版回复/);
const xss=fixture([{role:'user',text:'<img src=x onerror=alert(1)>'},{role:'assistant',text:'done',completed:true,finish:'stop'}]);
assert.doesNotMatch(xss,/<img /);
assert.match(xss,/&lt;img/);
console.log('Conversation grouping, final replies, continuations, interrupted/legacy messages and escaping: passed');
