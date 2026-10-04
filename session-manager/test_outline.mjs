// 话题脉络(小折叠树)验收(2026-10-04 用户「小折叠树…大致记录整个对话的大致流程，谈了几个话题」)。rc=1 = 有一项不对。
// node test_outline.mjs <有脉络的会话 id> <没有脉络的会话 id>
import { spawn } from 'node:child_process';
const [SID, NOSID] = process.argv.slice(2);
const p = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', ['--headless=new','--disable-gpu','--remote-debugging-port=9353','--user-data-dir='+process.env.TEMP+'/cdp_outline','--window-size=1400,900','about:blank']);
const sleep=ms=>new Promise(r=>setTimeout(r,ms)); let ws;
for(let i=0;i<40;i++){ try{ const j=await (await fetch('http://127.0.0.1:9353/json')).json(); const pg=j.find(x=>x.type==='page'); if(pg){ ws=new WebSocket(pg.webSocketDebuggerUrl); break; } }catch(e){} await sleep(250); }
await new Promise(r=>ws.onopen=r); let id=0; const pend={}, errs=[];
ws.onmessage=ev=>{const m=JSON.parse(ev.data); if(m.id&&pend[m.id]){pend[m.id](m);delete pend[m.id];} if(m.method==='Runtime.exceptionThrown') errs.push(m.params.exceptionDetails.exception?.description||m.params.exceptionDetails.text);};
const send=(method,params={})=>new Promise(r=>{const i=++id;pend[i]=r;ws.send(JSON.stringify({id:i,method,params}));});
const ev=async e=>{const r=await send('Runtime.evaluate',{expression:e,awaitPromise:true,returnByValue:true}); return r.result.result?.value ?? r.result.exceptionDetails?.text;};
await send('Runtime.enable'); await send('Page.navigate',{url:'http://127.0.0.1:8720/'});
for(let i=0;i<80;i++){ if(await ev(`!!document.querySelector('.row[data-id="${SID}"]')`)) break; await sleep(100); }
const out={}, bad=[];
await ev(`setFocus("${SID}")`);
for(let i=0;i<50;i++){ if(await ev(`!!document.querySelector('.row[data-id="${SID}"] .outline')`)) break; await sleep(100); }
out.has = await ev(`(()=>{ const o=document.querySelector('.row[data-id="${SID}"] .outline'); return o && {open:o.open, head:o.querySelector('summary').textContent, topics:[...o.querySelectorAll('ol>li .ot')].map(x=>x.textContent), ranges:[...o.querySelectorAll('.or')].map(x=>x.textContent)}; })()`);
if(!out.has || out.has.topics.length<1) bad.push('有脉络的会话没显示话题');
// 点第一个话题的轮号 → 跳到那一轮并展开
out.jump = await ev(`(async()=>{ const r=document.querySelector('.row[data-id="${SID}"] .outline .or'); const k=Number(r.dataset.from); r.click(); await new Promise(z=>setTimeout(z,400));
  const hit=document.querySelector('.row[data-id="${SID}"] .turn.hit'); return {k, hitNo: hit && hit.querySelector('.no').textContent, open: hit && !hit.querySelector('.an.full').hidden}; })()`);
if(!out.jump.hitNo || out.jump.hitNo!=='#'+out.jump.k || !out.jump.open) bad.push('点轮号没跳到对应那一轮');
if(NOSID){
  await ev(`setFocus("${NOSID}")`);
  for(let i=0;i<50;i++){ if(await ev(`!!document.querySelector('.row[data-id="${NOSID}"] .outline')`)) break; await sleep(100); }
  out.none = await ev(`(()=>{ const o=document.querySelector('.row[data-id="${NOSID}"] .outline'); return o && {gen: !!o.querySelector('[data-act="outgen"]'), head:o.querySelector('summary').textContent}; })()`);
  if(!out.none || !out.none.gen) bad.push('没有脉络的会话没给「生成脉络」按钮');
}
out.errs=errs; out.bad=bad; console.log(JSON.stringify(out,null,1)); ws.close(); p.kill(); process.exit(bad.length||errs.length?1:0);
