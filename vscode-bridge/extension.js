// Claude Sessions Bridge —— 让对话管理器能点到**具体那个终端标签页**。
//
// 为什么需要一个扩展: VS Code 没有任何 CLI 参数或 URI handler 能聚焦某个终端,
// Windows 那一层也帮不上 —— 标签不是窗口, 没有自己的 HWND, 一个 IDE 窗口里所有
// 标签共用一个句柄。但扩展 API 里三件套是齐的:
//
//     window.terminals              枚举当前打开的终端
//     Terminal.processId            该终端 shell 进程的 OS pid
//     Terminal.show(preserveFocus)  把这个终端显示出来
//
// 而 `Terminal.processId` 正好就是对话管理器已经在追踪的那个 pid(claude 进程的父
// shell), 所以这个桥只做一件事: 收到 pid -> 找到对应的终端 -> show() 或 dispose()。
//
// 只绑 127.0.0.1, 不做任何鉴权之外的事, 也不读你的任何内容。
const vscode = require("vscode");
const http = require("http");

let server = null;

function json(res, code, obj) {
  const body = Buffer.from(JSON.stringify(obj), "utf8");
  res.writeHead(code, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": body.length,
    // 明确不许跨源读取; 配合下面的 Origin 拒绝, 网页碰不到这个端口
    "Cache-Control": "no-store",
  });
  res.end(body);
}

async function terminalList() {
  const out = [];
  for (const t of vscode.window.terminals) {
    let pid = null;
    try {
      pid = await t.processId;
    } catch (e) {
      pid = null;
    }
    out.push({
      pid: pid === undefined ? null : pid,
      name: t.name,
      active: t === vscode.window.activeTerminal,
    });
  }
  return out;
}

async function findByPid(pid) {
  for (const t of vscode.window.terminals) {
    try {
      if ((await t.processId) === pid) return t;
    } catch (e) {
      /* 这个终端问不出 pid 就跳过 */
    }
  }
  return null;
}

// 10-04 改向(用户:「能不能更快，好像都是一个挪的，还有闪动，另外也不太稳定」): VS Code 没有给终端标签排序的 API,
// 唯一能用的是 moveToEditor + moveToTerminalPanel(挪回面板的排到末尾, 每挪一个闪一下)。旧版「上=在跑 下=等你」时,
// 用户在等你区的对话里提问 → 要把分割线 + 下面全部 ~20 个依次挪到末尾。改成「上=等你 下=在跑」后:
//   提问(→在跑): 只挪它自己 1 次;  本轮结束(→等你): 挪分割线 + 其余在跑的(通常 2–4 个);  新开对话本来就在末尾 = 在跑区, 0 次。
const DIVIDER = "──────── ▲ 等你回答　▼ 在跑 ────────";
const OLD_DIVIDERS = ["──────── ▲ 在跑　▼ 等你回答 ────────"];
let placing = Promise.resolve();                  // 串行: 两个对话同时结束时不让挪动交错

function findDivider() {
  return vscode.window.terminals.find((x) => x.name === DIVIDER) || null;
}

function ensureDivider() {
  for (const x of vscode.window.terminals) if (OLD_DIVIDERS.indexOf(x.name) >= 0) x.dispose();
  let d = findDivider();
  if (d) return d;
  const em = new vscode.EventEmitter();
  const pty = {
    onDidWrite: em.event,
    open: () => em.fire("\x1b[2m这是分割线, 不是对话。上面: 等你回答的 Claude 对话; 下面: 还在跑的。由 Claude Sessions Bridge 自动维护。\x1b[0m\r\n"),
    close: () => {},
    handleInput: () => {},
  };
  d = vscode.window.createTerminal({ name: DIVIDER, pty: pty });
  return d;
}

async function moveToEnd(t) {
  t.show(true);
  await vscode.commands.executeCommand("workbench.action.terminal.moveToEditor");
  await vscode.commands.executeCommand("workbench.action.terminal.moveToTerminalPanel");
}

