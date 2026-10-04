// 本地小扩展(2026-10-04): 看到请求文件就重启扩展宿主, 让改过的本地扩展(claude-sessions-bridge 等)生效。
// 用法: touch ~/.claude/scripts/vscode_reload_request; 每个窗口的宿主每 3 秒看一次, 只认「比自己启动更晚」的请求,
// 所以重启后的新宿主不会再重启(不循环)。终端与 claude 进程在 pty host 里, 不受扩展宿主重启影响。
const vscode = require("vscode");
const fs = require("fs");
const os = require("os");
const path = require("path");
const REQ = path.join(os.homedir(), ".claude", "scripts", "vscode_reload_request");
const LOG = REQ + ".log";
const BORN = Date.now();
let timer = null, fired = false;
function log(m) { try { fs.appendFileSync(LOG, new Date().toISOString() + " pid=" + process.pid + " " + m + "\n"); } catch (e) {} }
function check() {
  if (fired) return;
  let t;
  try { t = fs.statSync(REQ).mtimeMs; } catch (e) { return; }
  if (t <= BORN) return;
  fired = true; log("restartExtensionHost (req " + new Date(t).toISOString() + ")");
  vscode.commands.executeCommand("workbench.action.restartExtensionHost");
}
function activate() { log("activated"); timer = setInterval(check, 3000); }
function deactivate() { if (timer) clearInterval(timer); }
module.exports = { activate, deactivate };
