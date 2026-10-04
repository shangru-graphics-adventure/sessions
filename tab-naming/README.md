# tab-naming — 一眼看出每个 VS Code 终端标签里的 Claude 对话在干什么

> Name every Claude Code terminal tab in VS Code by what it is doing, put a ▶ in front of the ones that are still
> running, and only chime when it is really your turn — without moving, switching or flashing any tab.
> Optional: [`../vscode-bridge`](../vscode-bridge) (only for handoff housekeeping and the old API-rename fallback).

同时开七八个 Claude Code 对话时，最常见的困扰是：哪个标签在干什么？哪个还在跑、哪个在等我？为什么它响了我却不用回答？
这个目录是几个钩子脚本加一个命令行工具，解决这三件事。

## 它做什么

| 功能 | 怎么做 |
|---|---|
| **标签名 = 在干什么** | 第一次提问时标签立刻换成提问的前几个字（`～` 开头的临时名）；第一轮答完，`tabname.py autotitle` 在后台用 `claude -p --model haiku` 按 `tab_prompt.txt` 把你说过的话压成 ≤12 字的名字，改成「`<标签名> [<会话名>(<id 前 8 位>)]`」。 |
| **▶ = 还在跑** | 你一提问，标签前加「▶ 」；轮到你回答时去掉。改的是**控制台标题**（`tab_title.py`：AttachConsole + SetConsoleTitleW，ConPTY 把它当 OSC 0 发给 VS Code），不调 VS Code 的任何命令：不切标签、不挪位置、零闪动。 |
| **只在轮到你时响** | `turn_notify.py`（Stop / Notification / UserPromptSubmit 钩子）判断「是否轮到你」：交互会话、本轮结束、没有在跑的后台任务，或者弹了权限确认 → 响一声（`ding.ps1`，随机播放 `$CLAUDE_DING_DIR` 里的一个 .wav/.mp3，没有就两声 beep，700 ms 内只响一次）。 |
| **状态同源 + 事件推送** | 改 ▶ 的同一时刻写 `~/.claude/turn_state.json`（`{sid: {state: running｜waiting, ts, label}}`）。对话管理器（`../session-manager`）和任何本地看板都以它为准；`turn_push.py` 每 0.25 s 只 stat 这几个文件，变了就经 SSE 推给浏览器，不用轮询。 |
| **交接带版本号** | 一个对话把活交给新对话时，`tabname.py handoff --sid <旧> --topic "<话题>" --term-pid <新终端>`：老标签「`话题 [k, 名(id8)]`」，新标签「`话题 [k+1-最新, 名(id8)]`」；新会话由后台轮询认领。认领后等旧会话空闲 8 秒再经桥关掉旧标签（建空文件 `~/.claude/handoff_keep_old` 可关掉这一步；旧记录仍在磁盘，`claude --resume` 可找回）。链登记在 `$CLAUDE_TAB_META`（缺省 `~/.claude/tab_chains.json`）。 |

### 为什么以前「还没轮到我也会响」

把提示音直接挂在 Stop 上，以下情况也会响：
1. 脚本里起的无界面 `claude -p`（标题生成、批处理）结束时也触发 Stop；
2. 对话把任务放到后台然后结束本轮，过一会儿后台任务完成又把它叫醒接着干 —— 那一声 Stop 并不需要你。

`turn_notify.py` 用 `~/.claude/sessions/<pid>.json` 的 `kind` 排除第 1 种，用 Stop 载荷里的 `background_tasks` 排除第 2 种
（这时标签保持 ▶，状态也补记「在跑」）。每次判断都写进 `turn_notify.log`（含载荷字段名），方便核对。

### 为什么不用 VS Code 的改名 API

`renameWithArg` 只作用于**活动**终端，改后台标签得先切过去再切回来（面板闪一下）；而且被 API 改过名的标签会变成静态标题，
之后进程发的标题全被忽略。控制台标题没有这两个问题。

## 安装

1. 依赖：Windows、Python 3.9+、`psutil`（`pip install psutil`），`claude` 在 PATH 里。
2. `~/.claude/settings.json` 的 `env` 里加 `"CLAUDE_CODE_DISABLE_TERMINAL_TITLE": "1"`，否则 Claude Code 会用自己的标题盖掉 ▶（只对之后新开的会话生效）。
3. 在 `hooks` 里加（路径换成你的克隆位置；**去掉**原来直接挂在 Stop 上的提示音）：

```json
"UserPromptSubmit": [
  {"hooks": [{"type": "command", "command": "python \"<repo>/tab-naming/first_prompt_title.py\""}]},
  {"hooks": [{"type": "command", "command": "python \"<repo>/tab-naming/turn_notify.py\" UserPromptSubmit"}]}
],
"Stop":         [{"hooks": [{"type": "command", "command": "python \"<repo>/tab-naming/turn_notify.py\" Stop"}]}],
"Notification": [{"hooks": [{"type": "command", "command": "python \"<repo>/tab-naming/turn_notify.py\" Notification"}]}]
```

可选环境变量：`CLAUDE_DING_DIR`（提示音文件夹，缺省 `~/Music/ding`）、`CLAUDE_TAB_META`（交接链文件）。
只想响、不想改 ▶：在 `turn_notify.py` 旁边建一个空文件 `turn_notify_noplace`。

## 命令行

```
python tab_title.py set --sid <会话 id> --label "登录页重构"   # 手动改名(登记在 ~/.claude/tab_labels.json)
python tab_title.py all                                      # 按登记名 + 当前状态把所有活对话的标题重写一遍
python tab_title.py show
python tabname.py nameall [--force]   # 给所有活着的对话起名(已带 [ ] 的跳过, --force 重起)
python tabname.py refresh             # 按交接链把链上成员的标签重改一遍
python tabname.py show                # 看交接链
python tabname.py handoff --sid <旧会话 id> --topic "ABC-12(登录超时)" --term-pid <新终端 shell pid>
python turn_notify.py --selftest      # 「是否轮到你」判断的阳性 / 阴性对照
```

在自己的看板里接事件推送：

```python
import turn_push
# BaseHTTPRequestHandler 里:
if path == "/api/live/stream": return turn_push.sse(self)          # 浏览器: new EventSource("/api/live/stream")
state = turn_push.status_of(session_json, turn_push.states().get(sid))   # busy | idle | shell | closed
```

## 已知限制

- **改名前被 VS Code API 改过名的老标签是静态的**，控制台标题改不动它。解锁：在终端标签列表里选中它 → F2 → 清空 → 回车（空名只给提示不拦，会清掉静态标题），之后立刻显示控制台标题。
- 标签名最长 60 字符，所以只放会话 id 的前 8 位。
- `background_tasks` 的结构 Claude Code 没有文档化：带 `status` 字段就看状态，没有就当作在跑（宁可不响也不误响）。
- Claude Code 的会话文件里「在等你」有 `idle` 与 `waiting` 两个值，`turn_push.status_of` 都当作等你。
- 标签名生成每次几秒到二十秒（`claude -p` 冷启动），全在后台，不阻塞对话。
