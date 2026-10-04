// 10-04 用户「应该支持粘贴图片（像命令行）」「每个页面做一个到顶部、到底部的按钮」「开关要有反馈」验收。rc=1 = 有一项不对。不发送任何消息。
// node test_paste.mjs <会话 id(要在列表里)>
// 标准: ↑↓ 按钮能滚到顶/底; toast 进行中走表、done 后带用时; 往输入框粘一张图 → 缩略图出现、服务端存成文件、发送文本里带「[图片 #1: 路径]」
// 阳性对照: 粘纯文字不应出图; 非图片 dataURL 服务端必须拒
import { spawn } from 'node:child_process';
const SID = process.argv[2];
const p = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', ['--headless=new','--disable-gpu','--remote-debugging-port=9373','--user-data-dir='+process.env.TEMP+'/cdp_paste','--window-size=1400,900','about:blank']);
const sleep=ms=>new Promise(r=>setTimeout(r,ms)); let ws;
for(let i=0;i<40;i++){ try{ const j=await (await fetch('http://127.0.0.1:9373/json')).json(); const pg=j.find(x=>x.type==='page'); if(pg){ ws=new WebSocket(pg.webSocketDebuggerUrl); break; } }catch(e){} await sleep(250); }
await new Promise(r=>ws.onopen=r); let id=0; const pend={}, errs=[];
ws.onmessage=ev=>{const m=JSON.parse(ev.data); if(m.id&&pend[m.id]){pend[m.id](m);delete pend[m.id];} if(m.method==='Runtime.exceptionThrown') errs.push(m.params.exceptionDetails.exception?.description||m.params.exceptionDetails.text);};
const send=(method,params={})=>new Promise(r=>{const i=++id;pend[i]=r;ws.send(JSON.stringify({id:i,method,params}));});
const ev=async e=>{const r=await send('Runtime.evaluate',{expression:e,awaitPromise:true,returnByValue:true}); return r.result.result?.value ?? r.result.exceptionDetails?.text;};
await send('Runtime.enable');
await send('Page.navigate',{url:'http://127.0.0.1:8720/'});
for(let i=0;i<40 && !(await ev('ROWS && ROWS.length>0'));i++) await sleep(250);
const out={}, bad=[];
out.jump = await ev(`(async()=>{ $("#jBot").click(); await new Promise(r=>setTimeout(r,50)); const a=scrollY; $("#jTop").click(); await new Promise(r=>setTimeout(r,50)); return [a, scrollY]; })()`);
if(!(out.jump[0]>0 && out.jump[1]===0)) bad.push('↑↓ 没滚动: '+JSON.stringify(out.jump));
out.toast = await ev(`(async()=>{ const T=toast("正在开测试…"); await new Promise(r=>setTimeout(r,1100)); const a=document.querySelector(".toast").textContent; T.done("开了 ✓", true); return [a, document.querySelector(".toast").textContent, document.querySelector(".toast").className]; })()`);
if(!(/1秒/.test(out.toast[0]) && /开了 ✓用时/.test(out.toast[1]) && /ok/.test(out.toast[2]))) bad.push('toast 不对: '+JSON.stringify(out.toast));
await ev(`setFocus(${JSON.stringify(SID)})`);
for(let i=0;i<40 && !(await ev('!!document.querySelector(".compose textarea")'));i++) await sleep(250);
// 40x40 红图
out.paste = await ev(`(async()=>{ const c=document.createElement("canvas"); c.width=c.height=40; const g=c.getContext("2d"); g.fillStyle="red"; g.fillRect(0,0,40,40);
  const blob=await new Promise(r=>c.toBlob(r,"image/png")); const ta=document.querySelector(".compose textarea");
  const dt1=new DataTransfer(); dt1.setData("text/plain","只是文字"); ta.dispatchEvent(new ClipboardEvent("paste",{clipboardData:dt1,bubbles:true,cancelable:true}));
  const afterText=document.querySelectorAll(".compose .img").length;
  const dt=new DataTransfer(); dt.items.add(new File([blob],"x.png",{type:"image/png"})); ta.dispatchEvent(new ClipboardEvent("paste",{clipboardData:dt,bubbles:true,cancelable:true}));
  for(let i=0;i<40 && !/已存/.test(document.querySelector(".compose .cst").textContent);i++) await new Promise(r=>setTimeout(r,100));
  return {afterText, imgs: document.querySelectorAll(".compose .img").length, label: document.querySelector(".compose .img .lb")?.textContent, st: document.querySelector(".compose .cst").textContent}; })()`);
if(out.paste.afterText!==0) bad.push('粘纯文字也出了图');
if(!(out.paste.imgs===1 && out.paste.label==='#1' && /已存 .*uploads/.test(out.paste.st))) bad.push('粘图不对: '+JSON.stringify(out.paste));
// 拦截 fetch 看发送的文本, 不真发
out.sent = await ev(`(async()=>{ let body=null; const F=window.fetch; window.fetch=async(u,o)=>{ if(String(u).includes("/api/send")){ body=JSON.parse(o.body); return new Response(JSON.stringify({ok:false,why:"测试拦截"})); } return F(u,o); };
  const ta=document.querySelector(".compose textarea"); ta.value="看图"; document.querySelector(".compose .go").click(); await new Promise(r=>setTimeout(r,300)); window.fetch=F; return body && body.text; })()`);
if(!/^看图 \[图片 #1: C:\/.+\/uploads\/.+\.png\]$/.test(out.sent||'')) bad.push('发送文本没带图片路径: '+out.sent);
const neg = await (await fetch('http://127.0.0.1:8720/api/paste_image',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({data:'data:text/html;base64,AAAA'})})).json();
if(neg.ok) bad.push('阳性对照失败: 非图片被收了');
out.errs=errs; out.bad=bad; console.log(JSON.stringify(out,null,1)); const code=bad.length||errs.length?1:0; p.kill(); ws.onclose=()=>process.exit(code); ws.close(); setTimeout(()=>process.exit(code),500);   // 直接 exit 会撞 libuv 断言(rc=127)
