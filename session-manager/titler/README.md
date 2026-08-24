# titler — 对话自动标题生成

给 `~/.claude/projects/**/*.jsonl` 里每个 Claude Code 会话生成一个中文标题，
供 [对话管理器](../README.md) (localhost:8720) 显示与搜索。

## 1. 这是什么 / 溯源链

```
~/.claude/projects/**/*.jsonl        (源数据: 全部会话记录, 约 945MB / 3295 个)
        │  extract.py                 只取 type=user 的真实发言
        ▼
corpus.jsonl                          语料 (1.3MB)
        │  run_titles.py              claude -p --model haiku, 32 路并发
        ▼
titles.jsonl                          标题
        │  server.py: load_auto_titles()
        ▼
/api/sessions 的 auto_title 字段  →  index.html 标题栏 placeholder + 默认过滤框搜索
```

**为什么要这东西**: 管理器原先的"标题"其实是 `first`(首条用户发言前若干字)在冒充，
而 `topics` 每条截 46 字、超 14 条还把中间折叠成"…(中间还有 N 条发言)…"。
后果实测于 2026-08-23: 会话 `2b44a390` 前半在聊渲染引擎、后半是一场关于过拟合额度的
重要讨论，"过拟合"三个字**在列表可见文本里一次都没出现**，摘要级搜索必然零命中。
auto_title 就是补这个洞的。

## 2. 列字典

### `corpus.jsonl` (每行一个会话)

| 列 | 含义 |
|---|---|
| `sid` | 会话 id = jsonl 文件名去掉后缀 |
| `proj` | 项目目录名 (`C--Users-alice-stock-watch` 这种) |
| `mtime` | 源 jsonl 的 mtime, unix 秒 |
| `n` | 收进语料的发言条数 (**不是**会话总发言数, 见下面的截断口径) |
| `msgs` | 用户发言数组, 每条 **≤200 字符**; 超 40 条则首 20 + `"…(中间省略 N 条)…"` + 尾 20 |

### `titles.jsonl` (每行一个会话, **追加写**)

| 列 | 含义 |
|---|---|
| `sid` | 同上 |
| `title` | 生成的标题, 已清洗(取首行/去引号/截 60 字) |
| `ms` | 该条耗时毫秒 (中位 ~21000, 全是 CLI 冷启动) |
| `err` | 仅失败时存在, stderr 前 200 字 |

**同一 sid 可能出现多行**(重跑某条时直接追加)，读取时**后写的赢** —— `load_auto_titles()`
就是这么做的。

## 3. 质量标签 / 已知坑

- **全量实测 (2026-08-23, 3294 个会话)**: 报错 0, 标题长度中位 22 字, 单条耗时中位 20.0s。
  **空标题 433 条(13%) —— 见下面那节, 是并发过高导致的 CLI 静默失败, 已修并补跑。**
### ⚠⚠ 最大的坑: 批量 `claude -p` 会把 user-level hooks 跑满 N 次

**`--settings <空json>` 屏蔽不掉 user-level hooks。** 2026-08-23 实测(约 1050 次调用后):

| 来源 | 产生的垃圾 | 实测量 |
|---|---|---|
| `~/.claude/settings.json` Stop hook → `ding.ps1` | **每条响一次铃** | ~1050 次 |
| 同上 → markdown 存档 hook | 存档目录下的 `titler/<日期>/` 垃圾 transcript | 1895 文件 / 30MB |
| 同上 → `hook_state.py` | `claude_sessions\state\*.json` | 1036 个 |
| CLI 本身 | `~/.claude/projects/<titler 目录的 slug>/` 会话文件 | 1054 个 |

已做的缓解: `server.py` 与 `extract.py` 各有 `IGNORE_PROJ` 排除那个 projects 目录,
所以垃圾会话不会污染管理器列表与语料。但**响铃与归档拦不住** —— 那是 hook 侧的事。

**`CLAUDE_CONFIG_DIR` 指向空目录能真隔离, 但会丢认证** —— 实测副作用三项计数器全部
0 增长, 同时报 `Not logged in · Please run /login`; 把 `.claude.json` 里的
`oauthAccount`/`userID` 搬过去也没用, token 在系统 keychain 不在配置文件里。
要走这条路, 得先在隔离配置里手动 `/login` 一次。

**跑完必须清理三处**(见 §4)。**任何新的批量 `claude -p` 脚本都要先想清楚这一整节。**

### ⚠ 并发过高会让 CLI 静默失败(空标题), 且不报错

2026-08-23 实测: 并发 **32** 跑完 3294 条后, **433 条(13%)标题是空的**。特征:

- `returncode == 0`, `stderr` 空 —— **完全不报错**
- 耗时 **41-241ms**(正常是 ~20s), 即进程根本没干活就退了
- 同一条**单独重跑立刻正常**(14.9s, 标题质量正常)

