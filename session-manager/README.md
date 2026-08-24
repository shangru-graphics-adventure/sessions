# Claude 对话管理器

按最后更新时间列出本机**所有** Claude Code 对话(作者机器上 3292 个, 横跨 15 个项目目录),
列出每个对话里讲过的话题, 加自己的标题和注释, 一键在新终端窗口 resume。

**解决的问题**: CMD 窗口被误关后, 不知道刚才关掉的是哪个对话, 也翻不回去。
打开本工具, 列表最上面那条就是它。

## 启动

双击 `start.cmd`, 或:

```
cd session-manager
python server.py
```

然后开 http://localhost:8720/

Python 3.8+。**唯一的第三方包是 `psutil`** —— "这个对话的进程还在吗"只有它能干净地回答。
不装的话列表 / 搜索 / 预览 / resume 全都正常, 只是实时状态徽章与窗口感知(切过去 / 关闭)
会静默失效。要那部分就 `pip install psutil`。
**Windows only** —— 窗口定位、`explorer /select`、Windows Terminal 那一套都是 Win32 API。

### 配置(可选)

开箱即用: 所有默认值都从你的家目录推出来。要改就把 `config.example.json` 复制成
`config.json`(已在 .gitignore 里), 或用环境变量:

| config.json | 环境变量 | 默认 |
|---|---|---|
| `port` | `SESSIONS_PORT` | 8720 |
| `claude_home` | `CLAUDE_CONFIG_DIR` | `~/.claude` |
| `recap_dir` | `SESSIONS_RECAP_DIR` | `~/session_recaps` |
| `archive_dirs` | `SESSIONS_ARCHIVE_DIRS` | 空 |
| `project_roots` | `SESSIONS_PROJECT_ROOTS` | `~` |
| `auto_trust` | `SESSIONS_AUTO_TRUST` | `true` — resume 前把目标目录标成已信任 |
| `claude_procs` | `SESSIONS_CLAUDE_PROCS` | `claude.exe` — 判断"还开着吗"认哪些进程名 |
| `vscode_bridge_port` | `SESSIONS_VSCODE_BRIDGE_PORT` | `8721` — VS Code 桥的端口 |
| `vscode_bridge_span` | `SESSIONS_VSCODE_BRIDGE_SPAN` | `8` — 往后试几个(每个 IDE 窗口占一个) |

后两个只影响"能不能找到 markdown 版逐字记录", 见文末「可选」一节。

## 数据来自哪里

`~/.claude/projects/<项目slug>/<session_id>.jsonl` —— 每个文件就是一次对话的逐字记录,
文件名去掉后缀 = **session id = `claude --resume` 用的 id**, 文件 mtime = 最后活动时间。
本工具只读这些文件, 不改不删。

自己填的标题/注释/星标存在 `notes.json`(与本文件同目录, UTF-8)。
删掉它只是丢注释, 对话本身不受影响。

## 功能

| 操作 | 说明 |
|---|---|
| 列表 | 按最后更新时间倒序。`[1]` 就是最近活动的那个 |
| 话题行 | **这个对话里都讲了哪几件事** —— 抽出每一条有实质内容的真人发言开头, 编号排列。点一下展开成多行 |
| 标题框 | 留空时显示首条消息作占位;填了就用你的标题。失焦自动存 |
| 注释框 | 自由文本, 失焦自动存。聚焦时自动变高 |
| ★ | 星标, 配合顶部「只看星标」 |
| 搜索框 | 输入即过滤**已加载的**(标题/注释/话题/路径/id) |
| ↻ 重起标题 | 跑一次 `claude -p`(约 15-25 秒)。按钮上走秒, 状态行报"已 Ns · 预计还要 Ms" —— 预估值用前几次的实测滑动平均, 超时后如实说"比平常久"而不是继续装在预估内 |
| 全文搜索 | 按 Enter 或点按钮, 到对话正文里搜(默认扫最近 400 个, 约 2 秒) |
| Resume | 弹一个新 **Windows Terminal** 窗口, 自动 `cd` 回原 cwd 再 `claude --resume <id>` |
| 复制命令 | 不想弹窗时, 复制两行命令自己贴 |
| 看内容 | 展开该对话最后 30 条消息, 确认"是不是这个"再 resume |
| **产物行** | **这个对话交付了什么** —— 写过的文件与发布过的 artifact, 每个后面跟一句"它是怎么来的"。点一下展开; 文件名点开是浏览器内预览, `⌖` 是在资源管理器里定位 |

## 产物("这个对话到底交出了什么")

列表每行第二段是产物行。它回答的是话题行答不了的那个问题: **翻回这个对话, 能拿到什么东西。**

### 收录什么

