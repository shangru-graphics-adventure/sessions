// 接手前情验收(10-04 用户「handoff的时候，应该用一小段话讲一下来龙去脉」)。node test_story.mjs <接手对话 id> <非接手对话 id>; rc=1 = 不对
import { spawn } from 'node:child_process';
const [SID, NOT] = process.argv.slice(2);
const p = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', ['--headless=new','--disable-gpu','--remote-debugging-port=9354','--user-data-dir='+process.env.TEMP+'/cdp_story','--window-size=1400,900','about:blank']);
const sleep=ms=>new Promise(r=>setTimeout(r,ms)); let ws;
for(let i=0;i<40;i++){ try{ const j=await (await fetch('http://127.0.0.1:9354/json')).json(); const pg=j.find(x=>x.type==='page'); if(pg){ ws=new WebSocket(pg.webSocketDebuggerUrl); break; } }catch(e){} await sleep(250); }
await new Promise(r=>ws.onopen=r); let id=0; const pend={}, errs=[];
ws.onmessage=ev=>{const m=JSON.parse(ev.data); if(m.id&&pend[m.id]){pend[m.id](m);delete pend[m.id];} if(m.method==='Runtime.exceptionThrown') errs.push(m.params.exceptionDetails.exception?.description||m.params.exceptionDetails.text);};
const send=(method,params={})=>new Promise(r=>{const i=++id;pend[i]=r;ws.send(JSON.stringify({id:i,method,params}));});
const ev=async e=>{const r=await send('Runtime.evaluate',{expression:e,awaitPromise:true,returnByValue:true}); return r.result.result?.value ?? r.result.exceptionDetails?.text;};
await send('Runtime.enable'); await send('Page.navigate',{url:'http://127.0.0.1:8720/'});
const bad=[], out={};
for(const [sid, want] of [[SID, true], [NOT, false]]){
  for(let i=0;i<100;i++){ if(await ev(`!!document.querySelector('.row[data-id="${sid}"]')`)) break; await sleep(100); }
  await ev(`setFocus("${sid}")`);
  let txt=null; for(let i=0;i<80;i++){ txt = await ev(`(()=>{ const t=document.querySelector('.row[data-id="${sid}"] .tree .thead'); if(!t) return null; const s=document.querySelector('.row[data-id="${sid}"] .story'); return s ? s.innerText : ""; })()`); if(txt!==null) break; await sleep(100); }
  out[sid.slice(0,8)] = (txt||"").slice(0,120);
  if(want && !(txt && txt.length > 40)) bad.push('接手对话没显示来龙去脉');
  if(!want && txt) bad.push('非接手对话也显示了接手前情(阴性对照失败)');
}
if(errs.length) bad.push('页面报错: '+errs.join(' | '));
console.log(JSON.stringify(out,null,1)); console.log(bad.length ? 'FAIL\n- '+bad.join('\n- ') : 'PASS');
p.kill(); process.exit(bad.length?1:0);
