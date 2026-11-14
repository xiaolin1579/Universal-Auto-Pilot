@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
title Universal Config Manager

set "UAP_PYTHON_ARGS="
if exist "%~dp0venv\Scripts\python.exe" (
    set "UAP_PYTHON_BIN=%~dp0venv\Scripts\python.exe"
    goto launch
)
if exist "%~dp0.venv\Scripts\python.exe" (
    set "UAP_PYTHON_BIN=%~dp0.venv\Scripts\python.exe"
    goto launch
)
where py >nul 2>&1
if not errorlevel 1 (
    set "UAP_PYTHON_BIN=py"
    set "UAP_PYTHON_ARGS=-3"
    goto launch
)
where python >nul 2>&1
if not errorlevel 1 (
    set "UAP_PYTHON_BIN=python"
    goto launch
)
echo [!] ต้องติดตั้ง Python 3.10 ขึ้นไปเพื่อเปิดตัวจัดการ config
exit /b 1

:launch
"%UAP_PYTHON_BIN%" %UAP_PYTHON_ARGS% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 (
    echo [!] ต้องใช้ Python 3.10 ขึ้นไป โปรดตรวจ Python หรือ virtual environment ที่เลือก
    exit /b 1
)
"%UAP_PYTHON_BIN%" %UAP_PYTHON_ARGS% -X utf8 "%~dp0config_editor.py" --config "%~dp0config.json" %*
exit /b %errorlevel%
