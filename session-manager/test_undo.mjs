// 撤回按钮验收(2026-10-04 用户「应该有个撤回按钮，以防打错字」「左边标签页要高亮当前对话」)。rc=1 = 有一项不对。
// node test_undo.mjs <任一会话 id>   —— 页面里把 /api/send 换成假的, 不会真打字
// 标准: 发送后 UNDO_S 秒内不调 /api/send, 点「撤回」原文回输入框且始终 0 次调用(阳性: 点「立即发」必须调 1 次); 左栏当前对话 .it.on 是实底色
import { spawn } from 'node:child_process';
const SID = process.argv[2];
const p = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', ['--headless=new','--disable-gpu','--remote-debugging-port=9352','--user-data-dir='+process.env.TEMP+'/cdp_undo','--window-size=1400,900','about:blank']);
const sleep=ms=>new Promise(r=>setTimeout(r,ms)); let ws;
for(let i=0;i<40;i++){ try{ const j=await (await fetch('http://127.0.0.1:9352/json')).json(); const pg=j.find(x=>x.type==='page'); if(pg){ ws=new WebSocket(pg.webSocketDebuggerUrl); break; } }catch(e){} await sleep(250); }
await new Promise(r=>ws.onopen=r); let id=0; const pend={}, errs=[];
ws.onmessage=ev=>{const m=JSON.parse(ev.data); if(m.id&&pend[m.id]){pend[m.id](m);delete pend[m.id];} if(m.method==='Runtime.exceptionThrown') errs.push(m.params.exceptionDetails.exception?.description||m.params.exceptionDetails.text);};
const send=(method,params={})=>new Promise(r=>{const i=++id;pend[i]=r;ws.send(JSON.stringify({id:i,method,params}));});
const ev=async e=>{const r=await send('Runtime.evaluate',{expression:e,awaitPromise:true,returnByValue:true}); return r.result.result?.value ?? r.result.exceptionDetails?.text;};
await send('Runtime.enable');
await send('Page.navigate',{url:'http://127.0.0.1:8720/'});
for(let i=0;i<80;i++){ if(await ev(`!!document.querySelector('.row[data-id="${SID}"]')`)) break; await sleep(100); }
const bad=[], out={};
await ev(`window.__calls=[]; const _f=window.fetch; window.fetch=(u,o)=>{ if(String(u).startsWith("/api/send")){ __calls.push(o.body); return Promise.resolve({json:async()=>({ok:true,mode:"typed",why:"假的"})}); } return _f(u,o); }; 1`);
await ev(`setFocus("${SID}")`);
for(let i=0;i<50;i++){ if(await ev(`!!document.querySelector('.row[data-id="${SID}"] .compose textarea')`)) break; await sleep(100); }
const C=`document.querySelector('.row[data-id="${SID}"] .compose')`;
out.rail = await ev(`(()=>{ const it=document.querySelector('#rail .it.on'); return it ? {id: it.dataset.id, bg: getComputedStyle(it).backgroundColor} : null; })()`);
if(!out.rail || out.rail.id!==SID) bad.push('左栏没有高亮当前对话: '+JSON.stringify(out.rail));
await ev(`(()=>{ const c=${C}; c.querySelector("textarea").value="打错的字"; c.querySelector(".go").click(); return 1; })()`);
await sleep(300);
out.held = await ev(`(()=>{ const c=${C}; return {ta: c.querySelector("textarea").value, cst: c.querySelector(".cst").textContent, undo: !!c.querySelector(".ub.undo"), pend: document.querySelectorAll(".turn.pend").length, calls: __calls.length}; })()`);
if(out.held.calls) bad.push('反悔窗内就调了 /api/send');
if(!out.held.undo || out.held.pend<1) bad.push('没有撤回按钮或占位轮: '+JSON.stringify(out.held));
await ev(`${C}.querySelector(".ub.undo").click(), 1`);
await sleep(3500);
out.undone = await ev(`(()=>{ const c=${C}; return {ta: c.querySelector("textarea").value, cst: c.querySelector(".cst").textContent, pend: document.querySelectorAll(".turn.pend").length, calls: __calls.length}; })()`);
if(out.undone.calls) bad.push('撤回后仍调了 /api/send');
if(out.undone.ta!=="打错的字") bad.push('撤回后原文没放回输入框: '+out.undone.ta);
// 阳性对照: 立即发 → 必须调 1 次
await ev(`(()=>{ const c=${C}; c.querySelector(".go").click(); return 1; })()`); await sleep(200);
await ev(`${C}.querySelector(".ub.now").click(), 1`); await sleep(400);
out.now = await ev(`({calls: __calls.length, body: __calls[0]||"", cst: ${C}.querySelector(".cst").textContent})`);
if(out.now.calls!==1 || !out.now.body.includes("打错的字")) bad.push('阳性对照: 立即发没调 /api/send: '+JSON.stringify(out.now));
// 阳性对照2: 不点任何键, UNDO_S 秒后自动发
await ev(`(()=>{ const c=${C}; c.querySelector("textarea").value="自动发"; c.querySelector(".go").click(); return 1; })()`); await sleep(3600);
out.auto = await ev(`__calls.length`); if(out.auto!==2) bad.push('倒计时到了没自动发: calls='+out.auto);
out.opening = await ev(`(()=>{ OPENING={t0:Date.now()-5000,cwd:"D:/x"}; paintRail(); const t=document.querySelector("#rail .it.opening")?.textContent||""; OPENING=null; paintRail(); return t + " / 清掉后: " + !!document.querySelector("#rail .it.opening"); })()`);
if(!/正在打开新对话.*5秒 \/ 清掉后: false/.test(out.opening)) bad.push("左栏没显示正在打开: "+out.opening);
// 10-04 用户「开对话应该也可以直接回车开始」: 目录框里回车 → 调 /api/new(这里换成假的, 不真开)
out.enter = await ev(`(async()=>{ const _f=window.fetch; let hit=0; window.fetch=(u,o)=>{ if(String(u).startsWith("/api/new")){ hit++; return Promise.resolve({json:async()=>({ok:false,why:"假的"})}); } return _f(u,o); };
  $("#btnNew").click(); const c=$("#npCwd"); c.focus(); c.dispatchEvent(new KeyboardEvent("keydown",{key:"Enter",bubbles:true}));
  await new Promise(r=>setTimeout(r,300)); window.fetch=_f; $("#newPanel").hidden=true; return hit; })()`);
if(out.enter!==1) bad.push("新对话面板目录框回车没开始: hit="+out.enter);
if(errs.length) bad.push('页面异常: '+errs.join(' | '));
console.log(JSON.stringify(out,null,1)); console.log(bad.length?'FAIL\n- '+bad.join('\n- '):'PASS');
p.kill(); process.exit(bad.length?1:0);
