@echo off
SETLOCAL EnableDelayedExpansion

:: --- CONFIG ---
SET PYTHON_FILE=main.py
SET LOG_FILE=script_run.log
chcp 65001 >nul

echo 🚀 Starting TorrentDD Auto-Pilot (Wine/Windows Compatible)...

:: 1. ค้นหา Python (เหมือนเดิม)
SET PYTHON_EXE=python
%PYTHON_EXE% --version >nul 2>&1
if %errorlevel% neq 0 (
    SET PYTHON_EXE=py
    %PYTHON_EXE% --version >nul 2>&1
    if !errorlevel! neq 0 (
        SET PYTHON_EXE="C:\Windows\python.exe"
    )
)

:: 2. ข้าม venv ไปเลยถ้าอยู่บน Wine (เพื่อความเสถียร)
:: แต่ถ้าอยากใช้ venv ก็ให้ลบโฟลเดอร์เก่าทิ้งก่อนรันสคริปต์นี้
echo 📥 Installing Libraries...
%PYTHON_EXE% -m pip install --upgrade pip
%PYTHON_EXE% -m pip install -r requirements.txt

echo ----------------------------------------
echo 📝 Running %PYTHON_FILE%...

:: 3. เช็ค PowerShell แบบเน้นๆ
:: ถ้ากัปตันรันบน Wine แล้วไม่ได้ลง 'wine powershell' ไว้ มันจะรันบรรทัดล่างนี้ทันที
powershell -command "exit" >nul 2>&1
if %errorlevel% neq 0 (
    echo [System] PowerShell not found, running direct mode...
    %PYTHON_EXE% -u "%PYTHON_FILE%"
) else (
    echo [System] PowerShell detected, running with log...
    %PYTHON_EXE% -u "%PYTHON_FILE%" 2>&1 | powershell -command "$input | tee-object -filepath '%LOG_FILE%' -append"
)

if %errorlevel% neq 0 (
    echo ❌ Script stopped.
    pause
)