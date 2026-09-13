@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Agent 汉化 - 取消界面翻译
echo 请选择目标软件。操作前请保存工作，所选软件将关闭并重新启动。
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0AgentZh.ps1" -Cmd revert -Kill -Restart
echo.
pause
