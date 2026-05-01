@echo off
title TorrentDD Remote Control Bot (VENV/Wine)
cd /d "%~dp0"

:: --- CONFIG PATH ---
set LOG_FILE=remote_bot.log

:: 1. สร้าง venv ถ้ายังไม่มี
if not exist venv (
    echo [%date% %time%] 📦 Creating Virtual Environment... >> %LOG_FILE%
    python -m venv venv
)

:: 2. ตรวจสอบ Path ของ Python
if exist "venv\Scripts\python.exe" (
    set VENV_PYTHON=venv\Scripts\python.exe
) else if exist "venv\bin\python" (
    set VENV_PYTHON=venv\bin\python
) else (
    set VENV_PYTHON=python
)

:: 3. ติดตั้ง/อัปเดต Library
echo [%date% %time%] 📥 Checking dependencies... >> %LOG_FILE%
%VENV_PYTHON% -m pip install --upgrade pip >> %LOG_FILE% 2>&1
if exist requirements.txt (
    %VENV_PYTHON% -m pip install -r requirements.txt >> %LOG_FILE% 2>&1
)

:loop
cls
echo ==========================================
echo   🚀 TorrentDD Remote Bot is Running
echo   Mode: VENV (Wine Compatible)
echo   Log: %LOG_FILE%
echo   Time: %date% %time%
echo ==========================================

:: บันทึกเวลาเริ่มรันลง Log
echo [%date% %time%] 🚀 Remote Bot Started. >> %LOG_FILE%

:: 4. รันบอท (ใช้คำสั่งเพื่อแสดงผลหน้าจอ และบันทึกลงไฟล์พร้อมกัน)
:: สำหรับ Windows/Wine มาตรฐาน การใช้ ">> file 2>&1" จะบันทึก Log ได้ดีที่สุด
%VENV_PYTHON% remote_control.py >> %LOG_FILE% 2>&1

echo.
echo ------------------------------------------
echo ⚠️ Bot stopped or crashed at %time%
echo 🔄 Restarting in 5 seconds...
echo ------------------------------------------

:: บันทึกเหตุการณ์หยุดทำงานลง Log
echo [%date% %time%] ⚠️ Bot stopped/crashed. >> %LOG_FILE%

timeout /t 5 >nul
goto loop