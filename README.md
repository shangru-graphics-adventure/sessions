# sessions — 两个给 Claude Code 用的本地小工具

> Two tiny local-first tools for [Claude Code](https://claude.com/claude-code) on Windows:
> a **session manager** that lists every conversation on the machine (topics, deliverables,
> live status, one-click resume), and a **ticket desk** that times what you are working on.
> Pure Python standard library, no accounts, no network — everything stays on your box.

| 工具 | 端口 | 一句话 |
|---|---|---|
| [`session-manager/`](session-manager/) | 8720 | 本机**所有** Claude Code 对话的总目录：讲过哪些话题、交付了什么文件、哪些窗口此刻还活着，一键 resume / 切回已开着的窗口 / 关掉它 |
| [`vscode-bridge/`](vscode-bridge/) | 8721 | 可选的 VS Code 扩展：让上面那个能**点到具体哪个终端标签页**（标签没有窗口句柄，只有扩展 API 够得着） |
| [`ticket-desk/`](ticket-desk/) | 8730 | 个人任务计时台：记下达时刻、按秒表、子任务时间**按区间并集**并进父级（同时跑只算一份） |

两个各自独立跑，装哪个都行。放在一个仓库里，是因为它们回答同一个问题的两半：
**「我刚才在干什么、干了多久、东西在哪」**。

---

## 快速开始

```
git clone https://github.com/shangru-graphics-adventure/sessions.git
cd sessions

python session-manager/server.py      # -> http://localhost:8720/
python ticket-desk/server.py          # -> http://localhost:8730/
```

Windows 上也可以直接双击各自目录里的 `start.cmd`（会顺手把浏览器打开）。

**依赖**：Python 3.8+，标准库跑得起来。**唯一的第三方包是 `psutil`**，session-manager 用它
回答"这个对话的进程还在吗" —— 没装的话列表、搜索、预览、resume 全都正常，只是实时状态徽章
和窗口感知（切过去 / 关闭）会静默失效。要那部分就 `pip install psutil`。Ticket 台不需要任何第三方包。

**平台**：Windows only。窗口定位、`explorer /select,`、Windows Terminal 里开标签页并把命令
敲进去 —— 这几件事都是 Win32 特有的。Ticket 台本身是纯 HTTP + JSON，在别的系统上也能跑。

## 两个可选的加分项

1. **实时状态徽章**（session-manager）：`python session-manager/install_hooks.py` 把四个
   hook 装进 `~/.claude/settings.json`（幂等、自动备份、`--remove` 可卸，且只动它自己加的那几条）。
   装完，列表里每个还开着的对话会显示「正在跑 / 等你确认 / 开着·等你」以及它此刻在做什么。
   装完还多两件事：同一个对话被开在**多个窗口**里时页面会告警并列出它们（两个窗口写同一份
   记录会互相覆盖），每个后面带「切过去 / ✕ 关闭」；Resume 按钮在对话已经开着时自己变成
   「⇥ 切过去」，不会重复打开。
2. **让 Claude 自己开 ticket**：把 `ticket-desk/claude-skill/` 拷成 `~/.claude/skills/ticket/`，
   然后对 Claude 说「给这个对话建个 ticket，标题 xxx」，它会建好并立刻开始计时。

## 两件跨机器的小事

**编码**：所有入口脚本启动时把 stdout/stderr 切成 UTF-8，`start.cmd` 另外设了 `PYTHONUTF8=1`。
不这么做，在一台默认 code page 是 1252/936 的机器上，打印中文会直接抛 `UnicodeEncodeError`
或者悄悄变成乱码 —— 后者更坏，因为它不报错。

**深色模式**：Ticket 台默认跟随系统配色，所以在一台深色模式的电脑上打开是深色版而不是粉蓝。
右上角 `◐` 按钮在「跟随系统 / 浅色 / 深色」之间循环，选择记在 localStorage 里。

## 你的数据在哪

全部在本机，两个服务都只绑 `127.0.0.1`，不联网、不上报、没有账号。

| 文件 | 内容 | 是否进 git |
|---|---|---|
| `session-manager/notes.json` | 你自己填的标题/注释/星标 | ✗ 已 gitignore |
| `session-manager/cache.json` | 解析缓存，删了只是下次慢半秒 | ✗ |
| `session-manager/state/` | hook 写的每会话实时状态 | ✗ |
| `session-manager/config.json` | 你这台机器的端口与路径 | ✗ |
| `ticket-desk/tickets.json` | 你的 ticket 与计时区间 | ✗ |

session-manager **只读** `~/.claude/projects/*/*.jsonl`，从不改动或删除你的对话记录。

## 配置

开箱即用 —— 所有默认值都从家目录推出来。要改：复制 `session-manager/config.example.json`
成 `config.json`，或用环境变量（`SESSIONS_PORT` / `CLAUDE_CONFIG_DIR` / `SESSIONS_RECAP_DIR`
/ `SESSIONS_ARCHIVE_DIRS` / `SESSIONS_PROJECT_ROOTS`，ticket 台是 `TICKET_PORT` / `TICKET_DATA`）。
细节见各自的 README。

## 想看细节

两个 README 都写得比代码长，里面记着**踩过的坑与实测数字**，不是功能罗列：

- [session-manager/README.md](session-manager/README.md) —— 话题与产物是怎么抽出来的、
  为什么 resume 要「模拟手敲」而不是直接挂命令、hook 里为什么不能用 `sys.stdin.read()`、
  为什么绝不能拿窗口标题当 `taskkill` 的过滤条件（作者为此一次关掉了 34 个正在跑的对话）。
- [ticket-desk/README.md](ticket-desk/README.md) —— 为什么存「运行区间」而不是「累计秒数」，
  以及并集口径下父子任务同时在跑为什么只算一份。

## License

MIT，见 [LICENSE](LICENSE)。
