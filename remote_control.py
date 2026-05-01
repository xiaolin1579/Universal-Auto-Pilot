import telebot
from telebot import types
from telebot.async_telebot import AsyncTeleBot
import discord
from discord.ext import commands
import asyncio
import os
import json
import subprocess
import time
import re
import psutil
import pytz
from datetime import datetime, timedelta

# --- SETUP PATH & CONFIG ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')
LOG_PATH = os.path.join(BASE_DIR, 'script_run.log')
STATS_HISTORY_FILE = os.path.join(BASE_DIR, "stats_history.json")

# ตัวแปรเก็บสถานะการทำงานของ User (สำหรับ Async)
user_states = {} # { chat_id: "WAITING_MIN_SIZE" }

def load_config():
    if not os.path.exists(CONFIG_PATH): return {}
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_config(config):
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=4, ensure_ascii=False)

# กำหนด Timezone ไทย
tz = pytz.timezone('Asia/Bangkok')

def get_now():
    """ฟังก์ชันกลางสำหรับดึงเวลาไทยปัจจุบัน"""
    return datetime.now(tz)

# --- SHARED FUNCTIONS ---
def is_process_running(process_cmd_name):
    """
    ตรวจสอบว่า Process รันอยู่หรือไม่ 
    รองรับ: 'python3 -u main.py', 'python -u main.py', 'python main.py'
    """
    # สร้าง Pattern: เปลี่ยน 'python3' เป็น 'python[3]?' เพื่อให้ 3 เป็น optional
    # และจัดการช่องว่างให้ยืดหยุ่นด้วย \s+
    pattern = process_cmd_name.replace("python3", r"python[3]?")
    pattern = pattern.replace(" ", r"\s+")
    
    for proc in psutil.process_iter(['cmdline']):
        try:
            cmdline = proc.info['cmdline']
            if cmdline:
                # รวม list เป็น string เช่น ['python3', '-u', 'main.py'] -> 'python3 -u main.py'
                cmd_str = " ".join(cmdline)
                if re.search(pattern, cmd_str):
                    return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return False

def update_config_value(key, value):
    c = load_config()
    if 'SETTING' not in c: c['SETTING'] = {}
    c['SETTING'][key] = value
    save_config(c)

def get_bot_runtime(script_name="main.py"):
    """คำนวณเวลาที่บอททำงานมาแล้วจาก Process จริงอย่างแม่นยำ"""
    for proc in psutil.process_iter(['cmdline']):
        try:
            cmdline_list = proc.info.get('cmdline') or []
            
            # รวมอาร์กิวเมนต์ทั้งหมดเป็นสตริงเส้นเดียว เช่น "python3 -u main.py"
            full_cmdline = " ".join(cmdline_list)
            
            # เช็คว่ามีคำว่า main.py อยู่ในคำสั่งรันหรือไม่
            if script_name in full_cmdline:
                create_time = proc.create_time() 
                start_time = datetime.fromtimestamp(create_time)
                duration = datetime.now() - start_time
                
                days = duration.days
                hours, remainder = divmod(duration.seconds, 3600)
                minutes, seconds = divmod(remainder, 60)
                
                if days > 0:
                    return f"{days}d {hours}h {minutes}m"
                if hours > 0:
                    return f"{hours}h {minutes}m {seconds}s"
                if minutes > 0:
                    return f"{minutes}m {seconds}s"
                return f"{seconds}s"
                
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
            
    return "0s"

def get_status_text():
    c = load_config()
    SET = c.get('SETTING', {})
    main_running = is_process_running("python3 -u main.py")

    # ดึงค่า Config ต่างๆ
    is_freeload = SET.get('FREELOAD_ENABLE', False)
    min_gb = SET.get('MIN_SIZE_GB', 0)
    max_gb = SET.get('MAX_SIZE_GB', 0)
    min_percent = SET.get('MIN_FREE_PERCENT', 0) # สมมติว่าใช้คีย์นี้เก็บ %

    # ดึงค่า Run Time
    runtime = get_bot_runtime("python3 -u main.py") if main_running else "N/A"

    # จัดรูปแบบข้อความเริ่มต้น
    lines = [
        "📍 <b>System Status</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"• Main Bot: {'🟢 Online' if main_running else '🔴 Offline'}",
        f"• Run Time: <code>{runtime}</code>",
        f"• Min-Max: <code>{min_gb} - {max_gb} GB</code>",
        f"• Freeload Only: {'✅ Yes' if is_freeload else '❌ No'}"
    ]

    # ✅ เงื่อนไข: แสดง Freeload Percent เฉพาะเมื่อ Freeload Only เป็น Yes เท่านั้น
    if is_freeload:
        lines.append(f"• Freeload Percent: <code>{min_percent}%</code>")

    lines.append("━━━━━━━━━━━━━━━━━━")

    return "\n".join(lines)

