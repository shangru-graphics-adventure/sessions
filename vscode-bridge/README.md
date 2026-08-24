# Claude Sessions Bridge —— 让对话管理器点得到具体那个终端标签页

一个巴掌大的 VS Code 扩展(两个文件, 零依赖), 只做一件事: 收到一个 **shell 进程 pid**,
把对应的那个终端标签页显示出来, 或者关掉它。

## 为什么需要它

VS Code 的终端标签**不是窗口**, 没有自己的 HWND —— 一个 IDE 窗口里所有标签共用一个句柄。
所以 Windows 那一层最多只能"把 VS Code 窗口切到前台", 到不了具体是哪个标签。
而 VS Code **没有提供任何 CLI 参数或 URI handler** 能聚焦某个终端(查过官方文档)。

扩展 API 里却是齐的:

| API | 作用 |
|---|---|
| `window.terminals` | 枚举当前打开的终端 |
| `Terminal.processId` | 该终端 **shell 进程的 OS pid** |
| `Terminal.show(preserveFocus?)` | 把这个终端显示出来 |
| `Terminal.dispose()` | 关掉这个终端标签页 |

而 `Terminal.processId` **正好就是对话管理器已经在追踪的那个 pid**(claude 进程的父 shell)。
实测对上了:

```
桥:      {"terminals":[{"pid":45848,"name":"powershell","active":true}]}
管理器:  shell pid=45848 powershell.exe  (claude 的父进程)
```

于是闭环: 管理器 → pid → 桥 → `Terminal.show()`。

## 装

把这个目录拷到 VS Code 的扩展目录, 然后重载窗口:

```
xcopy /E /I vscode-bridge "%USERPROFILE%\.vscode\extensions\local.claude-sessions-bridge-0.1.0"
```

重载: `Ctrl+Shift+P` → **Developer: Reload Window**。
验证: `Ctrl+Shift+P` → **Claude Sessions Bridge: 看看现在监听在哪、有哪些终端**。

不装也完全没关系 —— 管理器会自动退回"只能切到 IDE 窗口"的行为, 别的功能一个不受影响。

## 接口

只绑 `127.0.0.1`。**带 `Origin` 头的请求一律拒绝** —— 否则任何网页都能悄悄关掉你的终端。

| 路由 | 作用 |
|---|---|
| `GET /ping` | 我在, 以及 VS Code 版本 |
| `GET /terminals` | `[{pid, name, active}]` |
| `POST /show {pid, preserveFocus?}` | 显示这个终端 |
| `POST /close {pid}` | 关掉这个标签页 |

`/close` 用 `Terminal.dispose()` —— 这是 VS Code 自己的关法, 标签干干净净地消失。
(管理器的兜底做法是杀掉那个 shell, 效果一样, 但退出码非零, VS Code 会多弹一条
"terminal process terminated with exit code"。)

## 多个 VS Code 窗口

**每个窗口有自己的扩展宿主, 都会跑一份这个桥**, 各占端口段(默认 `8721` 起, 往后 8 个)
里的第一个空位。管理器挨个端口问过去 —— 问到别的窗口只会得到
`{"ok":false,"why":"这个窗口里没有 pid 为 … 的终端"}`, **没有任何副作用**, 所以顺序试是安全的。

设置项: `claudeSessionsBridge.port` / `.portSpan` / `.enabled`。

## 它不做什么

不读你的代码、不读终端内容、不执行任何命令、不联网。整个 `extension.js` 200 行不到,
建议自己扫一眼再装。
