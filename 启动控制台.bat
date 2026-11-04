@echo off
chcp 65001 >nul
title 小鲸鱼 · 控制台
cd /d "%~dp0"

rem 优先用随卡走的便携 Python（runtime\python），没有才用系统 python。
set "PY=python"
if exist "%~dp0runtime\python\python.exe" set "PY=%~dp0runtime\python\python.exe"

echo.
echo   [1/3] 检查心智服务（8848）…
"%PY%" bridge.py health >nul 2>&1
if errorlevel 1 goto :nopython

echo   [2/3] 服务就绪，正在打开控制台…
start "" msedge --new-window --start-fullscreen "http://127.0.0.1:8848/console"
echo   [3/3] 好了。这个窗口可以关掉，服务是独立进程，不会跟着退。
echo.
ping -n 3 127.0.0.1 >nul
exit /b 0

:nopython
echo.
echo   [!] 启动失败：要么没有可用的 python，要么 mind_server 起不来。
echo       手动看一眼： "%PY%" bridge.py health
echo.
pause
exit /b 1
