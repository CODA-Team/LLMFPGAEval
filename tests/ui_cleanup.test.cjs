const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const nodes=new Map(),requests=[];
function element(id){if(!nodes.has(id))nodes.set(id,{value:'',textContent:'',innerHTML:'',hidden:false,disabled:false,open:false,checked:false,handlers:{},
 classList:{toggle(){}},setAttribute(){},focus(){},addEventListener(n,h){this.handlers[n]=h;},showModal(){this.open=true;},close(){this.open=false;}});return nodes.get(id);}
const inv={id:'aaa',title:'计算器',directory:'D:/lab/aaa',busy:false,bytes:1028,file_count:2,files:[{path:'fpga/post_route/run/wave.vcd',bytes:1024,kind:'waveform',deletable:true},{path:'session.json',bytes:4,kind:'other',deletable:false,reason:'记录'}]};
const context=vm.createContext({console,Map,Set,Date,JSON,setInterval(){},document:{getElementById:element,querySelectorAll:()=>[]},initEdaView(){},renderStageTree(){},
 fetch:async(path,options)=>{let body=options?.body?JSON.parse(options.body):undefined;requests.push({path,body});let value;
 if(path.includes('/storage?'))value=inv;
 else if(path.endsWith('-preview'))value={id:body.id,title:'计算器',directory:'D:/lab/aaa',whole:path.endsWith('/delete-preview'),token:'confirmed-preview',file_count:1,bytes:1024,paths:['fpga/post_route/run/wave.vcd'],impacts:[]};
 else if(path.endsWith('/cleanup')||path.endsWith('/delete'))value={deleted_files:1,bytes:1024,errors:[],deleted_experiment:path.endsWith('/delete')};
 else value={settings:{model:'deepseek-flash',tools:{}},sessions:[],active_id:null,token:'token'};
 return{ok:true,json:async()=>value};}});
vm.runInContext(fs.readFileSync('workbench/static/chat.js','utf8'),context);
const event={preventDefault(){}};
(async()=>{
 await new Promise(r=>setImmediate(r));vm.runInContext('refresh=async()=>{};selected="aaa";experiment={id:"aaa",title:"计算器"};',context);
 assert.equal(typeof element('cleanupFiles').handlers.click,'function','file cleanup entry required');
 await element('cleanupFiles').handlers.click();assert.equal(element('cleanupDialog').open,true);
 assert.match(element('cleanupList').innerHTML,/wave\.vcd/);
 await element('selectVisibleFiles').handlers.click();
 await element('reviewCleanup').handlers.click();
 assert.equal(element('deleteConfirmDialog').open,true);
 assert.equal(requests.at(-1).path,'/api/teaching/cleanup-preview');
 const before=requests.length;await element('cancelDelete').handlers.click();assert.equal(requests.length,before,'cancel cannot delete');
 await element('reviewCleanup').handlers.click();await element('deleteConfirmForm').handlers.submit(event);
 assert(requests.some(r=>r.path==='/api/teaching/cleanup'&&r.body.token==='confirmed-preview'));
 await vm.runInContext('openExperimentDelete("aaa")',context);
 vm.runInContext('selected="bbb";',context);
 await element('deleteConfirmForm').handlers.submit(event);
 assert.deepEqual(requests.findLast(r=>r.path==='/api/teaching/delete').body,{id:'aaa',token:'confirmed-preview'},'confirm keeps original experiment');
 console.log('Cleanup confirmation, cancellation, scoped selection and sidebar target: passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
