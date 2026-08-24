@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
echo Ticket Desk ... http://localhost:8730/
start "" http://localhost:8730/
python server.py
