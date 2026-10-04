// 左栏 ✕ 验收(2026-10-04 用户「在标签栏也加个关闭按钮，按了就关闭这个对话的全部；而且要马上让这个标签消失，显示下一个标签的对话（如果是最后一个标签就显示空）」)。
// node test_railx.mjs   —— 页面里把 /api/close 换成假的, 不会真关任何对话。rc=1 = 有一项不对。
// 标准: ①有开着窗口的会话在左栏有 ✕(阳性对照), 已关的没有
//       ②选中 A 点 ✕ → 150 ms 内 A 从左栏消失, 选中项变成原来的下一个标签
//       ③假失败 → A 弹回左栏并报原因
//       ④选中最后一个标签点 ✕ → 右边显示「这里空了」
//       ⑤全程无 JS 异常
import { spawn } from 'node:child_process';
const p = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', ['--headless=new','--disable-gpu','--remote-debugging-port=9354','--user-data-dir='+process.env.TEMP+'/cdp_railx','--window-size=1400,900','about:blank']);
const sleep=ms=>new Promise(r=>setTimeout(r,ms)); let ws;
for(let i=0;i<40;i++){ try{ const j=await (await fetch('http://127.0.0.1:9354/json')).json(); const pg=j.find(x=>x.type==='page'); if(pg){ ws=new WebSocket(pg.webSocketDebuggerUrl); break; } }catch(e){} await sleep(250); }
await new Promise(r=>ws.onopen=r); let id=0; const pend={}, errs=[];
ws.onmessage=ev=>{const m=JSON.parse(ev.data); if(m.id&&pend[m.id]){pend[m.id](m);delete pend[m.id];} if(m.method==='Runtime.exceptionThrown') errs.push(m.params.exceptionDetails.exception?.description||m.params.exceptionDetails.text);};
const send=(method,params={})=>new Promise(r=>{const i=++id;pend[i]=r;ws.send(JSON.stringify({id:i,method,params}));});
const ev=async e=>{const r=await send('Runtime.evaluate',{expression:e,awaitPromise:true,returnByValue:true}); return r.result.result?.value ?? r.result.exceptionDetails?.text;};
await send('Runtime.enable');
await send('Page.navigate',{url:'http://127.0.0.1:8720/'});
for(let i=0;i<100;i++){ if(await ev(`document.querySelectorAll('#rail .it[data-id]').length > 0`) === true) break; await sleep(100); }
await sleep(1500);
const bad=[], out={};
// 展开所有组, 统计有 ✕ 的 / 已关组里不该有 ✕
await ev(`RAIL_SHUT.clear(); paintRail(); 1`);
out.items = await ev(`document.querySelectorAll('#rail .it[data-id]').length`);
out.withX = await ev(`document.querySelectorAll('#rail .it[data-id] .x').length`);
out.closedWithX = await ev(`[...document.querySelectorAll('#rail .it[data-id]')].filter(el => railGroup(STATUS[el.dataset.id])==="closed" && el.querySelector('.x')).length`);
if(!out.withX) bad.push('阳性对照失败: 没有任何标签有 ✕ (items='+out.items+')');
if(out.closedWithX) bad.push('已关的会话也显示了 ✕: '+out.closedWithX);
// 假 /api/close; 同时让状态轮询把被关的 pid 剔掉(模拟真关成功), MODE=fail 时不剔
await ev(`window.__mode="ok"; window.__gone=new Set(); const _f=window.fetch; window.fetch=async (u,o)=>{
  if(String(u).startsWith("/api/close")){ const b=JSON.parse(o.body); await new Promise(r=>setTimeout(r,300));
    if(__mode==="fail") return {json:async()=>({ok:false,why:"假失败"})}; __gone.add(b.pid); return {json:async()=>({ok:true})}; }
  const r=await _f(u,o); return r; };
  const _ps=pollStatus; pollStatus=async function(){ await _ps.apply(this,arguments); for(const k in RAW_STATUS){ const s=RAW_STATUS[k]; if(s&&s.wins){ const w=s.wins.filter(x=>!__gone.has(x.pid)); if(w.length!==s.wins.length) RAW_STATUS[k]=Object.assign({},s,{wins:w},w.length?{}:{state:"closed"}); } } STATUS=stripClosing(Object.assign({},RAW_STATUS)); paintStatus(); }; 1`);
