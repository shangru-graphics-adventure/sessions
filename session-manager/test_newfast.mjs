// 秒开新对话验收(2026-10-04 用户「管理器创建新对话直接秒开，关掉那个创建新对话的窗口…这时，我已经可以再创建新的对话」)。node test_newfast.mjs —— /api/new 是假的, 不会真开对话。rc=1 = 有一项不对。
import { spawn } from 'node:child_process';
const p = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', ['--headless=new','--disable-gpu','--remote-debugging-port=9356','--user-data-dir='+process.env.TEMP+'/cdp_newfast','--window-size=1400,900','about:blank']);
const sleep=ms=>new Promise(r=>setTimeout(r,ms)); let ws;
for(let i=0;i<40;i++){ try{ const j=await (await fetch('http://127.0.0.1:9356/json')).json(); const pg=j.find(x=>x.type==='page'); if(pg){ ws=new WebSocket(pg.webSocketDebuggerUrl); break; } }catch(e){} await sleep(250); }
await new Promise(r=>ws.onopen=r); let id=0; const pend={}, errs=[];
ws.onmessage=ev=>{const m=JSON.parse(ev.data); if(m.id&&pend[m.id]){pend[m.id](m);delete pend[m.id];} if(m.method==='Runtime.exceptionThrown') errs.push(m.params.exceptionDetails.exception?.description||m.params.exceptionDetails.text);};
const send=(method,params={})=>new Promise(r=>{const i=++id;pend[i]=r;ws.send(JSON.stringify({id:i,method,params}));});
const ev=async e=>{const r=await send('Runtime.evaluate',{expression:e,awaitPromise:true,returnByValue:true}); return r.result.result?.value ?? r.result.exceptionDetails?.text;};
await send('Runtime.enable');
await send('Page.navigate',{url:'http://127.0.0.1:8720/'});
for(let i=0;i<100;i++){ if(await ev(`document.querySelectorAll('#rail .it[data-id]').length > 0`) === true) break; await sleep(100); }
await sleep(1500);
const bad=[], out={};
// 假 /api/new: 2 s 后回一个已存在的会话 id(第一次)/ 失败(第二次)
const sids = await ev(`ROWS.slice(0,2).map(r=>r.id)`);
await ev(`window.__n=0; const _f=window.fetch; window.fetch=async (u,o)=>{ if(String(u).startsWith("/api/new")){ const k=++__n; await new Promise(r=>setTimeout(r,2000)); return {json:async()=>(k===1?{ok:true,sid:${JSON.stringify(sids[0])},cwd:"C:/x",ms:2000}:{ok:false,why:"假失败"})}; } return _f(u,o); }; 1`);
await ev(`setFocus(${JSON.stringify(sids[1])}); 1`); await sleep(500);
// 第一次
await ev(`$("#btnNew").click(); $("#npText").value="第一句A"; $("#npGo").click(); 1`);
out.a = await ev(`({hidden: $("#newPanel").hidden, text: $("#npText").value, opening: document.querySelectorAll("#rail .it.opening").length})`);
if(!out.a.hidden || out.a.text || out.a.opening !== 1) bad.push('第一次点开始后面板没立刻关/占位不对 '+JSON.stringify(out.a));
// 立刻再开一个
await sleep(200);
await ev(`$("#btnNew").click(); $("#npText").value="第一句B"; $("#npGo").click(); 1`);
out.b = await ev(`({hidden: $("#newPanel").hidden, opening: document.querySelectorAll("#rail .it.opening").length, focus: FOCUS})`);
if(!out.b.hidden || out.b.opening !== 2) bad.push('第二次没能立刻再开 '+JSON.stringify(out.b));
if(out.b.focus !== sids[1]) bad.push('还没开好就切走了 '+JSON.stringify(out.b));
await sleep(2600);
out.c = await ev(`({focus: FOCUS, opening: document.querySelectorAll("#rail .it.opening").length, text: $("#npText").value})`);
if(out.c.focus !== sids[0]) bad.push('开好后没切过去 '+JSON.stringify(out.c));
if(out.c.text !== "第一句B") bad.push('失败的那个第一句话没放回面板 '+JSON.stringify(out.c));
if(out.c.opening !== 0) bad.push('占位没清掉 '+JSON.stringify(out.c));
await sleep(300);
if(errs.length) bad.push('JS 异常: '+errs.join(' | '));
console.log(JSON.stringify(out, null, 1));
console.log(bad.length ? 'FAIL'+'\n- '+bad.join('\n- ') : 'PASS');
p.kill(); process.exit(bad.length ? 1 : 0);
