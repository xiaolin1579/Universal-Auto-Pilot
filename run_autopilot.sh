#!/bin/bash

# --- CONFIGURATION ---
PYTHON_FILE="main.py"       # ตรวจสอบชื่อไฟล์ให้ตรงกับเครื่องของคุณ
VENV_PATH="./venv"
LOG_FILE="script_run.log"

# เข้าไปยังโฟลเดอร์ที่สคริปต์อยู่
cd "$(dirname "$0")"

echo "🚀 [$(date)] Starting Bearbit Auto-DL Suite..."

# 1. ตรวจสอบและสร้าง Virtual Environment
if [ ! -d "$VENV_PATH" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv "$VENV_PATH"
fi

# 2. Activate Virtual Environment
source "$VENV_PATH/bin/activate"

# 3. ติดตั้ง Library ที่จำเป็น (เพิ่มส่วนนี้เข้าไป)
echo "📥 Checking/Installing dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# 4. ติดตั้ง/ตรวจสอบ Playwright Browsers
# เช็คว่ามี browser หรือยัง ถ้าไม่มีให้ติดตั้ง
if [ ! -d "$HOME/.cache/ms-playwright" ]; then
    echo "🌐 Installing Playwright browsers (Chromium)..."
    playwright install chromium
    playwright install-deps chromium
fi

echo "✅ Environment is ready!"
echo "----------------------------------------"

# 5. รันสคริปต์ Python
# ใช้ python3 (ที่อยู่ใน venv) รัน
python3 -u "$PYTHON_FILE" 2>&1 | tee -a "$LOG_FILE"