// A 路线验收(2026-10-04 用户「以后和claude聊统一通过对话管理器」「什么时候完成的，想了多长时间」「字也要及时出来」)。rc=1 = 有一项不对。
// node test_send.mjs <空闲的测试会话 id>   —— 会真往它的终端里打一句话, 只用一次性测试会话, 别拿正在用的对话测
// 标准: 网页输入框发送 → 1 s 内显示「在跑 · 已 X」 → 回复写入后自动出现(不刷新), 带「完成 HH:MM:SS · 想了 X」
// 阳性对照: 发给不存在的会话 / 空消息必须报失败
import { spawn } from 'node:child_process';
const SID = process.argv[2];
const p = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', ['--headless=new','--disable-gpu','--remote-debugging-port=9351','--user-data-dir='+process.env.TEMP+'/cdp_send','--window-size=1400,900','about:blank']);
const sleep=ms=>new Promise(r=>setTimeout(r,ms)); let ws;
for(let i=0;i<40;i++){ try{ const j=await (await fetch('http://127.0.0.1:9351/json')).json(); const pg=j.find(x=>x.type==='page'); if(pg){ ws=new WebSocket(pg.webSocketDebuggerUrl); break; } }catch(e){} await sleep(250); }
await new Promise(r=>ws.onopen=r); let id=0; const pend={}, errs=[];
ws.onmessage=ev=>{const m=JSON.parse(ev.data); if(m.id&&pend[m.id]){pend[m.id](m);delete pend[m.id];} if(m.method==='Runtime.exceptionThrown') errs.push(m.params.exceptionDetails.exception?.description||m.params.exceptionDetails.text);};
const send=(method,params={})=>new Promise(r=>{const i=++id;pend[i]=r;ws.send(JSON.stringify({id:i,method,params}));});
const ev=async e=>{const r=await send('Runtime.evaluate',{expression:e,awaitPromise:true,returnByValue:true}); return r.result.result?.value ?? r.result.exceptionDetails?.text;};
await send('Runtime.enable');
await send('Page.navigate',{url:'http://127.0.0.1:8720/'});
for(let i=0;i<80;i++){ if(await ev(`!!document.querySelector('.row[data-id="${SID}"]')`)) break; await sleep(100); }
const out={}, bad=[];
// 阳性对照
out.neg = await ev(`(async()=>{ const f=b=>fetch("/api/send",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b)}).then(r=>r.json());
  return [await f({id:"00000000-0000-0000-0000-000000000000",text:"x"}), await f({id:"${SID}",text:"   "})]; })()`);
if(out.neg.some(r=>r.ok)) bad.push('阳性对照: 不存在的会话或空消息居然报成功');
// 进单对话视图
await ev(`setFocus("${SID}")`);
for(let i=0;i<50;i++){ if(await ev(`!!document.querySelector('.row[data-id="${SID}"] .compose textarea')`)) break; await sleep(100); }
const probe=`(()=>{ const row=document.querySelector('.row[data-id="${SID}"]'); const ts=[...row.querySelectorAll(".tree .turn:not(.pend)")];
  const last=ts[ts.length-1]; return {turns:ts.length, pend:row.querySelectorAll(".turn.pend").length, run:!!row.querySelector(".dur.run"), dur:last&&last.querySelector(".dur")?last.querySelector(".dur").textContent:"",
  ans:last?last.querySelector(".an.full").textContent.trim().slice(0,80):"", cst:row.querySelector(".compose .cst")?.textContent||"", compose:!!row.querySelector(".compose")}; })()`;
out.before=await ev(probe);
if(!out.before.compose) bad.push('单对话视图没有输入框');
if(out.before.turns && !/^完成 \d\d:\d\d:\d\d · 想了 /.test(out.before.dur)) bad.push('已完成的一轮没显示「完成 · 想了」: '+out.before.dur);
const word='T'+Date.now()%100000;
const t0=Date.now();
await ev(`(()=>{ const ta=document.querySelector('.row[data-id="${SID}"] .compose textarea'); ta.value="这是测试, 请只回复 ${word} 这个词"; document.querySelector('.row[data-id="${SID}"] .compose .go').click(); })()`);
let r, tRun=null, tTurn=null, tDone=null;
for(let i=0;i<600;i++){ r=await ev(probe);
  if(tRun===null && (r.run || r.turns>out.before.turns)) tRun=Date.now()-t0;
  if(tTurn===null && r.turns>out.before.turns) tTurn=Date.now()-t0;
  if(r.turns>out.before.turns && !r.run && r.ans.includes(word)){ tDone=Date.now()-t0; break; }
  await sleep(100); }
out.after=r; out.ms_to_running=tRun; out.ms_to_turn_in_jsonl=tTurn; out.ms_to_reply=tDone;
if(r.pend) bad.push("回复出现后占位轮没消失");
if(!/已打进终端|已排队/.test(r.cst)) bad.push('发送后状态行不对: '+r.cst);
if(tRun===null || tRun>1000) bad.push('网页 1 s 内没显示「已送出/在跑」 ('+tRun+' ms)');
if(tDone===null) bad.push('60 s 内回复没自动出现');
if(tDone!==null && !/^完成 \d\d:\d\d:\d\d · 想了 /.test(r.dur)) bad.push('新一轮没显示「完成 · 想了」: '+r.dur);
// 第二段: 对话在跑时从网页发 → 「已排队」占位 → 钩子送达后占位消失, 作为插问挂在那一轮下面(需本机配了 relay; 没配应报失败)
await ev(`(()=>{ const ta=document.querySelector('.row[data-id="${SID}"] .compose textarea'); ta.value="测试: 先用 Bash 跑 sleep 8 再回复 A1"; document.querySelector('.row[data-id="${SID}"] .compose .go').click(); })()`);
for(let i=0;i<100;i++){ if(await ev(`(STATUS["${SID}"]||{}).state==="running"`)) break; await sleep(100); }
const w2='Q'+Date.now()%100000;
await ev(`(()=>{ const ta=document.querySelector('.row[data-id="${SID}"] .compose textarea'); ta.value="测试排队: 回复 ${w2}"; document.querySelector('.row[data-id="${SID}"] .compose .go').click(); })()`);
await sleep(1500);
out.queued = await ev(`(()=>{ const row=document.querySelector('.row[data-id="${SID}"]'); return {cst: row.querySelector('.compose .cst').textContent, pend:[...row.querySelectorAll('.turn.pend .dur')].map(x=>x.textContent)}; })()`);
if(/已排队/.test(out.queued.cst)){
  if(!out.queued.pend.some(x=>/已排队/.test(x))) bad.push('排队后没显示「已排队」占位');
  let q2=null; for(let i=0;i<600;i++){ q2=await ev(`(()=>{ const row=document.querySelector('.row[data-id="${SID}"]'); return {pend: row.querySelectorAll('.turn.pend').length, mid: [...row.querySelectorAll('.qmid')].some(x=>x.textContent.includes("${w2}"))}; })()`); if(q2.mid && !q2.pend) break; await sleep(100); }
  out.queued.after=q2; if(!q2.mid) bad.push('排队消息送达后没作为插问出现'); if(q2.pend) bad.push('排队消息送达后占位没消失');
} else if(!/没发出去/.test(out.queued.cst)) bad.push('在跑时发送既没排队也没报失败: '+out.queued.cst);
out.errs=errs; out.bad=bad; console.log(JSON.stringify(out,null,1)); ws.close(); p.kill(); process.exit(bad.length||errs.length?1:0);
