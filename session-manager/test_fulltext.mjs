// 「全文」与「点某一轮」验收(2026-10-04 用户:「点上面这里和点全文怎么不一样, 全文没有细节」「默认还是在最上面」)。rc=1 = 有一项不对。
// node test_fulltext.mjs <会话 id(要在列表里)>
import { spawn } from 'node:child_process';
const SID = process.argv[2];
const p = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', ['--headless=new','--disable-gpu','--remote-debugging-port=9347','--user-data-dir='+process.env.TEMP+'/cdp_fulltext','--window-size=1600,1000','about:blank']);
const sleep=ms=>new Promise(r=>setTimeout(r,ms)); let ws;
for(let i=0;i<40;i++){ try{ const j=await (await fetch('http://127.0.0.1:9347/json')).json(); const pg=j.find(x=>x.type==='page'); if(pg){ ws=new WebSocket(pg.webSocketDebuggerUrl); break; } }catch(e){} await sleep(250); }
await new Promise(r=>ws.onopen=r); let id=0; const pend={}, errs=[];
ws.onmessage=ev=>{const m=JSON.parse(ev.data); if(m.id&&pend[m.id]){pend[m.id](m);delete pend[m.id];} if(m.method==='Runtime.exceptionThrown') errs.push(m.params.exceptionDetails.exception?.description||m.params.exceptionDetails.text);};
const send=(method,params={})=>new Promise(r=>{const i=++id;pend[i]=r;ws.send(JSON.stringify({id:i,method,params}));});
const ev=async e=>{const r=await send('Runtime.evaluate',{expression:e,awaitPromise:true,returnByValue:true}); return r.result.result?.value ?? r.result.exceptionDetails?.text;};
await send('Runtime.enable');
await send('Page.navigate',{url:'http://127.0.0.1:8720/'});
for(let i=0;i<80;i++){ if(await ev(`!!document.querySelector('.row[data-id="${SID}"]')`)) break; await sleep(100); }
const out={}, bad=[];
const probe = `(()=>{ const row=document.querySelector('.row[data-id="${SID}"]'); const ts=[...row.querySelectorAll(".tree .turn")];
  const fulls=ts.map(t=>t.querySelector(".an.full")); const open=fulls.filter(f=>f&&!f.hidden);
  const over=open.filter(f=>f.scrollHeight>f.clientHeight+4);
  const atBottom=over.filter(f=>f.scrollHeight-f.clientHeight-f.scrollTop<=4);
  return {turns:ts.length, open:open.length, overflow:over.length, atBottom:atBottom.length}; })()`;
// 1) 全文
await ev(`document.querySelector('.row[data-id="${SID}"] [data-act="tree"]').click()`);
for(let i=0;i<60;i++){ const r=await ev(probe); if(r.turns&&r.open) break; await sleep(100); } await sleep(300);
out.fulltext = await ev(probe);
if(!out.fulltext.turns || out.fulltext.open!==out.fulltext.turns) bad.push('全文没有把每一轮都展开');
if(out.fulltext.atBottom!==out.fulltext.overflow) bad.push('全文: 有回复框没停在底部');
// 2) 收起后点上面某一轮(第 1 轮)
await ev(`document.querySelector('.row[data-id="${SID}"] [data-act="tree"]').click()`); await sleep(200);
await ev(`document.querySelector('.row[data-id="${SID}"] [data-act="topic"]').click()`);
for(let i=0;i<60;i++){ const r=await ev(probe); if(r.open) break; await sleep(100); } await sleep(300);
out.topic = await ev(probe);
if(out.topic.open<1) bad.push('点某一轮没展开');
if(out.topic.atBottom!==out.topic.overflow) bad.push('点某一轮: 回复框没停在底部');
out.errs=errs; out.bad=bad; console.log(JSON.stringify(out)); ws.close(); p.kill(); process.exit(bad.length||errs.length?1:0);
