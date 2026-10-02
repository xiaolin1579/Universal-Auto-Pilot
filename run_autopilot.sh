#!/bin/bash

# --- CONFIGURATION ---
PYTHON_FILE="main.py"
VENV_PATH="./venv"
LOG_FILE="script_run.log"

# เข้าไปยังโฟลเดอร์ที่สคริปต์อยู่
cd "$(dirname "$0")"

echo "🚀 [$(date)] Starting Universal Auto-Pilot (nodriver Edition)..."

# 1. ตรวจสอบและสร้าง Virtual Environment
if [ ! -d "$VENV_PATH" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv "$VENV_PATH"
fi

# 2. Activate Virtual Environment
source "$VENV_PATH/bin/activate"

# 3. ตรวจสอบและลบแพ็กเกจส่วนเกินที่ไม่ได้อยู่ใน requirements.txt ทิ้ง
echo "🧹 Checking for obsolete packages..."
python3 -c "
import importlib.metadata
from pathlib import Path
import subprocess

req_file = Path('requirements.txt')
if req_file.exists():
    reqs = set()
    for line in req_file.read_text().splitlines():
        line = line.split('#')[0].strip()
        if line:
            # ดึงเฉพาะชื่อแพ็กเกจ ตัดเงื่อนไขเวอร์ชันออก (เช่น ==, >=, ฯลฯ)
            name = line.split('==')[0].split('>=')[0].split('<=')[0].split('>')[0].split('<')[0].split('~=')[0].strip().lower()
            reqs.add(name)
    
    # ดึงรายชื่อแพ็กเกจที่ติดตั้งอยู่ใน venv ปัจจุบัน
    installed = {dist.metadata['Name'].lower() for dist in importlib.metadata.distributions()}
    protected = {'pip', 'setuptools', 'wheel'}
    
    # หาแพ็กเกจที่มีอยู่แต่ไม่อยู่ใน requirements.txt
    obsolete = installed - reqs - protected
    if obsolete:
        for pkg in obsolete:
            print(f'🗑️ Uninstalling obsolete package: {pkg}')
            subprocess.run(['pip', 'uninstall', '-y', pkg], stdout=subprocess.DEVNULL)
    else:
        print('✨ No obsolete packages found.')
"

# 4. ติดตั้ง/อัปเดตแพ็กเกจจาก requirements.txt
echo "📥 Checking/Installing dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

echo "✅ Environment is ready!"
echo "----------------------------------------"

# 5. รันสคริปต์ Python
python3 -u "$PYTHON_FILE" 2>&1 | tee -a "$LOG_FILE"