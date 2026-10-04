// ＋ 新对话 验收(2026-10-04 用户「加一个开新对话功能…默认也在vscode开」)。rc=1 = 有一项不对。会真开一个 claude(haiku 不可选, 用默认), 测完关掉。
// node test_new.mjs <工作目录>
// 标准: 点「＋ 新对话」→ VS Code 出现新标签 → 管理器进入它的单对话视图 → 第一句话终端收到、回复出现在网页; 阳性对照见 test_new_neg(另起一个桥端口错的实例)
import { spawn } from 'node:child_process';
const CWD = process.argv[2];
const p = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', ['--headless=new','--disable-gpu','--remote-debugging-port=9367','--user-data-dir='+process.env.TEMP+'/cdp_new','--window-size=1400,900','about:blank']);
const sleep=ms=>new Promise(r=>setTimeout(r,ms)); let ws;
for(let i=0;i<40;i++){ try{ const j=await (await fetch('http://127.0.0.1:9367/json')).json(); const pg=j.find(x=>x.type==='page'); if(pg){ ws=new WebSocket(pg.webSocketDebuggerUrl); break; } }catch(e){} await sleep(250); }
await new Promise(r=>ws.onopen=r); let id=0; const pend={}, errs=[];
ws.onmessage=ev=>{const m=JSON.parse(ev.data); if(m.id&&pend[m.id]){pend[m.id](m);delete pend[m.id];} if(m.method==='Runtime.exceptionThrown') errs.push(m.params.exceptionDetails.exception?.description||m.params.exceptionDetails.text);};
const send=(method,params={})=>new Promise(r=>{const i=++id;pend[i]=r;ws.send(JSON.stringify({id:i,method,params}));});
const ev=async e=>{const r=await send('Runtime.evaluate',{expression:e,awaitPromise:true,returnByValue:true}); return r.result.result?.value ?? r.result.exceptionDetails?.text;};
await send('Runtime.enable'); await send('Page.navigate',{url:'http://127.0.0.1:8720/'});
for(let i=0;i<80;i++){ if(await ev('typeof ROWS!=="undefined" && ROWS.length>0')) break; await sleep(100); }
const out={}, bad=[], word='N'+Date.now()%100000;
const terms0 = (await (await fetch('http://127.0.0.1:8721/terminals')).json()).terminals.map(t=>t.pid);
const t0=Date.now();
await ev(`(()=>{ document.querySelector("#btnNew").click(); document.querySelector("#npCwd").value=${JSON.stringify(CWD)}; document.querySelector("#npText").value="这是测试, 请只回复 ${word} 这个词"; document.querySelector("#npGo").click(); })()`);
let f=null; for(let i=0;i<400;i++){ f=await ev(`(()=>({focus:FOCUS, st:document.querySelector("#npSt").textContent, hidden:document.querySelector("#newPanel").hidden}))()`); if(f.focus || /✗/.test(f.st)) break; await sleep(100); }
out.ms_focus=Date.now()-t0; out.focus=f;
if(!f.focus) bad.push('没进入新对话的单对话视图: '+f.st);
const terms1 = (await (await fetch('http://127.0.0.1:8721/terminals')).json()).terminals;
out.new_tabs = terms1.filter(t=>!terms0.includes(t.pid)).map(t=>t.name);
if(!out.new_tabs.length) bad.push('VS Code 里没出现新标签');
let r=null; if(f.focus){ for(let i=0;i<600;i++){ r=await ev(`(()=>{ const row=document.querySelector('.row[data-id="${f.focus}"]'); if(!row) return {row:false}; const ts=[...row.querySelectorAll('.tree .turn:not(.pend)')]; const last=ts[ts.length-1];
  return {row:true, turns:ts.length, ans:last?last.querySelector('.an.full').textContent.trim().slice(0,60):"", dur:last&&last.querySelector('.dur')?last.querySelector('.dur').textContent:"", compose:!!row.querySelector('.compose')}; })()`); if(r.ans && r.ans.includes(word)) break; await sleep(100); } }
out.ms_reply=Date.now()-t0; out.view=r;
if(!r || !r.ans || !r.ans.includes(word)) bad.push('回复没出现在网页');
if(r && !r.compose) bad.push('单对话视图没有输入框');
// 清理: 关掉这个测试会话及其标签
if(f.focus){ const st=(await (await fetch('http://127.0.0.1:8720/api/status')).json()).status[f.focus]||{}; const pid=st.pid||(st.wins&&st.wins[0]&&st.wins[0].pid);
  out.cleanup = pid ? await (await fetch('http://127.0.0.1:8720/api/close',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:f.focus,pid,close_tab:true})})).json() : 'no pid'; }
out.errs=errs; out.bad=bad; console.log(JSON.stringify(out,null,1)); ws.close(); p.kill(); process.exit(bad.length||errs.length?1:0);
