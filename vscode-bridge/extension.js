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
      version: "0.1.0",
      vscode: vscode.version,
      pid: process.pid,
      windowTitle: (vscode.workspace.workspaceFolders || []).map((f) => f.name),
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
    if (!/^claude(\s|$)/.test(cmd)) {
      return json(res, 400, { ok: false, why: "这个接口只用来起 claude" });
    }
    let opts = { name: String(body.name || "claude") };
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
  context.subscriptions.push(
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