const xIds = `[...document.querySelectorAll('#rail .it[data-id]')].filter(el=>el.querySelector('.x')).map(el=>el.dataset.id)`;
const order = `[...document.querySelectorAll('#rail .it[data-id]')].map(el=>el.dataset.id)`;
async function closeOne(sid, pre){
  await ev(`setFocus(${JSON.stringify(sid)}); 1`); await sleep(400);
  const ord = await ev(order); const expectNext = ord[ord.indexOf(sid)+1] || null;
  const t0 = Date.now();
  await ev(`${pre||""}; document.querySelector('#rail .it[data-id="${sid}"] .x').click(), 1`);
  const r = await ev(`({gone: !document.querySelector('#rail .it[data-id="${sid}"]'), on: RAIL_ON, focus: FOCUS, empty: (document.querySelector('#list .hint')||{}).textContent||""})`);
  r.ms = Date.now()-t0; r.expectNext = expectNext; return r;
}
// ③ 先测假失败: 弹回
{ const ids = await ev(xIds); const sid = ids[0];
  await ev(`window.__mode="fail"; 1`);
  const r = await closeOne(sid); out.fail = r;
  if(!r.gone || r.ms > 150) bad.push('假失败轮: 点 ✕ 后没有立刻消失 '+JSON.stringify(r));
  let back=false; for(let i=0;i<60;i++){ if(await ev(`!!document.querySelector('#rail .it[data-id="${sid}"] .x')`)){ back=true; break; } await sleep(100); }
  out.fail.back = back; out.fail.toast = await ev(`[...document.querySelectorAll('.toast,[class*=toast]')].map(e=>e.textContent).join(' | ').slice(-200)`);
  if(!back) bad.push('假失败轮: 没有弹回左栏');
  await sleep(300); }
// ② 成功: 消失 + 切到下一个
{ await ev(`window.__mode="ok"; 1`);
  const ids = await ev(xIds); const ord = await ev(order);
  const sid = ids.find(i => ord.indexOf(i) < ord.length-1);   // 不是最后一个的
  if(!sid) bad.push('没有「非最后一个」且带 ✕ 的标签, 无法测切到下一个');
  else { const r = await closeOne(sid); out.ok = r;
    if(!r.gone || r.ms > 150) bad.push('成功轮: 点 ✕ 后没有立刻消失 '+JSON.stringify(r));
    if(r.on !== r.expectNext) bad.push('成功轮: 没切到下一个标签 '+JSON.stringify(r));
    await sleep(1500);
    out.ok.stillGone = await ev(`!document.querySelector('#rail .it[data-id="${sid}"]')`);
    if(!out.ok.stillGone) bad.push('成功轮: 确认后又出现在左栏'); } }
// ④ 最后一个: 把它的 ✕ 造出来(给最后一项一个假窗口), 关掉后右边空
{ const ord = await ev(order); const last = ord[ord.length-1];
  const fake = `(()=>{ const s=RAW_STATUS[${JSON.stringify(last)}]||{}; if(!(s.wins||[]).length){ RAW_STATUS[${JSON.stringify(last)}]=Object.assign({},s,{wins:[{pid:999999001,title:"假窗口"}]}); STATUS=stripClosing(Object.assign({},RAW_STATUS)); paintRail(); } })()`;
  const r = await closeOne(last, fake); out.last = r; out.last.id = last;
  if(r.focus !== '∅' || !/空了/.test(r.empty)) bad.push('最后一个: 右边没显示空 '+JSON.stringify(r)); }
await sleep(500);
if(errs.length) bad.push('JS 异常: '+errs.join(' | '));
console.log(JSON.stringify(out, null, 1));
console.log(bad.length ? 'FAIL\n- '+bad.join('\n- ') : 'PASS');
p.kill(); process.exit(bad.length ? 1 : 0);
