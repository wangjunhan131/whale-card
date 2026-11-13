@echo off
rem ---------------------------------------------------------------------------
rem  Take the card out: mirror this card onto the travel disk and retire
rem  this machine's copy (read-only) so only one "me" can write.
rem  PURE ASCII on purpose; Chinese text comes from tools\go_out.py.
rem ---------------------------------------------------------------------------
chcp 65001 >nul
title Digital Life Card - Go Out
cd /d "%~dp0"

set "PY=%~dp0runtime\python\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" -X utf8 "tools\go_out.py" %*
set "RC=%ERRORLEVEL%"

echo.
echo ------------------------------------------------------------
if "%RC%"=="0" (
  echo   OK - copy is on the travel disk.
) else (
  echo   FAILED - exit code %RC%. Read the messages above.
)
echo ------------------------------------------------------------
echo.
echo   Now unplug the disk and take it with you.
echo   (Run it with --dry first if you just want a rehearsal.)
pause >nul
exit /b %RC%