def format_size(size_gb):
    """
    แปลงค่าจากหน่วยพื้นฐาน (GB) ให้เป็นหน่วยที่เหมาะสมที่สุดโดยอัตโนมัติ
    รองรับตั้งแต่ KB ไปจนถึง PB
    """
    if size_gb == 0: return "0.00 GB"
    
    # แปลงจาก GB กลับไปเป็น Bytes ก่อนเพื่อให้เริ่มคำนวณจากหน่วยเล็กสุดได้แม่นยำ
    # (หรือจะเริ่มจาก GB เลยก็ได้ แต่การเริ่มจากหน่วยกลางๆ จะทำให้หารง่ายกว่า)
    units = ("B", "KB", "MB", "GB", "TB", "PB", "EB")
    
    # เริ่มต้นที่หน่วย GB (index 3 ในลิสต์ units)
    current_size = float(abs(size_gb))
    unit_index = 3 
    
    # ถ้าค่ามากกว่า 1024 ให้ขยับหน่วยขึ้น (เช่น GB -> TB)
    while current_size >= 1024 and unit_index < len(units) - 1:
        current_size /= 1024
        unit_index += 1
        
    # ถ้าค่าน้อยกว่า 1 (แต่ไม่ใช่ 0) ให้ขยับหน่วยลง (เช่น GB -> MB)
    while current_size < 1 and unit_index > 0:
        current_size *= 1024
        unit_index -= 1
        
    # คืนค่าพร้อมเครื่องหมาย (บวก/ลบ) ตามค่าเดิมที่ส่งมา
    sign = "-" if size_gb < 0 else ""
    return f"{sign}{current_size:.2f} {units[unit_index]}"

def parse_size(size_str):
    try:
        size_str = size_str.upper().replace(',', '')
        match = re.search(r"([0-9.]+)\s*(TB|GB|MB|KB|GIB|MIB|TIB)", size_str)
        if not match: return 0.0
        num, unit = float(match.group(1)), match.group(2)
        factors = {"TB": 1024, "TIB": 1024, "GB": 1, "GIB": 1, "MB": 1/1024, "MIB": 1/1024}
        return num * factors.get(unit, 1)
    except: return 0.0

def get_filtered_logs(n=15):
    if not os.path.exists(LOG_PATH): return "❌ ไม่พบไฟล์ Log"
    try:
        if os.name != 'nt':
            raw_logs = subprocess.check_output(["tail", "-n", "50", LOG_PATH]).decode('utf-8')
            lines = raw_logs.split('\n')
        else:
            with open(LOG_PATH, 'r', encoding='utf-8') as f:
                lines = f.readlines()

        filtered = [l for l in lines if "Next cycle in" not in l and l.strip() != ""]
        return "\n".join(filtered[-n:])
    except: return "⚠️ อ่าน Log ขัดข้อง"