// vscode.window.terminals 的顺序不保证等于面板显示顺序, 所以自己记「分割线下面(在跑)有谁」。
// 面板顺序 = 等你(没进名单的) + 分割线 + running(按名单顺序), 每次挪动都维持这个不变式。
const running = [];
const WFILE = require("path").join(require("os").homedir(), ".claude", "scripts", "tab_running.json");
function readW() { try { return JSON.parse(require("fs").readFileSync(WFILE, "utf8")) || []; } catch (e) { return []; } }
async function saveRunning() {
  const mine = [], all = [];
  for (const x of vscode.window.terminals) { try { all.push(await x.processId); } catch (e) {} }
  for (const x of running) { try { mine.push(await x.processId); } catch (e) {} }
  const keep = readW().filter((p) => all.indexOf(p) < 0);           // 别的 VS Code 窗口的保留
  try { require("fs").writeFileSync(WFILE, JSON.stringify(keep.concat(mine))); } catch (e) {}
}

async function withRestore(fn) {
  const prev = vscode.window.activeTerminal;
  const moved = await fn();
  const d = findDivider();
  if (prev && prev !== d && moved) prev.show(false);
  if (moved) await saveRunning();
  return moved;
}

function placeTerminal(t, where) {
  const job = placing.then(async () => {
    const fresh = !findDivider();
    const d = ensureDivider();
    if (fresh) await new Promise((r) => setTimeout(r, 150));   // 新建的分割线排在末尾 = 此刻所有终端都在它上面(等你区)
    const i = running.indexOf(t);
    let moved = 0;
    if (where === "above-new") {                               // 新开的终端已在末尾 = 在跑区, 不用挪
      if (i < 0) { running.push(t); await saveRunning(); }
    } else if (where === "above" || where === "run") {         // 用户刚提问 → 在跑(线下)
      if (i < 0) moved = await withRestore(async () => { await moveToEnd(t); running.push(t); return 1; });
    } else {                                                   // below / wait: 本轮结束 → 等你(线上)
      if (i >= 0 || fresh) moved = await withRestore(async () => {
        if (i >= 0) running.splice(i, 1);
        let n = 0; await moveToEnd(d); n++;
        for (const x of running) { await moveToEnd(x); n++; }
        return n;
      });
    }
    return { ok: true, where: where, moved: moved, running: running.map((x) => x.name) };
  });
  placing = job.catch(() => {});
  return job.catch((e) => ({ ok: false, why: String(e) }));
}

// 全量对齐: 给出当前在跑的 shell pid 列表 → 分割线挪到末尾, 再把在跑的依次挪到它下面(共 1 + 在跑数 次)
function sortAll(pids) {
  const job = placing.then(async () => {
    ensureDivider(); await new Promise((r) => setTimeout(r, 150));
    const ts = [];
    for (const pid of pids) { const t = await findByPid(pid); if (t) ts.push(t); }
    const moved = await withRestore(async () => {
      running.length = 0;
      let n = 0; await moveToEnd(findDivider()); n++;
      for (const t of ts) { await moveToEnd(t); running.push(t); n++; }
      return n;
    });
    return { ok: true, moved: moved, running: running.map((x) => x.name) };
  });
  placing = job.catch(() => {});
  return job.catch((e) => ({ ok: false, why: String(e) }));
}

async function loadRunning() {
  const pids = readW(); const ts = [];
  for (const pid of pids) { const t = await findByPid(pid); if (t) ts.push(t); }
  if (!ts.length) return;
  const d = findDivider();
  if (d) { for (const t of ts) running.push(t); }              // 分割线还在 = 面板顺序没变, 只恢复名单
  else await sortAll(pids);                                    // 分割线没了(宿主重启时随旧宿主消失) → 重排
}

