// 「可回话 · 后台在跑」状态验收(2026-10-04 用户「模型空闲可回话、但该对话仍有后台子 agent 或后台 shell 在跑」)。node test_bg.mjs —— 只注入假状态。rc=1 = 有一项不对。
import { spawn } from 'node:child_process';
const p = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', ['--headless=new','--disable-gpu','--remote-debugging-port=9357','--user-data-dir='+process.env.TEMP+'/cdp_bg','--window-size=1400,900','about:blank']);
const sleep=ms=>new Promise(r=>setTimeout(r,ms)); let ws;
for(let i=0;i<40;i++){ try{ const j=await (await fetch('http://127.0.0.1:9357/json')).json(); const pg=j.find(x=>x.type==='page'); if(pg){ ws=new WebSocket(pg.webSocketDebuggerUrl); break; } }catch(e){} await sleep(250); }
await new Promise(r=>ws.onopen=r); let id=0; const pend={}, errs=[];
ws.onmessage=ev=>{const m=JSON.parse(ev.data); if(m.id&&pend[m.id]){pend[m.id](m);delete pend[m.id];} if(m.method==='Runtime.exceptionThrown') errs.push(m.params.exceptionDetails.exception?.description||m.params.exceptionDetails.text);};
const send=(method,params={})=>new Promise(r=>{const i=++id;pend[i]=r;ws.send(JSON.stringify({id:i,method,params}));});
const ev=async e=>{const r=await send('Runtime.evaluate',{expression:e,awaitPromise:true,returnByValue:true}); return r.result.result?.value ?? r.result.exceptionDetails?.text;};
await send('Runtime.enable');
await send('Page.navigate',{url:'http://127.0.0.1:8720/'});
for(let i=0;i<100;i++){ if(await ev(`document.querySelectorAll('#rail .it[data-id]').length > 0`) === true) break; await sleep(100); }
await sleep(1500);
const bad=[], out={};
// 拿一个活着的会话, 注入「空闲 + 后台 1 agent 1 shell」, 看左栏分到「可回话 · 后台在跑」组、状态框写明; 撤掉后回到原组(阴性)
const sid = await ev(`Object.keys(RAW_STATUS).find(k => RAW_STATUS[k] && RAW_STATUS[k].state !== "closed")`);
const orig = await ev(`JSON.stringify(RAW_STATUS[${JSON.stringify(sid)}])`);
const inj = on => `(()=>{ const s=Object.assign({},RAW_STATUS[${JSON.stringify(sid)}]); if(${on}){ Object.assign(s,{state:"done",sub:"bg",bg:[{id:"a1",kind:"agent",age:120},{id:"b2",kind:"shell",age:30}]}); } else { Object.assign(s, JSON.parse(${JSON.stringify(orig)})); if(!JSON.parse(${JSON.stringify(orig)}).bg) delete s.bg; } RAW_STATUS[${JSON.stringify(sid)}]=s; STATUS=stripClosing(Object.assign({},RAW_STATUS)); paintStatus(); return 1; })()`;
await ev(`RAIL_SHUT.clear(); setFocus(${JSON.stringify(sid)}); 1`); await sleep(800);
await ev(inj(true));
out.on = await ev(`({group: (()=>{ let el=document.querySelector('#rail .it[data-id="${sid}"]'); while(el && !el.classList.contains("g")) el=el.previousElementSibling; return el && el.dataset.g; })(), box: (document.querySelector('.row[data-id="${sid}"] .st')||{}).textContent||""})`);
if(out.on.group !== "bg") bad.push('左栏没分到 bg 组: '+out.on.group);
if(!/可回话 · 后台在跑/.test(out.on.box) || !/1 个子 agent \+ 1 个 shell/.test(out.on.box)) bad.push('状态框没写清: '+out.on.box.replace(/\s+/g," ").slice(0,200));
await ev(inj(false));
out.off = await ev(`(()=>{ let el=document.querySelector('#rail .it[data-id="${sid}"]'); while(el && !el.classList.contains("g")) el=el.previousElementSibling; return el && el.dataset.g; })()`);
if(out.off === "bg") bad.push('撤掉后还在 bg 组');
await sleep(300);
if(errs.length) bad.push('JS 异常: '+errs.join(' | '));
console.log(JSON.stringify(out, null, 1));
console.log(bad.length ? 'FAIL\n- '+bad.join('\n- ') : 'PASS');
p.kill(); process.exit(bad.length ? 1 : 0);