来源是对话里 `Write` / `Edit` / `MultiEdit` / `NotebookEdit` / `Artifact` 五个工具的调用记录,
按对话内**首次写入**的时间排序(开局那几个信息量最大), 每个对话最多列 40 个, 超出只报数。

排掉的(`ART_SKIP_DIR` / `ART_SKIP_EXT`):

- `.claude\` 下的一切 —— 记忆、配置、skill、对话记录本身, 那是账本不是交付物
- `__pycache__` / `node_modules` / `.git` / `site-packages` —— 机器生成的
- `scratchpad\` 与 `AppData\Local\Temp\` —— 临时件
- `_docs\chat\` —— 会话自动归档
- `*.log` `*.tmp` `*.bak` `*.lock` `*.pyc`

`.npz` `.parquet` `.pkl` 这类大块中间数据**记名但弱化显示**(前面标 `▦`), 它们是过程不是结论,
不该把真正的报告挤下去。

另外单列一行 `▲ 已发布 artifact` —— 正文里出现过的 `claude.ai/code/artifact/<id>` 链接,
点开直接到那个页面。

### "怎么来的"是怎么算出来的

**这个文件第一次被写之前, 你说的最后一句话。** 没有第二套逻辑, 也不调模型。

理由和话题行一样: 你的原话就是最准的解释, 而且零成本、可追溯、不会编。实测拿一份 28 行的想法台账 CSV 验证 —— 它前面那句正是用户当场的原话
"我在看 X 的时候发现...这有没有可能有机会?", 一句话就说清了这份文件的来历。

一句话产出多个文件时, 它们会共用同一句来历 —— 这是事实, 不是 bug。

**坑(实测踩过)**: skill 会往对话里注入长得像真人发言的指令壳(不以 `<` 开头, 所以
`_is_real_user_text` 拦不住), 典型是 `Approach this as the design lead...` 与
`Draw as the engineer...`。不过滤的话, 8 个已发布 artifact 里有 7 个的"来历"会变成同一句
设计腔。这些前缀已进 `JUNK_PREFIX`, **以后再发现新的 skill 壳, 往那里加。**

### 两个入口

| 入口 | 路由 | 行为 |
|---|---|---|
| 文件名 | `GET /file?path=<绝对路径>` | 浏览器内预览。`.md` 走内置渲染(标题/表格/代码块/列表), `.csv` 渲染成表格, `.html` 原样吐出, 其余 `<pre>` 转义。`.npz` 这类与 >8MB 的不读, 只报大小 |
| `⌖` | `GET /reveal?path=<绝对路径>` | `explorer /select,` 在资源管理器里**选中**它(选中不是打开 —— 打开可能触发一堆东西) |

预览逻辑在 `preview.py`(单独一个文件, 不引第三方 markdown 库 —— 这工具的价值是双击就能用)。
产物已被删掉/移走时, 文件名变暗且不给链接, 不会给你一个点开就 404 的东西。

### 实测性能(2026-08-23, 3291 个对话)

| 动作 | 耗时 |
|---|---|
| 全库全扫(缓存全冷, 3291 个对话) | **3.1 秒** |
| `/api/sessions?limit=60` 冷扫 | 634 ms |
| 同上, 缓存命中 | 87 ms |
| 单个 7.2MB 对话解析出 32 个产物 | 27 ms |
| `/file` 预览 md / csv | 22 / 16 ms |

抽产物要多解析 assistant 行, 但仍靠**纯字符串预筛**扛住: 只有含 `"file_path"`+`"tool_use"`
或 artifact 链接的行才付 `json.loads` 的钱。
**改了 `scan_file` 的输出结构就要把 `SCAN_VER` 加 1**, 否则磁盘缓存会喂给你旧结构。

全库当前收录: **701 个本地产物(去重 581 条路径) + 44 个已发布 artifact**。

## 话题是怎么抽出来的

一次对话经常横跨好几件事, 只看首/末两条会漏掉中间。所以全文件扫一遍, 取出**每一条真人发言**
的开头 46 字, 然后:

- 丢掉"继续/好的/ok"这类纯确认词(`FILLER` 集合)
- 丢掉 `Base directory for this skill` / `Caveat:` 这类 skill 与续接注入的壳(`JUNK_PREFIX`)
- 丢掉与已有话题**前 14 字相同**的连续追问(算同一件事)
- 超过 14 条时, **首 7 + 尾 7**, 中间折叠成"…(中间还有 N 条发言)…" ——
  长对话不能只留开头, 那样"最后在干嘛"就看不见了

不用 LLM 概括: 你自己的原话就是最准的目录, 而且零成本、可搜索。想要人话总结就自己填标题栏。

## 终端: 为什么要"模拟手敲"而不是直接挂命令

走过三版, 前两版都错:

| 版本 | 做法 | 结果 |
|---|---|---|
| v1 | `start cmd /k claude --resume <id>` | 黑白的**旧版 conhost 窗口** —— 本机 `HKCU\Console\%%Startup` 的两个 Delegation 键都是全 0 GUID, 默认终端还是老 conhost |
| v2 | `wt -w new ... cmd /k claude --resume <id>` | 窗口对了, 但 **claude 的界面还是单色** |
| v3 | 开一个**纯 cmd 标签页**, 再把命令**敲进去** | 和自己手敲完全等价, 颜色正常 |

v2 为什么不行: 把命令挂在 wt 的 profile 上启动, 和从一个已经跑起来的 cmd 提示符里
敲命令, 对被启动的程序不是一回事。**探针实测过颜色环境本身是好的**
(`isTTY=true / colorDepth=24 / TERM=xterm-256color`, `NO_COLOR`·`FORCE_COLOR`·`CI` 都没设),
所以不是"终端没有颜色能力", 而是启动路径的差别。与其继续查, 不如直接复制那条已知
可用的路径 —— 用户原话: "你直接模拟打开 cmd, 然后输入 claude --resume id"。

现在的 `type` 模式(默认), 三步:

```
1. wt -w 0 new-tab --title "claude <id前8位>" -p "{命令提示符 profile 的 guid}" -d <cwd>
   ^ 注意: 不给任何 commandline —— 出来的就是一个普通 cmd 标签页,
     和你按 Ctrl+Shift+T 开的完全一样
