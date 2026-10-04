// 单对话视图在跑时的局部重画验收(10-04): 推送重画后, 没变的那一轮 DOM 节点保持原样(你手动滚到中间的回复框不被滚回底部)。rc=1 = 不对。
// node test_live_diff.mjs <空闲的测试会话 id(至少 2 轮)>   —— 测试里会经 /api/send 给它发一句, 让它跑起来
import { spawn } from 'node:child_process';
const SID = process.argv[2];
const p = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', ['--headless=new','--disable-gpu','--remote-debugging-port=9355','--user-data-dir='+process.env.TEMP+'/cdp_diff','--window-size=1400,900','about:blank']);
const sleep=ms=>new Promise(r=>setTimeout(r,ms)); let ws;
for(let i=0;i<40;i++){ try{ const j=await (await fetch('http://127.0.0.1:9355/json')).json(); const pg=j.find(x=>x.type==='page'); if(pg){ ws=new WebSocket(pg.webSocketDebuggerUrl); break; } }catch(e){} await sleep(250); }
await new Promise(r=>ws.onopen=r); let id=0; const pend={}, errs=[];
ws.onmessage=ev=>{const m=JSON.parse(ev.data); if(m.id&&pend[m.id]){pend[m.id](m);delete pend[m.id];} if(m.method==='Runtime.exceptionThrown') errs.push(m.params.exceptionDetails.exception?.description||m.params.exceptionDetails.text);};
const send=(method,params={})=>new Promise(r=>{const i=++id;pend[i]=r;ws.send(JSON.stringify({id:i,method,params}));});
const ev=async e=>{const r=await send('Runtime.evaluate',{expression:e,awaitPromise:true,returnByValue:true}); return r.result.result?.value ?? r.result.exceptionDetails?.text;};
await send('Runtime.enable'); await send('Page.navigate',{url:'http://127.0.0.1:8720/'});
for(let i=0;i<80;i++){ if(await ev(`!!document.querySelector('.row[data-id="${SID}"]')`)) break; await sleep(100); }
const out={}, bad=[];
await ev(`setFocus("${SID}")`); await sleep(1500);
// 给第 1 轮打记号、把它的回复框滚到顶; 再数重画次数
out.mark = await ev(`(()=>{ window.__n=0; const o=refreshFocus; refreshFocus=async function(){ window.__n++; return o.apply(this,arguments); };
  const t=document.querySelector('.row[data-id="${SID}"] .turn'); t.__mark=1; const f=t.querySelector('.an.full'); f.scrollTop=0; return {h:f.scrollHeight}; })()`);
out.poke = await ev(`fetch("/api/send",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({id:"${SID}",text:"测试, 请只回复 OK"})}).then(r=>r.json())`);
await sleep(12000);   // 这段时间里会话在跑, jsonl 会被写好几次
out.after = await ev(`(()=>{ const t=document.querySelector('.row[data-id="${SID}"] .turn'); const f=t.querySelector('.an.full'); return {refreshes:window.__n, same:t.__mark===1, top:f.scrollTop}; })()`);
if(!out.after.refreshes) bad.push('12 秒里一次推送重画都没有(会话真的在跑吗)');
if(!out.after.same) bad.push('没变的第 1 轮被整块重建了');
if(out.after.top!==0) bad.push('第 1 轮回复框被滚动了');
out.errs=errs; out.bad=bad; console.log(JSON.stringify(out)); ws.close(); p.kill(); process.exit(bad.length||errs.length?1:0);
