---
name: ticket
description: 为当前对话在本机 Ticket 台(localhost:8730)开一张 ticket 并开始计时,也能建子 ticket、暂停、标记完成、看清单。Trigger when user says "/ticket"、"给这个对话建个 ticket"、"开一张 ticket 标题 xxx"、"这活开个 ticket"、"ticket 暂停/完成"、"看看我的 ticket"。
---

# ticket — 给当前对话开 ticket 并计时

Ticket 台的代码与口径说明见仓库里的 `ticket-desk/README.md`。本 skill 只是它的命令行腿：
**用户说"给这个对话建个 ticket，标题 xxx"，立刻建、立刻开始计时，不要问确认。**

服务没在跑会被脚本静默拉起（pythonw，不弹窗；起之前先探端口，不会双绑）。

## 一条命令搞定

```powershell
python ~/.claude/skills/ticket/ticket.py add "标题" --note "<当前对话上下文>"
```

- **默认建完就开始计时**（用户开这张 ticket 通常是因为现在就在干它）。
  用户明确说"先登记着 / 还没开始"→ 加 `--todo`。
- `--note` 里写**当前会话的上下文**，让 ticket 在页面上能认出是哪次对话：cwd + 一句话在干嘛，
  例如 `--note "D:\workpi — 修登录超时"`。页面上悬停标题即可看到。
- **计划时间**（可选，纯参考不影响计时）：用户说"明天九点开始/下午三点前做完"就带上
  `--plan-start "明天 09:00"` / `--plan-end "15:00"`。格式认 `"8/24 09:00"`、`"09:00"`（今天）、
  `今天/明天/后天 HH:MM`。用户没提就别加——UI 上不勾选就等于没设。
- 标题用户给什么就用什么，别自己润色。用户没给标题时，用当前任务的一句话概括，并在回复里说明你填了什么。

### 子 ticket

```powershell
python ...\ticket.py add "子任务标题" --parent "父ticket标题关键词"
```

`--parent` 接标题子串或 id；匹配到多条会列出来要求更具体（这时把候选报给用户让 ta 选，别乱猜）。
**拆子 ticket 的时候记住台子的口径**：多个子 ticket 同时在跑，父只计一份（区间并集），
所以同时开几张子 ticket 是安全的，不会把父的时长灌水。

### 其他动作

```powershell
python ...\ticket.py start|pause|done|undone|archive "标题关键词"
python ...\ticket.py list [--all]      # 默认只列未完成；--all 连完成与归档一起
```

`done` 会自动收掉正在跑的计时区间。`archive` 只对已完成的开放，且连整棵子树一起归档。

## 回复用户时

一行报清楚就行：建了什么、是否已开始计时、下达时刻，附 http://localhost:8730/ 。
做完这件事后如果用户说"完事了/收工"，顺手 `done` 掉对应 ticket 再汇报。

## 别做的事

- 别为了"整洁"去删或归档用户的 ticket——归档/删除只在用户明确要求时做。
- 别自己发明 ticket。只有用户要求时才建。
- 服务已在跑就直接用，**不要 kill 后重起**（会丢别的窗口正在看的页面状态）。
