// 对话管理器左栏 + 状态推送验收(2026-10-04 用户:「claude对话管理器也加个左栏方便选取」「用事件中断」)。rc=1 = 有一项不对。
// node test_rail.mjs <正在跑的会话 id>   —— 会把它切成「等你」再切回「在跑」(经 tab_title.apply, 与钩子同一处)
import { spawn, execFileSync } from 'node:child_process';
const SID = process.argv[2];
const p = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', ['--headless=new','--disable-gpu','--remote-debugging-port=9343','--user-data-dir='+process.env.TEMP+'/cdp_rail','--window-size=1400,900','about:blank']);
const sleep=ms=>new Promise(r=>setTimeout(r,ms)); let ws;
for(let i=0;i<40;i++){ try{ const j=await (await fetch('http://127.0.0.1:9343/json')).json(); const pg=j.find(x=>x.type==='page'); if(pg){ ws=new WebSocket(pg.webSocketDebuggerUrl); break; } }catch(e){} await sleep(250); }
await new Promise(r=>ws.onopen=r); let id=0; const pend={}, errs=[];
ws.onmessage=ev=>{const m=JSON.parse(ev.data); if(m.id&&pend[m.id]){pend[m.id](m);delete pend[m.id];} if(m.method==='Runtime.exceptionThrown') errs.push(m.params.exceptionDetails.exception?.description||m.params.exceptionDetails.text);};
const send=(method,params={})=>new Promise(r=>{const i=++id;pend[i]=r;ws.send(JSON.stringify({id:i,method,params}));});
const ev=async e=>{const r=await send('Runtime.evaluate',{expression:e,awaitPromise:true,returnByValue:true}); return r.result.result?.value ?? r.result.exceptionDetails?.text;};
await send('Runtime.enable');
const out={}, bad=[];
const t0=Date.now(); await send('Page.navigate',{url:'http://127.0.0.1:8720/'});
for(let i=0;i<80;i++){ if(await ev('document.querySelectorAll("#rail .it").length>0 && document.querySelectorAll(".row").length>0')) break; await sleep(100); }
out.load_ms=Date.now()-t0; if(out.load_ms>5000) bad.push('加载慢 '+out.load_ms);
const groups=()=>ev(`[...document.querySelectorAll("#rail .g")].map(g=>g.dataset.g+":"+g.querySelector(".n").textContent)`);
const where=()=>ev(`(()=>{ const it=document.querySelector('#rail .it[data-id="${SID}"]'); if(!it) return null; let g=it.previousElementSibling; while(g&&!g.classList.contains("g")) g=g.previousElementSibling; return g&&g.dataset.g; })()`);
out.groups=await groups();
if(!out.groups.length) bad.push('左栏没有分组');
// 点一个左栏项 → 右边只剩这一个对话(单对话视图)且全文展开; 点「全部对话」回到列表(用户 10-04「只显示当前选择的tab那个对话」)
out.click=await ev(`(async()=>{ const n0=document.querySelectorAll(".row").length; const it=[...document.querySelectorAll("#rail .it")].slice(-1)[0]; const id=it.dataset.id; it.click();
  for(let i=0;i<50&&!document.querySelector(".row .tree.on .turn");i++) await new Promise(r=>setTimeout(r,100));
  const rows=[...document.querySelectorAll(".row")]; const r={n0, rows:rows.length, same:rows[0]&&rows[0].dataset.id===id, treeTurns:document.querySelectorAll(".row .tree.on .turn").length};
  document.querySelector('#rail [data-all]').click(); await new Promise(r=>setTimeout(r,300)); r.back=document.querySelectorAll(".row").length; return r; })()`);
if(out.click.rows!==1||!out.click.same) bad.push('点左栏后右边不止一个对话');
if(!out.click.treeTurns) bad.push('单对话视图没展开全文');
if(out.click.back!==out.click.n0) bad.push('点「全部对话」没回到列表');
// 状态推送: 切等你 → 左栏应自动把它挪到「开着·等你」, 切回在跑 → 回到「正在跑」(不刷新页面)
const py=(w)=>execFileSync('python',['-c',`import sys;sys.path.insert(0,r"${process.env.USERPROFILE}/.claude/scripts");import tab_title;print(tab_title.apply("${SID}",${w}))`]);
out.before=await where();
for(const [w,want] of [["True","reply|closable"],["False","running"]]){
  const t=Date.now(); py(w); let g=null;
  for(let i=0;i<50;i++){ g=await where(); if(want.split("|").includes(g)) break; await sleep(100); }
  out["to_"+want]={group:g, ms:Date.now()-t};
  if(!want.split("|").includes(g)) bad.push(`推送后左栏没换组(要 ${want}, 实际 ${g})`);
}
// 手机宽度
await send('Emulation.setDeviceMetricsOverride',{width:390,height:900,deviceScaleFactor:1,mobile:true}); await sleep(400);
out.mobile=await ev('({iw:innerWidth, sw:document.documentElement.scrollWidth, railHidden:getComputedStyle(document.querySelector("#rail")).transform!=="none", btn:getComputedStyle(document.querySelector("#railBtn")).display})');
if(out.mobile.sw>out.mobile.iw+1) bad.push('手机宽度横向溢出');
if(!out.mobile.railHidden||out.mobile.btn==='none') bad.push('手机上左栏没收成抽屉');
out.errs=errs; out.bad=bad; console.log(JSON.stringify(out,null,1)); ws.close(); p.kill(); process.exit(bad.length||errs.length?1:0);