2. 轮询等某个可见窗口的标题里出现 "claude <id前8位>"
3. actions.type_into_window(hwnd, "claude --resume <id>", press_enter=True)
```

几处关键:

- **profile 按 commandline 认, 不按名字**: 本机那个 profile 叫"命令提示符"(中文),
  按名字找不到; 改成"commandline 以 `cmd.exe` 结尾且不含 `activate`"来认,
  顺手排掉 Anaconda / VS 那几个带激活参数的变体。
- **标题匹配 = 双重确认**: wt 是单窗口多标签, 窗口标题跟着**当前活动标签**走。
  标题匹配上了, 就同时证明了"新标签起来了"和"它在最前面" —— 这正是敢往里敲字的前提。
- **等 0.45 秒再敲**: cmd 画完提示符之前敲会掉字。
- **敲不进去就放弃**: `actions.type_into_window` 里有硬约束 —— 切不到目标窗口、
  或焦点两次采样不稳定, 直接返回失败, **绝不对着别的窗口乱敲**。

**实测确认**(2026-08-23, 进程链):

```
WindowsTerminal.exe(147184)
  └─ cmd.exe(259272)                     <- 裸的, 无参数
       └─ claude.exe --resume 1d109d83…  <- 从 cmd 提示符里起来的
```

耗时 1.4 秒。

四种模式, POST `/api/resume` 的 `terminal` 字段:

| 值 | 行为 |
|---|---|
| `type`(默认) | 并进当前 wt 窗口开 cmd 标签页, 敲命令进去 |
| `type-new` | 同上但开独立新窗口(`-w new`)。页面顶部**取消勾选「并入当前终端窗口」**即走这条 |
| `dock` / `new` | 老做法, 命令直接挂 profile 上(不敲键盘)。留着做对照 |
| `conhost` | 兜底; 找不到 wt.exe 时自动回退这条 |

**副作用提醒**: `type` 会抢一下前台焦点(要发键盘事件)。这是这个工具里唯一有副作用的
操作, 所以上面那条"切不到就放弃"的约束不能删。

## 实时状态: 每个对话现在在干嘛

页面上每个还开着的对话会挂一个徽章, 2 秒刷新一次:

| 徽章 | 含义 | 内容 |
|---|---|---|
| ● 正在跑 | 你提交了, Claude 在干活 | **目标**=你那句原话 ｜ **正在** Bash: 重启并验证新鲜度 |
| ● 正在跑? | 同上, 但超过 120 秒没有任何新动作 | 可能卡住了, 值得去看一眼 |
| ● 等你确认 | Claude 在等权限/选择 | 具体等什么 |
| ● 开着·等你 | 窗口开着但空闲 | 上一回合的结论开头 + 耗时 |
| (无徽章) | 进程没了 = 窗口已关 | — |

**每条状态都自报新鲜度**("刚刚 / 12秒前 / 3分前"), 顶部还有一行"状态 刚刚 · 2ms"。
轮询要是断了, 那行会变成"⚠ 状态取不到 · 上次 X 秒前" —— **宁可显示"我不知道多新",
也不能让一个陈旧数字装成实时的**。

### 三个信号源, 各管一段

1. **hook 记账(回合级)** — `hook_state.py` 挂在三个事件上, 每会话写 `state/<sid>.json`:
   - `UserPromptSubmit` → `running` + 你的原话 + **claude.exe 的 pid 和创建时间** + 终端窗口 pid
   - `Notification` → `waiting` + 等什么
   - `Stop` → `done` + 我最后一段话的开头 + 本回合耗时
2. **进程存活(唯一可靠的"窗口还开着吗")** — 拿 pid 去查。窗口被直接 X 掉时
   `SessionEnd` 基本不触发(**实测: 2299 个有 Stop 记录的会话里, 只有 148 个留下过
   SessionEnd, 命中率 6.4%**), 所以绝不能拿 hook 当关闭信号。pid + 创建时间双匹配防复用。
3. **jsonl 尾部(工具级, 零开销)** — "此刻在跑哪个工具"直接读 jsonl 最后几条。
   **不用 PostToolUse hook**: jsonl 本来就是实时落盘的, 每次工具调用都写一条 assistant
   记录, 读尾部 160KB 只要 3ms, 而挂 PostToolUse 等于每次工具调用都启一个 python。

### 装/卸

```
cd session-manager
python install_hooks.py            # 装(幂等, 自动备份 settings.json)
python install_hooks.py --remove   # 卸(只删本工具那几条, 不碰你原有的 hook)
```

装完对**新开的**对话立即生效; 已经开着的窗口要等它下次读配置。

### 两个踩过的坑(都有回归测试兜着)

1. **`psutil.process_iter` 全表遍历要 9022 ms**(本机 47 个 claude 进程), 而页面 2 秒轮询一次
   —— 直接把接口拖成 9.8 秒。改成只查 state 里记着的那几十个 pid: **1.0 ms**, 结果完全一致
   (对比脚本 `bench_pids.py`)。Toolhelp32 快照 52ms 也够快但没必要。
2. **hook 里绝不能用 `sys.stdin.read()`**。它跟随控制台 code page: 我的测试环境是 65001,
   而 claude 派生的 hook 环境是 cp936, 于是 payload 里的中文全变成 `璇诲苟琛ラ綈`
   (实测 `\xe8\xaf\xbb\xe5\xb9\xb6` = "读并" 被当 GBK 读)。必须
   `sys.stdin.buffer.read().decode("utf-8")`。
   **更要命的是第一版测试没抓到** —— 它跑的是 python→python, 而线上是 claude→powershell→python。
   `test_hook.py` 现在补了按线上形状调用 + 强制 `PYTHONIOENCODING=cp936` 两条用例。

### 测试

```
python test_hook.py         # hook 本身: 各个事件 + 编码 + 异常输入, 带耗时
python test_status.py <sid> # 端到端: hook 写账 -> server 合成 -> /api/status, 含 pid 复用防护
python test_windows.py 8799 # 多窗口检测 / 切过去 / 关闭 / 自动 trust (29 项)
python bench_pids.py        # 进程存活检测的方案对比
```

`test_windows.py` **不碰任何真实对话**: 它把 `python.exe` 复制成 `claude.exe` 起两个睡着的
假进程, 再让 server 去关它们 —— 走的是完整的真实代码路径, 但不可能误伤你正开着的窗口。
建议用一个空 state 目录、跑在别的端口上(`SESSIONS_PORT=8799 python server.py`)。

hook 单次耗时实测 196-486 ms(`UserPromptSubmit` 最慢, 因为要爬父进程链), 全部 `async`,
不阻塞你输入。

## 同一个对话开在多个窗口里

`claude --resume <id>` 是可以对同一个 id 开好几遍的 —— 开出来的每个窗口都写**同一份**
jsonl。这不是"多开了几个终端"那么无害: 两边的记录会互相覆盖。

所以 Resume 这个按钮有三种结局, **由后端按此刻实际开着几个窗口来决定**, 前端不自己猜:

| 开着几个 | 按钮显示 | 点下去 |
|---|---|---|
| 0 | `Resume` | 新开一个: 顶部勾着「在 VS Code 里打开」就走桥, 否则开 Windows Terminal |
| 1 | `⇥ 切过去` | 切到那个窗口(不新开)。切不过去时如实说"系统拒绝了前台切换, 请点任务栏" |

**Windows Terminal 里还会切到具体那个标签**: WT 单窗口多标签共用一个 HWND, 光提前台,
活动的还是原来那个标签 —— 六个对话开在同一个窗口里时, 六个"切过去"会全落在同一个标签上。
WT 没有任何 CLI/API 能按标题聚焦标签(`wt focus-tab` 只认 index, 而 index 与对话没有映射),
所以做法是: 提前台 → 发 Ctrl+Tab 轮转 → 直到**窗口标题**(它跟着活动标签走)与记下的标签
标题匹配, 至多轮 16 次(~3 秒)。发键盘前必须确认前台就是目标窗口, 焦点被抢走立刻停手。

**ConPTY 伪窗口是个大坑**(实测踩到, 症状是"点 A 的切过去, 前台变成无关的 B"):
WT 里每个标签的 shell 名下挂着一个类名 `PseudoConsoleWindow` 的 0x0 伪窗口, 而
`IsWindowVisible` 对它返回 **True**。三道修法缺一不可:
- hook 的宿主白名单**不含 cmd/powershell** —— 它们是 shell 不是宿主, 停在它们身上,
  记下的"窗口"就是伪窗口(shell 另有 `shell_pid` 记账);
- 一切枚举窗口的地方按 **类名 + 非零矩形** 过滤伪窗口;
- focus 时**记过的句柄也要验真**, 验不过就从活着的 claude 进程沿父链重找,
  分流(WT 标签轮转 / VS Code 桥)按**真窗口的实际主人**定, 不信旧账里的 term 字段。

两条边界, 都是实测踩出来的:
- **重名标签分不开**(把同一对话开两份就会重名): 标题是唯一的定位手段, 轮转会停在先遇到
  的那个, 这时返回里会明说"可能停在了别的同名对话上", 不装作精确。
- 判断"轮满一圈"**不能**用"标题重复出现" —— 重名标签会让它提前误判回到起点, 连明明存在
  的独名标签都找不到(实测踩到)。老老实实轮满次数上限。
| ≥2 | `⚠ 开着 N 个` | 拒绝, 并在状态条下面把它们一个个列出来 |

多窗口时状态条会变成告警色, 下面一行是这样的:

```
⚠ 同一个对话开在 2 个窗口里 — 它们写同一份记录会互相覆盖, 请关到只剩一个:
   [◐ 看盘工具重构  pid 11240  ⇥ 切过去  ✕ 关闭] [◑ 看盘工具重构  pid 31728  ⇥ 切过去  ✕ 关闭]