def get_historical_report(site_name="TORRENTDD"):
    try:
        if not os.path.exists(STATS_HISTORY_FILE):
            return "⚠️ ยังไม่มีไฟล์ประวัติสถิติ"

        with open(STATS_HISTORY_FILE, 'r', encoding='utf-8') as f:
            all_history = json.load(f)

        # เข้าถึงข้อมูลตาม Site Name (TORRENTDD / BEARBIT)
        history = all_history.get(site_name, {})
        if not history: 
            return f"⚠️ ไม่พบข้อมูลของเว็บ {site_name}"

        now = get_now()
        today_str = now.strftime("%Y-%m-%d")
        today_keys = sorted([k for k in history.keys() if k.startswith(today_str)])

        if not today_keys:
            return f"📊 ยังไม่มีข้อมูลของวันนี้ ({today_str}) สำหรับ {site_name}"

        first_snapshot = history[today_keys[0]]
        latest_snapshot = history[today_keys[-1]]
        h1_snapshot = history.get(today_keys[-2]) if len(today_keys) > 1 else None

        # ฟังก์ชันคำนวณส่วนต่างภายใน (เรียกใช้ format_size ตัวเทพของคุณ)
        def calc_gain(new_val, old_val):
            diff = new_val - old_val
            if diff == 0: return "➖ 0.00 GB"
            prefix = "📈 +" if diff > 0 else "📉 "
            return f"{prefix}{format_size(diff)}"

        msg = [
            f"📊 <b>{site_name} Report: {today_str}</b>",
            "━━━━━━━━━━━━━━━━━━",
            f"👤 <b>User:</b> <code>{latest_snapshot['username']}</code>",
            f"📤 <b>Uploaded:</b> <code>{format_size(latest_snapshot['up'])}</code>",
            f"📥 <b>Downloaded:</b> <code>{format_size(latest_snapshot['dl'])}</code>",
            f"📊 <b>Ratio:</b> <code>{latest_snapshot['ratio']:.3f}</code>"
        ]

        # เพิ่ม Bonus เฉพาะถ้ามีข้อมูล (เช่น ใน BearBit อาจจะไม่ได้เก็บ)
        if 'bonus' in latest_snapshot:
            msg.append(f"💰 <b>Bonus:</b> <code>{latest_snapshot['bonus']:,.1f}</code>")

        msg.extend([
            "━━━━━━━━━━━━━━━━━━",
            "⚡ <b>Last 1 Hour</b>",
            f"  └ 📤 {calc_gain(latest_snapshot['up'], h1_snapshot['up']) if h1_snapshot else 'Collecting...'}",
            f"  └ 📥 {calc_gain(latest_snapshot['dl'], h1_snapshot['dl']) if h1_snapshot else 'Collecting...'}",
            "━━━━━━━━━━━━━━━━━━",
            f"📅 <b>Today's Gain</b>",
            f"  └ 📤 {calc_gain(latest_snapshot['up'], first_snapshot['up'])}",
            f"  └ 📥 {calc_gain(latest_snapshot['dl'], first_snapshot['dl'])}",
            "━━━━━━━━━━━━━━━━━━"
        ])
        return "\n".join(msg)

    except Exception as e:
        return f"❌ Report Error [{site_name}]: {str(e)}"

def get_monthly_report(site_name="TORRENTDD"):
    try:
        if not os.path.exists(STATS_HISTORY_FILE):
            return "⚠️ ยังไม่มีไฟล์ประวัติสถิติ"

        with open(STATS_HISTORY_FILE, 'r', encoding='utf-8') as f:
            all_history = json.load(f)

        # เข้าถึงข้อมูลตาม Site Name
        history = all_history.get(site_name, {})
        if not history: 
            return f"⚠️ ไม่พบข้อมูลของเว็บ {site_name}"

        now = get_now()
        current_month = now.strftime("%Y-%m") # เช่น "2026-05"

        # กรองเอาเฉพาะ Key ของเดือนนี้และเรียงลำดับ
        monthly_keys = sorted([k for k in history.keys() if k.startswith(current_month)])

        if len(monthly_keys) < 2:
            return f"📊 ข้อมูลของเดือน {current_month} ({site_name}) ยังไม่เพียงพอสำหรับสรุปยอด"

        # ดึงข้อมูลตัวแรกและล่าสุดของเดือน
        first_data = history[monthly_keys[0]]
        last_data = history[monthly_keys[-1]]

        # คำนวณส่วนต่าง (ใช้หน่วย GB จาก JSON และ format_size ตัวเทพของคุณ)
        up_gain = last_data['up'] - first_data['up']
        dl_gain = last_data['dl'] - first_data['dl']
        
        # ป้องกัน Error กรณีไม่มีคีย์ bonus
        bonus_gain = last_data.get('bonus', 0) - first_data.get('bonus', 0)

        # นับจำนวนวันที่เริ่มมีข้อมูลในเดือนนี้
        active_days = len(set([k.split()[0] for k in monthly_keys]))

        msg = [
            f"🗓️ <b>{site_name} Monthly: {current_month}</b>",
            "━━━━━━━━━━━━━━━━━━",
            f"👤 <b>User:</b> <code>{last_data['username']}</code>",
            f"📤 <b>Total Uploaded:</b> +{format_size(up_gain)}", # เรียกใช้ฟังก์ชันแปลงหน่วย
            f"📥 <b>Total Downloaded:</b> +{format_size(dl_gain)}",
        ]

        # แสดง Bonus เฉพาะเมื่อมีการเปลี่ยนแปลง
        if bonus_gain != 0 or 'bonus' in last_data:
            msg.append(f"💰 <b>Total Bonus:</b> +{bonus_gain:,.1f} pts")

        msg.extend([
            "━━━━━━━━━━━━━━━━━━",
            f"📅 ข้อมูลสะสม: {active_days} วัน",
            f"⏱️ ตั้งแต่: {monthly_keys[0]}",
            f"⏱️ ถึง: {monthly_keys[-1]}",
            "━━━━━━━━━━━━━━━━━━"
        ])
        return "\n".join(msg)

    except Exception as e:
        return f"⚠️ Monthly Error [{site_name}]: {str(e)}"

