@echo off
SETLOCAL EnableDelayedExpansion

:: --- CONFIG ---
SET PYTHON_FILE=main.py
SET LOG_FILE=script_run.log
chcp 65001 >nul

echo 🚀 Starting Universal Auto-Pilot (nodriver Edition)...

:: 1. ค้นหา Python
SET PYTHON_EXE=python
%PYTHON_EXE% --version >nul 2>&1
if %errorlevel% neq 0 (
    SET PYTHON_EXE=py
    %PYTHON_EXE% --version >nul 2>&1
    if !errorlevel! neq 0 (
        SET PYTHON_EXE="C:\Windows\python.exe"
    )
)

:: 2. ตรวจสอบและลบแพ็กเกจส่วนเกินที่ไม่อยู่ใน requirements.txt ทิ้งอัตโนมัติ
echo 🧹 Checking for obsolete packages...
%PYTHON_EXE% -c "import importlib.metadata, subprocess, pathlib; req_file=pathlib.Path('requirements.txt'); reqs={l.split('#')[0].strip().lower().split('==')[0].split('>=')[0].split('<=')[0].split('>')[0].split('<')[0].strip() for l in req_file.read_text(encoding='utf-8', errors='ignore').splitlines() if l.strip() and not l.startswith('#')}; installed={d.metadata['Name'].lower() for d in importlib.metadata.distributions()}; obsolete=installed - reqs - {'pip','setuptools','wheel'}; [subprocess.run(['pip','uninstall','-y',p]) for p in obsolete]"

:: 3. ติดตั้ง/อัปเดตแพ็กเกจ
echo 📥 Installing Libraries...
%PYTHON_EXE% -m pip install --upgrade pip
%PYTHON_EXE% -m pip install -r requirements.txt

echo ----------------------------------------
echo 📝 Running %PYTHON_FILE%...

:: 4. รันสคริปต์พร้อมระบบจัดการ Log
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