```

### ✕ 关闭做了什么(以及故意没做什么)

**动手前先验明正身**, 这是硬约束: 进程名必须在 `claude_procs` 里, 创建时间必须和记账时
对得上(±2s)。pid 会被系统回收再分配 —— 少了这一步, 一个早退出的对话的旧 pid 可能已经
属于别人的进程, "关窗口"就变成了随机杀进程。三道闸都有回归测试。

- 关的是**那个 claude 进程连同它派生的子进程**(一棵 pid 树), 和你直接关掉终端窗口的效果一致。
- 该终端窗口里**没有别的已知对话**时, 进程收干净后再给窗口发一个 `WM_CLOSE`, 空标签页也一并收掉;
  **有别的对话就绝不关窗** —— Windows Terminal 是单窗口多标签, 关窗会连带关掉别人的对话。
  这种情况接口会明说"这个终端窗口里还开着 N 个别的对话, 所以只结束了这一个"。
- **永远不用窗口标题当 `taskkill` 的过滤条件。** 那个过滤器在 Win10+ 上会静默失效并杀光同名
  进程 —— 本文档后面记着这笔学费。

### 它靠什么知道"开着几个窗口"

靠 hook。`state/<sid>.json` 里存的是一个 `procs` 数组而不是单个 pid:

```json
"procs": [{"pid": 11240, "pid_ctime": 1787511544.5, "term_pid": 13108,
           "term_name": "WindowsTerminal.exe", "hwnd": 65946, "win_title": "◐ 看盘工具重构"}]
