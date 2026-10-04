// 秒关验收(2026-10-04 用户「我希望秒关-你可以先在标签页去掉，关闭失败再弹回来」「关闭标签那里总是有个untitled vscode」)。rc=1 = 有一项不对。
// node test_close.mjs <一个开着窗口的会话 id>   —— 页面里把 /api/close 换成假的, 不会真关任何对话
// 标准: ①窗口名不是「Untitled-1 - Visual Studio Code」这类 IDE 主窗口标题
//       ②点确认后 150 ms 内该窗口从右侧和左栏消失(阳性对照: 点之前它在)
//       ③假失败(ok:false)→ 弹回来并报原因  ④假成功但进程还在 → 8 s 后弹回来
import { spawn } from 'node:child_process';
const SID = process.argv[2];
const p = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', ['--headless=new','--disable-gpu','--remote-debugging-port=9353','--user-data-dir='+process.env.TEMP+'/cdp_close','--window-size=1400,900','about:blank']);
const sleep=ms=>new Promise(r=>setTimeout(r,ms)); let ws;
for(let i=0;i<40;i++){ try{ const j=await (await fetch('http://127.0.0.1:9353/json')).json(); const pg=j.find(x=>x.type==='page'); if(pg){ ws=new WebSocket(pg.webSocketDebuggerUrl); break; } }catch(e){} await sleep(250); }
await new Promise(r=>ws.onopen=r); let id=0; const pend={}, errs=[];
ws.onmessage=ev=>{const m=JSON.parse(ev.data); if(m.id&&pend[m.id]){pend[m.id](m);delete pend[m.id];} if(m.method==='Runtime.exceptionThrown') errs.push(m.params.exceptionDetails.exception?.description||m.params.exceptionDetails.text);};
const send=(method,params={})=>new Promise(r=>{const i=++id;pend[i]=r;ws.send(JSON.stringify({id:i,method,params}));});
const ev=async e=>{const r=await send('Runtime.evaluate',{expression:e,awaitPromise:true,returnByValue:true}); return r.result.result?.value ?? r.result.exceptionDetails?.text;};
await send('Runtime.enable');
await send('Page.navigate',{url:'http://127.0.0.1:8720/'});
const BTN=`document.querySelector('.row[data-id="${SID}"] [data-act="closetab"], .row[data-id="${SID}"] [data-act="close"]')`;
for(let i=0;i<100;i++){ if(await ev(`!!${BTN}`)) break; await sleep(100); }
const bad=[], out={}; console.error("page ready", await ev(`!!${BTN}`));
out.title = await ev(`(document.querySelector('.row[data-id="${SID}"] .win .wt')||{}).textContent`);
if(!out.title || /Visual Studio Code|Untitled/i.test(out.title)) bad.push('窗口名还是 IDE 主窗口标题: '+out.title);
const visible = `(()=>{ const b=${BTN}; return {right: !!b, rail: !!document.querySelector('#rail .it[data-id="${SID}"]') && (document.querySelector('#rail .it[data-id="${SID}"]').closest('.g')||{}).dataset?.g}; })()`;
// 假 /api/close: MODE=fail → ok:false; MODE=okstay → ok:true 但进程(真实状态)还在
await ev(`window.__calls=0; window.__mode="fail"; const _f=window.fetch; window.fetch=(u,o)=>{ if(String(u).startsWith("/api/close")){ __calls++; return new Promise(r=>setTimeout(()=>r({json:async()=>(__mode==="fail"?{ok:false,why:"假失败"}:{ok:true})}),1200)); } return _f(u,o); }; 1`);
async function round(mode){ console.error('round', mode);
  await ev(`window.__mode="${mode}"; 1`);
  const before = await ev(visible);
  if(!before.right) { bad.push(mode+': 阳性对照失败, 点之前就没有关闭按钮'); return; }
  await ev(`${BTN}.click(), 1`); await sleep(50);
  const t0 = Date.now();
  await ev(`${BTN}.click(), 1`);
  const gone = await ev(`(()=>{ const b=${BTN}; return !b; })()`);
  const ms = Date.now() - t0;
  out[mode] = {before, goneFast: gone, ms};
  if(!gone || ms > 150) bad.push(mode+': 确认后没有立刻消失 '+JSON.stringify(out[mode]));
  let back = false; const tb = Date.now();
  for(let i=0;i<120;i++){ if(await ev(`!!${BTN}`)){ back = true; break; } await sleep(100); }
  out[mode].backAfter = ((Date.now()-tb)/1000).toFixed(1)+'s';
  out[mode].stat = await ev(`document.querySelector('#stat').textContent`);
  if(!back) bad.push(mode+': 失败后没有弹回来');
}
await round('fail');
await sleep(500);
await round('okstay');
out.calls = await ev(`__calls`);
if(out.calls !== 2) bad.push('假 /api/close 调用次数 '+out.calls+' ≠ 2');
if(errs.length) bad.push('页面报错: '+errs.join(' | '));
console.log(JSON.stringify(out, null, 1));
console.log(bad.length ? 'FAIL\n- '+bad.join('\n- ') : 'PASS');
p.kill(); process.exit(bad.length ? 1 : 0);
