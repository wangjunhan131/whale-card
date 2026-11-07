@echo off
rem ---------------------------------------------------------------------------
rem  Hook the whale card into DSH (copies the plugin into the DSH profile).
rem  PURE ASCII: cmd.exe mangles non-ASCII .bat files on some machines.
rem ---------------------------------------------------------------------------
chcp 65001 >nul
cd /d "%~dp0"

set "PY=%~dp0runtime\python\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" -X utf8 "tools\install_to_dsh.py" %*
set "RC=%ERRORLEVEL%"

echo.
echo ------------------------------------------------------------
if "%RC%"=="0" (
  echo   OK - restart DSH to pick it up.
) else (
  echo   FAILED - exit code %RC%. Read the messages above.
)
echo ------------------------------------------------------------
echo.
echo   Press any key to close this window.
pause >nul
exit /b %RC%
