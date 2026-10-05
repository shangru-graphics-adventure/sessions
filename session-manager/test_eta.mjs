// 报时显示验收(2026-10-04 用户「各个对话有较长计算的能否都给个时间预期，显示到对话管理器上」)。先在本对话 eta.py set, 再 node test_eta.mjs。rc=1 = 有一项不对。
import { spawn } from 'node:child_process';
const p = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', ['--headless=new','--disable-gpu','--remote-debugging-port=9358','--user-data-dir='+process.env.TEMP+'/cdp_eta','--window-size=1400,900','about:blank']);
const sleep=ms=>new Promise(r=>setTimeout(r,ms)); let ws;
for(let i=0;i<40;i++){ try{ const j=await (await fetch('http://127.0.0.1:9358/json')).json(); const pg=j.find(x=>x.type==='page'); if(pg){ ws=new WebSocket(pg.webSocketDebuggerUrl); break; } }catch(e){} await sleep(250); }
await new Promise(r=>ws.onopen=r); let id=0; const pend={}, errs=[];
ws.onmessage=ev=>{const m=JSON.parse(ev.data); if(m.id&&pend[m.id]){pend[m.id](m);delete pend[m.id];} if(m.method==='Runtime.exceptionThrown') errs.push(m.params.exceptionDetails.exception?.description||m.params.exceptionDetails.text);};
const send=(method,params={})=>new Promise(r=>{const i=++id;pend[i]=r;ws.send(JSON.stringify({id:i,method,params}));});
const ev=async e=>{const r=await send('Runtime.evaluate',{expression:e,awaitPromise:true,returnByValue:true}); return r.result.result?.value ?? r.result.exceptionDetails?.text;};
await send('Runtime.enable');
await send('Page.navigate',{url:'http://127.0.0.1:8720/'});
for(let i=0;i<100;i++){ if(await ev(`document.querySelectorAll('#rail .it[data-id]').length > 0`) === true) break; await sleep(100); }
await sleep(1500);
const bad=[], out={};
const sid = process.argv[2];
await ev('RAIL_SHUT.clear(); setFocus(' + JSON.stringify(sid) + '); 1'); await sleep(1500);
out.rail = await ev(`(document.querySelector('#rail .it[data-id="${sid}"] .etab')||{}).textContent||""`);
out.box = await ev(`(document.querySelector('.row[data-id="${sid}"] .etabox')||{}).textContent||""`);
const t1 = await ev('(document.querySelector(".etaT")||{}).textContent||""'); await sleep(2100);
const t2 = await ev('(document.querySelector(".etaT")||{}).textContent||""');
out.tick = [t1, t2];
if(!/⏱\d+分/.test(out.rail)) bad.push('左栏没有 ⏱ 徽标: ' + out.rail);
if(!/测试报时/.test(out.box) || !/依据/.test(out.box) || !/还要约/.test(out.box)) bad.push('状态框不全: ' + out.box);
if(t1 === t2) bad.push('倒计时没走: ' + t1);
await sleep(300);
if(errs.length) bad.push('JS 异常: '+errs.join(' | '));
console.log(JSON.stringify(out, null, 1));
console.log(bad.length ? 'FAIL\n- '+bad.join('\n- ') : 'PASS');
p.kill(); process.exit(bad.length ? 1 : 0);
