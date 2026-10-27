"use strict";
let conversationSession=null;
function conversationTurns(messages){
  const turns=[];
  for(const [index,message] of messages.entries()){
    const human=message.role==='user'&&message.origin!=='platform';
    if(human||!turns.length)turns.push({start:index,user:human?{message,index}:null,process:[],answer:null});
    if(!human)turns.at(-1).process.push({message,index});
  }
  for(const turn of turns){
    const last=turn.process.at(-1),m=last?.message;
    if(m?.role==='assistant'&&m.text?.trim()&&m.completed!==false&&
       (m.finish==='stop'||(!m.finish&&!m.tools?.length))){
      turn.answer=turn.process.pop();
    }
  }
  return turns;
}
function conversationMessage(entry,answer=false,active=false){
  const m=entry.message,i=entry.index,platform=m.origin==='platform';
  const label=platform?'平台 · 自动续跑':m.role==='user'?'你':'RTL 助手';
  const body=m.text?'<div class="message-body">'+(m.role==='assistant'?richText(m.text):esc(m.text))+'</div>':'';
  const calls=(m.tools||[]).map((t,j)=>'<details class="tool-call" data-fold="call-'+i+'-'+j+'"><summary>'+esc(t.name)+' · '+
    esc({completed:'已完成',running:'执行中',pending:'等待执行',error:'失败'}[t.status]||t.status)+
    '</summary><pre>'+esc(JSON.stringify(t.input,null,2))+'\n'+esc(t.output)+'</pre></details>').join('');
  const tools=calls?'<details class="tool-group" data-fold="tools-'+i+'"><summary>工具操作 · '+m.tools.length+' 项</summary>'+calls+'</details>':'';
  let detail='';
  if(m.role==='assistant'){
    if(m.finish==='length')detail='模型回复达到长度上限，未完整输出。后续续跑记录保留在本轮处理过程中。';
    else if(!m.text?.trim()&&!calls)detail=active?(m.activity==='reasoning'?'模型正在思考，尚未输出正文或调用工具…':'正在等待模型回复…'):'这条回复没有正文。';
  }
  return '<article class="message '+esc(platform?'platform':m.role)+'" data-message-index="'+i+'"'+(answer?' data-answer':'')+
    '><div class="message-heading"><span class="avatar">'+(platform?'↻':m.role==='user'?'你':'✧')+'</span>'+label+
    '</div>'+body+tools+(detail?'<p class="hint">'+esc(detail)+'</p>':'')+'</article>';
}
function processPreview(turn){
  const m=turn.process.at(-1)?.message;
  const tool=m?.tools?.find(t=>t.status==='running'||t.status==='pending');
  if(tool)return '正在调用 '+tool.name+'…';
  if(m?.activity==='reasoning')return '模型正在思考…';
  return m?.text?.trim()?m.text.replace(/\s+/g,' ').slice(0,160):'正在等待模型回复…';
}
function renderMessages(){
  const scroll=$('chatScroll'),changed=conversationSession!==selected;
  const nearBottom=changed||scroll.scrollHeight-scroll.scrollTop-scroll.clientHeight<80,oldTop=scroll.scrollTop;
  const focused=typeof document!=='undefined'?document.activeElement:null;
  const focusedFold=!changed&&focused?.matches('summary')&&scroll.contains(focused)?focused.parentElement.dataset.fold:null;
  const focusedJump=!changed&&focused?.matches('[data-chat-jump]')?focused.dataset.chatJump:null;
  const logPositions=changed?[]:Array.from(scroll.querySelectorAll('.tool-call[data-fold] pre')).filter(n=>n.scrollTop||n.scrollLeft)
    .map(n=>({fold:n.parentElement.dataset.fold,top:n.scrollTop,left:n.scrollLeft}));
  // Anchor the first visible message while polling appends work elsewhere.
  let anchor=null;
  if(!nearBottom&&scroll.getBoundingClientRect){
    const top=scroll.getBoundingClientRect().top;
    const node=Array.from(scroll.querySelectorAll('[data-message-index]')).find(n=>!n.closest('details:not([open])')&&n.getClientRects().length&&n.getBoundingClientRect().bottom>top);
    if(node)anchor={index:node.dataset.messageIndex,turn:node.closest('.chat-turn')?.id,offset:node.getBoundingClientRect().top-top};
  }
  const turns=conversationTurns(experiment?.messages||[]);
  const html=turns.map((turn,number)=>{
    const active=experiment?.status==='running'&&number===turns.length-1;
    const toolCount=turn.process.reduce((n,e)=>n+(e.message.tools?.length||0),0);
    const failed=turn.process.reduce((n,e)=>n+(e.message.tools||[]).filter(t=>t.status==='error').length,0);
    const process=turn.process.length?'<details class="process-group" data-fold="process-'+turn.start+'"><summary>'+
      '<span>'+(active?'处理中':'处理过程')+' · '+turn.process.length+' 条进展'+(toolCount?' · '+toolCount+' 次工具操作':'')+'</span>'+
      (failed?'<small>含 '+failed+' 条工具错误</small>':'')+'</summary>'+
      turn.process.map((entry,i)=>conversationMessage(entry,false,active&&i===turn.process.length-1)).join('')+'</details>':'';
    return '<section class="chat-turn" id="chat-turn-'+turn.start+'" tabindex="-1">'+
      (turn.user?conversationMessage(turn.user):'')+process+
      (active&&!turn.answer?'<p class="process-preview" role="status">'+esc(processPreview(turn))+'</p>':'')+
      (turn.answer?conversationMessage(turn.answer,true):
        !active&&turn.process.length?'<p class="hint turn-incomplete">本轮没有完整的最终回复，可展开处理过程查看记录。</p>':'')+'</section>';
  }).join('');
  paint('messages',html);
  for(const position of logPositions){
    const node=scroll.querySelector('.tool-call[data-fold="'+position.fold+'"] pre');
    if(node){node.scrollTop=position.top;node.scrollLeft=position.left;}
  }
  if($('conversationIndexItems')){
    paint('conversationIndexItems',turns.length?turns.map((turn,i)=>{
      const title=(turn.user?.message.text||'早期记录').replace(/\s+/g,' ').trim();
      return '<button type="button" data-chat-jump="chat-turn-'+turn.start+'" title="'+esc(title)+'"><span>'+String(i+1).padStart(2,'0')+
        '</span>'+esc(title.slice(0,90))+'</button>';
    }).join(''):'<p class="hint">开始对话后，在这里跳转到每次提问。</p>');
  }
  if($('conversationIndexCount'))$('conversationIndexCount').textContent=turns.length;
  if(focusedFold){
    const node=scroll.querySelector('details[data-fold="'+focusedFold+'"] > summary');
    if(node&&!node.parentElement.parentElement.closest('details:not([open])'))node.focus({preventScroll:true});
  }
  if(focusedJump&&$('conversationIndex').open){
    $('conversationIndexItems').querySelector('[data-chat-jump="'+focusedJump+'"]')?.focus({preventScroll:true});
  }
  conversationSession=selected;
  if(nearBottom)scroll.scrollTop=scroll.scrollHeight;
  else{
    let node=anchor&&scroll.querySelector('[data-message-index="'+anchor.index+'"]'),offset=anchor?.offset;
    if(node&&(node.closest('details:not([open])')||!node.getClientRects().length)){
      const turn=scroll.querySelector('#'+anchor.turn);
      node=turn?.querySelector('.process-group > summary')||turn;offset=12;
    }
    scroll.scrollTop=node?scroll.scrollTop+node.getBoundingClientRect().top-scroll.getBoundingClientRect().top-offset:oldTop;
  }
}
function initConversationUI(){
  document.addEventListener('click',event=>{
    const jump=event.target.closest('[data-chat-jump]');
    if(!jump)return;
    const target=document.getElementById(jump.dataset.chatJump);
    if(!target)return;
    $('conversationIndex').open=false;
    const scroll=$('chatScroll');
    scroll.scrollTop+=target.getBoundingClientRect().top-scroll.getBoundingClientRect().top-12;
    target.focus({preventScroll:true});
  });
  $('chatLatest').addEventListener('click',()=>{$('chatScroll').scrollTop=$('chatScroll').scrollHeight;});
}
if(typeof document!=='undefined')document.addEventListener('DOMContentLoaded',initConversationUI);
