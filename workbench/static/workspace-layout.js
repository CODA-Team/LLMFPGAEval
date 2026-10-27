"use strict";
const WORKSPACE_LAYOUT_KEY='rtlLab.workspaceLayout.v1';
const WORKSPACE_DEFAULTS={sidebar:218,split:0.535,sidebarClosed:false,inspectorClosed:false};
function normalizeWorkspaceLayout(value){
  const v=value&&typeof value==='object'?value:{};
  const numeric=(key,min,max)=>typeof v[key]==='number'&&Number.isFinite(v[key])?Math.min(max,Math.max(min,v[key])):WORKSPACE_DEFAULTS[key];
  return {sidebar:numeric('sidebar',170,420),split:numeric('split',0.2,0.8),
    sidebarClosed:v.sidebarClosed===true,inspectorClosed:v.inspectorClosed===true};
}
function fitWorkspaceLayout(value,viewport){
  const layout=normalizeWorkspaceLayout(value);
  if(viewport>950){
    layout.sidebar=Math.min(layout.sidebar,Math.max(170,viewport-650));
    const available=viewport-(layout.sidebarClosed?0:layout.sidebar)-28;
    layout.split=Math.max(320/available,Math.min(1-280/available,layout.split));
  }
  return layout;
}
function initWorkspaceLayout(){
  let layout;
  try{layout=normalizeWorkspaceLayout(JSON.parse(localStorage.getItem(WORKSPACE_LAYOUT_KEY)));}catch{layout=normalizeWorkspaceLayout(null);}
  const nav=$('sidebar'),workspace=$('workspace'),inspector=$('inspector'),leftGrip=$('sidebarResize'),middleGrip=$('workspaceResize');
  let drag=null;
  function persist(){try{localStorage.setItem(WORKSPACE_LAYOUT_KEY,JSON.stringify(layout));}catch{/* Layout still works without storage. */}}
  function apply(){
    const fit=fitWorkspaceLayout(layout,window.innerWidth);
    document.documentElement.style.setProperty('--nav-width',fit.sidebar+'px');
    document.documentElement.style.setProperty('--chat-share',fit.split+'fr');
    document.documentElement.style.setProperty('--result-share',(1-fit.split)+'fr');
    document.body.classList.toggle('sidebar-closed',fit.sidebarClosed);
    document.body.classList.toggle('inspector-closed',fit.inspectorClosed);
    nav.hidden=fit.sidebarClosed;inspector.hidden=fit.inspectorClosed;
    leftGrip.hidden=fit.sidebarClosed;middleGrip.hidden=fit.inspectorClosed;
    for(const [id,closed,label] of [['toggleSidebar',fit.sidebarClosed,'实验列表'],['toggleInspector',fit.inspectorClosed,'工程结果']]){
      $(id).setAttribute('aria-expanded',String(!closed));
      $(id).textContent=(closed?'展开':'收起')+label;
    }
    leftGrip.setAttribute('aria-valuenow',String(Math.round(fit.sidebar)));
    leftGrip.setAttribute('aria-valuemax',String(Math.min(420,Math.max(170,window.innerWidth-650))));
    middleGrip.setAttribute('aria-valuenow',String(Math.round(fit.split*100)));
  }
  function setSplit(clientX){
    const rect=workspace.getBoundingClientRect(),available=rect.width-8;
    const left=Math.max(Math.min(320,available/2),Math.min(available-Math.min(280,available/2),clientX-rect.left-4));
    layout.split=left/available;
  }
  function finish(cancel=false){
    if(!drag)return;
    const previous=drag;drag=null;
    if(cancel)layout=previous.layout;
    if(previous.grip.hasPointerCapture(previous.pointerId))previous.grip.releasePointerCapture(previous.pointerId);
    document.body.classList.remove('resizing-columns');apply();persist();
  }
  for(const [grip,kind] of [[leftGrip,'sidebar'],[middleGrip,'split']]){
    grip.addEventListener('pointerdown',event=>{
      if(event.button!==0||window.innerWidth<=950)return;
      event.preventDefault();grip.focus();
      drag={kind,grip,pointerId:event.pointerId,layout:{...layout}};
      grip.setPointerCapture(event.pointerId);document.body.classList.add('resizing-columns');
    });
    grip.addEventListener('pointermove',event=>{
      if(!drag||drag.grip!==grip||drag.pointerId!==event.pointerId)return;
      if(kind==='sidebar')layout.sidebar=event.clientX;
      else setSplit(event.clientX);
      layout=fitWorkspaceLayout(layout,window.innerWidth);apply();
    });
    grip.addEventListener('pointerup',()=>finish());
    grip.addEventListener('pointercancel',()=>finish(true));
    grip.addEventListener('lostpointercapture',()=>finish());
    grip.addEventListener('dblclick',()=>{layout[kind]=WORKSPACE_DEFAULTS[kind];apply();persist();});
    grip.addEventListener('keydown',event=>{
      if(event.key==='Escape'){finish(true);return;}
      if(!['ArrowLeft','ArrowRight','Home','End','Enter'].includes(event.key))return;
      event.preventDefault();
      if(event.key==='Enter')layout[kind]=WORKSPACE_DEFAULTS[kind];
      else if(kind==='sidebar')layout.sidebar=event.key==='Home'?170:event.key==='End'?420:fitWorkspaceLayout(layout,window.innerWidth).sidebar+(event.key==='ArrowRight'?16:-16);
      else layout.split=event.key==='Home'?0.2:event.key==='End'?0.8:fitWorkspaceLayout(layout,window.innerWidth).split+(event.key==='ArrowRight'?0.03:-0.03);
      layout=fitWorkspaceLayout(layout,window.innerWidth);apply();persist();
    });
  }
  $('toggleSidebar').addEventListener('click',()=>{finish();layout.sidebarClosed=!layout.sidebarClosed;apply();persist();});
  $('toggleInspector').addEventListener('click',()=>{finish();layout.inspectorClosed=!layout.inspectorClosed;apply();persist();});
  $('resetLayout').addEventListener('click',()=>{finish();layout={...WORKSPACE_DEFAULTS};apply();persist();});
  window.addEventListener('resize',apply);
  apply();
}
if(typeof document!=='undefined')document.addEventListener('DOMContentLoaded',initWorkspaceLayout);
