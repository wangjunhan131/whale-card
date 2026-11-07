@echo off
chcp 65001 >nul
title Digital Life Card - Come Back
cd /d "%~dp0"

rem Pure ASCII on purpose: cmd handles non-ASCII .bat inconsistently
rem and may skip the whole file (double-click looks like nothing happened).
rem All Chinese messages come from python.

set "PY=python"
if exist "%~dp0runtime\python\python.exe" set "PY=%~dp0runtime\python\python.exe"

"%PY%" -X utf8 "%~dp0tools\come_back.py" %*
echo.
pause