function readBody(req) {
  return new Promise((resolve) => {
    let buf = "";
    req.on("data", (c) => {
      buf += c;
      if (buf.length > 64 * 1024) req.destroy();     // 这个接口不需要大包
    });
    req.on("end", () => {
      try {
        resolve(JSON.parse(buf || "{}"));
      } catch (e) {
        resolve({});
      }
    });
  });
}

async function handle(req, res) {
  // 浏览器发出的请求一律拒绝: 否则任何网页都能悄悄关掉你的终端。
  // 本地脚本(curl / python / 对话管理器)不会带 Origin, 正常通过。
  if (req.headers.origin) return json(res, 403, { ok: false, why: "no cross-origin" });

  const url = (req.url || "").split("?")[0];
  if (url === "/ping") {
    return json(res, 200, {
      ok: true,
      what: "claude-sessions-bridge",
      resumeProviders: ["claude", "codex"],
      version: "0.1.0",
      vscode: vscode.version,
      pid: process.pid,
      windowTitle: (vscode.workspace.workspaceFolders || []).map((f) => f.name),
      routes: ["terminals", "new", "rename", "place", "sort", "show", "close", "type", "type2", "type3", "enter"], layout: "wait-above-run-below",
    });
  }
  if (url === "/terminals") {
    return json(res, 200, { ok: true, terminals: await terminalList() });
  }
  if (url === "/new") {
    // 在这个 VS Code 窗口里新开一个终端标签, 并把命令敲进去。
    //
    // 比"往窗口发键盘事件"可靠得多: createTerminal + sendText 是官方 API, 不抢焦点、
    // 不会敲错窗口、不用等提示符画完。
    //
    // **只允许起 claude。** sendText 等于在你的 shell 里执行任意命令, 所以这里卡死
    // 一个白名单 —— 本机别的程序本来就能直接执行命令(这个接口不扩大攻击面), 但一个
    // 只能干一件事的接口, 出问题时的排查成本低得多。
    const body = await readBody(req);
    const cmd = String(body.cmd || "");
    const codexResume = /^codex resume [0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(cmd);
    if (!/^claude(\s|$)/.test(cmd) && !codexResume) {
      return json(res, 400, { ok: false, why: "只允许启动 claude 或 codex resume <UUID>" });
    }
    // 不给 name: VS Code 对 createTerminal({name}) 走 _setTitle(name, Api) → 静态标题, 之后控制台标题(▶ 等你)全被忽略。
    // 要静态名才传 staticName:true; 平时标签名由 ~/.claude/scripts/tab_title.py 写控制台标题。
    let opts = body.staticName && body.name ? { name: String(body.name) } : {};
    if (body.cwd) opts.cwd = String(body.cwd);
    let t;
    try {
      t = vscode.window.createTerminal(opts);
    } catch (e) {
      return json(res, 200, { ok: false, why: "建不出终端: " + String(e) });
    }
    t.show();
    t.sendText(cmd, true);
    let pid = null;
    try {
      pid = await t.processId;
    } catch (e) {
      pid = null;
    }
    return json(res, 200, { ok: true, pid: pid, name: t.name });
  }
  if (url === "/rename") {
    // 把某个终端标签改名(例如改成 Claude 会话名, 形如 <用户名>-e9)。
    // VS Code 没有直接给 Terminal 改名的 API, 只能先让它成为活动终端, 再执行内置命令
    // workbench.action.terminal.renameWithArg。preserveFocus 保证不抢键盘焦点。
    const body = await readBody(req);
    const pid = Number(body.pid);
    const name = String(body.name || "").slice(0, 60);
    if (!pid || !name) return json(res, 400, { ok: false, why: "need pid and name" });
    const t = await findByPid(pid);
    if (!t) return json(res, 200, { ok: false, why: "这个窗口里没有 pid 为 " + pid + " 的终端" });
    const before = t.name;
    try {
      t.show(true);
      await vscode.commands.executeCommand("workbench.action.terminal.renameWithArg", { name: name });
    } catch (e) {
      return json(res, 200, { ok: false, why: "改名失败: " + String(e) });
    }
    return json(res, 200, { ok: true, from: before, to: t.name });
  }
  if (url === "/place") {
    // 用户 2026-10-03: 一个空的分割线终端, 上面 = 还在跑的对话, 下面 = 等用户回答的对话。
    // VS Code 没有给终端标签排序的 API; 已知可用的是 moveToEditor + moveToTerminalPanel:
    // 移回面板的终端排到列表末尾。所以:
    //   below(到分割线下): 把它移到末尾;
    //   above(回分割线上): 依次把 分割线 + 分割线下面除它以外的终端 移到末尾。
    // 挪完把原来的活动终端 show() 回来(尽量不打断正在打字的那个标签)。
    const body = await readBody(req);
    const pid = Number(body.pid);
    const where = String(body.where || "below");
    if (!pid) return json(res, 400, { ok: false, why: "need pid" });
    const t = await findByPid(pid);
    if (!t) return json(res, 200, { ok: false, why: "这个窗口里没有 pid 为 " + pid + " 的终端" });
    return json(res, 200, await placeTerminal(t, where));
  }
  if (url === "/sort") {
    const body = await readBody(req);
    return json(res, 200, await sortAll((body.running || []).map(Number).filter(Boolean)));
  }
  if (url === "/type") {
    // 往一个已有终端里打一行字并回车(给空闲的 claude 对话发文字: 对话管理器的发送框、ticketdesk 的回答 / 回复)。
    // 调用方负责先确认: 这个终端的 shell 底下正跑着一个 status=idle 的 claude 进程。
    // 这里再卡两条: 只收单行、≤4000 字; 而且必须以「【」开头(调用方的消息头), 不像一条 shell 命令。
    const body = await readBody(req);
    const pid = Number(body.pid);
    const text = String(body.text || "");
    if (!pid || !text) return json(res, 400, { ok: false, why: "need pid and text" });
    if (text.indexOf("\n") >= 0 || text.indexOf("\r") >= 0 || text.length > 4000) return json(res, 400, { ok: false, why: "只收单行且 ≤4000 字" });
    if (!text.startsWith("【")) return json(res, 400, { ok: false, why: "消息必须以【开头" });
    const t = await findByPid(pid);
    if (!t) return json(res, 200, { ok: false, why: "这个窗口里没有 pid 为 " + pid + " 的终端" });
    // 10-04 用户「从网页开vs控制台，没发出去，只是换行了」: sendText(text, true) 把字和回车放在同一块里写进 pty,
    // claude 的输入框按「一次到达的大块 = 粘贴」处理, 里面的回车成了换行。字和回车分两次写, 中间隔开, 回车才算按键。
    // 10-04 再修(11:35 实发: 约 130 字中文, 隔 250 ms 回车仍成换行): VS Code 把长文本分块写进 pty, 250 ms 时字还没写完。
    // 等待按长度放长: 300 ms + 每字 6 ms, 上限 2 s。管理器那边再确认是否真的收到, 没收到就调 /enter 补一次回车。
    t.sendText(text, false);
    const wait = Math.min(2000, 300 + text.length * 6);
    await new Promise((r) => setTimeout(r, wait));
    t.sendText("\r", false);
    return json(res, 200, { ok: true, typed: t.name, enter: "separate", wait_ms: wait });
  }
  if (url === "/enter") {
    // 只补一次回车(管理器确认 /type 后对话没收到提问时调用)。只按 pid 找终端, 不写任何字。
    const body = await readBody(req);
    const pid = Number(body.pid);
    if (!pid) return json(res, 400, { ok: false, why: "need pid" });
    const t = await findByPid(pid);
    if (!t) return json(res, 200, { ok: false, why: "这个窗口里没有 pid 为 " + pid + " 的终端" });
    t.sendText("\r", false);
    return json(res, 200, { ok: true, entered: t.name });
  }
  if (url === "/show" || url === "/close") {
    const body = await readBody(req);
    const pid = Number(body.pid);
    if (!pid) return json(res, 400, { ok: false, why: "need pid" });
    const t = await findByPid(pid);
    if (!t) {
      return json(res, 200, {
        ok: false,
        why: "这个窗口里没有 pid 为 " + pid + " 的终端",
        terminals: await terminalList(),
      });
    }
    if (url === "/show") {
      t.show(Boolean(body.preserveFocus));
      return json(res, 200, { ok: true, shown: t.name });
    }
    // dispose() 是 VS Code 自己的关法: 标签页干净地消失, 不会留下
    // "terminal process terminated with exit code" 那条提示。
    t.dispose();
    return json(res, 200, { ok: true, closed: t.name });
  }
  return json(res, 404, { ok: false, why: "no route" });
}

function start(context) {
  stop();
  const cfg = vscode.workspace.getConfiguration("claudeSessionsBridge");
  if (!cfg.get("enabled", true)) return;
  const port = cfg.get("port", 8721);
  // 每个 VS Code 窗口有自己的扩展宿主, 都会跑一份这个桥。所以不能只认一个端口 ——
  // 那样第二个窗口起来就哑了, 它里面的终端谁也够不着。改成占用端口段里第一个空位,
  // 对话管理器那边挨个端口问过去(问错了只会得到 ok:false, 没有副作用)。
  const span = Math.max(1, cfg.get("portSpan", 8));
  const tryListen = (i) => {
    if (i >= span) {
      console.log("[claude-sessions-bridge] 端口 " + port + "-" + (port + span - 1) + " 都被占了, 放弃");
      server = null;
      return;
    }
    const s = http.createServer((req, res) => {
      handle(req, res).catch((e) => json(res, 500, { ok: false, why: String(e) }));
    });
    s.on("error", () => { try { s.close(); } catch (e) {} tryListen(i + 1); });
    s.listen(port + i, "127.0.0.1", () => {
      server = s;
      console.log("[claude-sessions-bridge] listening on 127.0.0.1:" + (port + i));
    });
  };
  tryListen(0);
  context.subscriptions.push({ dispose: stop });
}

function stop() {
  if (server) {
    try { server.close(); } catch (e) { /* ignore */ }
    server = null;
  }
}

function activate(context) {
  start(context);
  // 10-04 用户:「闪动是不行的…不挪动窗口，只是重命名，用一个实心向右三角指示这些窗口在等我」→ 不再挪标签、不要分割线。
  // 等你标记改由 ~/.claude/scripts/tab_title.py 写控制台标题(零闪动)。/place 与 /sort 保留但没人调用。
  setTimeout(() => { for (const x of vscode.window.terminals) if (x.name === DIVIDER || OLD_DIVIDERS.indexOf(x.name) >= 0) x.dispose(); }, 1500);
  context.subscriptions.push(
    // 新开的终端默认排在末尾 = 分割线下面; 它是新对话(在跑), 挪回分割线上面
    vscode.window.onDidCloseTerminal((t) => {
      const i = running.indexOf(t);
      if (i >= 0) { running.splice(i, 1); saveRunning(); }
    }),
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("claudeSessionsBridge")) start(context);
    }),
    vscode.commands.registerCommand("claudeSessionsBridge.status", async () => {
      const cfg = vscode.workspace.getConfiguration("claudeSessionsBridge");
      const list = await terminalList();
      vscode.window.showInformationMessage(
        (server ? "监听中 127.0.0.1:" + server.address().port : "没在监听(端口段被占满, 或已关闭)") +
          " · 本窗口有 " + list.length + " 个终端: " +
          list.map((t) => t.name + "(" + t.pid + ")").join(", ")
      );
    })
  );
}

function deactivate() { stop(); }

module.exports = { activate, deactivate };
