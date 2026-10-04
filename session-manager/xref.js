// 对话文字里的自动链接(用户 2026-10-04:「提到Q257，就自动有一个超链接…C2523也是要弹出悬浮窗…其他的有用的也作相应超链接处理，
// 使得我对话中遇到任何晦涩概念或代号都可以跳到相应的地方（注意成本）」「网页、文件夹之类的，也给一个可以直接点过去的超链接；文件夹甚至可以悬停去点parent folder」)
//
// 成本: 纯前端正则 + 悬停时才向本机服务要一次数据(带缓存), 不调模型。
// 内置(公开版也有): 网址 → 新标签打开; 文件/文件夹路径 → 点击预览文件 / 打开文件夹, 悬停列出逐级上级目录(每级可点)。
// 本机规则(config.json 的 "xref", 不进公开版): 编号正则 → 点击去哪 + 悬停读哪个 JSON; 缩写表 → 悬停给释义。
//   XREF.apply(el, cwd)  —— 对 el 里的文字节点加链接(跳过已有 <a>、<pre>、按钮)。cwd = 相对路径的基准目录。
(function(){
  // 界面字随页面语言(<html lang>); ticketdesk 可切中英
  const ZH = () => (document.documentElement.lang || navigator.language || "").toLowerCase().startsWith("zh");
  const T = (zh, en) => ZH() ? zh : en;
  const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
  let RULES = [], GLOSS = null, GRE = null, READY = null, PREVIEW = true;
  // 打开文件夹 / 在资源管理器里选中: POST(带 X-Desk-Client 头), 免得任意网页用一个 GET 就能在本机弹窗
  const openLocal = (path, select) => fetch("/api/xref/open", {method: "POST", headers: {"Content-Type": "application/json", "X-Desk-Client": "1"}, body: JSON.stringify({path, select: !!select})});
  const CACHE = {};
  function load(){
    if(READY) return READY;
    READY = fetch("/api/xref/rules").then(r => r.json()).then(d => {
      RULES = (d.rules || []).map((r, i) => Object.assign({}, r, {i, rx: new RegExp(r.re, "g")}));
      GLOSS = d.gloss || null; PREVIEW = d.preview !== false;
      const ks = GLOSS ? Object.keys(GLOSS).filter(k => k.length >= 2).sort((a, b) => b.length - a.length) : [];
      // ASCII 缩写要求两侧不是字母数字(避免 HD 匹配进 HDMI); 中文词直接匹配
      if(ks.length) GRE = new RegExp(ks.map(k => /^[\x00-\x7f]+$/.test(k) ? `(?<![A-Za-z0-9_])${k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?![A-Za-z0-9_])` : k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|"), "g");
    }).catch(() => {});
    return READY;
  }
  const URL_RE = /https?:\/\/[^\s<>"'`，。；、）】]+[^\s<>"'`，。；、）】.,;:!?)\]]/g;
  // 绝对路径: C:\… / C:/… / ~/…; 相对路径: 至少一个 / 且以 .扩展名 结尾(docs/notes/x.md)
  const PATH_RE = /(?:[A-Za-z]:[\\/]|~[\\/])[^\s<>"'`|*?，。；：、）)】\]]*[^\s<>"'`|*?，。；：、）)】\].,;:]|(?<![\w/.\\:-])(?:[\w.\-\u4e00-\u9fa5]+\/)+[\w.\-\u4e00-\u9fa5]+\.[A-Za-z0-9]{1,6}(?![\w/])/g;

  function matches(text){
    const out = [];
    const add = (rx, kind, extra) => { rx.lastIndex = 0; let m; while((m = rx.exec(text))){ if(!m[0]) { rx.lastIndex++; continue; } out.push(Object.assign({a: m.index, b: m.index + m[0].length, s: m[0], kind}, extra || {})); } };
    add(URL_RE, "url");
    add(PATH_RE, "path");
    RULES.forEach(r => add(r.rx, "rule", {r}));
    if(GRE) add(GRE, "gloss");
    out.sort((x, y) => x.a - y.a || (y.b - y.a) - (x.b - x.a));
    const keep = []; let end = -1;                   // 重叠时取先出现、更长的(网址里的编号不再单独链)
    for(const m of out) if(m.a >= end){ keep.push(m); end = m.b; }
    return keep;
  }
  function node(m){
    if(m.kind === "url"){ const a = document.createElement("a"); a.href = m.s; a.target = "_blank"; a.rel = "noopener noreferrer"; a.className = "xr xr-url"; a.textContent = m.s; return a; }
    const sp = document.createElement(m.kind === "rule" && m.r.url ? "a" : "span");
    sp.textContent = m.s; sp.className = "xr xr-" + m.kind; sp.dataset.k = m.s;
    if(m.kind === "rule"){ sp.dataset.r = m.r.i; if(m.r.url){ sp.href = m.r.url.replace(/\{0\}/g, encodeURIComponent(m.s)); sp.target = "_blank"; sp.rel = "noopener"; } }
    return sp;
  }
  function apply(el, cwd){
    if(!el) return;
    load().then(() => {
      const w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, {acceptNode: n =>
        !n.nodeValue.trim() || n.parentElement.closest("a,pre,button,textarea,.xr,.xtip,summary button") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT});
      const nodes = []; while(w.nextNode()) nodes.push(w.currentNode);
      for(const n of nodes){
        const t = n.nodeValue, ms = matches(t); if(!ms.length) continue;
        const f = document.createDocumentFragment(); let p = 0;
        for(const m of ms){ if(m.a > p) f.appendChild(document.createTextNode(t.slice(p, m.a))); const x = node(m); if(cwd) x.dataset.cwd = cwd; f.appendChild(x); p = m.b; }
        if(p < t.length) f.appendChild(document.createTextNode(t.slice(p)));
        n.parentNode.replaceChild(f, n);
      }
    });
  }

  // ── 悬停浮窗 ──
  const tip = document.createElement("div"); tip.className = "xtip"; tip.hidden = true;
  document.addEventListener("DOMContentLoaded", () => document.body.appendChild(tip));
  if(document.body) document.body.appendChild(tip);
  let cur = null, hideT = null, pinned = false;
  function place(el){
    const r = el.getBoundingClientRect(), W = Math.min(520, innerWidth * 0.92);
    tip.style.left = Math.max(8, Math.min(r.left, innerWidth - W - 8)) + "px";
    tip.style.maxWidth = W + "px";
    const below = r.bottom + 6, h = tip.offsetHeight || 160;
    tip.style.top = (below + h > innerHeight - 8 ? Math.max(8, r.top - h - 6) : below) + "px";
  }
  async function getJSON(u){ if(!(u in CACHE)) CACHE[u] = fetch(u).then(r => r.ok ? r.json() : null).catch(() => null); return CACHE[u]; }
  function fmtRule(x, k){
    if(!x) return `${esc(k)}${T("：没查到", ": not found")}`;
    const head = [x.id || k, x.status, x.family, x.priority, x.recorded || x.updated].filter(Boolean).map(esc).join(" · ");
    const val = x.metric ? `${esc(x.metric)}${x.value != null ? " = " + esc(Array.isArray(x.value) ? x.value.join(", ") : x.value) + esc(x.unit || "") + (x.se != null ? " ± " + esc(x.se) : "") : ""}` : "";
    const title = x.title || x.answer_title || "";
    const body = x.statement || x.summary || x.answer || x.question || "";
    let refs = "";
    const t = x.tickets;
    const ids = Array.isArray(t) ? t : (t && typeof t === "object" ? [].concat(t.text || [], t.attached || []) : []);
    if(ids.length) refs = `<div class="xm">${T("引用它的：", "Cited by: ")}${ids.map(esc).join(" ")}</div>`;
    return `<div class="xm">${head}</div>${title ? `<b>${esc(title)}</b>` : ""}${val ? `<div><b>${val}</b></div>` : ""}<div>${esc(String(body).slice(0, 420))}${String(body).length > 420 ? "…" : ""}</div>${refs}`;
  }
  async function show(el){
    cur = el; tip.innerHTML = "…"; tip.hidden = false; place(el);
    let html = "";
    if(el.classList.contains("xr-gloss")) html = `<b>${esc(el.dataset.k)}</b>：${esc(GLOSS[el.dataset.k] || "")}`;
    else if(el.classList.contains("xr-rule")){
      const r = RULES[Number(el.dataset.r)];
      if(!r.hover){ html = `${esc(r.name || "")} ${esc(el.dataset.k)}`; }
      else { const x = await getJSON(`/api/xref/hover?r=${r.i}&k=${encodeURIComponent(el.dataset.k)}`); html = `<div class="xm">${esc(r.name || "")}</div>` + fmtRule(x, el.dataset.k); }
    }else if(el.classList.contains("xr-path")){
      const x = await getJSON(`/api/xref/path?p=${encodeURIComponent(el.dataset.k)}&cwd=${encodeURIComponent(el.dataset.cwd || "")}`);
      if(!x || !x.abs) html = T("解析不了这个路径", "Cannot resolve this path");
      else html = `<div class="xm">${x.exists ? (x.isdir ? T("文件夹", "Folder") : T("文件", "File") + (x.size != null ? " · " + (x.size > 1048576 ? (x.size / 1048576).toFixed(1) + " MB" : Math.ceil(x.size / 1024) + " KB") : "")) : T("⚠ 不存在(可能已移动或是相对别处的路径)", "⚠ Not found (moved, or relative to another folder)")}</div>`
        + `<div class="xpath">${esc(x.abs)}</div>`
        + `<div class="xm">${T("点一级打开那个文件夹：", "Open a parent folder:")}</div><div class="xpar">${(x.parents || []).map(p => `<a href="#" data-dir="${esc(p)}">${esc(p.split(/[\\/]/).filter(Boolean).pop() || p)}</a>`).join(" › ")}</div>`
        + (x.exists ? `<div class="xact">${x.isdir ? `<a href="#" data-dir="${esc(x.abs)}">${T("打开文件夹", "Open folder")}</a>` : (PREVIEW ? `<a href="/file?path=${encodeURIComponent(x.abs)}" target="_blank">${T("预览", "Preview")}</a> · ` : "") + `<a href="#" data-sel="${esc(x.abs)}">${T("在资源管理器里选中", "Show in file manager")}</a>`}</div>` : "");
    }
    if(cur !== el) return;
    tip.innerHTML = html; place(el);
    if(el.classList.contains("xr-rule")) apply(tip);       // 浮窗里的编号也能点(引用它的 Q…)
  }
  document.addEventListener("mouseover", e => {
    const el = e.target.closest && e.target.closest(".xr-rule,.xr-gloss,.xr-path");
    if(el){ clearTimeout(hideT); if(el !== cur && !pinned) show(el); return; }
    if(e.target.closest && e.target.closest(".xtip")){ clearTimeout(hideT); return; }
    if(!pinned && !tip.hidden){ clearTimeout(hideT); hideT = setTimeout(() => { tip.hidden = true; cur = null; }, 350); }
  });
  document.addEventListener("click", async e => {
    const d = e.target.closest && e.target.closest(".xtip [data-dir], .xtip [data-sel]");
    if(d){ e.preventDefault(); await openLocal(d.dataset.dir || d.dataset.sel, !d.dataset.dir); return; }
    const p = e.target.closest && e.target.closest(".xr-path");
    if(p){                                                  // 点路径: 文件 → 预览, 文件夹 → 打开
      e.preventDefault(); e.stopPropagation();
      const x = await getJSON(`/api/xref/path?p=${encodeURIComponent(p.dataset.k)}&cwd=${encodeURIComponent(p.dataset.cwd || "")}`);
      if(x && x.exists){ if(x.isdir) openLocal(x.abs, false); else if(PREVIEW) window.open("/file?path=" + encodeURIComponent(x.abs), "_blank"); else openLocal(x.abs, true); }
      else { pinned = true; show(p); }
      return;
    }
    const r = e.target.closest && e.target.closest("span.xr-rule");   // 没有跳转目标的编号(配置里只给了 hover 的规则): 点一下钉住浮窗
    if(r){ e.preventDefault(); e.stopPropagation(); pinned = true; show(r); return; }
    if(pinned && !(e.target.closest && e.target.closest(".xtip"))){ pinned = false; tip.hidden = true; cur = null; }
  }, true);
  document.addEventListener("keydown", e => { if(e.key === "Escape" && !tip.hidden){ pinned = false; tip.hidden = true; cur = null; } });
  window.XREF = {apply, load};
})();
