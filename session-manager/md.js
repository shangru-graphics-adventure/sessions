// 小型 Markdown 渲染(2026-10-04 用户:「完整回复栏，那些加粗、颜色之类的 claude 在控制台有的字体形态，能否也如实显示」)。
// 覆盖 Claude 回复里实际会出现的: **加粗** *斜体* ~~删除~~ `行内代码` [链接](https://…) 标题 # 列表(含缩进/有序) > 引用 表格 ``` 代码块 --- 分隔线。
// 安全: 先整体 HTML 转义再加标签, 链接只认 http(s); 回复里的任何 HTML 都按原文显示, 不执行。
// mdRender(text, link) / mdInline(text, link): link(已转义文本) → 加了编号链接的 HTML(例: ticketdesk 用 T/P/K)。
(function(){
  const esc = s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  function inline(raw, link){
    link = link || (x=>x);
    return String(raw).split(/(`[^`\n]+`)/).map((seg,i)=>{
      if(i%2) return `<code>${esc(seg.slice(1,-1))}</code>`;
      let s = esc(seg)
        .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, (m,t,u)=>`<a href="${u}" target="_blank" rel="noopener noreferrer">${t}</a>`)
        .replace(/\*\*([^*\n]+?)\*\*/g, "<b>$1</b>")
        .replace(/__([^_\n]+?)__/g, "<b>$1</b>")
        .replace(/(^|[^*\w])\*([^*\s][^*\n]*?)\*(?!\w)/g, "$1<i>$2</i>")
        .replace(/~~([^~\n]+?)~~/g, "<s>$1</s>");
      return s.replace(/(<a [^>]*>.*?<\/a>)|([^<]+|<[^>]*>)/g, (m,a,txt)=> a ? a : (txt && txt[0]!=="<" ? link(txt) : txt));
    }).join("")
      // 粗体里包着行内代码(**`x`** 或 **文字 `x` 文字**): 先按反引号切段会把两边的 ** 分到不同段, 这里在拼回后补配对(只认跨过 <code> 的那种, 不碰代码内部)
      .replace(/\*\*((?:(?!\*\*)[^\n])*?<code>[^<]*<\/code>(?:(?!\*\*)[^\n])*?)\*\*/g, "<b>$1</b>");
  }
  function cells(l){ return l.trim().replace(/^\||\|$/g,"").split("|").map(c=>c.trim()); }
  function render(raw, link){
    const L = String(raw||"").replace(/\r/g,"").split("\n"); let h = "", i = 0, para = [];
    const flush = ()=>{ if(para.length){ h += `<p>${para.map(x=>inline(x,link)).join("<br>")}</p>`; para = []; } };
    while(i < L.length){
      const l = L[i], t = l.trim();
      if(/^```/.test(t)){                                            // 代码块
        flush(); const lang = t.slice(3).trim(); const buf = []; i++;
        while(i < L.length && !/^```/.test(L[i].trim())) buf.push(L[i++]);
        i++; h += `<pre${lang?` data-lang="${esc(lang)}"`:""}><code>${esc(buf.join("\n"))}</code></pre>`; continue;
      }
      if(/^\|.*\|\s*$/.test(t) && i+1 < L.length && /^\|?\s*:?-{2,}/.test(L[i+1].trim())){   // 表格
        flush(); const head = cells(t); i += 2; const rows = [];
        while(i < L.length && /^\|/.test(L[i].trim())) rows.push(cells(L[i++]));
        h += `<div class="mdtw"><table><thead><tr>${head.map(c=>`<th>${inline(c,link)}</th>`).join("")}</tr></thead><tbody>${rows.map(r=>`<tr>${r.map(c=>`<td>${inline(c,link)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`; continue;
      }
      if(!t){ flush(); i++; continue; }
      let m;
      if((m = t.match(/^(#{1,6})\s+(.*)$/))){ flush(); h += `<div class="mdh h${m[1].length}">${inline(m[2],link)}</div>`; i++; continue; }
      if(/^(-{3,}|\*{3,}|_{3,})$/.test(t)){ flush(); h += "<hr>"; i++; continue; }
      if(/^>\s?/.test(t)){                                           // 引用
        flush(); const buf = [];
        while(i < L.length && /^>\s?/.test(L[i].trim())) buf.push(L[i++].trim().replace(/^>\s?/,""));
        h += `<blockquote>${render(buf.join("\n"), link)}</blockquote>`; continue;
      }
      if((m = l.match(/^(\s*)([-*•+]|\d+[.)])\s+(.*)$/))){            // 列表(按缩进分层)
        flush(); let out = "";
        while(i < L.length && (m = L[i].match(/^(\s*)([-*•+]|\d+[.)])\s+(.*)$/))){
          const lvl = Math.min(4, Math.floor(m[1].replace(/\t/g,"  ").length/2)), ol = /\d/.test(m[2]);
          out += `<div class="mdli${ol?" ol":""}" style="margin-left:${lvl*1.3}em"><span class="mdb">${ol?esc(m[2]):"•"}</span><span>${inline(m[3],link)}</span></div>`; i++;
          while(i < L.length && L[i].trim() && /^\s{2,}\S/.test(L[i]) && !/^\s*([-*•+]|\d+[.)])\s+/.test(L[i])){   // 列表项的续行
            out += `<div class="mdli cont" style="margin-left:${lvl*1.3+1.1}em">${inline(L[i].trim(),link)}</div>`; i++;
          }
        }
        h += `<div class="mdl">${out}</div>`; continue;
      }
      para.push(t); i++;
    }
    flush(); return h;
  }
  window.mdRender = render; window.mdInline = inline;
  document.head.insertAdjacentHTML("beforeend", `<style>
/* 10-04 用户:「回答正文的字体和颜色、高亮也优化一下」—— 正文深色字白底; **加粗** = 蓝字 + 荧光笔底纹(与流水账「**…** 蓝色高亮」同一约定) */
:root{--md-hl:rgba(74,139,214,.16);--md-b:#1f5fa8;--md-code-bg:#f3eef8;--md-code:#8a3f6e;--md-h:#3a2f58}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--md-hl:rgba(106,163,230,.22);--md-b:#a9cdf5;--md-code-bg:#332c40;--md-code:#f0a8cc;--md-h:#e3dcf5}}
:root[data-theme="dark"]{--md-hl:rgba(106,163,230,.22);--md-b:#a9cdf5;--md-code-bg:#332c40;--md-code:#f0a8cc;--md-h:#e3dcf5}
.md{line-height:1.75;overflow-wrap:anywhere;font-family:"Segoe UI","Microsoft YaHei UI","PingFang SC",-apple-system,sans-serif;color:var(--ink);letter-spacing:.01em}
.md p{margin:.45em 0}
.md b{font-weight:700;color:var(--md-b);background:linear-gradient(transparent 55%,var(--md-hl) 55%);border-radius:2px;padding:0 1px}
.md i{color:var(--md-h)}
.md s{color:var(--muted)}
.md code{font-family:"Cascadia Mono",Consolas,ui-monospace,monospace;font-size:.88em;background:var(--md-code-bg);color:var(--md-code);border-radius:5px;padding:1px 5px}
.md b code{color:inherit}
.md pre{background:#1f2330;color:#e6e6e6;border-radius:8px;padding:8px 10px;overflow:auto;font-size:12.5px;line-height:1.5;margin:.6em 0}
.md pre code{background:none;color:inherit;padding:0;font-size:inherit}
.md .mdh{font-weight:700;color:var(--md-h);margin:.9em 0 .3em;padding-left:.5em;border-left:3px solid var(--blue)} .md .mdh.h1{font-size:1.22em} .md .mdh.h2{font-size:1.12em} .md .mdh.h3{font-size:1.04em}
.md .mdh b{background:none;color:inherit}
.md .mdl{margin:.35em 0} .md .mdli{display:flex;gap:.5em;margin-top:.25em} .md .mdb{color:var(--blue);flex:none;min-width:.9em;font-weight:700}
.md .mdli.ol .mdb{font-variant-numeric:tabular-nums}
.md .mdli.cont{display:block;color:inherit}
.md blockquote{margin:.5em 0;padding:.15em .9em;border-left:3px solid var(--lav);background:var(--lav-soft);border-radius:0 6px 6px 0;color:inherit}
.md hr{border:0;border-top:1px solid var(--line);margin:.8em 0}
.md .mdtw{overflow:auto;margin:.6em 0} .md table{border-collapse:collapse;font-size:.92em} .md th,.md td{border:1px solid var(--line);padding:4px 9px;text-align:left;vertical-align:top} .md th{background:var(--hover,rgba(0,0,0,.04));color:var(--md-h)}
.md a{color:var(--blue-ink);text-decoration:underline;text-underline-offset:2px}
</style>`);
})();
