// 左栏拖动宽度验收(2026-10-04 用户「网页左栏应该可以拖动宽度，长的字看不见」)。rc=1 = 有一项不对。
// node test_railw.mjs [网址 左栏选择器 默认宽 localStorage键 条目选择器]   默认 = 对话管理器; 别的看板: node test_railw.mjs http://127.0.0.1:<端口>/ <左栏选择器> <默认宽> <键> <条目选择器>
import { spawn } from 'node:child_process';
const [URL0='http://127.0.0.1:8720/', RAIL='#rail', DEF='260', KEY='railW', ITEM='#rail .it .t'] = process.argv.slice(2); const W0=Number(DEF);
const p = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', ['--headless=new','--disable-gpu','--remote-debugging-port=9365','--user-data-dir='+process.env.TEMP+'/cdp_railw','--window-size=1400,900','about:blank']);
const sleep=ms=>new Promise(r=>setTimeout(r,ms)); let ws;
for(let i=0;i<40;i++){ try{ const j=await (await fetch('http://127.0.0.1:9365/json')).json(); const pg=j.find(x=>x.type==='page'); if(pg){ ws=new WebSocket(pg.webSocketDebuggerUrl); break; } }catch(e){} await sleep(250); }
await new Promise(r=>ws.onopen=r); let id=0; const pend={}, errs=[];
ws.onmessage=ev=>{const m=JSON.parse(ev.data); if(m.id&&pend[m.id]){pend[m.id](m);delete pend[m.id];} if(m.method==='Runtime.exceptionThrown') errs.push(m.params.exceptionDetails.exception?.description||m.params.exceptionDetails.text);};
const send=(method,params={})=>new Promise(r=>{const i=++id;pend[i]=r;ws.send(JSON.stringify({id:i,method,params}));});
const ev=async e=>{const r=await send('Runtime.evaluate',{expression:e,awaitPromise:true,returnByValue:true}); return r.result.result?.value ?? r.result.exceptionDetails?.text;};
const load=async()=>{ await send('Page.navigate',{url:URL0}); for(let i=0;i<80;i++){ if(await ev(`document.querySelectorAll(${JSON.stringify(ITEM)}).length>0`)) break; await sleep(100); } await sleep(300); };
const probe=`(()=>{ const r=document.querySelector(${JSON.stringify(RAIL)}); const ts=[...document.querySelectorAll(${JSON.stringify(ITEM)})]; return {w:Math.round(r.getBoundingClientRect().width), cut:ts.filter(t=>t.scrollWidth>t.clientWidth+1).length, n:ts.length, sw:document.documentElement.scrollWidth, iw:innerWidth}; })()`;
const mouse=(type,x,y)=>send('Input.dispatchMouseEvent',{type,x,y,button:'left',buttons:type==='mouseReleased'?0:1,clickCount:1});
await send('Runtime.enable'); await load(); await ev(`localStorage.removeItem(${JSON.stringify(KEY)})`); await load();
const out={}, bad=[];
out.before=await ev(probe);
const h=await ev(`(()=>{ const b=document.querySelector("#railDrag").getBoundingClientRect(); return {x:b.left+b.width/2, y:b.top+200}; })()`);
await mouse('mouseMoved',h.x,h.y); await mouse('mousePressed',h.x,h.y);
for(const x of [320,400,480]) await mouse('mouseMoved',x,h.y);
await mouse('mouseReleased',480,h.y); await sleep(200);
out.dragged=await ev(probe);
if(Math.abs(out.dragged.w-480)>8) bad.push('拖到 480 后左栏宽 '+out.dragged.w);
if(!(out.dragged.cut<out.before.cut || out.before.cut===0)) bad.push('变宽后被截断的标题没变少');
if(out.dragged.sw>out.dragged.iw+1) bad.push('页面横向溢出');
await load(); out.reloaded=await ev(probe);
if(Math.abs(out.reloaded.w-480)>8) bad.push('刷新后宽度没保留 '+out.reloaded.w);
const h2=await ev(`(()=>{ const b=document.querySelector("#railDrag").getBoundingClientRect(); return {x:b.left+b.width/2, y:b.top+200}; })()`);
await send('Input.dispatchMouseEvent',{type:'mousePressed',x:h2.x,y:h2.y,button:'left',clickCount:2}); await send('Input.dispatchMouseEvent',{type:'mouseReleased',x:h2.x,y:h2.y,button:'left',clickCount:2});
await ev(`document.querySelector("#railDrag").dispatchEvent(new MouseEvent("dblclick",{bubbles:true}))`); await sleep(150);
out.reset=await ev(probe);
if(Math.abs(out.reset.w-W0)>4) bad.push('双击没恢复 '+W0+' ('+out.reset.w+')');
if(Math.abs(out.before.w-W0)>4) bad.push('初始宽度不是 '+W0+' ('+out.before.w+')');
out.errs=errs; out.bad=bad; console.log(JSON.stringify(out)); ws.close(); p.kill(); process.exit(bad.length||errs.length?1:0);
