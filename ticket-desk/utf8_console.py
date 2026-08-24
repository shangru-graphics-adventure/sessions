# -*- coding: utf-8 -*-
"""让输出在**任何** Windows 控制台上都是 UTF-8。

为什么需要这个: Python 的 `sys.stdout` 编码跟随控制台 code page。开发机的
`chcp` 是 65001(UTF-8) 时一切正常, 换一台默认 code page 是 1252(西欧) 或
936(中文 GBK) 的机器, 同一份代码 print 中文就会:

    UnicodeEncodeError: 'charmap' codec can't encode characters ...

或者悄悄打出一堆问号/乱码 —— 后者更坏, 因为它不报错。

`enable()` 做两件独立的事, 任何一件失败都不影响另一件:
  1. 把 stdout/stderr 的编码器换成 UTF-8(`errors="replace"`, 宁可显示成 `?`
     也绝不让一条日志把程序打崩);
  2. 顺手把控制台的输出 code page 设成 65001, 让终端那一侧也按 UTF-8 解读。

调用它是幂等的, 没有控制台(pythonw / 重定向到文件 / 服务方式启动)时静默跳过。

另一条更彻底的路是环境变量 `PYTHONUTF8=1`(PEP 540 UTF-8 模式) —— 它连子进程
和文件默认编码一起管。`start.cmd` 里已经设了; 这个模块管的是"别人直接
`python server.py`"的那种情况。

(与 `session-manager/utf8_console.py` 是同一份 —— 两个工具各自独立可跑, 不互相 import。)
"""
import sys


def enable():
    for stream in ("stdout", "stderr"):
        s = getattr(sys, stream, None)
        rc = getattr(s, "reconfigure", None)          # Python 3.7+
        if rc is None:
            continue
        try:
            rc(encoding="utf-8", errors="replace")
        except Exception:
            pass                                      # 已重定向/已关闭, 不是错误

    if sys.platform == "win32":
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            k32.SetConsoleOutputCP(65001)
            k32.SetConsoleCP(65001)
        except Exception:
            pass                                      # 没有控制台就算了