```

多个窗口共用一个 session_id, 写的是同一个 state 文件, 所以**必须是数组** —— 只留一个 pid 的话
后开的会把先开的顶掉, 页面上永远只看得见最后活动的那个, 而另外几个恰恰是要提醒你去关掉的。

`SessionStart` hook 也是为这个装的: 它是"这个对话又被开到一个新窗口里了"的**最早**信号。
没有它, 一个刚 resume 出来还没说话的窗口不会被记上, 而"你已经开着一个了"的提醒恰恰要在你
重复 resume 之前给出来。

**没装 hook 的话这一整节都不生效** —— 页面会退回"每次点 Resume 都新开一个"的老行为。

### 如果对话是在 VS Code 的集成终端里跑的

**以下全部是 2026-08-24 在本机实测的**(VS Code 1.134.0, 两个终端标签各跑一个对话)。
实测到的进程链是:

```
claude.exe -> powershell.exe -> Code.exe(渲染进程, 无窗口) -> Code.exe(主进程, 有窗口) -> explorer.exe
             ^ 每个终端标签一个                ^ hook 认的"终端"就是它
```

两个要点直接决定了下面这张表: **每个终端标签有自己独立的 shell 进程**(所以分得清哪个对话
在哪个标签), 但**标签共用一个窗口句柄**(所以切不到具体标签)。

| 功能 | 在 VS Code 里 | 为什么 |
|---|---|---|
| 实时状态徽章 | **照常** | 只看 claude 进程的 pid, 与终端是谁无关 |
| 多窗口告警 | **照常** | 同上; 同一 IDE 窗口里的多个标签共用 term_pid, 兄弟检测也因此有效 |
| `✕ 关闭` 结束对话 | **照常**(实测: 进程与子进程都收干净) | 杀的是那棵 pid 树 |
| `✕ 关闭` 顺手关窗 | **不会做** | 见下 |
| `⇥ 切过去` | 默认切到 IDE 窗口; **装了 [`vscode-bridge/`](../vscode-bridge/) 就能点到具体标签页** | 标签没有 HWND, 只有扩展 API 够得着 |
| 关掉标签页本身 | **`✕ 连标签页` 按钮** | 走扩展的 `dispose()`, 没装则杀掉标签自己的 shell |
| `Resume` 新开 | **默认就开在 VS Code 里**(装了桥的话), 顶部可切回终端 | 走扩展的 `createTerminal` + `sendText` |

hook 认的终端(那个渲染进程)**自己没有窗口**, 所以 `capture_window` 找不到句柄时会
**沿父链再往上找最多 4 层**, 于是拿到 IDE 主窗口; `win_owner` 字段记下这个句柄实际属于谁,
页面据此把话说清楚("只能到窗口, 到不了标签页")。没有这一步, VS Code 里的对话永远是
"没记到窗口句柄"。

**关窗那条是一个必须挡住的坑。** hook 认终端宿主的名单里有 `Code.exe` 和 `explorer.exe`
(它们确实可能是 claude 的祖先进程)。少了防护, "关闭这个对话"在只开着一个对话的
VS Code 里就会给**编辑器主窗口**发 WM_CLOSE —— 关掉你整个 IDE(还可能带着未保存的改动);
`explorer.exe` 更狠, 关掉 = 桌面和任务栏一起没。

所以关窗走**白名单**(`config.CLOSABLE_TERMS`), 只认纯终端:
`WindowsTerminal.exe` / `conhost.exe` / `OpenConsole.exe` / `cmd.exe` / `powershell.exe` / `pwsh.exe`。
**认不出来的宿主一律当作不能关** —— 这类判断只能白名单, 黑名单漏一个就是一次事故。
这时接口会明说"宿主是 Code.exe, 不是纯终端 —— 只结束了对话, 窗口留着", 页面上那个
`✕ 关闭` 的悬停提示也会提前告诉你窗口不会关。有回归测试盯着这两个宿主名。

页面上每个窗口都标着宿主进程名(`Code.exe · pid 31728`), 一眼能看出这个对话跑在哪。

### `✕ 连标签页` —— VS Code 里怎么把标签页也收掉

实测: **只杀 claude, 标签页会留下一个 PowerShell 提示符**(和 Windows Terminal 里留下 cmd
提示符是一回事)。**杀掉那个标签自己的 shell, VS Code 就会把标签页收走**(实测确认标签页
消失了)。一个标签一个 shell, 所以语义是干净的。

所以 VS Code 宿主的窗口会多出一个 `✕ 连标签页` 按钮(纯终端宿主没有这个按钮 —— 它们
直接关窗口就行)。安全前提照旧要验: **父进程必须是已知的 shell 名**(`actions.SHELLS`),
不是就只关对话并在返回里说明, 绝不对着一个认不出来的父进程开枪。两条都有回归测试。

顺带一个官方文档里的边界(**[外部先验]**, 未实测):
`terminal.integrated.showExitAlert` 控制"进程以非零退出码结束"时那条提示要不要弹 ——
我们是 terminate 掉 shell 的, 退出码非零, 所以你可能会看到那条通知。它只是通知,
不影响标签页被收掉。

### 切到具体那个标签页: 装 [`vscode-bridge/`](../vscode-bridge/)

Windows 这一层给不了 —— 标签不是窗口, 没有自己的 HWND; VS Code 也**没有提供任何 CLI
参数或 URI handler** 能聚焦某个终端(查过官方文档)。唯一干净的路是扩展 API, 所以仓库里
带了一个巴掌大的扩展(两个文件, 零依赖), 装法见它自己的 README。

闭环靠的是一个巧合般的对齐: 扩展 API 的 `Terminal.processId` **正好就是本工具一直在追踪
的那个 shell pid**(claude 进程的父)。实测对上了:

```
桥:      {"terminals":[{"pid":45848,"name":"powershell","active":true}]}
hook:    shell pid=45848 powershell.exe  (claude 的父进程)
```

装了之后:

- `⇥ 切过去` 直接把**那个终端标签页**显示出来(`Terminal.show()`), 再自己把 IDE 窗口提到前台
  —— **两步都要做**: `Terminal.show()` 只在 VS Code 内部切标签, 不会把 OS 窗口拉到前面,
  那一步得靠 `SetForegroundWindow`。少了它, 标签是切了, 但你还盯着原来那个窗口, 看起来像没反应。
- `Resume` 直接在 VS Code 里新开一个终端标签并把 `claude --resume` 敲进去
  (`createTerminal` + `sendText`, 官方 API, 不抢焦点也不会敲错窗口)
- `✕ 连标签页` 改走 `Terminal.dispose()` —— VS Code 自己的关法, 标签干干净净消失,
  不会留下"terminal process terminated with exit code"那条提示(杀 shell 的兜底做法会)

`shell_pid` 与 `hwnd` 都有**现场兜底**: hook 没记(老的 state 文件里就没有)时,
server 会当场查一次 claude 的父进程 / 沿父链找窗口。没有这个兜底, 升级之后每个还开着
的对话都得先说一句话让 hook 补记才能用 —— 太别扭了。查一次就缓存, 一个进程的父不会
中途换人。

**没装完全不影响别的功能**: `actions.bridge()` 连不上就返回 None, 一切退回原来的行为。
超时 1.2 秒且总额有上限 —— 这是"有更好就用"的增强, 不能因为它让页面卡住。

多个 VS Code 窗口时, 每个窗口跑一份桥、各占端口段里的一个, 管理器挨个端口问过去;
问到别的窗口只会得到 `ok:false`("这个窗口里没有这个终端"), **没有副作用**, 所以顺序试
是安全的。相关配置: `vscode_bridge_port`(默认 8721) / `vscode_bridge_span`(默认 8)。

### 另一个官方行为要知道(**[外部先验]**, 未实测)

`terminal.integrated.enablePersistentSessions`: **重载窗口**时 VS Code 会重连原来的进程
(pid 不变, 我们的追踪继续有效); **重启 VS Code** 时它会用原环境**重新启动** shell ——
那是个新 pid, 而且 claude 不会被重新拉起, 所以本工具会如实显示"已关闭"。

### 在哪儿打开: 顶部那个勾

「在 VS Code 里打开」**默认勾上**。VS Code 那边用不了(没装扩展 / VS Code 没开 / 端口段被占满)
时**自动退回终端**, 并在状态行如实说明退回的原因 —— 不会假装成功, 也不会因此失败。
不勾就走原来那条路(Windows Terminal), 那时旁边的「并入当前终端窗口」才有意义, 所以勾着
VS Code 时它会被灰掉。

## Resume 前自动信任目录

新窗口里 `claude` 起来时, 如果这个目录没被信任过, 第一屏是
"Do you trust the files in this folder?", 敲进去的 `claude --resume` 会卡在那儿等你按 y。

所以 resume 之前先把目标目录标成已信任。实测(2026-08-24)它存在 `~/.claude.json` 里:

```json
{"projects": {"C:/Users/me/work": {"hasTrustDialogAccepted": true}}}
```

两个实测出来的细节: key 用**正斜杠**且没有结尾斜杠(写成反斜杠认不出来, 对话框照样弹);
这个文件是 Claude Code 自己在维护的, 所以**只翻这一个布尔字段, 其余内容原样回写**, 并且
先写 `.tmp` 再 `os.replace` 原子替换。坏 json / 文件不存在都只是返回 False, 绝不因此让
resume 失败 —— 大不了你自己点一下那个对话框。

只在你**按下 Resume 的那一刻**、针对**那一个目录**做; 切到已有窗口那条路不碰它。
不想要就 `config.json` 里 `"auto_trust": false`(或 `SESSIONS_AUTO_TRUST=0`)。

## 别让子进程弹黑框

`↻ 重起标题` 会跑一次 `claude -p`。**`claude.exe` 是控制台程序**: 当 server 本身没有控制台
(用 `pythonw` 起的常态)时, 它会给自己新建一个控制台窗口 —— 于是每总结一个标题就闪一个黑框。

用 `python.exe` 起时子进程继承了控制台, 看不出问题, **这个坑只在换成 `pythonw` 之后才现形**。
所有 `claude -p` 的调用都带上了 `creationflags=CREATE_NO_WINDOW`(`actions.py` / `titler/gen.py`
/ `titler/run_titles.py` 三处)。

验证方式不是"看一眼屏幕", 而是: 调 `/api/retitle` 的同时每 150ms 枚举一次所有可见窗口,
比对前后差集 —— 实测新增 0 个窗口。

## 换一台机器: 编码

Python 的 `sys.stdout` 编码跟随控制台 code page。开发机 `chcp` 是 65001 时一切正常, 换一台
默认是 1252(西欧)或 936(GBK)的机器, 同一份代码 print 中文就会 `UnicodeEncodeError`, 或者
悄悄打出一堆问号 —— 后者更坏, 因为它不报错。

`utf8_console.enable()` 在每个入口脚本开头做两件互不依赖的事: 把 stdout/stderr 换成
UTF-8(`errors="replace"`, 宁可显示成 `?` 也绝不让一条日志把程序打崩), 再顺手把控制台输出
code page 设成 65001。没有控制台(pythonw / 重定向到文件)时静默跳过。

`start.cmd` 另外设了 `PYTHONUTF8=1`(PEP 540) —— 那条更彻底, 连子进程和默认文件编码一起管。

(hook 读 stdin 那一侧的编码坑是另一回事, 见下面"两个踩过的坑"。)

## 两个必须知道的坑

1. **全文搜索只搜真人发言与 Claude 回答**, 不搜 system 提示。
   这是刻意的: 全局 `CLAUDE.md` 会被注入进**每一个**对话, 直接 grep 整个 jsonl 时
   搜一个只在你 `CLAUDE.md` 里出现过的词, 会在 400 个对话里命中 99 个, 全是同一段系统提示。
   修正后同一关键词命中 10 个。
2. **别 resume 还带着徽章的对话** —— 徽章在就说明进程还活着, resume 会冲突。
   (早期版本靠时间猜"大概还开着", 现在是 pid 实证, 不用猜了。)

## 已实测的性能(2026-08-22, 3292 个对话)

全量扫描版(为了抽话题, 每个文件要整个读一遍):

| 操作 | 耗时 |
|---|---|
| 列最近 150(全冷启动) | 后端 479-508ms |
| 列最近 150(缓存命中) | 后端 48ms |
| 列最近 1000(冷) | 后端 832ms |
| 全文搜索(扫 400 个) | 后端 1.6-2.0s |
| 看内容(单个对话) | 3ms |

解析结果按 `(mtime, size)` 缓存, 内存 + 落盘 `cache.json`(150 个约 126KB), 重启 server
也不用重算; 文件变了才重扫。cache.json 删掉只是下次慢半秒。

**端到端验证过**(2026-08-22): 点 Resume -> wt 新窗口 -> cmd -> `claude --resume <真实id>`,
按命令行匹配到了对应的 claude 进程。`-d <cwd>` 生效也单独验证过(让 wt 跑 `cd` 写文件回读)。

## 踩过的坑

### `taskkill /FI "WINDOWTITLE eq ..."` 会杀光你所有的 cmd(2026-08-23 实发)

`test_inject.py` 的清理步骤里曾写过一句"兜底":

```
taskkill /F /FI "WINDOWTITLE eq INJECT_TEST_WINDOW*"
```

Win10+ 的 conhost 架构下, `taskkill` 拿不到控制台进程的窗口标题(它看到的是 `N/A`),
过滤器于是**静默失效** —— 配上 `/F`, 结果是把机器上**全部 `cmd.exe` 一起强杀**。
当场关掉了 34 个正在跑的 claude 对话窗口(jsonl 里能看到 34 个 SessionEnd 在同一秒落盘)。

**规矩: 清理只按自己的 pid 树杀**(`psutil.Process(pid).children(recursive=True)` + `proc.kill()`),
**永远不要用窗口标题当 taskkill 的过滤条件。**

讽刺的是这个工具本身就是为"CMD 窗口被误关"写的 —— 它当天把自己的用例造了出来,
34 个对话一个没丢, 从列表里逐个 resume 回来了。

## 可选: 接上你自己的存档与 recap

这两条都是**可选**的, 不配也不影响主功能, 配了会在每行多出两个入口:

- **markdown 逐字存档** —— 有些人用 Stop/SessionEnd hook 把对话另存成 markdown。
  把导出根目录填进 `config.json` 的 `archive_dirs`(会去找 `<root>/*/*/<sid>.md`)
  或 `project_roots`(会去找 `<root>/*/_docs/chat/*/<sid>.md`), 本工具就能反查到它。
- **recap** —— 若你有一个 recap 目录, 里面用 `SESSIONS.md` 登记「一句话 summary ↔ session id」
  (或在 recap 正文头部写一行 `> session: <id>`), 填进 `recap_dir` 即可反查。
  那张表是**人工挑选过的重要对话**, 本工具是**全量**, 两者互补。

两个都不填, 页面只是少两个链接, 其余功能不受影响。
