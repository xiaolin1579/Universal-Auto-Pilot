#!/bin/bash

# --- CONFIGURATION ---
PYTHON_FILE="remote_control.py"       # ตรวจสอบชื่อไฟล์ให้ตรงกับเครื่องของคุณ
VENV_PATH="./venv"
LOG_FILE="remote_run.log"

# เข้าไปยังโฟลเดอร์ที่สคริปต์นี้ตั้งอยู่
cd "$(dirname "$0")"

# 1. สร้าง venv ถ้ายังไม่มี
if [ ! -d "$VENV_PATH" ]; then
    echo "📦 Creating Virtual Environment..."
    python3 -m venv "$VENV_PATH"
fi

# 2. Activate และติดตั้ง requirements
source "$VENV_PATH/bin/activate"
echo "📥 Installing/Updating dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

while true
do
    echo "------------------------------------------"
    echo "🚀 Starting Remote Control Bot (VENV)..."
    echo "Time: $(date)"
    echo "------------------------------------------"
    
    # รันบอทโดยใช้ python ใน venv
    python3 -u "$PYTHON_FILE" 2>&1 | tee -a "$LOG_FILE"
    
    echo "------------------------------------------"
    echo "⚠️ Bot stopped/crashed. Restarting in 5 seconds..."
    echo "------------------------------------------"
    
    sleep 5
done