def format_report(report_raw, platform='tg'):
    if platform == 'dc':
        # เปลี่ยน HTML เป็น Markdown สำหรับ Discord
        return report_raw.replace('<b>', '**').replace('</b>', '**')\
                         .replace('<code>', '`').replace('</code>', '`')
    return report_raw # ส่ง HTML ไปตามปกติสำหรับ Telegram

# --- MAIN RUNNER ---
async def main():
    CONF = load_config()
    tasks = []
    print("🚀 Initializing Hybrid Remote Control...")

    # --- 🔵 TELEGRAM SECTION ---
    tg_cfg = CONF.get('TELEGRAM_CONFIG', {})
    if tg_cfg.get('remote_enable', False):
        try:
            tg_bot = AsyncTeleBot(tg_cfg['remote_bot_token'])
            TG_CHAT_ID = str(tg_cfg['chat_id'])

            def main_menu():
                m = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
                m.add('📊 Status Check', '📈 Stats Report', '📈 Stats Monthly Report', '⚙️ Config Settings', '📄 View Log', '📁 Download Log', '🎮 Bot Controls')
                return m

            def settings_menu():
                m = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
                m.add('📏 Set Min Size', '📐 Set Max Size', '♻️ Toggle Freeload', '📊 Set Min %', '⬅️ Back')
                return m

            def controls_menu():
                m = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
                m.add('🚀 Start Bot', '🚫 Stop Bot')
                m.add('🔄 Restart & Update', '♻️ Restart Remote', '⬅️ Back')
                return m

            @tg_bot.message_handler(commands=['start'])
            async def tg_start(message):
                if str(message.chat.id) == TG_CHAT_ID:
                    await tg_bot.send_message(message.chat.id, "🕹️ Universal Remote Online", reply_markup=main_menu())

            # --- เพิ่มส่วนการดึงข้อมูลบอทเพื่อโชว์ตอน Online ---
            async def get_bot_info():
                bot_info = await tg_bot.get_me()
                print(f"📡 Telegram Remote Online as: @{bot_info.username}")

            # เรียกใช้งาน function แจ้งเตือนสถานะ
            import asyncio
            asyncio.create_task(get_bot_info())

            @tg_bot.message_handler(func=lambda m: True)
            async def tg_handle(message):
                chat_id = str(message.chat.id)
                if chat_id != TG_CHAT_ID: return
                txt = message.text

                # --- 1. ตรวจสอบสถานะการรอรับค่า (State Management) ---
                if chat_id in user_states:
                    state = user_states[chat_id]
                    try:
                        val = float(txt)
                        c = load_config()
                        if 'SETTING' not in c: c['SETTING'] = {}

                        if state == "WAIT_MIN":
                            c['SETTING']['MIN_SIZE_GB'] = val
                            await tg_bot.send_message(chat_id, f"✅ อัปเดต Min Size: `{val}` GB", parse_mode='Markdown', reply_markup=settings_menu())
                        elif state == "WAIT_MAX":
                            c['SETTING']['MAX_SIZE_GB'] = val
                            await tg_bot.send_message(chat_id, f"✅ อัปเดต Max Size: `{val}` GB", parse_mode='Markdown', reply_markup=settings_menu())
                        elif state == "WAIT_PERCENT": # ✅ รับค่า %
                            c['SETTING']['MIN_FREE_PERCENT'] = int(val)
                            await tg_bot.send_message(chat_id, f"✅ อัปเดต Freeload Percent: `{val}` %", parse_mode='Markdown',reply_markup=settings_menu())
                        save_config(c)
                        del user_states[chat_id]
                        return
                    except ValueError:
                        await tg_bot.send_message(chat_id, "❌ กรุณาส่งเป็นตัวเลขเท่านั้น (เช่น 10.5) หรือส่งข้อความอื่นเพื่อยกเลิก")
                        del user_states[chat_id]
                        return

                # --- 2. เมนูหลัก ---
                if txt == '📊 Status Check':
                    await tg_bot.send_message(chat_id, get_status_text(), parse_mode='HTML')
                elif txt == '📈 Stats Report':
                    # ดึงรายชื่อเว็บทั้งหมดที่มีประวัติ
                    with open(STATS_HISTORY_FILE, 'r', encoding='utf-8') as f:
                        all_history = json.load(f)
                    # วนลูปส่งรายงานทีละเว็บ หรือรวมเป็นข้อความเดียว
                    for site in all_history.keys():
                        report = get_historical_report(site_name=site)
                        await tg_bot.send_message(chat_id, report, parse_mode='HTML')
                elif txt == '📈 Stats Monthly Report':
                    # ดึงรายชื่อเว็บทั้งหมดที่มีประวัติ
                    with open(STATS_HISTORY_FILE, 'r', encoding='utf-8') as f:
                        all_history = json.load(f)
                    for site in all_history.keys():
                        report = get_monthly_report(site_name=site)
                        await tg_bot.send_message(chat_id, report, parse_mode='HTML')
                elif txt == '⚙️ Config Settings':
                    c = load_config().get('SETTING', {})
                    status_free = "✅ ON" if c.get('FREELOAD_ENABLE', True) else "❌ OFF"
                    min_p = c.get('MIN_FREE_PERCENT', 0)
                    
                    msg = (f"🛠️ **Settings Menu**\n"
                           f"━━━━━━━━━━━━━━━━━━\n"
                           f"• Freeload: `{status_free}`\n"
                           f"• Min Percent: `{min_p}%`\n"
                           f"━━━━━━━━━━━━━━━━━━\n"
                           f"เลือกหัวข้อที่ต้องการปรับเปลี่ยน:")
                    await tg_bot.send_message(chat_id, msg, parse_mode='Markdown', reply_markup=settings_menu())

                elif txt == '🎮 Bot Controls':
                    await tg_bot.send_message(chat_id, "🕹️ ควบคุมระบบ", reply_markup=controls_menu())

                elif txt == '⬅️ Back':
                    await tg_bot.send_message(chat_id, "🏠 กลับหน้าหลัก", reply_markup=main_menu())
                elif txt == '📄 View Log':
                    await tg_bot.send_message(chat_id, f"📄 **Last Log:**\n```\n{get_filtered_logs()}\n```", parse_mode='Markdown')
                elif txt == '📁 Download Log':
                    if os.path.exists(LOG_PATH):
                        try:
                            with open(LOG_PATH, 'rb') as f:
                                # ✅ สำหรับ AsyncTeleBot ให้ส่ง f ไปตรงๆ
                                # หากต้องการระบุชื่อไฟล์ให้ใช้ tuple (ชื่อไฟล์, ไฟล์ข้อมูล)
                                await tg_bot.send_document(
                                    chat_id,
                                    document=(os.path.basename(LOG_PATH), f),
                                    caption="📄 Full Log"
                                )
                        except Exception as e:
                            await tg_bot.send_message(chat_id, f"❌ เกิดข้อผิดพลาดในการส่งไฟล์: {e}")
                    else:
                        await tg_bot.send_message(chat_id, "❌ ไม่พบไฟล์ Log")

                # --- Config Actions ---
                elif txt == '📏 Set Min Size':
                    user_states[chat_id] = "WAIT_MIN"
                    await tg_bot.send_message(chat_id, "📏 ส่งตัวเลขขนาดไฟล์ **ขั้นต่ำ** (GB):", reply_markup=types.ReplyKeyboardRemove())
                elif txt == '📐 Set Max Size':
                    user_states[chat_id] = "WAIT_MAX"
                    await tg_bot.send_message(chat_id, "📐 ส่งตัวเลขขนาดไฟล์ **สูงสุด** (GB):", reply_markup=types.ReplyKeyboardRemove())
                elif txt == '📊 Set Min %': # คุณต้องไปเพิ่มปุ่มนี้ใน settings_menu()
                    user_states[chat_id] = "WAIT_PERCENT"
                    await tg_bot.send_message(chat_id, "📊 ส่งตัวเลข **% ขั้นต่ำ** ที่ต้องการ (เช่น 10):", reply_markup=types.ReplyKeyboardRemove())

                elif txt == '♻️ Toggle Freeload':
                    c = load_config()
                    if 'SETTING' not in c: c['SETTING'] = {}
                    curr = c['SETTING'].get('FREELOAD_ENABLE', True)
                    new_val = not curr
                    c['SETTING']['FREELOAD_ENABLE'] = new_val
                    save_config(c)
                    await tg_bot.send_message(chat_id, f"♻️ เปลี่ยนโหมด Freeload เป็น: `{'✅ ON' if new_val else '❌ OFF'}`", parse_mode='Markdown')

                # --- Control Actions ---
                elif txt == '🚀 Start Bot':
                    if is_process_running("python3 -u main.py"):
                        await tg_bot.send_message(chat_id, "⚠️ บอทหลักทำงานอยู่ในขณะนี้")
                    else:
                        try:
                            await tg_bot.send_message(chat_id, "⏳ กำลังรันบอทหลัก...")

                            # รันคำสั่งตาม OS (เน้น nohup สำหรับ Linux ของพี่)
                            if os.name != 'nt':
                                run_cmd = f"nohup ./run_autopilot.sh > {LOG_PATH} 2>&1 &"
                                subprocess.Popen(run_cmd, shell=True, cwd=BASE_DIR, preexec_fn=os.setpgrp)
                            else:
                                run_cmd = f'start /b "" "run_autopilot.bat"'
                                subprocess.Popen(run_cmd, shell=True, cwd=BASE_DIR)

                            # --- วนเช็ค 6 รอบ รอบละ 15 วินาที (เผื่อเวลาติดตั้ง pip) ---
                            is_started = False
                            max_retries = 6
                            for i in range(1, max_retries + 1):
                                print(f"🔍 Checking bot status... Round {i}")
                                await asyncio.sleep(15)  # รอ 10 วินาทีในแต่ละรอบ
            
                                if is_process_running("python3 -u main.py"):
                                    is_started = True
                                    break
            
                                if i < max_retries:
                                    await tg_bot.send_message(chat_id, f"⏳ บอทกำลังเริ่มทำงาน (รอบที่ {i}/{max_retries})...")

                            if is_started:
                                await tg_bot.send_message(chat_id, "✅ บอทหลักทำงานแล้ว")
                            else:
                                await tg_bot.send_message(chat_id, "❌ <b>รันบอทไม่สำเร็จ:</b> ไม่พบโปรเซสในระบบ โปรดเช็ค Log", parse_mode='HTML')

                        except Exception as e:
                            await tg_bot.send_message(chat_id, f"❌ <b>Error:</b> {str(e)}", parse_mode='HTML')

                elif txt == '🚫 Stop Bot':
                    if not is_process_running("python3 -u main.py"):
                        await tg_bot.send_message(chat_id, "⚠️ บอทหลักไม่ได้ทำงานอยู่ในขณะนี้")
                    else:
                        try:
                            await tg_bot.send_message(chat_id, "⏳ กำลังส่งสัญญาณหยุดบอทหลัก...")

                            if os.name != 'nt':  # Linux/Unix
                                # ใช้ SIGTERM (15) เพื่อให้บอทเคลียร์งานก่อนปิด หรือ SIGKILL (9) ถ้าต้องการปิดทันที
                                stop_cmd = "pkill -15 -f 'python3 -u main.py'"
                                os.system(stop_cmd)
                            else:  # Windows
                                # ✅ แก้ไข: ใช้ WMIC หรือ Taskkill แบบระบุชื่อไฟล์สคริปต์
                                # เพื่อไม่ให้มันไปฆ่า Python ตัวอื่นๆ (เช่น บอทรีโมทตัวนี้)
                                stop_cmd = 'wmic process where "commandline like \'%main.py%\'" delete'
                                os.system(stop_cmd)

                            # หน่วงเวลาให้ระบบเคลียร์ Process
                            await asyncio.sleep(15)

                            # ตรวจสอบอีกครั้ง
                            if not is_process_running("python3 -u main.py"):
                                await tg_bot.send_message(chat_id, "🛑 หยุดบอทหลักสำเร็จแล้ว")
                            else:
                                await tg_bot.send_message(chat_id, "❌ <b>ไม่สามารถหยุดบอทได้:</b> โปรเซสยังค้างอยู่ในระบบ", parse_mode='HTML')

                        except Exception as e:
                            await tg_bot.send_message(chat_id, f"❌ <b>Error:</b> {str(e)}", parse_mode='HTML')
                elif txt == '🔄 Restart & Update':
                    await tg_bot.send_message(chat_id, "⏳ กำลังเริ่มกระบวนการ Update...")

                    try:
                        # 1. ปิดบอทเดิมก่อน
                        if is_process_running("python3 -u main.py"):
                            stop_cmd = "pkill -15 -f 'python3 -u main.py'" if os.name != 'nt' else 'wmic process where "commandline like \'%main.py%\'" delete'
                            os.system(stop_cmd)
                            await asyncio.sleep(15) # รอให้ Process คลายตัว

                        # 2. ดึงโค้ดใหม่จาก Git
                        # ใช้ && เพื่อให้มั่นใจว่าคำสั่งถัดไปจะรันเมื่อคำสั่งก่อนหน้าสำเร็จเท่านั้น
                        git_cmd = f"cd {BASE_DIR} && git fetch --all && git reset --hard origin/main"
                        git_result = os.system(git_cmd)

                        if git_result != 0:
                            await tg_bot.send_message(chat_id, "⚠️ <b>Git Update Failed:</b> ตรวจสอบการเชื่อมต่อหรือ Git Conflict", parse_mode='HTML')
                            # ไม่ควรไปต่อถ้ารีเซ็ตโค้ดไม่สำเร็จ
                        else:
                            await tg_bot.send_message(chat_id, "📥 ดึงโค้ดเวอร์ชันล่าสุดสำเร็จ... กำลังเริ่มบอทใหม่")

                        # 3. รันบอทใหม่ (ใช้ Popen เพื่อความเสถียร)
                        if os.name != 'nt':
                            run_cmd = f"nohup ./run_autopilot.sh > {LOG_PATH} 2>&1 &"
                            subprocess.Popen(run_cmd, shell=True, cwd=BASE_DIR, preexec_fn=os.setpgrp)
                        else:
                            run_cmd = 'start /b "" "run_autopilot.bat"'
                            subprocess.Popen(run_cmd, shell=True, cwd=BASE_DIR)

                        # 4. ตรวจสอบสถานะสุดท้าย
                        # วนเช็ค 6 รอบ รอบละ 15 วินาที (เผื่อเวลาติดตั้ง pip)
                        max_retries = 6
                        is_online = False

                        for i in range(1, max_retries + 1):
                            print(f"🔍 Checking bot status... Round {i}")
                            await asyncio.sleep(15)  # รอ 10 วินาทีในแต่ละรอบ
            
                            if is_process_running("python3 -u main.py"):
                                is_online = True
                                break
            
                            if i < max_retries:
                                await tg_bot.send_message(chat_id, f"⏳ บอทกำลังเริ่มทำงาน (รอบที่ {i}/{max_retries})...")

                        if is_online:
                            await tg_bot.send_message(
                                chat_id, 
                                "✅ <b>Update & Restart Success!</b>\nบอทหลักกลับมาทำงานปกติแล้ว", 
                                parse_mode='HTML'
                            )
                        else:
                            await tg_bot.send_message(
                                chat_id, 
                                "❌ <b>Update Error:</b> บอทไม่ออนไลน์หลังอัปเดต 3 รอบ\nอาจจะติดที่ขั้นตอน <code>pip install</code> หรือมี Error ในโค้ด",
                                parse_mode='HTML'
                            )
                    except Exception as e:
                        await tg_bot.send_message(chat_id, f"❌ <b>Update System Error:</b> {str(e)}")

                elif txt == '♻️ Restart Remote':
                    await tg_bot.send_message(chat_id, "♻️ รีสตาร์ท Remote...")
                    os._exit(0)
                elif txt == '⬅️ Back':
                    await tg_bot.send_message(chat_id, "🏠 กลับหน้าหลัก", reply_markup=main_menu())

            tasks.append(tg_bot.polling(non_stop=True))
        except Exception as e: print(f"❌ TG Error: {e}")

    # --- 🟣 DISCORD SECTION (Private DM Mode) ---
    dc_cfg = CONF.get('DISCORD_CONFIG', {})
    if dc_cfg.get('remote_enable', False):
        try:
            intents = discord.Intents.default()
            intents.message_content = True  # ต้องเปิดใน Discord Developer Portal ด้วย

            # บอทจะไม่ตอบรับคำสั่งใน Server (Prefix setup)
            dc_bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
            DC_ADMIN_ID = int(dc_cfg.get('admin_id', 0))

            @dc_bot.event
            async def on_ready():
                print(f"✅ Discord Remote Online (DM Mode) as: {dc_bot.user}")
                # ส่งข้อความทักทายเข้า DM เมื่อบอทออนไลน์
                try:
                    admin = await dc_bot.fetch_user(DC_ADMIN_ID)
                    await admin.send("🔌 **Universal Remote Online**\nบอทพร้อมรับคำสั่งผ่าน DM แล้วครับ")
                except: pass

            @dc_bot.event
            async def on_message(message):
                # 1. กรองเฉพาะข้อความที่เป็น DM และส่งมาจาก Admin เท่านั้น
                is_dm = isinstance(message.channel, discord.DMChannel)
                is_admin = message.author.id == DC_ADMIN_ID

                if message.author == dc_bot.user: return

                if is_dm and is_admin:
                    # ประมวลผลคำสั่งปกติ (!status, !log, !report)
                    await dc_bot.process_commands(message)
                elif not is_admin:
                    # ถ้าคนอื่นทักมา ไม่ตอบโต้ใดๆ เพื่อความเป็นส่วนตัว
                    return

            @dc_bot.command(name="status")
            async def dc_status(ctx):
                # ไม่ต้องเช็ค ID ซ้ำเพราะกรองที่ on_message แล้ว
                await ctx.send(format_report(get_status_text(), platform='dc'))

            @dc_bot.command(name="report")
            async def dc_report(ctx, site: str = None):
                if site:
                    # กรณีระบุชื่อเว็บ เช่น !report BEARBIT
                    report_data = get_historical_report(site_name=site.upper())
                    await ctx.send(format_report(report_data, platform='dc'))
                else:
                    # ดึงรายชื่อเว็บทั้งหมดที่มีประวัติ
                    with open(STATS_HISTORY_FILE, 'r', encoding='utf-8') as f:
                        all_history = json.load(f)
                    for site_key in all_history:
                        report_data = get_historical_report(site_name=site_key)
                        await ctx.send(format_report(report_data, platform='dc'))

            @dc_bot.command(name="month")
            async def dc_month_report(ctx, site: str = None):
                if site:
                    report_data = get_monthly_report(site_name=site.upper())
                    await ctx.send(format_report(report_data, platform='dc'))
                else:
                    # ดึงรายชื่อเว็บทั้งหมดที่มีประวัติ
                    with open(STATS_HISTORY_FILE, 'r', encoding='utf-8') as f:
                        all_history = json.load(f)
                    # วนลูปส่งรายงานทีละเว็บ หรือรวมเป็นข้อความเดียว
                    for site_key in all_history:
                        report_data = get_monthly_report(site_name=site_key)
                        await ctx.send(format_report(report_data, platform='dc'))

            @dc_bot.command(name="log")
            async def dc_log(ctx):
                await ctx.send(f"📄 **Logs:**\n```\n{get_filtered_logs()}\n```")

            @dc_bot.command(name="help")
            async def dc_help(ctx):
                embed = discord.Embed(
                    title="🛠️ TorrentDD DM Remote Help",
                    description="ควบคุมระบบ TorrentDD ผ่านแชทส่วนตัว",
                    color=discord.Color.green()
                )
                embed.add_field(
                    name="📜 Commands",
                    value="`!status` - เช็คสถานะโหนด\n`!report` - ดูสถิติ 24 ชม.\n`!month` - ดูสถิติรายเดือน.\n`!log` - ดู Log ล่าสุด",
                    inline=False
                )
                await ctx.send(embed=embed)

            tasks.append(dc_bot.start(dc_cfg['remote_bot_token']))
        except Exception as e: print(f"❌ Discord Error: {e}")
    if tasks: await asyncio.gather(*tasks)

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
