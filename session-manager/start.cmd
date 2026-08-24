@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
echo Claude 对话管理器启动中 ... http://localhost:8720/
start "" http://localhost:8720/
python server.py
