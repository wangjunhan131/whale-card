@echo off
rem ---------------------------------------------------------------------------
rem  One-click landing for the whale card.
rem  This file is intentionally PURE ASCII: cmd.exe mangles non-ASCII .bat files
rem  on some machines (GBK vs UTF-8), and that failure looks like "double click,
rem  nothing happens". All Chinese text is printed by first_run.py instead.
rem ---------------------------------------------------------------------------
chcp 65001 >nul
cd /d "%~dp0"

set "PY=%~dp0runtime\python\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" -X utf8 "tools\first_run.py" %*
set "RC=%ERRORLEVEL%"

echo.
echo ------------------------------------------------------------
if "%RC%"=="0" (
  echo   OK - everything is ready.
) else (
  echo   FAILED - exit code %RC%. Read the messages above.
)
echo ------------------------------------------------------------
echo.
echo   Press any key to close this window.
echo   ^(The card service keeps running in the background.^)
pause >nul
exit /b %RC%
