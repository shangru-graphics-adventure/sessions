@echo off
chcp 65001 >nul 2>&1
rem UTF-8 模式: 连 Python 的默认文件编码与子进程一起管, 换台
rem code page 不是 65001 的机器也不会输出乱码 (PEP 540)
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
echo Ticket Desk ... http://localhost:8730/
start "" http://localhost:8730/
python server.py
