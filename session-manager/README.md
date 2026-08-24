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
| 0 | `Resume` | 照旧: 开新标签页, 把 `claude --resume` 敲进去 |
| 1 | `⇥ 切过去` | 切到那个窗口(不新开)。切不过去时如实说"系统拒绝了前台切换, 请点任务栏" |
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
