# tab-naming — 一眼看出每个 VS Code 终端标签里的 Claude 对话在干什么

> Name every Claude Code terminal tab in VS Code by what it is doing, keep a divider tab
> (running above, waiting-for-you below), and only chime when it is really your turn.
> Needs [`../vscode-bridge`](../vscode-bridge) (≥ the version with `/rename` and `/place`).

同时开七八个 Claude Code 对话时，最常见的困扰是：哪个标签在干什么？该去回答哪一个？为什么它响了我却不用回答？这个目录是三个小钩子脚本加一个命令行工具，解决这三件事。

## 它做什么

| 功能 | 怎么做 |
|---|---|
| **首问自动命名** | 每个对话第一次提问时，`first_prompt_title.py`（UserPromptSubmit 钩子）在后台起 `tabname.py autotitle`：用 `claude -p --model haiku` 按 `tab_prompt.txt` 把你说过的话压成 ≤12 字的标签名，改成「`<标签名> [<会话名>(<id 前 8 位>)]`」。斜杠命令（如 `/goal …`）取它的参数当内容。 |
| **交接带版本号** | 一个对话把活交给新对话时，`tabname.py handoff --sid <旧> --topic "<话题>" --term-pid <新终端>`：老标签改成「`话题 [k, 名(id8)]`」，新标签「`话题 [k+1-最新, 名(id8)]`」；新会话由后台轮询认领后补上名字。链登记在 `$CLAUDE_TAB_META`（缺省 `~/.claude/tab_chains.json`）。 |
| **分割线：在跑 / 等你** | 桥扩展的 `/place` 维护一个不跑任何进程的分割线伪终端：上面是还在跑的对话，下面是等你回答的。新开的终端自动放回分割线上面。 |
| **只在轮到你时响** | `turn_notify.py`（Stop / Notification / UserPromptSubmit 钩子）判断「是否轮到你」：交互会话、本轮结束、没有在跑的后台任务，或者弹了权限确认 → 响一声，并把它挪到分割线下面；你一提问就挪回上面。 |

### 为什么以前「还没轮到我也会响」

把提示音直接挂在 Stop 上，以下情况也会响：
1. 脚本里起的无界面 `claude -p`（标题生成、批处理）结束时也触发 Stop；
2. 对话把任务放到后台然后结束本轮，过一会儿后台任务完成又把它叫醒接着干 —— 那一声 Stop 并不需要你。

`turn_notify.py` 用 `~/.claude/sessions/<pid>.json` 的 `kind` 排除第 1 种，用 Stop 载荷里的 `background_tasks` 排除第 2 种。每次判断都写进 `turn_notify.log`（含载荷字段名），方便核对。

## 安装

1. 先装 [`../vscode-bridge`](../vscode-bridge)，改过扩展后在 VS Code 里执行 **Developer: Restart Extension Host**（终端不会断）。
2. 依赖：Python 3.9+、`psutil`（`pip install psutil`），`claude` 在 PATH 里。
3. 在 `~/.claude/settings.json` 的 `hooks` 里加（路径换成你的克隆位置；**去掉**原来直接挂在 Stop 上的提示音）：

```json
"UserPromptSubmit": [
  {"hooks": [{"type": "command", "command": "python \"<repo>/tab-naming/first_prompt_title.py\""}]},
  {"hooks": [{"type": "command", "command": "python \"<repo>/tab-naming/turn_notify.py\" UserPromptSubmit"}]}
],
"Stop":         [{"hooks": [{"type": "command", "command": "python \"<repo>/tab-naming/turn_notify.py\" Stop"}]}],
"Notification": [{"hooks": [{"type": "command", "command": "python \"<repo>/tab-naming/turn_notify.py\" Notification"}]}]
```

可选环境变量：`CLAUDE_TURN_SOUND`（提示音 .wav 路径，缺省系统提示音）、`CLAUDE_TAB_META`（交接链文件）。只想响不想挪：在 `turn_notify.py` 旁边建一个空文件 `turn_notify_noplace`。

## 命令行

```
python tabname.py nameall [--force]   # 给所有活着的对话改名(已带 [ ] 的跳过, --force 重起)
python tabname.py refresh             # 按交接链把链上成员的标签重改一遍(VS Code 重载后用)
python tabname.py show                # 看交接链
python tabname.py handoff --sid <旧会话 id> --topic "ABC-12(登录超时)" --term-pid <新终端 shell pid>
```

## 已知限制

- VS Code 没有给终端标签排序的 API；`/place` 用「移到编辑区再移回面板 = 排到末尾」实现，挪动瞬间焦点会闪一下，挪完会把焦点还给原来的活动终端。
- 标签名最长 60 字符，所以只放会话 id 的前 8 位。
- `background_tasks` 的结构 Claude Code 没有文档化：带 `status` 字段就看状态，没有就当作在跑（宁可不响也不误响）。
- 标签名生成每次约 20 秒（`claude -p` 冷启动），全在后台，不阻塞对话。
