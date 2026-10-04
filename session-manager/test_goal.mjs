// goal 显示验收(2026-10-04 用户「在标签栏也加个关闭按钮，按了就关闭这个对话的全部；而且要马上让这个标签消失，显示下一个标签的对话（如果是最后一个标签就显示空）」)。(2026-10-04 用户「如果goal active的话，在对话管理器也显示」)。node test_goal.mjs —— 只在页面里注入假状态, 不碰任何对话。rc=1 = 有一项不对。
import { spawn } from 'node:child_process';
const p = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', ['--headless=new','--disable-gpu','--remote-debugging-port=9355','--user-data-dir='+process.env.TEMP+'/cdp_goal','--window-size=1400,900','about:blank']);
const sleep=ms=>new Promise(r=>setTimeout(r,ms)); let ws;
for(let i=0;i<40;i++){ try{ const j=await (await fetch('http://127.0.0.1:9355/json')).json(); const pg=j.find(x=>x.type==='page'); if(pg){ ws=new WebSocket(pg.webSocketDebuggerUrl); break; } }catch(e){} await sleep(250); }
await new Promise(r=>ws.onopen=r); let id=0; const pend={}, errs=[];
ws.onmessage=ev=>{const m=JSON.parse(ev.data); if(m.id&&pend[m.id]){pend[m.id](m);delete pend[m.id];} if(m.method==='Runtime.exceptionThrown') errs.push(m.params.exceptionDetails.exception?.description||m.params.exceptionDetails.text);};
const send=(method,params={})=>new Promise(r=>{const i=++id;pend[i]=r;ws.send(JSON.stringify({id:i,method,params}));});
const ev=async e=>{const r=await send('Runtime.evaluate',{expression:e,awaitPromise:true,returnByValue:true}); return r.result.result?.value ?? r.result.exceptionDetails?.text;};
await send('Runtime.enable');
await send('Page.navigate',{url:'http://127.0.0.1:8720/'});
for(let i=0;i<100;i++){ if(await ev(`document.querySelectorAll('#rail .it[data-id]').length > 0`) === true) break; await sleep(100); }
await sleep(1500);
const bad=[], out={};
// 拿一个活着的会话, 注入 goal 状态(服务端字段名同 status_map), 看左栏 🎯 与状态框; 再撤掉看是否消失(阴性)
const sid = await ev(`Object.keys(RAW_STATUS).find(k => RAW_STATUS[k] && RAW_STATUS[k].state !== "closed")`);
if(!sid) bad.push('没有活着的会话可测');
const inj = on => `(()=>{ const s=Object.assign({},RAW_STATUS[${JSON.stringify(sid)}]); if(${on}){ Object.assign(s,{goal_on:true,goal_cond:"测试条件ABC",goal_reason:"测试理由XYZ",goal_n:3}); } else { delete s.goal_on; } RAW_STATUS[${JSON.stringify(sid)}]=s; STATUS=stripClosing(Object.assign({},RAW_STATUS)); paintStatus(); return 1; })()`;
await ev(`RAIL_SHUT.clear(); setFocus(${JSON.stringify(sid)}); 1`); await sleep(800);
await ev(inj(true));
out.on = await ev(`({rail: !!document.querySelector('#rail .it[data-id="${sid}"] .gl'), box: (document.querySelector('.row[data-id="${sid}"] .goalbox')||{}).textContent||""})`);
if(!out.on.rail) bad.push('goal 开着但左栏没 🎯');
if(!/测试条件ABC/.test(out.on.box) || !/测试理由XYZ/.test(out.on.box) || !/3 次/.test(out.on.box) || !/goal clear/.test(out.on.box)) bad.push('状态框内容不全: '+out.on.box.slice(0,200));
await ev(inj(false));
out.off = await ev(`({rail: !!document.querySelector('#rail .it[data-id="${sid}"] .gl'), box: !!document.querySelector('.row[data-id="${sid}"] .goalbox')})`);
if(out.off.rail || out.off.box) bad.push('goal 撤掉后还显示 '+JSON.stringify(out.off));
await sleep(300);
if(errs.length) bad.push('JS 异常: '+errs.join(' | '));
console.log(JSON.stringify(out, null, 1));
console.log(bad.length ? 'FAIL\n- '+bad.join('\n- ') : 'PASS');
p.kill(); process.exit(bad.length ? 1 : 0);
