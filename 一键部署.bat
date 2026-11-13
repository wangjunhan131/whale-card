@echo off
rem ---------------------------------------------------------------------------
rem  One-click deploy: bring this card to life on a new computer.
rem  This file is intentionally PURE ASCII: cmd.exe mangles non-ASCII .bat
rem  on some machines, and that failure looks like "double click, nothing".
rem  All Chinese text is printed by tools\deploy.py.
rem ---------------------------------------------------------------------------
chcp 65001 >nul
title Digital Life Card - Deploy
cd /d "%~dp0"

set "PY=%~dp0runtime\python\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" -X utf8 "tools\deploy.py" %*
set "RC=%ERRORLEVEL%"

echo.
echo ------------------------------------------------------------
if "%RC%"=="0" (
  echo   OK - deploy finished.
) else (
  echo   FAILED - exit code %RC%. Read the messages above.
)
echo ------------------------------------------------------------
echo.
echo   Press any key to close this window.
echo   ^(The card service keeps running in the background.^)
pause >nul
exit /b %RC%
