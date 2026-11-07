@echo off
rem ---------------------------------------------------------------------------
rem  Open the whale-card console in a browser.
rem  PURE ASCII on purpose (cmd.exe mangles non-ASCII .bat on some machines,
rem  and the failure mode is "double click -> nothing happens").
rem  All Chinese text is printed by tools\open_console.py.
rem ---------------------------------------------------------------------------
chcp 65001 >nul
cd /d "%~dp0"

set "PY=%~dp0runtime\python\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" -X utf8 "tools\open_console.py"
set "RC=%ERRORLEVEL%"

echo.
echo ------------------------------------------------------------
if "%RC%"=="0" (
  echo   OK - console should be opening.
) else (
  echo   FAILED - exit code %RC%. Read the messages above.
)
echo ------------------------------------------------------------
echo.
echo   Press any key to close this window.
pause >nul
exit /b %RC%