所以这是 CLI 侧的静默失败, 不是模型返回空, 也不是语料问题。已在 `run_titles.py` 里修:

1. `one()` 里空结果**就地重试 3 次**(退避 2s/5s), 重试次数记进 `tries` 字段。
2. `have` 集合**只把非空标题算已完成** —— 否则空记录会让断点续跑永久跳过它们。
3. 补跑时把并发降到 **12**。

**教训(与用户台账的"幸存者复检铁律"同源)**: 早期抽检 646 条时空标题是 **0**, 我据此
报告了"0 报错 0 空标题"; 全量跑完才发现 13% 是空的 —— **抽检结论在样本扩大后失效,
而我一直在引用那个旧数字**。任何"已验证"的质量结论, 口径或样本量一变就必须重测。

### ⚠ 更根本的: 这个任务本不该用 `claude -p`

判据: 这个小任务需要工具 / 文件系统 / 多轮吗? 不需要 → 直接调 Messages API。
titler 是标准的"不需要": 读 20 句话吐一个 20 字标题。但每条却要付 Node 冷启动 +
加载全局 CLAUDE.md + MEMORY.md + 全部 skill 描述 + 全部工具定义, 单条挂钟 ~18s,
**与 token 量无关**(实测: 覆盖 system prompt、禁 MCP、禁 settings 都只省 1-2 秒)。

之所以仍走了 `claude -p`: 作者机器上 **没有 `ANTHROPIC_API_KEY`, 也没有 `ant` CLI**,
`Anthropic()` 零参构造直接失败。**哪天配好 key, 应把 `one()` 里的 `subprocess.run`
换成一次 `messages.create`** —— 断点续跑 / have 集合 / append jsonl / err 字段全部
原样可用, 只换那一处。预计单条 1-2 秒, 且以上所有 hook 副作用一次性消失。

- **标题是"用户说了什么"的摘要, 不是"结论是什么"的摘要**。语料里只有用户发言,
  没有 AI 回答 —— 这是刻意的(便宜、且用户的话最能标识"这个对话是干嘛的"),
  但意味着 AI 单方面产出的重要结论**不会**进标题。要找结论仍需全文搜索。
- **探针会话的标题就是 "OK"** (语料是 `Reply exactly OK`)。不是 bug, 是如实反映。
- **`n=1` 且极短的会话占比不小** —— 语料总字符中位数只有 200, 说明一半以上的会话
  只有一条用户发言。这类标题信息量天然有限。
- **标题不覆盖手填标题**。`notes.json` 的 `title` 永远优先; auto_title 只做 placeholder。
- 生成用的是 **haiku**(用户 2026-08-23 指定)。换模型 = 换标题风格, 属于口径变更,
  换了要整体重跑, 不要新旧混存。

## 4. 标准用法

读标题(与 `server.py` 同口径, 后写的赢):

```python
import io, json, os
path = os.path.expanduser("~/claude_sessions/titler/titles.jsonl")
titles = {}
for line in io.open(path, encoding="utf-8", errors="replace"):
    try:
        d = json.loads(line)
    except Exception:
        continue
    if d.get("sid") and (d.get("title") or "").strip():
        titles[d["sid"]] = d["title"].strip()
print(len(titles), titles.get("<某个 session id>"))
```

增量补跑(只处理还没有标题的会话, 已完成的自动跳过):

```
cd session-manager/titler
python extract.py
python run_titles.py 32
```

重跑某几条: 先从 `titles.jsonl` 里删掉对应行(或不删, 直接追加新行覆盖), 再跑上面。

跑完清理三处垃圾(全部是本目录的批处理产生的, 不含真数据):

```
rem 1) CLI 为本目录建的会话文件(路径里带 titler 的那个 projects 子目录)
rmdir /s /q "%%USERPROFILE%%\.claude\projects\<本目录的 slug>"
rem 2) 若你有 markdown 存档 hook, 它也会给这批调用留一堆 transcript, 一并删掉
```

第三处是 `session-manager/state/` 里 cwd 含 titler 的 json —— 那个目录
是 `hook_state.py` 在管的活状态, **别整目录删**, 只删匹配的:

```
cd session-manager
python -c "import glob,io,os; [os.remove(f) for f in glob.glob('state/*.json') if 'titler' in io.open(f,encoding='utf-8',errors='replace').read()]"
```

## 5. 成本

走 `claude -p` 的订阅额度, 无额外计费。3295 条 32 路并发约 **40 分钟**(1.4 条/秒),
瓶颈全在 CLI 冷启动(单条 ~18s, 与 token 量无关)。

如果哪天有了 `ANTHROPIC_API_KEY`, 走 Batch API 更快更省:
语料实测 ~1.0M input token, Haiku 4.5 按 $1/MTok in + $5/MTok out ≈ **$1.4**, Batch 再减半。
