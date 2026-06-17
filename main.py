from fake_useragent import UserAgent
import random
import cloudscraper
import threading
import chardet
import gzip
import time
import os
import re
import hashlib
import json
import bencodepy
import requests
import urllib3
import ssl
import base64
import signal
import sys
import platform
import shutil
from requests.adapters import HTTPAdapter
from urllib3.poolmanager import PoolManager
from pyvirtualdisplay import Display
import asyncio
import nodriver as uc
from nodriver import cdp, Config
import pytz
from contextlib import asynccontextmanager
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from requests.auth import HTTPBasicAuth, HTTPDigestAuth
from bs4 import BeautifulSoup
import ddddocr
import io
from xml.sax.saxutils import escape
import functools
print = functools.partial(print, flush=True)

ocr = ddddocr.DdddOcr(show_ad=False)

stop_event = asyncio.Event()

async def handle_exit(sig):
    if stop_event.is_set(): return
    stop_event.set()
    
    print(f"\n🛑 ได้รับสัญญาณ {sig}, กำลังสั่งปิดระบบแบบเร่งด่วน...")

    # 1. ปิด Browser และ Xvfb ไปพร้อมกัน (ไม่รอทีละขั้นตอน)
    async def fast_close_browser():
        global browser_instance
        if browser_instance:
            print("⏳ [Step 1/3] กำลังหยุด Browser...")
            try:
                if hasattr(browser_instance, 'stop'):
                    await asyncio.wait_for(browser_instance.stop(), timeout=3)
            except: pass
            kill_specific_browser()
            await cleanup_profile()
            browser_instance = None
            print("✅ [Step 1/3] Browser ปิดเรียบร้อย")

    # สั่งงานปิด Browser และ Xvfb แบบคู่ขนาน
    cleanup_tasks = [
        asyncio.create_task(fast_close_browser()),
        asyncio.create_task(asyncio.to_thread(kill_xvfb)) 
    ]

    # 2. ยกเลิก Task อื่นๆ ในระบบทันที
    print("⏳ [Step 2/3] กำลังเคลียร์ Task ค้าง...")
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and t not in cleanup_tasks]
    for task in tasks:
        task.cancel()
    
    # 3. แจ้งเตือน (ทำไปพร้อมกับการปิดของ)
    print("⏳ [Step 3/3] กำลังส่งข้อความแจ้งเตือน...")
    try:
        await safe_send_notify(f"🛑 Universal Auto-Pilot : Stopped\nReason: Signal {sig}")
    except: pass

    # รอให้ทุกอย่างจบลงอย่างสมบูรณ์
    await asyncio.gather(*cleanup_tasks, return_exceptions=True)
    
    print("👋 ระบบปิดตัวสมบูรณ์")
    os._exit(0)

urllib3.disable_warnings()

# ========================= CONFIGURATION =========================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
STATS_CACHE_FILE = os.path.join(BASE_DIR, "stats_cache.json")
STATS_HISTORY_FILE = os.path.join(BASE_DIR, "stats_history.json")
CFG = {} 
ORIGINAL_SETTING = None

def load_full_config():
    if not os.path.exists(CONFIG_PATH):
        print(f"❌ Error: ไม่พบไฟล์ {CONFIG_PATH}")
        sys.exit(1)
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)

async def safe_send_notify(msg, *args, **kwargs):
    try:
        # เรียก send_notify ตรงๆ ไม่ต้องผ่าน globals()
        # ตรวจสอบก่อนว่ามันเป็น coroutine หรือไม่
        if asyncio.iscoroutinefunction(send_notify):
            await send_notify(msg, *args, **kwargs)
        else:
            await asyncio.to_thread(send_notify, msg, *args, **kwargs)
    except Exception as e:
        print(f"🚨 [SafeNotify System Error]: {e}")
        # สำคัญ: ต้องคืนค่าเสมอ เพื่อไม่ให้ await ต่อไปพัง
        return False 
    return True

async def send_notify(msg, raw_data=None):
    """
    Async wrapper สำหรับการส่งแจ้งเตือน แบบปลอดภัยและไม่บล็อก Loop
    """
    try:
        # ใช้ to_thread ซึ่งจัดการเรื่อง thread pool ให้อัตโนมัติและเสถียรกว่า
        await asyncio.to_thread(_send_notify_sync, msg)
    except Exception as e:
        with open("bot_error.log", "a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now()} - Error: {e}\n")
        print(f"⚠️ [Notification Error] รายละเอียดถูกบันทึกลง log แล้ว: {e}")

def _send_notify_sync(msg):
    try:
        cfg = load_full_config()
        msg = msg.strip()
        discord_msg = msg.replace('<b>', '**').replace('</b>', '**')
        line_clean_msg = msg.replace('<b>', '').replace('</b>', '')

        # 1. LINE
        line_cfg = cfg.get('LINE_CONFIG', {})
        if line_cfg.get('enable') and line_cfg.get('access_token'):
            try:
                requests.post("https://api.line.me/v2/bot/message/push", 
                              json={"to": line_cfg.get('user_id'), "messages": [{"type": "text", "text": line_clean_msg}]},
                              headers={"Authorization": f"Bearer {line_cfg.get('access_token')}"}, timeout=10)
            except Exception as e:
                print(f"LINE Error: {e}")

        # 2. Telegram
        tele_cfg = cfg.get('TELEGRAM_CONFIG', {})
        if tele_cfg.get('notify_enable') and tele_cfg.get('main_bot_token'):
            try:
                requests.post(f"https://api.telegram.org/bot{tele_cfg.get('main_bot_token')}/sendMessage",
                              json={'chat_id': tele_cfg.get('chat_id'), 'text': msg, 'parse_mode': 'HTML'}, timeout=10)
            except Exception as e:
                print(f"Telegram Error: {e}")

        # 3. Discord
        disc_cfg = cfg.get('DISCORD_CONFIG', {})
        bot_token = disc_cfg.get('remote_bot_token')
        admin_id = disc_cfg.get('admin_id')
        if disc_cfg.get('notify_enable') and bot_token and admin_id:
            try:
                headers = {"Authorization": f"Bot {bot_token}", "Content-Type": "application/json"}
                res = requests.post("https://discord.com/api/v10/users/@me/channels", 
                                    json={"recipient_id": str(admin_id)}, headers=headers, timeout=10)
                if res.status_code == 200:
                    channel_id = res.json().get('id')
                    requests.post(f"https://discord.com/api/v10/channels/{channel_id}/messages",
                                  json={"content": f"🔔 **[Universal Notification]**\n{discord_msg}"}, headers=headers, timeout=10)
            except Exception as e:
                print(f"Discord Error: {e}")
        
        return True # <--- สำคัญมาก: ให้ฟังก์ชันส่งค่ากลับเสมอ
        
    except Exception as e:
        print(f"Critical _send_notify_sync Error: {e}")
        return False # <--- คืนค่า False เมื่อเกิดความผิดพลาด

# กำหนด Timezone ไทย
tz = pytz.timezone('Asia/Bangkok')

def get_now():
    """ฟังก์ชันกลางสำหรับดึงเวลาไทยปัจจุบัน"""
    return datetime.now(tz)
    
def load_data(path):
    if not os.path.exists(path): return set()
    with open(path, "r", encoding='utf-8') as f: return set(x.strip().lower() for x in f if x.strip())

def get_auth_file(site_key):
    """ส่งกลับชื่อไฟล์ auth แยกตามเว็บ เช่น auth_BEARBIT.json"""
    try:
        # ใช้ os.path.dirname เพื่อให้แน่ใจว่าเก็บในโฟลเดอร์เดียวกับสคริปต์
        BASE_DIR = os.path.dirname(os.path.abspath(__file__))
        auth_dir = os.path.join(BASE_DIR, "auth")
        
        # สร้างโฟลเดอร์
        os.makedirs(auth_dir, exist_ok=True)
            
        # ทำความสะอาด site_key ให้เป็นตัวพิมพ์ใหญ่และเปลี่ยนช่องว่างเป็น _
        clean_key = "".join(c for c in site_key if c.isalnum() or c in (' ', '.', '_'))
        clean_key = clean_key.strip().replace(' ', '_').upper()
        
        filename = f"auth_{clean_key}.json"
        return os.path.join(auth_dir, filename)
        
    except Exception as e:
        print(f"❌ [Auth] เกิดข้อผิดพลาดในการสร้างเส้นทางไฟล์: {e}")
        return None

def get_seen_file(site_key):
    """ส่งกลับชื่อไฟล์ seen แยกตามเว็บ เช่น seen_BEARBIT.txt"""
    # สร้างโฟลเดอร์สำหรับเก็บประวัติถ้ายังไม่มี
    history_dir = os.path.join(BASE_DIR, "history")
    if not os.path.exists(history_dir):
        os.makedirs(history_dir)
    return os.path.join(history_dir, f"seen_{site_key.upper()}.txt")

def is_already_seen(site_key, work_id):
    """เช็คว่า ID งานนี้เคยทำไปหรือยังในเว็บนั้นๆ"""
    filename = get_seen_file(site_key)
    if not os.path.exists(filename):
        return False
    with open(filename, 'r') as f:
        # ใช้ set เพื่อการค้นหาที่รวดเร็ว (เหมาะกับเน็ตแรงๆ งานเยอะๆ)
        seen_ids = set(line.strip() for line in f)
    return str(work_id) in seen_ids

def add_to_seen(site_key, work_id):
    """บันทึก ID งานลงในไฟล์ seen ของเว็บนั้น"""
    filename = get_seen_file(site_key)
    with open(filename, 'a') as f:
        f.write(f"{work_id}\n")

def get_hash_file(site_key):
    """ส่งกลับชื่อไฟล์ hash แยกตามเว็บ เช่น hash_seen_BEARBIT.txt"""
    history_dir = os.path.join(BASE_DIR, "history")
    if not os.path.exists(history_dir):
        os.makedirs(history_dir)
    return os.path.join(history_dir, f"hash_seen_{site_key.upper()}.txt")

def load_global_hashes(site_keys):
    """โหลด Hash จากทุกเว็บเข้า Memory ตอนเริ่มโปรแกรม เพื่อกันไฟล์ซ้ำข้ามค่าย"""
    for key in site_keys:
        filename = get_hash_file(key)
        if os.path.exists(filename):
            with open(filename, 'r') as f:
                for line in f:
                    global_seen_hashes.add(line.strip().lower())

def add_hash_to_site(site_key, t_hash):
    """บันทึก Hash ลงไฟล์แยกเว็บ และเพิ่มเข้า Global Memory"""
    t_hash = t_hash.lower()
    global_seen_hashes.add(t_hash)
    
    filename = get_hash_file(site_key)
    with open(filename, 'a') as f:
        f.write(f"{t_hash}\n")

def save_data(path, data):
    with open(path, "w", encoding='utf-8') as f: f.write("\n".join(sorted(list(data))))

def extract_info_hash(torrent_content):
    try:
        # ใช้ bencodepy ในการ decode ไฟล์ .torrent
        # ข้อมูลที่ได้จะเป็น OrderedDict หรือ dict ที่ key เป็น bytes
        metadata = bencodepy.decode(torrent_content)
        
        # เข้าถึงคีย์ 'info' ด้วย byte string b'info'
        info_data = metadata[b'info']
        
        # เข้ารหัสส่วน info กลับเป็น bencode เพื่อคำนวณ hash
        info_encoded = bencodepy.encode(info_data)
        
        # คำนวณ SHA1 Hash
        return hashlib.sha1(info_encoded).hexdigest().lower()
        
    except Exception as e:
        # หาก decode ไม่สำเร็จ แสดงว่าไฟล์อาจไม่ใช่ torrent หรือ corrupted
        print(f"DEBUG: ไม่สามารถสกัด Hash ได้: {e}")
        return None

def parse_size(size_str):
    """แปลง Text สถิติจากหน้าเว็บ (TB, GB, MB) ให้เป็นตัวเลขหน่วย GB"""
    try:
        if not size_str: return 0.0
        size_str = size_str.upper().replace(',', '').strip()
        # เพิ่มการรองรับ B, KB, และหน่วย iB
        match = re.search(r"([0-9.]+)\s*(TB|TIB|GB|GIB|MB|MIB|KB|KIB|B)", size_str)
        if not match: return 0.0
        
        num = float(match.group(1))
        unit = match.group(2)
        
        # กำหนด Factor โดยให้ GB = 1
        factors = {
            "TB": 1024, "TIB": 1024,
            "GB": 1, "GIB": 1,
            "MB": 1/1024, "MIB": 1/1024,
            "KB": 1/(1024**2), "KIB": 1/(1024**2),
            "B": 1/(1024**3)
        }
        return num * factors.get(unit, 1)
    except:
        return 0.0

def check_freeload_status(row):
    row_html = str(row)
    row_html_lower = row_html.lower()
    cells = row.find_all("td")
    
    # --- 1. ตรวจสอบจากรูปภาพไอคอน (ครอบคลุมเกือบทุกเว็บ) ---
    # ใช้ชื่อไฟล์ภาพเป็นตัวตัดสินหลัก เพราะเป็น Static Asset ที่เปลี่ยนยาก
    free_images = [
        "freeload.png", "freedownload.gif", "free_download", 
        "s-free", "free.gif", "free.png", "gold.gif", "free_silver.gif"
    ]
    if any(icon in row_html_lower for icon in free_images):
        return 100

    # --- 2. ตรวจสอบจาก CSS Class (Bootstrap Badge / Custom Tag) ---
    # เช่น TorrentDD หรือเว็บสมัยใหม่ที่ใช้ Badge
    if 'badge' in row_html_lower and 'free' in row_html_lower:
        # เช็คสีของ Badge (มักจะเป็น success, green, หรือสีทอง)
        if any(c in row_html_lower for c in ['success', 'green', 'gold']):
            return 100

    # --- 3. สแกนหาเปอร์เซ็นต์แบบ "ข้ามคอลัมน์ชื่อไฟล์" (สำคัญมาก) ---
    # เราจะวนลูปเช็คทุก Cell แต่จะใช้วิธี "กรองคอลัมน์ต้องสงสัย" ออก
    for i, cell in enumerate(cells):
        # ข้ามคอลัมน์ 0, 1, 2 (มักเป็น รูปหมวดหมู่, ตัวเลือก, และชื่อไฟล์)
        # ตัวเลข % โปรโมชั่นมักจะเริ่มที่คอลัมน์ 3 เป็นต้นไป ( index 3 )
        if i <= 2: 
            continue 
            
        cell_str = str(cell).lower()
        cell_text = cell.get_text(strip=True)
        
        # ก) เช็คจาก "สีข้อความ" (Inline Style)
        # ถ้าเจอตัวเลข % ที่อยู่ใน Tag สีเขียว ให้สันนิษฐานว่าเป็นค่า Free
        is_active_color = any(c in cell_str for c in ['color="green"', 'color: green', '#00ff00', 'success'])
        
        # ข) สกัดตัวเลข %
        pct_match = re.search(r"(\d+)\s*%", cell_text)
        if pct_match:
            val = int(pct_match.group(1))
            # ใส่ Sanity Check: เปอร์เซ็นต์ฟรีต้องไม่เกิน 100 (ป้องกันเลขตอน One Piece 1044)
            if val <= 100:
                # ถ้าเป็นสีเขียวด้วย ให้มั่นใจได้ 100%
                if is_active_color:
                    return val
                # ถ้าไม่ใช่สีเขียว แต่อยู่ในคอลัมน์โปรโมชั่น (3-8) ก็ยังพอเชื่อถือได้
                if 3 <= i <= 8:
                    return val

        # ค) เช็ค Keyword "Free" หรือ "ฟรี" ที่เป็นสีเขียว (กรณีไม่มีตัวเลข %)
        if is_active_color and any(w in cell_text for w in ["ฟรี", "free"]):
            return 100

    return 0

async def check_pending_status(session, details_url):
    """
    ตรวจสอบสถานะ (รอการอนุมัติ) โดยเข้าหน้ารายละเอียดโดยตรง
    """
    try:
        # 1. ต้องใช้ await เพราะเมธอด get ของ BrowserSessionWrapper เป็น async
        r = await session.get(details_url, timeout=10)
        
        # 2. ตรวจสอบว่า r ไม่ใช่ None (เผื่อกรณี get แล้วพัง)
        if r and hasattr(r, 'status_code') and r.status_code == 200:
            soup = BeautifulSoup(r.text, 'html.parser')
            page_text = soup.get_text()
            
            if "(รอการอนุมัติ)" in page_text or "รอการอนุมัติ" in page_text:
                return True
        return False
    except Exception as e:
        print(f"      ⚠️ Error checking pending status: {e}")
        return False

# ========================= BROWSER ENGINE =========================

def get_universal_browser_path():
    """ค้นหาตำแหน่งการติดตั้งเบราว์เซอร์ตระกูล Chromium ภายในเครื่องอย่างละเอียด"""
    current_os = platform.system().lower()
    
    if current_os == "windows":
        # ดึง Environment Variables ที่จำเป็นแบบปลอดภัย
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        local_app_data = os.environ.get("LOCALAPPDATA", os.path.expanduser(r"~\AppData\Local"))
        
        search_paths = [
            # Google Chrome
            os.path.join(program_files, "Google", "Application", "chrome.exe"),
            os.path.join(program_files_x86, "Google", "Application", "chrome.exe"),
            os.path.join(local_app_data, "Google", "Chrome", "Application", "chrome.exe"),
            # Microsoft Edge
            os.path.join(program_files, "Microsoft", "Edge", "Application", "msedge.exe"),
            os.path.join(program_files_x86, "Microsoft", "Edge", "Application", "msedge.exe"),
            # Brave Browser
            os.path.join(program_files, "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
            os.path.join(local_app_data, "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
        ]
        
        for path in search_paths:
            if os.path.exists(path):
                return path

    else: # Linux (Ubuntu, Debian, Mint, etc.)
        # 1. ลองหาผ่านระบบ PATH ของ OS ก่อน (ยืดหยุ่นที่สุด)
        executables = ["google-chrome-stable", "google-chrome", "chromium-browser", "chromium", "brave-browser"]
        for exe in executables:
            path = shutil.which(exe)
            if path:
                # ใช้ realpath เพื่อตาม Link ไปยังไฟล์ Binary จริงๆ
                real_path = os.path.realpath(path)
                if os.access(real_path, os.X_OK):
                    return real_path
        
        # 2. Fallback เผื่อไว้ในกรณีที่ PATH ไม่ครอบคลุม
        fallback_paths = [
            "/usr/bin/google-chrome-stable",
            "/usr/bin/google-chrome",
            "/usr/bin/chromium-browser",
            "/usr/bin/brave-browser",
            "/snap/bin/chromium", # เผื่อเป็น Ubuntu Snap pack
        ]
        for path in fallback_paths:
            if os.path.exists(path):
                return path
                
    return None

_global_display = None
_active_browser_instance = None #ตัวแปรเพื่อติดตาม instance
_current_profile_path = None #ตัวแปรเก็บ pathpath

async def launch_any_browser(custom_args=None):
    global _global_display, _active_browser_instance, _current_profile_path
    
    # 1. เคลียร์ Instance เก่า (เพิ่มการหน่วงเวลาเพื่อความเสถียร)
    if _active_browser_instance:
        try:
            await _active_browser_instance.stop()
            await asyncio.sleep(3) # รอให้ระบบเคลียร์ Process เก่า
        except:
            pass
        _active_browser_instance = None

    # 2. Xvfb Setup
    xvfb_exists = shutil.which("Xvfb") is not None
    if xvfb_exists and _global_display is None:
        print("🖥️ [System] กำลังเปิด Display เสมือน")
        _global_display = Display(visible=0, size=(1920, 1080))
        _global_display.start()
        await asyncio.sleep(2)

    # 3. ใช้ Config ของ nodriver
    browser_path = get_universal_browser_path()
    
    # กำหนด path ใหม่ทุกครั้งที่เปิด
    _current_profile_path = f"./uc_profile_{random.randint(100, 999)}"
    
    config = Config(
        browser_executable_path=browser_path,
        user_data_dir=_current_profile_path,
        headless=False
    )

    config.sandbox = False 
    config.no_sandbox = True # เพิ่มตัวนี้เพื่อกัน Error connect
    config.add_argument("--disable-dev-shm-usage")
    config.add_argument("--disable-gpu")
    
    if isinstance(custom_args, list):
        for arg in custom_args:
            config.add_argument(arg)

    # 4. รัน Browser
    try:
        _active_browser_instance = await uc.start(config=config)
        
        # ตั้งค่า Download Behavior
        await _active_browser_instance.send(
            uc.cdp.browser.set_download_behavior(
                behavior="deny",
                download_path="/dev/null"
            )
        )
        print(f"🚀 [System] Browser รันสำเร็จ")
        return _active_browser_instance

    except Exception as e:
        print(f"❌ [Critical] Browser Start Error: {e}")
        # ล้างการเชื่อมต่อทั้งหมดหากเปิดไม่สำเร็จ
        _active_browser_instance = None
        # ปิด Display หากค้าง
        if _global_display:
            try:
                _global_display.stop()
            except:
                pass
            _global_display = None
        raise e

def kill_specific_browser():
    global _active_browser_instance
    if _active_browser_instance and hasattr(_active_browser_instance, 'browser_pid'):
        pid = _active_browser_instance.browser_pid
        try:
            print(f"🔪 [System] กำลังตรวจสอบและฆ่า Browser PID: {pid}")
            
            if sys.platform == "win32":
                # Windows ใช้ taskkill สั่งฆ่าทั้ง Tree
                os.system(f"taskkill /F /PID {pid} /T")
            else:
                # Linux/Unix: เช็คก่อนว่า process ยังอยู่ไหม
                try:
                    os.kill(pid, 0) # ส่ง signal 0 เพื่อเช็คว่า pid ยังมีชีวิตอยู่
                    pgid = os.getpgid(pid)
                    os.killpg(pgid, signal.SIGKILL)
                    print(f"✅ [System] ฆ่า Browser PID {pid} และลูกหลานเรียบร้อย")
                except ProcessLookupError:
                    print(f"ℹ️ PID {pid} ไม่พบในระบบ (อาจปิดไปแล้ว)")
                    
        except Exception as e:
            print(f"⚠️ ไม่สามารถฆ่า PID {pid}: {e}")
        finally:
            # ไม่ว่าจะฆ่าสำเร็จหรือไม่ ต้องเคลียร์ตัวแปรทิ้งเสมอ
            _active_browser_instance = None

async def cleanup_profile():
    global _current_profile_path
    if _current_profile_path and os.path.exists(_current_profile_path):
        try:
            # 1. ใส่ await ให้กับ asyncio.sleep
            await asyncio.sleep(2) 
            
            # 2. ใช้ shutil.rmtree
            shutil.rmtree(_current_profile_path, ignore_errors=False)
            print(f"🧹 [System] ลบ Profile สำเร็จ: {_current_profile_path}")
        except Exception as e:
            print(f"⚠️ ลบ Profile ไม่ได้ในรอบนี้ (อาจติด Lock): {e}")
        finally:
            _current_profile_path = None

def kill_xvfb():
    global _global_display
    if _global_display and hasattr(_global_display, 'pid'):
        pid = _global_display.pid
        try:
            print(f"🖥️ [System] กำลังปิด Xvfb (PID: {pid})...")
            # ฆ่าตาม PID ที่เก็บไว้
            os.kill(pid, signal.SIGKILL)
        except Exception as e:
            print(f"⚠️ ไม่สามารถปิด Xvfb (PID {pid}): {e}")
        finally:
            _global_display = None
    else:
        # กรณีไม่มี PID ใน object ให้ลองสั่ง pkill ทั่วไป (เผื่อกรณีค้าง)
        os.system("pkill -9 -f Xvfb")

async def load_cookies_to_browser(tab, site_cfg):
    auth_path = get_auth_file(site_cfg['name'])
    if not auth_path or not os.path.exists(auth_path):
        return
        
    with open(auth_path, "r") as f:
        cookies = json.load(f)
    
    for cookie in cookies:
        try:
            # ใช้พารามิเตอร์ที่ครบถ้วนและปลอดภัย
            await tab.send(uc.cdp.network.set_cookie(
                name=cookie.get('name'),
                value=cookie.get('value'),
                domain=cookie.get('domain'),
                path=cookie.get('path', '/'),
                secure=cookie.get('secure', False), # เปลี่ยนเป็น False กรณีเข้าผ่าน HTTP
                http_only=cookie.get('httpOnly', False)
            ))
        except Exception as e:
            # บางครั้งคุกกี้ที่พังๆ ไม่ควรหยุดทั้งการทำงาน
            continue
    print(f"✅ [Auth] โหลด Cookie เข้า Browser สำเร็จ")

def create_robust_scraper():
    # 1. สร้าง scraper ปกติ
    scraper = cloudscraper.create_scraper(
        browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
    )
    
    # 2. สร้าง SSL Context แบบไม่เช็คอะไรเลย
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    # 3. สร้าง Adapter โดยใช้ PoolManager ที่มี ssl_context
    class SSLAdapter(HTTPAdapter):
        def __init__(self, ssl_context=None, **kwargs):
            self.ssl_context = ssl_context
            super().__init__(**kwargs)

        def init_poolmanager(self, connections, maxsize, block=False):
            self.poolmanager = PoolManager(
                num_pools=connections, maxsize=maxsize, block=block,
                ssl_context=self.ssl_context
            )

    # 4. ใช้ SSLAdapter ที่เตรียมไว้
    adapter = SSLAdapter(ssl_context=ctx)
    
    # การ mount ต้องระวัง: การ mount ทับ 'https://' จะแทนที่ adapter เดิมของ cloudscraper
    # วิธีที่ปลอดภัยกว่าคือการให้ cloudscraper จัดการตัวมันเอง 
    # แต่ถ้าจำเป็นต้องใช้ SSLAdapter เพื่อข้าม SSL ปัญหา ก็ต้อง mount แบบนี้ครับ
    scraper.mount('https://', adapter)
    
    return scraper
    
# ========================= NODE CLASSES =========================

class QbitNode:
    def __init__(self, cfg):
        self.name, self.url = cfg["name"], cfg["url"].rstrip("/")
        self.user, self.pw = cfg["qb_user"], cfg["qb_pass"]
        self.quota_gb = cfg.get("quota_gb", 0)
        self.auth = HTTPBasicAuth(self.user, self.pw) if cfg.get("nginx") else None
        self.s = requests.Session()
        self.free_gb = 0
        self.is_connected = False
        self.jobs = 0
        self.stat_msg = "Active/Total: 0/0"

    def login(self):
        try:
            # 1. ล้างคุกกี้เก่าทิ้งก่อนเริ่มใหม่ เพื่อป้องกัน Session ทับซ้อน
            self.s.cookies.clear()
            
            # 2. เพิ่ม Header Referer (เวอร์ชัน 5.x.x บางครั้งต้องการเพื่อป้องกัน CSRF)
            headers = {'Referer': self.url}
            
            r = self.s.post(
                f"{self.url}/api/v2/auth/login", 
                data={"username": self.user, "password": self.pw}, 
                headers=headers,
                auth=self.auth, 
                verify=False, 
                timeout=10
            )

            # 3. เช็คเงื่อนไขความสำเร็จที่กว้างขึ้น
            # - 5.1.4 มักตอบ 200 "Ok."
            # - 5.2.0 มักตอบ 204 (No Content) และไม่มี Text
            if r.status_code in [200, 204]:
                # ตรวจสอบคุกกี้แบบเจาะจง (5.2.0 จะส่ง SID หรือ QBT_SID_xxxx)
                has_cookie = any("SID" in cookie.name for cookie in self.s.cookies)
                
                # ถ้ามีคุกกี้ หรือใน r.text มีคำว่า Ok (สำหรับรุ่นเก่า)
                self.is_connected = has_cookie or "Ok." in r.text
            else:
                self.is_connected = False
                
            if self.is_connected:
                print(f" ✅ [{self.name}] Login Success (v{r.status_code})")
            return self.is_connected

        except Exception as e:
            print(f" ⚠️ [{self.name}] Login Error: {e}")
            self.is_connected = False
            return False

    def refresh_status(self):
        if not self.is_connected: return False
        try:
            # 1. ดึงข้อมูลระบบหลัก (Maindata) พร้อมเสริมเกราะ Retry ลูปสั้น
            r_main = None
            for attempt in range(3):
                try:
                    r_main = self.s.get(f"{self.url}/api/v2/sync/maindata", auth=self.auth, verify=False, timeout=10)
                    break
                except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
                    if attempt < 2:
                        time.sleep(1.0)
                    else:
                        print(f"⚠️ [{self.name}] อัปเดต Maindata ไม่สำเร็จ (ใช้ค่าสถานะเดิมชั่วคราว)")
                        return True  # ใช้ค่าเก่าประคองตัวลูปหลัก

            # ตรวจสอบเซสชันหลุด (401/403)
            if r_main.status_code in [401, 403]:
                print(f" 🔄 [{self.name}] qBittorrent Session expired ({r_main.status_code}), re-logging in...")
                if self.login(): return False

            try:
                main_data = r_main.json()
            except Exception:
                print(f" ⚠️ [{self.name}] Response is not JSON. Re-logging in...")
                if self.login(): return False
                return False
                
            server_state = main_data.get('server_state', {})
            
            # 2. ดึงลิสต์ทอร์เรนต์ทั้งหมด พร้อมเกราะ Retry ลูปสั้น
            r_torrents = None
            for attempt in range(3):
                try:
                    r_torrents = self.s.get(f"{self.url}/api/v2/torrents/info", auth=self.auth, verify=False, timeout=10)
                    break
                except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
                    if attempt < 2:
                        time.sleep(1.0)
                    else:
                        print(f"⚠️ [{self.name}] อัปเดตข้อมูลทอร์เรนต์ไม่สำเร็จ (ใช้ค่าสถานะเดิมชั่วคราว)")
                        return True

            if r_torrents.status_code in [401, 403]:
                if self.login(): return False

            try:
                torrents = r_torrents.json()
            except Exception:
                if self.login(): return False
                return False
            
            # 3. คำนวณข้อมูลในลูปเดียวเพื่อความรวดเร็ว
            used_bytes = 0
            active_count = 0
            
            # กลุ่มสถานะหยุดนิ่ง/รอคิว (ไม่นับเป็น Active)
            inactive_states = {
                'pausedDL', 'pausedUP', 
                'queuedDL', 'queuedUP', 
                'checkingResumeData', 
                'stalledUP'
            }
            
            for t in torrents:
                state = t.get('state', '')
                size = t.get('total_size', t.get('size', 0))
                completed_bytes = t.get('completed', 0)
                
                downloading_states = {'downloading', 'stalledDL', 'metaDL', 'allocating', 'forcedDL'}
                
                # คิดขนาดพื้นที่บนดิสก์จริง
                if 'checking' in state.lower():
                    current_on_disk = size
                elif state in downloading_states:
                    current_on_disk = completed_bytes
                else:
                    current_on_disk = size

                used_bytes += current_on_disk
                
                # นับจำนวน Active Torrent เฉพาะตัวที่กำลังทำงานจริง
                if state not in inactive_states:
                    active_count += 1

            # แปลงหน่วยเป็น GB
            used_gb = used_bytes / (1024**3)
            safety_buffer = 15.0

            # 4. คำนวณพื้นที่ว่างและการดึงข้อมูล Disk Free ในมาตรฐานเดียวกับ rTorrent (สายซิ่ง ไม่หัก pending ค้าง)
            if self.quota_gb > 0:
                my_quota_free = max(0, self.quota_gb - used_gb)
                display_free = my_quota_free
                
                # 🎯 สูตร Safe เวอร์ชันปลดล็อกความเร็วในการ Racing (หักแค่บัฟเฟอร์ 15GB กันเหนียว)
                self.free_gb = max(0, my_quota_free - safety_buffer)
            else:
                # เคสไม่มีโควตา ดึงเนื้อที่ว่างจริงจากตัวเครื่องแม่
                real_disk_free = server_state.get('free_space_on_disk', 0) / (1024**3)
                display_free = real_disk_free
                self.free_gb = max(0, real_disk_free - safety_buffer)

            # 5. ประกอบร่างข้อความแสดงผลใหม่ (Format เดียวกับ rTorrent เป๊ะๆ)
            if self.quota_gb > 0:
                self.stat_msg = f"FREE: {display_free:.1f}GB | A: {active_count} | Used: {used_gb:.1f}G / {self.quota_gb:.0f}G | Safe: {self.free_gb:.1f}G"
            else:
                self.stat_msg = f"FREE: {display_free:.1f}GB | A: {active_count} | Used: {used_gb:.1f}G | Safe: {self.free_gb:.1f}G"
                
            return True
            
        except Exception as e:
            print(f"⚠️ [{self.name}] qBittorrent Refresh Status Error: {e}")
            return False

    def add(self, content, site_name="Universal", size=None, n_cfg=None):
        try:
            if len(content) < 1000: return False

            # เตรียมไฟล์ในรูปแบบ Multipart
            files = {"torrents": ("f.torrent", content, "application/x-bittorrent")}

            # data สำหรับ API qBittorrent
            data = {
                "paused": "false",
                "firstLastPiecePrio": "true",
                "sequentialDownload": "false", # ปิดไว้เพื่อให้กระจายขอชิ้นส่วนไฟล์พร้อมกัน รีดสปีดเน็ตเวิร์กได้เต็มข้อ
                "category": site_name,
                "tags": "AutoPilot",
                "autoTMM": "false" # บังคับให้ใช้ Path ที่เราคุมเอง (ถ้ามีระบุเพิ่ม)
            }

            # เพิ่ม Referer ป้องกัน CSRF สำหรับ qBit 5.2.0+
            headers = {'Referer': self.url}

            r = self.s.post(
                f"{self.url}/api/v2/torrents/add",
                files=files,
                data=data,
                headers=headers,
                auth=self.auth,
                verify=False,
                timeout=30
            )

            # เช็คความสำเร็จ: 200 คือผ่าน
            if r.status_code == 200:
                return True
            else:
                # กรณี 401, 403 ให้ลองสั่ง Login ใหม่ทันทีเผื่อ Session หลุด
                if r.status_code in [401, 403]:
                    self.login()
                print(f"⚠️ [API Error] {self.name}: {r.status_code} - {r.text}")
                return False

        except Exception as e:
            print(f"❌ [Exception] {self.name}: {str(e)}")
            return False

    def get_all_torrents_info(self):
        try:
            # 🔥 ปลดล็อกฟิลเตอร์จาก 'completed' เป็น 'all' เพื่อดึงข้อมูลครอบคลุมทุกสภาวะดิสก์เอ๋อ
            r = self.s.get(
                f"{self.url}/api/v2/torrents/info", 
                params={'filter': 'all'},  # <--- เปลี่ยนเป็น ALL เพื่อกวาดมาให้หมดเกลี้ยงเครื่อง!
                auth=self.auth, 
                verify=False, 
                timeout=15 
            )
        
            if r.status_code == 200:
                try:
                    data = r.json()
                except:
                    return []

                data.sort(key=lambda x: x.get('ratio', 0), reverse=True)

                results = []
                for t in data:
                    size_bytes = t.get('total_size', t.get('size', 0))
                
                    # แมปปิ้งคีย์สำรองให้ครบถ้วน เพื่อส่งต่อเข้าปากระบบควบคุมพื้นที่ได้แบบไม่มีพลาด
                    results.append({
                        'hash': t.get('hash'),
                        'ratio': t.get('ratio', 0),
                        'name': t.get('name', 'Unknown'),
                        'size': size_bytes / (1024**3), 
                        'size_bytes': size_bytes,
                        'amount_left': t.get('amount_left', t.get('left', -1)),
                        'progress': t.get('progress', 0.0),
                        'state': t.get('state', t.get('status', 'unknown')),
                        'added_on': t.get('added_on'),
                        'category': t.get('category') 
                    })
                return results
            
            elif r.status_code in [401, 403]:
                self.is_connected = False 
            
            return []
        except Exception as e:
            return []

    def is_torrent_exists(self, t_hash):
        if not self.is_connected: self.login()
        try:
            # ตรวจสอบจาก hash โดยตรงผ่าน API ของ qBittorrent
            r = self.s.get(f"{self.url}/api/v2/torrents/info", params={'hashes': t_hash}, auth=self.auth, timeout=10)
            return r.status_code == 200 and len(r.json()) > 0
        except: return False

    def delete_torrent(self, hash_str):
        try:
            self.s.post(f"{self.url}/api/v2/torrents/delete", data={"hashes": hash_str, "deleteFiles": "true"}, auth=self.auth, verify=False, timeout=10)
            return True
        except: return False

    def get_downloading_size(self):
        try:
            # 1. เพิ่ม auth และ verify เพื่อให้ผ่าน Nginx และ SSL ของ AppBox
            # 2. ใช้ params เพื่อกรองเฉพาะตัวที่กำลังโหลด (ลดภาระ CPU/Network)
            r = self.s.get(
                f"{self.url}/api/v2/torrents/info", 
                params={'filter': 'downloading'}, 
                auth=self.auth, 
                verify=False, 
                timeout=10
            )
            
            if r.status_code == 200:
                torrents = r.json()
                # amount_left คือ Bytes ที่เหลือ | size คือขนาดเต็ม (กรณีจองพื้นที่แบบ Pre-allocate)
                # ในสาย Racing เรามักสน amount_left เพื่อดูว่าดิสก์จะลดลงอีกเท่าไหร่
                total_left = sum(t.get('amount_left', 0) for t in torrents)
                
                # หากต้องการความปลอดภัยสูงสุด (เผื่อกรณีไฟล์ Error แล้วต้องโหลดใหม่ทั้งหมด)
                # สามารถพิจารณาใช้ t.get('size', 0) แทนได้ในบางกรณี
                
                return total_left / (1024**3) # แปลงเป็น GB
            
            # กรณี Token หมดอายุ (403) หรือ Error อื่นๆ
            return 0.0
        except Exception as e:
            # print(f" ⚠️ [{self.name}] get_downloading_size error: {e}")
            return 0.0
            
    def get_active_downloads(self):
        try:
            if not self.is_connected: self.login()

            results = []
            # ใช้การวน Loop ดึงทั้ง downloading และ checking
            for filter_type in ['downloading', 'checking']:
                r = self.s.get(f"{self.url}/api/v2/torrents/info", params={'filter': filter_type}, auth=self.auth, verify=False, timeout=10)

                if r.status_code == 200 and r.text:
                    try:
                        torrents = r.json()
                        for t in torrents:
                            results.append({
                                'hash': t.get('hash'),
                                'size_bytes': t.get('size', 0),
                                'state': t.get('state'),
                                'amount_left': t.get('amount_left', 0)
                            })
                    except:
                        continue # ถ้า Parse JSON ไม่ได้ให้ข้ามไปก่อน
                elif r.status_code in [401, 403]:
                    self.is_connected = False # สั่งให้ Login ใหม่ในรอบหน้า

            return results
        except Exception as e:
            self.is_connected = False
            return []

    def _sweeper_force_start(self):
        """ระบบกวาดงานค้างอัตโนมัติ"""
        if not self.is_connected and not self.login():
            return False

        try:
            # ดึงเฉพาะงานที่ไม่ได้รัน (pausedUP, pausedDL, queuedUP, queuedDL, stalledUP)
            # เราใช้ filter 'paused' จะครอบคลุมกรณีที่ Client สั่งหยุดคิวไว้
            r = self.s.get(f"{self.url}/api/v2/torrents/info", params={'filter': 'paused'}, auth=self.auth, verify=False, timeout=10)
            
            if r.status_code == 200:
                torrents = r.json()
                # กรองเอาเฉพาะ Hash ของงานที่สถานะเป็น 'pausedUP' หรือ 'pausedDL' 
                # (ถ้างานไหนเราตั้งใจหยุดเอง พี่อาจต้องเช็ค tag เพิ่มเติม)
                hashes_to_resume = [t['hash'] for t in torrents if t.get('state') in ['pausedUP', 'pausedDL', 'queuedUP', 'queuedDL']]
                
                if hashes_to_resume:
                    # สั่ง Resume เป็นชุดเพื่อลดการยิง API ถี่เกินไป
                    self.s.post(
                        f"{self.url}/api/v2/torrents/resume", 
                        data={"hashes": "|".join(hashes_to_resume)}, 
                        auth=self.auth, headers={'Referer': self.url}, verify=False, timeout=10
                    )
                    # print(f"🔄 [{self.name}] Sweeper resumed {len(hashes_to_resume)} torrents.")
        except Exception:
            pass

    def reannounce_all(self):
        """ สั่ง Re-announce ทุก Torrent (หรือเฉพาะที่กำลังโหลด) """
        if not self.is_connected and not self.login(): 
            return False
            
        try:
            # เพิ่ม Referer ป้องกัน CSRF สำหรับ 5.2.0+
            headers = {'Referer': self.url}
            
            # การส่ง hashes: all คือวิธีที่เร็วที่สุด แต่ต้องมั่นใจว่า Tracker ไม่แบน
            r = self.s.post(
                f"{self.url}/api/v2/torrents/reannounce", 
                data={"hashes": "all"}, 
                headers=headers,
                auth=self.auth, 
                verify=False, 
                timeout=15
            )
            
            if r.status_code == 200:
                # print(f" ✅ [{self.name}] Re-announced all torrents.")
                return True
            else:
                # ถ้าเจอ 403/401 ให้หลุดไป Login ใหม่
                if r.status_code in [401, 403]:
                    self.is_connected = False
                return False
        except Exception as e:
            # print(f" ⚠️ [{self.name}] Re-announce Error: {e}")
            return False

    def get_stats_by_site(self):
        # 1. เช็คการเชื่อมต่อก่อนเริ่ม
        if not self.is_connected and not self.login(): 
            return {}
            
        try:
            # 2. เพิ่ม auth และ verify สำหรับ Nginx/SSL
            r = self.s.get(
                f"{self.url}/api/v2/torrents/info", 
                auth=self.auth, 
                verify=False, 
                timeout=15 # สถิติรวมอาจใช้เวลาประมวลผลนานกว่าปกติ
            )
            
            if r.status_code != 200:
                if r.status_code in [401, 403]: self.is_connected = False
                return {}

            torrents = r.json()
            site_stats = {}
            
            for t in torrents:
                # 3. ใช้ Category เป็นตัวแยกชื่อเว็บ (เช่น BEARBIT, TORRENTDD)
                # ถ้าไม่มี Category ให้ลงถัง "General" หรือ "Unknown"
                site = t.get('category') or "Uncategorized"
                
                # ดึงค่าความเร็วอัปโหลดปัจจุบัน และยอดอัปโหลดรวม (Bytes)
                up_speed = t.get('upspeed', 0)
                total_up = t.get('uploaded', 0)
                downloaded = t.get('downloaded', 0) # แถม: เก็บยอดดาวน์โหลดไว้ดู Ratio ราย Site ก็ได้

                if site not in site_stats:
                    site_stats[site] = {
                        'total_up_bytes': 0, 
                        'total_dl_bytes': 0,
                        'current_speed_bytes': 0, 
                        'count': 0
                    }
            
                site_stats[site]['total_up_bytes'] += total_up
                site_stats[site]['total_dl_bytes'] += downloaded
                site_stats[site]['current_speed_bytes'] += up_speed
                site_stats[site]['count'] += 1
                
            return site_stats
        except Exception as e:
            # print(f"⚠️ [{self.name}] Stats Error: {e}")
            return {}

class RtorrentNode:
    def __init__(self, cfg):
        self.name, self.url = cfg["name"], cfg["url"].rstrip("/")
        self.user, self.pw = cfg["rt_user"], cfg["rt_pass"]
        self.quota_gb = cfg.get("quota_gb", 0)
        
        # สร้าง Session ไว้ใช้ยาวๆ ลด Overhead การสร้าง Connection ใหม่
        self.s = requests.Session() 
        
        self.auth = HTTPBasicAuth(self.user, self.pw)
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0 Safari/537.36',
            'Content-Type': 'text/xml'
        }
        self.free_gb = 0
        self.jobs = 0
        self.is_connected = False
        self.stat_msg = "Active/Total: 0/0"

    def login(self):
        try:
            # 1. ยิงทดสอบด้วยรหัสที่มีอยู่ (เริ่มต้นด้วย BasicAuth)
            # ใช้ system.listMethods เป็นคำสั่งที่เบาที่สุดในการเช็คสิทธิ์
            r = self.s.post(
                self.url, 
                data='<?xml version="1.0"?><methodCall><methodName>system.listMethods</methodName></methodCall>', 
                auth=self.auth, 
                headers=self.headers,
                timeout=10,
                verify=False
            )
            
            # 2. กรณี 401 Unauthorized: เช็คว่าต้องการ Digest หรือไม่
            if r.status_code == 401:
                auth_header = r.headers.get('WWW-Authenticate', '').lower()
                if 'digest' in auth_header:
                    # สลับไปใช้ Digest Auth และยิงใหม่
                    self.auth = HTTPDigestAuth(self.user, self.pw)
                    r = self.s.post(
                        self.url, 
                        data='<?xml version="1.0"?><methodCall><methodName>system.listMethods</methodName></methodCall>', 
                        auth=self.auth, 
                        headers=self.headers,
                        timeout=10,
                        verify=False
                    )
            
            # 3. ตัดสินผลการเชื่อมต่อ
            if r.status_code == 200:
                self.is_connected = True
                return True
            else:
                self.is_connected = False
                # print(f"⚠️ [{self.name}] Login Failed: {r.status_code}")
                return False
                
        except requests.exceptions.RequestException as e:
            self.is_connected = False
            # print(f"❌ [{self.name}] Connection Error: {e}")
            return False

    def refresh_status(self):
        if not self.is_connected: return False
        try:
            # 1. ยิง XML-RPC ดึง 3 ฟิลด์สำคัญ
            xml = (
                '<?xml version="1.0"?>'
                '<methodCall>'
                '<methodName>d.multicall2</methodName>'
                '<params>'
                '<param><value><string></string></value></param>'
                '<param><value><string>main</string></value></param>'
                '<param><value><string>d.is_active=</string></value></param>'
                '<param><value><string>d.size_bytes=</string></value></param>'
                '<param><value><string>d.bytes_done=</string></value></param>'
                '</params>'
                '</methodCall>'
            )
        
            # 🔥 วนลูป Retry สั้นป้องกัน Timeout
            r = None
            for attempt in range(3):
                try:
                    r = self.s.post(self.url, data=xml, auth=self.auth, headers=self.headers, timeout=10, verify=False)
                    break
                except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
                    if attempt < 2:
                        time.sleep(1.0)
                    else:
                        print(f"⚠️ [{self.name}] อัปเดตโหลดไม่สำเร็จเนื่องจากเน็ตเวิร์กขัดข้อง (ใช้ค่าสถานะเดิมชั่วคราว)")
                        return True 
        
            if r.status_code in [401, 403]:
                print(f" 🔄 [{self.name}] rTorrent Session expired ({r.status_code}), re-logging in...")
                if self.login(): return False
        
            soup = BeautifulSoup(r.text, "xml")
        
            active = 0
            used_bytes = 0
            raw_vals = []
        
            torrent_nodes = soup.find_all("data")
            if len(torrent_nodes) > 1:
                for node in torrent_nodes[1:]:
                    items = node.find_all("value", recursive=False)
                    if len(items) == 3:
                        try:
                            val_active = int(items[0].get_text().strip())
                            val_size   = int(items[1].get_text().strip())
                            val_done   = int(items[2].get_text().strip())
                            raw_vals.extend([val_active, val_size, val_done])
                        except ValueError:
                            pass
        
            if not raw_vals:
                for val in soup.find_all("value"):
                    if not val.find():
                        try:
                            raw_vals.append(int(val.get_text().strip()))
                        except ValueError:
                            pass
    
            for i in range(0, len(raw_vals), 3):
                vals = raw_vals[i:i+3]
                if len(vals) == 3:
                    is_active  = vals[0]
                    bytes_done = vals[2]
                
                    if is_active == 1:
                        active += 1
                    
                    used_bytes += bytes_done

            # แปลงหน่วยปริมาณทอร์เรนต์ในเครื่องเป็น GB
            used_gb = used_bytes / (1024**3)
            safety_buffer = 15.0

            # 3. คำนวณพื้นที่ว่างและการดึงข้อมูล Disk Free อิงตามโควตา
            if self.quota_gb > 0:
                my_quota_free = max(0, self.quota_gb - used_gb)
                display_free = my_quota_free
            
                # 🎯 สูตรสายซิ่ง: หักแค่บัฟเฟอร์กันตาย 15GB ไม่หัก pending_gb ซ้ำซ้อน
                self.free_gb = max(0, my_quota_free - safety_buffer)
            else:
                xml_disk = '<?xml version="1.0"?><methodCall><methodName>network.disk_free_4gb</methodName></methodCall>'
                r_free = None
                for attempt in range(3):
                    try:
                        r_free = self.s.post(self.url, data=xml_disk, auth=self.auth, headers=self.headers, timeout=10, verify=False)
                        break
                    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
                        if attempt < 2: time.sleep(1.0)
                        else: return True

                real_free = 0.0
                if r_free:
                    free_soup = BeautifulSoup(r_free.text, "xml")
                    free_node = free_soup.find(["i8", "int", "i4", "value"])
                    if free_node:
                        try:
                            real_free = (int(free_node.get_text().strip()) * 4096) / (1024**3)
                        except Exception:
                            pass
            
                display_free = real_free
                self.free_gb = max(0, real_free - safety_buffer)

            # 4. ประกอบร่างข้อความแสดงผลใหม่มาตรฐานเดียวกัน
            self.stat_msg = f"FREE: {display_free:.1f}GB | A: {active} | Used: {used_gb:.1f}G / {self.quota_gb:.0f}G | Safe: {self.free_gb:.1f}G"
            return True
        
        except Exception as e:
            print(f"⚠️ [{self.name}] rTorrent Refresh Status Error: {e}")
            return False

    def get_all_torrents_info(self):
        try:
            # ⚡ [Unified Payload]: ดึงค่าครบทุกสล็อตเพื่อเอาไปให้ตัวเคลียร์คำนวณได้อย่างอิสระ
            xml = '''<?xml version="1.0"?>
            <methodCall>
            <methodName>d.multicall2</methodName>
            <params>
                <param><value><string></string></value></param>
                <param><value><string>main</string></value></param>
                <param><value><string>d.hash=</string></value></param>
                <param><value><string>d.ratio=</string></value></param>
                <param><value><string>d.complete=</string></value></param>
                <param><value><string>d.name=</string></value></param>
                <param><value><string>d.size_bytes=</string></value></param>
                <param><value><string>d.left_bytes=</string></value></param>
                <param><value><string>d.timestamp.finished=</string></value></param>
                <param><value><string>d.state=</string></value></param>
            </params>
            </methodCall>'''

            req_headers = getattr(self, 'headers', {}).copy()
            if "Connection" not in req_headers: 
                req_headers["Connection"] = "close"

            r = self.s.post(self.url, data=xml, auth=self.auth, headers=req_headers, timeout=20, verify=False)
            if r.status_code != 200: return []

            import xml.etree.ElementTree as ET
            root = ET.fromstring(r.text)
            data = root.findall(".//value/array/data/value/array/data")

            results = []
            for item in data:
                values = item.findall("./value")
                if len(values) < 7: continue 

                def safe_get_text(val_node, tag_list=["./string", "./i4", "./int"]):
                    if val_node is None: return ""
                    for tag in tag_list:
                        target = val_node.find(tag)
                        if target is not None and target.text is not None:
                            return target.text.strip()
                    return val_node.text.strip() if val_node.text else ""

                t_hash = safe_get_text(values[0])
                t_ratio_str = safe_get_text(values[1])
                t_complete_str = safe_get_text(values[2])
                t_name = safe_get_text(values[3])
                t_size_str = safe_get_text(values[4])
                t_left_str = safe_get_text(values[5])
                t_ts_finished_str = safe_get_text(values[6])
                t_state_str = safe_get_text(values[7]) if len(values) > 7 else "1"

                # 🎯 ปลดล็อก: ส่งข้อมูลดิบออกไปทั้งหมด ไม่ใช้คำสั่ง continue เตะงานทิ้งกลางคัน
                # ย้ายการตัดสินใจเรื่องความเสร็จสมบูรณ์ไปให้ฟังก์ชันสลัดดิสก์ภายนอกจัดการ
                is_complete_flag = (t_complete_str == "1")
                left_bytes = int(t_left_str) if t_left_str.isdigit() else 0

                if t_hash:
                    try:
                        ratio_val = int(t_ratio_str) / 1000.0 if t_ratio_str.isdigit() else 0.0
                        if ratio_val < 0: ratio_val = 0.0
                        size_bytes = int(t_size_str) if t_size_str.isdigit() else 0
                        ts_finished = int(t_ts_finished_str) if t_ts_finished_str.isdigit() else 0
                    
                        if ts_finished <= 0:
                            age_hours = 99.0 
                        else:
                            age_hours = (time.time() - ts_finished) / 3600.0

                    except Exception:
                        ratio_val = 0.0
                        size_bytes = 0
                        age_hours = 0.0

                    # แปลงสถานะตัวเลขของ rTorrent ให้เป็นข้อความล้อไปกับลักษณะของ qBit
                    mapped_state = "seeding" if is_complete_flag else "downloading"
                    if t_state_str == "0": mapped_state = "paused"

                    results.append({
                        'hash': t_hash,
                        'ratio': ratio_val,
                        'name': t_name,
                        'size': size_bytes / (1024**3), 
                        'size_bytes': size_bytes,
                        'amount_left': left_bytes,
                        'age_hours': age_hours,         
                        'progress': 1.0 if is_complete_flag else 0.0,
                        'state': mapped_state
                    })
                
            results.sort(key=lambda x: x.get('ratio', 0), reverse=True)
            return results

        except Exception as e:
            print(f"❌ rTorrent Fetch Info Error: {e}")
            return []

    def is_torrent_exists(self, t_hash):
        if not self.is_connected: self.login()
        try:
            # ใช้ XML-RPC ตรวจสอบชื่อไฟล์หรือ hash (ในที่นี้ใช้ hash ซึ่งแม่นยำที่สุด)
            xml = f'<?xml version="1.0"?><methodCall><methodName>d.name</methodName><params><param><value><string>{t_hash.upper()}</string></value></param></params></methodCall>'
            r = self.s.post(self.url, data=xml, auth=self.auth, headers=self.headers, timeout=10, verify=False)
            # ถ้า rTorrent คืนค่าสำเร็จ (ไม่ error) แสดงว่ามีไฟล์อยู่
            return r.status_code == 200 and "<fault>" not in r.text
        except: return False

    def get_downloading_size(self):
        try:
            import xmlrpc.client
            auth_url = self.url.replace("://", f"://{self.user}:{self.pw}@")
            proxy = xmlrpc.client.ServerProxy(auth_url)
            # ดึง size และ completed เฉพาะตัวที่ยังโหลดไม่เสร็จ (view 'started')
            response = proxy.d.multicall2("", "started", "d.size_bytes=", "d.completed_bytes=")
        
            total_remaining = sum(int(t[0]) - int(t[1]) for t in response)
            return total_remaining / (1024**3)
        except:
            return 0.0
                    
    def get_active_downloads(self):
        """ดึงรายการที่กำลังโหลด โดยเปลี่ยนไปใช้ view 'main' เพื่อเลี่ยง Error 503"""
        try:
            import xmlrpc.client
            # ผสม Auth ลงใน URL
            auth_url = self.url.replace("://", f"://{self.user}:{self.pw}@")
            proxy = xmlrpc.client.ServerProxy(auth_url)

            # ดึงจาก view "main" ซึ่งเป็นมาตรฐานของ rTorrent ทุกเวอร์ชั่น
            token = ""
            # เพิ่ม d.get_complete= เพื่อเช็คว่าตัวไหนยังโหลดไม่เสร็จ
            params = ("main", "d.hash=", "d.size_bytes=", "d.complete=")
            response = proxy.d.multicall2(token, *params)

            results = []
            for t in response:
                # d.complete == 0 หมายถึงกำลังดาวน์โหลด (หรือยังโหลดไม่เสร็จ)
                if int(t[2]) == 0:
                    results.append({
                        'hash': t[0],
                        'size_bytes': int(t[1]),
                        'state': 'downloading'
                    })
            return results
        except Exception as e:
            # หากเกิด Error ให้พยายาม Login ใหม่ในรอบหน้า
            self.is_connected = False
            print(f"❌ [{self.name}] rTorrent Error: {e}")
            return []

    def _xml_escape(self, data):
        """ใช้ Library มาตรฐานเพื่อความชัวร์"""
        if not isinstance(data, str):
            data = str(data)
        # เพิ่มการลบอักขระควบคุม (ASCII 0-31) ที่อาจทำให้ XML พัง
        data = "".join(ch for ch in data if ord(ch) >= 32 or ch in "\n\r\t")
        return escape(data, {'"': '&quot;', "'": '&apos;'})
    
    def safe_xml_escape(self, data):
        try:
            return self._xml_escape(data)
        except Exception:
            return str(data).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def add(self, content, site_name="Universal", size=None, n_cfg=None):
        if len(content) < 1000:
            print(f"❌ [{self.name}] Torrent file is too small or invalid.")
            return False

        info_hash = None
        try:
            info_start = content.find(b'4:infod')
            if info_start != -1:
                pos = info_start + 7
                # ... (ใส่ Logic การแกะ Bencode เดิมของคุณให้ครบ)
                info_data = content[info_start + 2:pos]
                info_hash = hashlib.sha1(info_data).hexdigest().lower()
        except Exception as e:
            print(f"⚠️ [{self.name}] Hash extraction failed: {e}")

        # 🔥 [แก้ไข]: ส่งให้ _add_fallback_clean จัดการเบ็ดเสร็จในที่เดียว ไม่ต้องมีลูปยิง Label ซ้ำเติมข้างล่างอีก
        return self._add_fallback_clean(content, site_name, info_hash)

    def _add_fallback_clean(self, content, site_name, info_hash=None):
        safe_site = self.safe_xml_escape(site_name)

        if info_hash and self._verify_torrent_in_client(info_hash):
            print(f"ℹ️ [{self.name}] Torrent already exists -> บังคับติดป้ายค่ายเว็บและสับสวิตช์เริ่มงานซ้ำทันที...")
            self._force_start_torrent(info_hash, safe_site)
            return True
            
        encoded_content = base64.b64encode(content).decode('ascii')
        
        # 🎯 [BULLETPROOF INJECTION]: ยัดคำสั่งติดป้าย Label (d.custom1.set) พ่วงไปกับตัวไฟล์ตั้งแต่แรกแอดงาน
        # วิธีนี้ร้อยทั้งร้อย rTorrent จะสร้างอ็อบเจกต์งานขึ้นมาพร้อมกับป้ายฉลากค่ายเว็บทันที ไม่ต้องกลัวดิสก์หน่วงแย่งสิทธิ์คำสั่ง
        xml_payload = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<methodCall>'
            '<methodName>load.raw_start</methodName>'
            '<params>'
            '<param><value><string></string></value></param>'
            '<param><value><base64>{}</base64></value></param>'
            f'<param><value><string>d.custom1.set={safe_site}</string></value></param>' # 💎 ฝังติดป้ายเข้าไปที่นี่เลย!
            '</params>'
            '</methodCall>'
        ).format(encoded_content).encode('utf-8')
        
        r = None
        
        for attempt in range(3):
            try:
                r = self.s.post(self.url, data=xml_payload, auth=self.auth, headers=self.headers, timeout=15, verify=False)
                break  
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as net_err:
                if attempt < 2:
                    print(f"🔄 [{self.name}] Network Timeout/Error ({net_err}) กำลังลองใหม่อีกครั้ง... (รอบที่ {attempt+1}/2)")
                    time.sleep(1.5)  
                else:
                    print(f"❌ [{self.name}] Exception during upload หลังพยายามครบ 3 รอบ: {net_err}")
                    return False
            except Exception as e:
                print(f"❌ [{self.name}] Fatal Exception during upload: {e}")
                return False
        
        try:
            if r is None or r.status_code != 200 or "fault" in r.text.lower():
                print(f"⚠️ [{self.name}] Load failed: {r.text[:50] if r else 'No Response'}")
                return False

            print(f"✅ [{self.name}] ส่งไฟล์เข้า rTorrent สำเร็จ -> เริ่มกระบวนการตรวจสอบสถานะระบบดิสก์...")
            
            # วนลูปตรวจสอบว่างานเข้าระบบหรือยัง
            found = False
            for i in range(25):  
                time.sleep(0.4)
                if info_hash and self._verify_torrent_in_client(info_hash):
                    found = True
                    break
            
            # 🔥 [CENTRALIZED CONTROL COMBO]: ยิงชุดคอมโบสตาร์ทย้ำ และยิงถล่มป้ายซ้ำอีกรอบกันหลุด
            if info_hash:
                if not found:
                    print(f"⚠️ [{self.name}] Detection Delay: บอทตรวจไม่เจอในตารางหลักทันที กำลังเจาะทะลวงระบบแชร์...")
                
                # สั่งยิงคอมโบจัดการรอบปกติ
                self._force_start_torrent(info_hash, safe_site)
                
                # 🛡️ แผนกดย้ำกรณีดิสก์แชร์สล็อตทำงานดีเลย์
                if not found:
                    time.sleep(1.5)
                    print(f"🔄 [{self.name}] Burst-Emphasize: กำลังยิงชุดคำสั่งจัดการย้ำรอบที่ 2 ป้องกันดิสก์ค้างช้า...")
                    self._force_start_torrent(info_hash, safe_site)

            return True

        except Exception as e:
            print(f"❌ [{self.name}] Exception during response parsing: {e}")
            return False

    def _force_start_torrent(self, info_hash, safe_site=None):
        """[SUPER UPGRADED]: ชุดคอมโบสั่งติดป้ายค่ายเว็บ + บังคับรัน + ดึงเพียร์ จบในที่เดียวแบบนิ่งสนิท"""
        if not info_hash: return
        target_hash = info_hash.strip().lower()
        
        # ปรับขยับเอาคำสั่งยัดทาสก์และเปิดเปิดดึงนำร่อง
        sequence = ['view.add_task', 'd.open', 'd.custom1.set', 'd.start', 'd.tracker_announce']
        
        for method in sequence:
            try:
                if method == 'd.custom1.set' and not safe_site:
                    continue
                    
                if method == 'view.add_task':
                    xml_cmd = (
                        f'<?xml version="1.0"?><methodCall><methodName>view.add_task</methodName>'
                        f'<params><param><value><string>main</string></value></param>'
                        f'<param><value><string>{target_hash}</string></value></param></params></methodCall>'
                    )
                elif method == 'd.custom1.set':
                    xml_cmd = (
                        f'<?xml version="1.0"?><methodCall><methodName>d.custom1.set</methodName>'
                        f'<params><param><value><string>{target_hash}</string></value></param>'
                        f'<param><value><string>{safe_site}</string></value></param></params></methodCall>'
                    )
                else:
                    xml_cmd = (
                        '<?xml version="1.0" encoding="UTF-8"?>'
                        '<methodCall><methodName>{}</methodName>'
                        '<params><param><value><string>{}</string></value></param></params>'
                        '</methodCall>'
                    ).format(method, target_hash)

                req_headers = {**getattr(self, 'headers', {}), 'Content-Type': 'text/xml'}
                self.s.post(self.url, data=xml_cmd.encode('utf-8'), auth=self.auth, headers=req_headers, timeout=5, verify=False)
                time.sleep(0.1)
            except Exception as e:
                print(f"⚠️ [{self.name}] _force_start ({method}) failed: {e}")

    def _verify_torrent_in_client(self, info_hash):
        """[UPGRADED]: เช็กการมีอยู่จริงของงานผ่าน XML-RPC เดี่ยว (d.name) เบาเครื่องและแม่นยำ 100%"""
        if not info_hash: return False
        target_hash = info_hash.strip().lower() 
    
        xml_check = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<methodCall>'
            '<methodName>d.name</methodName>'
            '<params>'
            f'<param><value><string>{target_hash}</string></value></param>' 
            '</params>'
            '</methodCall>'
        ).encode('utf-8')
    
        try:
            req_headers = {**getattr(self, 'headers', {}), 'Content-Type': 'text/xml; charset=utf-8'}
            r = self.s.post(self.url, data=xml_check, auth=self.auth, headers=req_headers, timeout=6, verify=False)
            
            if r.status_code == 200 and "fault" not in r.text.lower():
                if "value" in r.text.lower() and len(r.text) > 80:
                    return True
        except Exception as e:
            print(f"⚠️ [{self.name}] _verify_torrent_in_client เกิดข้อผิดพลาดเน็ตเวิร์ก: {e}")
            
        return False

    def _sweeper_force_start(self):
        """ระบบกวาดงานค้างอัตโนมัติ"""
        # d.multicall2 จะกวาดงานที่ status = stopped ทั้งหมดในครั้งเดียว
        # d.is_active=0 หมายถึงกวาดเฉพาะงานที่หยุดทำงานอยู่ (Stopped)
        xml_sweep = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<methodCall><methodName>d.multicall2</methodName>'
            '<params>'
            '<param><value><string></string></value></param>' # target: all
            '<param><value><string>main</string></value></param>' # view: main
            '<param><value><string>d.is_active=0</string></value></param>' # condition: inactive
            '<param><value><string>d.open=</string></value></param>'       # command: open
            '<param><value><string>d.start=</string></value></param>'      # command: start
            '<param><value><string>d.resume=</string></value></param>'    # command: resume
            '</params></methodCall>'
        ).encode('utf-8')
        
        try:
            self.s.post(self.url, data=xml_sweep, auth=self.auth, headers=self.headers, timeout=5, verify=False)
        except:
            pass
            
    def delete_torrent(self, t_hash):
        """Hard Delete: หยุดและลบข้อมูลในคำสั่งเดียว (Atomic Operation)"""
        #สิ่งที่ต้องเพิ่มใน .rtorrent.rc
        #ให้เพิ่มบรรทัดนี้ไว้ก่อนบรรทัด # -- END HERE --:
        #method.set_key = event.download.erased, delete_tied, "execute={rm,-rf,--,$d.base_path=}"
        try:
            # รวม d.stop และ d.erase เข้าเป็นก้อนเดียว
            xml = f'''<?xml version="1.0"?>
            <methodCall>
              <methodName>system.multicall</methodName>
              <params>
                <param><value><array><data>
                  <value><struct>
                    <member><name>methodName</name><value><string>d.stop</string></value></member>
                    <member><name>params</name><value><array><data><value><string>{t_hash}</string></value></data></array></value></member>
                  </struct></value>
                  <value><struct>
                    <member><name>methodName</name><value><string>d.erase</string></value></member>
                    <member><name>params</name><value><array><data><value><string>{t_hash}</string></value></data></array></value></member>
                  </struct></value>
                </data></array></value></param>
              </params>
            </methodCall>'''

            r = self.s.post(self.url, data=xml, auth=self.auth, headers=self.headers, verify=False, timeout=10)
            return r.status_code == 200
        except Exception as e:
            print(f"❌ [{self.name}] Delete Error: {e}")
            return False

    def reannounce_all(self):
        if not self.is_connected and not self.login(): return False
        try:
            # ใช้ multicall2 สั่ง d.tracker_announce ทุกตัวในหน้าหลัก (main) พร้อมกัน
            xml = (
                '<?xml version="1.0"?>'
                '<methodCall>'
                '<methodName>d.multicall2</methodName>'
                '<params>'
                '<param><value><string></string></value></param>'
                '<param><value><string>main</string></value></param>'
                '<param><value><string>d.tracker_announce=</string></value></param>'
                '</params>'
                '</methodCall>'
            )
            self.s.post(self.url, data=xml, auth=self.auth, headers=self.headers, timeout=15, verify=False)
            return True
        except: return False
        
    def get_stats_by_site(self):
        if not self.is_connected: 
            self.login()
            
        try:
            import xmlrpc.client
            from urllib.parse import unquote

            # 1. สร้าง Proxy เชื่อมต่อหลังบ้าน
            auth_url = self.url.replace("://", f"://{self.user}:{self.pw}@")
            proxy = xmlrpc.client.ServerProxy(auth_url)

            # 2. 🛡️ [ULTIMATE CORE FIX]: เปลี่ยนเป็นคำสั่ง Standard rTorrent API สายตรงทั้งหมด 100%
            # d.custom1=           (ดึง Label ชื่อเว็บ)
            # d.up.total=          (ดึงยอดอัปโหลดรวมหน่วย Bytes)
            # d.up.rate=           (ดึงความเร็วอัปโหลดปัจจุบัน Bytes/s) <- แก้ไขจาก d.get_up_rate=
            # d.down.total=        (ดึงยอดดาวน์โหลดรวมหน่วย Bytes)
            response = proxy.d.multicall2("", "main", "d.custom1=", "d.up.total=", "d.up.rate=", "d.down.total=")

            site_stats = {}
            for t in response:
                # ล้างชื่อ Site และปรับเป็นตัวพิมพ์ใหญ่ทั้งหมด (Upper Case)
                raw_site = t[0] if t[0] else "Uncategorized"
                site = unquote(raw_site).strip().upper()
                
                # แมตช์ชื่อคีย์หลักให้ตรงกับระบบรายงานผล
                if "BEARBIT" in site: site = "BEARBIT"
                if "TORRENTDD" in site: site = "TORRENTDD"
            
                total_up = int(t[1])
                up_speed = int(t[2])  # รับค่าจาก d.up.rate มาคำนวณสปีดปัจจุบัน
                total_dl = int(t[3])

                if site not in site_stats:
                    site_stats[site] = {
                        'total_up_bytes': 0, 
                        'total_dl_bytes': 0,      
                        'current_speed_bytes': 0, 
                        'count': 0
                    }
        
                site_stats[site]['total_up_bytes'] += total_up
                site_stats[site]['total_dl_bytes'] += total_dl
                site_stats[site]['current_speed_bytes'] += up_speed
                site_stats[site]['count'] += 1
            
            return site_stats
        except Exception as e:
            self.is_connected = False
            print(f"⚠️ [{self.name}] rTorrent Stats Error: {e}")
            return {}

# ========================= UPDATE TRACKER =========================

def update_trackers(node):
    """ สั่งให้ Node อัปเดตข้อมูลไปยัง Tracker """
    try:
        # สมมติว่าคุณเพิ่ม method reannounce_all ใน Class ไว้แล้วตามที่คุยกันก่อนหน้า
        if hasattr(node, 'reannounce_all'):
            if node.reannounce_all():
                print(f"  ✅ [{node.name}] Trackers re-announced.")
                return True
        else:
            # กรณีไม่ได้เพิ่ม method ใน class สามารถใช้ logic นี้แทนได้
            if isinstance(node, QbitNode):
                node.s.post(f"{node.url}/api/v2/torrents/reannounce", data={"hashes": "all"}, auth=node.auth, timeout=10)
            elif isinstance(node, RtorrentNode):
                # logic rtorrent re-announce
                pass
            print(f"  ✅ [{node.name}] Sent re-announce request.")
    except Exception as e:
        print(f"  ⚠️ [{node.name}] Update trackers failed: {e}")
    return False

# ========================= ADD TORRENT =========================

def safe_add_torrent(node, content, site):
    # ตรวจสอบก่อนว่ามันเป็น callable หรือไม่
    if hasattr(node, 'add') and callable(node.add):
        return node.add(content, site_name=site)
    else:
        # ถ้ามันถูกเขียนทับเป็น bool เราจะลบมันทิ้งและพยายามเรียกจาก class 
        # หรือแจ้งเตือนให้ชัดเจน
        print(f"🚨 ALERT: {node.name}.add ถูกเขียนทับด้วย {type(node.add)}")
        return False
        
# ========================= AUTO CLEAN =========================

class NodeCleaner:
    def __init__(self, node_obj, node_clean_cfg, global_clean_cfg):
        self.node = node_obj
        self.node_cfg = node_clean_cfg or {}
        self.global_cfg = global_clean_cfg or {}

    def _get_node_free_gb(self):
        try:
            val = getattr(self.node, 'free_gb', 100.0)
            return float(val) if val is not None else 100.0
        except Exception:
            return 100.0

    def _hard_purge_sequence(self, t_hash, node_type):
        """
        🔥 [ANTI-GHOST SAFEGUARD SEQUENCE]
        ขั้นตอนสับสวิตช์ทำลายไฟล์แบบ 3 สเต็ปปลอดภัยสูง:
        1. อัพเดตแทรกเกอร์ (Re-announce) เพื่อบันทึก Ratio รอบสุดท้าย
        2. Settle Down: สั่งหยุดงาน (Stop / Pause) เพื่อคลาย File Handle Lock จากระบบดิสก์
        3. ยิงคำสั่งลบข้อมูลจริง (Delete) ออกจากดิสก์หลังบ้าน
        """
        try:
            if node_type == "qbit":
                # 1. Update Tracker
                self.node.s.post(f"{self.node.url}/api/v2/torrents/reannounce", data={"hashes": t_hash}, auth=self.node.auth, verify=False, timeout=5)
                time.sleep(0.5) 
                
                # 2. Stop/Pause Torrent
                self.node.s.post(f"{self.node.url}/api/v2/torrents/pause", data={"hashes": t_hash}, auth=self.node.auth, verify=False, timeout=5)
                time.sleep(0.5) 
                
                # 3. ลบตัวทอร์เรนต์พร้อมเนื้อไฟล์จริงทั้งหมด
                return self.node.delete_torrent(t_hash)

            elif node_type == "rtorrent":
                # 1. Update Tracker
                xml_announce = f'<?xml version="1.0"?><methodCall><methodName>d.tracker_announce</methodName><params><param><value><string>{t_hash}</string></value></param></params></methodCall>'
                self.node.s.post(self.node.url, data=xml_announce, auth=self.node.auth, verify=False, timeout=5)
                time.sleep(0.5)
                
                # 2. Stop Torrent
                xml_stop = f'<?xml version="1.0"?><methodCall><methodName>d.stop</methodName><params><param><value><string>{t_hash}</string></value></param></params></methodCall>'
                self.node.s.post(self.node.url, data=xml_stop, auth=self.node.auth, verify=False, timeout=5)
                time.sleep(0.5)
                
                # 3. ลบข้อมูลและตัวทอร์เรนต์
                return self.node.delete_torrent(t_hash)
                
        except Exception as e:
            print(f"⚠️ [{self.node.name}] ผิดพลาดในขั้นตอน Hard Purge Sequence: {e}")
            try:
                return self.node.delete_torrent(t_hash)
            except Exception:
                return False
        return False

    def process(self, force_emergency=False):
       # 1. เช็คสถานะการเปิดใช้งาน (Updated Logic)
        node_enable = self.node_cfg.get('enable', self.node_cfg.get('ENABLE', None))
        global_enable = self.global_cfg.get('enable', self.global_cfg.get('ENABLE', False))
        
        # ตรรกะ: ถ้า node_enable เป็น True -> เปิด | ถ้าเป็น False -> ใช้ global | ถ้าเป็น None -> ใช้ global
        if node_enable is True:
            is_enabled = True
        else:
            is_enabled = bool(global_enable)

        print(f"⚙️ [Cleaner Engine] Node: {self.node.name} | Status: {'ACTIVE' if is_enabled else 'DISABLED'} (Node: {node_enable}, Global: {global_enable})")

        if not is_enabled:
            print(f"💤 [Cleaner Bypass] Skipped [{self.node.name}] เพราะระบบปิดการใช้งาน")
            return

        # 2. เช็คสถานะพื้นที่ (Emergency หรือ Normal)
        current_free = self._get_node_free_gb()
        is_emergency = force_emergency or (current_free < 10.0)

        if is_emergency:
            print(f"🚨 [EMERGENCY] [{self.node.name}] พื้นที่วิกฤตเหลือ {current_free:.2f}GB")
            node_type = "qbit" if "qbit" in self.node.__class__.__name__.lower() else "rtorrent"
            success = smart_reclaim_process(self.node, required_gb=10.0, is_emergency=True, node_type=node_type)
            print(f"♻️ [Emergency Result] Success: {success}")
            return

        # 3. โหมดปกติ (Idle Cleanup)
        print(f"🔍 Debug: [{self.node.name}] Starting cleanup... (Free: {current_free:.2f}GB)")
        try:
            class_name = self.node.__class__.__name__.lower()
            grouped_logs = self._clean_qbit() if "qbit" in class_name else self._clean_rtorrent()
        
            if not isinstance(grouped_logs, dict): grouped_logs = {}
        
            # กรองเฉพาะกลุ่มที่มีการลบจริง
            active_logs = {r: d for r, d in grouped_logs.items() if d.get("torrents")}

            if not active_logs:
                print(f"✨ [{self.node.name}] ตรวจสอบเสร็จสิ้น: ไม่มีไฟล์ขยะ")
                return

            # Mapping Emoji
            emoji_map = {
                "Max Time Exceeded": "🚨",
                "Idle Dead": "💤",
                "Target Reached": "💰"
            }

            notify_lines = []
            for reason, log_data in active_logs.items():
                emoji = emoji_map.get(reason, "🧹")
            
                # Print Console Log
                print(f"{emoji} [{reason}] {log_data['header']}")
                for t_line in log_data["torrents"]:
                    print(f"  {emoji} {t_line}")
                
                # จัดเตรียมข้อความ Notify
                notify_lines.append(f"{emoji} <b>[{reason}]</b> {log_data['header']}")
                notify_lines.extend([f"  {emoji} {line}" for line in log_data["torrents"]])

            # 4. ส่งแจ้งเตือน
            msg = f"<b>🧹 Cleanup Summary (Idle Only)</b> [{self.node.name}]:\n" + "\n".join(notify_lines)
        
            send_func = globals().get('send_notify')
            if callable(send_func):
                asyncio.create_task(send_func(msg))
            else:
                print(f"📢 Notification:\n{msg}")

        except Exception as e:
            print(f"⚠️ [{self.node.name}] Clean Error: {e}")

    def _should_remove(self, ratio, age_hours, up_speed, leechers):
        def get_cfg_value(key, default):
            clean_sets = self.node_cfg.get('clean_settings', {})
            if clean_sets and clean_sets.get('enable', False) is True:
                if key in clean_sets: return clean_sets[key]
                if key.upper() in clean_sets: return clean_sets[key.upper()]
            return self.global_cfg.get(key.lower(), self.global_cfg.get(key.upper(), default))

        min_ratio = float(get_cfg_value('min_ratio', 1.5))
        min_time = float(get_cfg_value('min_time', 720)) / 60.0  
        max_time = float(get_cfg_value('max_time', 1440)) / 60.0 
        max_idle_hours = float(get_cfg_value('max_idle_hours', 6))
        
        min_active_speed = 5.0 * 1024  # 5 KB/s
        is_completely_idle = (up_speed < min_active_speed and leechers == 0)

        # 🔥 [HARDENED ANTIVIRUS LOCK]: ดัดหลังบั๊กค่าเวลาเอ๋อจากระบบ Shared
        if age_hours < 0 or age_hours >= 9000.0:
            return False

        # 🛡️ เกราะป้องกันขั้นที่ 2: งานเก่าแก่อายุเกิน 30 วัน ของจริง
        if age_hours > 720.0:
            if is_completely_idle or ratio >= 1.0:
                return "Legacy Expired", f"อายุเกิน 30 วัน -> ปล่อยพื้นที่คืนระบบ"
            return False

        # ถ้าน้องทอร์เรนต์กำลัง "เรซซิ่ง" ดึงสปีดเชื่อม Peer อยู่ -> ห้ามลบเด็ดขาด
        if not is_completely_idle:
            return False

        # 🚨 [Max Time Exceeded] ค้างสล็อตจนชนเพดานสูงสุด
        if age_hours >= max_time:
            return "Max Time Exceeded", f"อายุชนเพดานสูงสุด: {max_time:.1f}h -> สั่งสับทิ้ง"

        # 💤 [Idle Dead] ขยะนอนนิ่งตั้งแต่ออกตัว
        if age_hours >= max_idle_hours and ratio < 0.1:
            return "Idle Dead", f"อายุขั้นต่ำ: {max_idle_hours:.1f}h + Ratio ต่ำกว่าเกณฑ์: 0.1 -> สั่งลบ"

        # 💰 [Target Reached] เรโชชนเป้าคู่สำเร็จตามแผน
        if age_hours >= min_time:
            if ratio >= min_ratio:
                return "Target Reached", f"อายุพ้นเกณฑ์ ขั้นต่ำ: {min_time}h) + Ratio ขั้นต่ำ: {min_ratio} ชนเป้าคู่ -> เคลียร์พื้นที่"
            return False

        return False

    def _clean_qbit(self):
        res_grouped = {}
        try:
            headers = {"Accept-Encoding": "gzip, deflate", "Connection": "keep-alive"}
            r = self.node.s.get(f"{self.node.url}/api/v2/torrents/info", auth=self.node.auth, headers=headers, verify=False, timeout=15)
            if r.status_code != 200: 
                return {}

            torrents = r.json()
            now = time.time()
            for t in torrents:
                progress = t.get('progress', 0)
                if progress < 1 or t.get('completion_on', 0) <= 0: 
                    continue

                try:
                    age_hours = (now - float(t['completion_on'])) / 3600
                    ratio = float(t.get('ratio', 0))
                    up_speed = float(t.get('upspeed', 0))      
                    leechers = int(t.get('num_leechers', 0))
                except (ValueError, TypeError):
                    continue

                remove_check = self._should_remove(ratio, age_hours, up_speed, leechers)
                if remove_check:
                    reason_key, header_msg = remove_check
                    
                    if self._hard_purge_sequence(t['hash'], node_type="qbit"):
                        raw_name = t.get('name', 'Unknown')
                        name_safeguard = raw_name[:27] + "..." if len(raw_name) > 27 else raw_name
                        # 🛠️ [FIX]: ลบอิโมจิ 💤 ตรงนี้ออก ให้เหลือข้อมูลรายชื่อเพียว ๆ
                        line = f"{name_safeguard} (R:{ratio:.2f}, {age_hours:.1f}h)"
                        
                        if reason_key not in res_grouped:
                            res_grouped[reason_key] = {"header": header_msg, "torrents": []}
                        res_grouped[reason_key]["torrents"].append(line)
                        
        except Exception as e:
            print(f"⚠️ [{self.node.name}] qBittorrent Fetch Error: {e}")
        return res_grouped

    def _clean_rtorrent(self):
        if BeautifulSoup is None:
            print(f"❌ [{self.node.name}] rTorrent Clean Failed: bs4 (BeautifulSoup) is not installed.")
            return {}

        res_grouped = {}
        xml = (
            '<?xml version="1.0"?><methodCall><methodName>d.multicall2</methodName>'
            '<params><param><value><string></string></value></param>'
            '<param><value><string>main</string></value></param>'
            '<param><value><string>d.hash=</string></value></param>'
            '<param><value><string>d.ratio=</string></value></param>'
            '<param><value><string>d.timestamp.finished=</string></value></param>'
            '<param><value><string>d.up.rate=</string></value></param>'
            '<param><value><string>d.incomplete=</string></value></param>'
            '<param><value><string>d.name=</string></value></param></params></methodCall>'
        )

        soup = None
        try:
            req_headers = getattr(self.node, 'headers', {}).copy()
            if "Connection" not in req_headers:
                req_headers["Connection"] = "close" 

            r = self.node.s.post(self.node.url, data=xml, auth=self.node.auth, headers=req_headers, verify=False, timeout=15)
            if r.status_code != 200: 
                return {}

            soup = BeautifulSoup(r.text, "xml")
            items = soup.select("methodResponse > params > param > value > array > data > value")
            if not items:
                items = soup.select("value > array > data > value")

            now = time.time()

            for item in items:
                t_data = item.find('data')
                if not t_data:
                    t_data = item.find('array') if item.find('array') else item
                    
                val_nodes = t_data.find_all('value', recursive=False)
                vals = []
                for v in val_nodes:
                    inner_data = v.find(['string', 'i4', 'int'])
                    if inner_data:
                        vals.append(inner_data.get_text().strip())
                    else:
                        vals.append(v.get_text().strip())

                if len(vals) < 6:
                    continue

                t_hash, t_ratio_raw, t_finish, t_uprate, t_leechers_raw, t_name = vals[0], vals[1], vals[2], vals[3], vals[4], vals[5]

                if not t_finish: 
                    continue

                try:
                    ratio = int(t_ratio_raw) / 1000 if t_ratio_raw.isdigit() else 0.0
                    ts_val = int(t_finish)
                    if ts_val <= 0:
                        age_hours = 9999.0  
                    else:
                        age_hours = (now - ts_val) / 3600
                    up_speed = int(t_uprate) if t_uprate.isdigit() else 0
                    leechers = int(t_leechers_raw) if t_leechers_raw.isdigit() else 0
                except (ValueError, TypeError):
                    continue

                remove_check = self._should_remove(ratio, age_hours, up_speed, leechers)
                if remove_check:
                    reason_key, header_msg = remove_check
                    
                    if self._hard_purge_sequence(t_hash, node_type="rtorrent"):
                        name_safeguard = t_name[:27] + "..." if len(t_name) > 27 else t_name
                        # 🛠️ [FIX]: ลบอิโมจิ 🧹 ตรงนี้ออก ให้ส่งกลับแค่สายอักขระข้อมูลดิบ
                        line = f"{name_safeguard} (R:{ratio:.2f}, {age_hours:.1f}h)"
                        
                        if reason_key not in res_grouped:
                            res_grouped[reason_key] = {"header": header_msg, "torrents": []}
                        res_grouped[reason_key]["torrents"].append(line)

        except Exception as e:
            print(f"⚠️ [{self.node.name}] rTorrent Clean Error: {str(e)}")
        finally:
            if soup is not None:
                soup.decompose()
            
        return res_grouped

# ========================= Smart Reclaim Space (Hardened Version) =========================

def _bulk_delete_qbit(node, target_hashes):
    """ 
    ส่งคำสั่งลบแบบ Batch ของ qBittorrent ระดับ Hardened 
    🔥 ลอจิก 3 สเต็ป: Re-announce -> Pause -> Bulk Delete
    """
    try:
        # 1. Update Tracker (Re-announce) พร้อมกันทุกตัว
        node.s.post(f"{node.url}/api/v2/torrents/reannounce", data={"hashes": "|".join(target_hashes)}, auth=node.auth, verify=False, timeout=5)
        time.sleep(0.5) # รอเน็ตเวิร์กเคลียร์แพ็กเก็ต
        
        # 2. สั่งเบรกงานทั้งหมด (Pause) เพื่อคลาย File Handle Lock จาก SSD
        node.s.post(f"{node.url}/api/v2/torrents/pause", data={"hashes": "|".join(target_hashes)}, auth=node.auth, verify=False, timeout=5)
        time.sleep(0.5) # รอ Engine สั่งเคลียร์ RAM Cache ลงระบบดิสก์
        
        # 3. ยิงคำสั่งสังหารตัวงานพร้อมทลายเนื้อไฟล์จริงทิ้งในครั้งเดียว
        url = f"{node.url}/api/v2/torrents/delete"
        r = node.s.post(url, data={"hashes": "|".join(target_hashes), "deleteFiles": "true"}, auth=node.auth, timeout=15, verify=False)
        return r.status_code == 200
    except Exception as e:
        print(f"❌ Bulk Delete qBit Error: {e}")
        return False

def _bulk_delete_rtorrent(node, target_hashes):
    """ 
    ห่อหุ้มคำสั่งลบส่งแบบ XML-RPC ระดับ Hardened สำหรับ rTorrent 
    🔥 ลอจิก 3 สเต็ป: d.tracker_announce -> d.stop -> d.erase_data (ยิงคอมโบมัลติคอล)
    """
    try:
        # สเต็ปที่ 1: สั่ง Re-announce แทร็กเกอร์พร้อมกันเพื่อบันทึกสถิติรอบสุดท้าย
        xml_announce_parts = ['<?xml version="1.0"?><methodCall><methodName>system.multicall</methodName><params><param><value><array><data>']
        for h in target_hashes:
            xml_announce_parts.append(
                f'<value><struct>'
                f'<member><name>methodName</name><value><string>d.tracker_announce</string></value></member>'
                f'<member><name>params</name><value><array><data><value><string>{h}</string></value></data></array></value></member>'
                f'</struct></value>'
            )
        xml_announce_parts.append('</data></array></value></param></params></methodCall>')
        node.s.post(node.url, data="".join(xml_announce_parts), auth=node.auth, headers={"Connection": "close", "Content-Type": "text/xml"}, verify=False, timeout=10)
        time.sleep(0.5)

        # สเต็ปที่ 2: สั่งหยุดงาน (d.stop) ทั้งหมด เพื่อตัด File Lock จาก Engine บอร์ดแชร์
        xml_stop_parts = ['<?xml version="1.0"?><methodCall><methodName>system.multicall</methodName><params><param><value><array><data>']
        for h in target_hashes:
            xml_stop_parts.append(
                f'<value><struct>'
                f'<member><name>methodName</name><value><string>d.stop</string></value></member>'
                f'<member><name>params</name><value><array><data><value><string>{h}</string></value></data></array></value></member>'
                f'</struct></value>'
            )
        xml_stop_parts.append('</data></array></value></param></params></methodCall>')
        node.s.post(node.url, data="".join(xml_stop_parts), auth=node.auth, headers={"Connection": "close", "Content-Type": "text/xml"}, verify=False, timeout=10)
        time.sleep(0.5)

        # สเต็ปที่ 3: สั่งกวาดล้างไฟล์จาก storage จริงทั้งหมด (d.erase_data) คืนพื้นที่ 100%
        xml_delete_parts = ['<?xml version="1.0"?><methodCall><methodName>system.multicall</methodName><params><param><value><array><data>']
        for h in target_hashes:
            xml_delete_parts.append(
                f'<value><struct>'
                f'<member><name>methodName</name><value><string>d.erase_data</string></value></member>'
                f'<member><name>params</name><value><array><data><value><string>{h}</string></value></data></array></value></member>'
                f'</struct></value>'
            )
        xml_delete_parts.append('</data></array></value></param></params></methodCall>')
        
        req_headers = getattr(node, 'headers', {}).copy()
        req_headers["Connection"] = "close"
        req_headers["Content-Type"] = "text/xml"
        
        r = node.s.post(node.url, data="".join(xml_delete_parts), auth=node.auth, headers=req_headers, verify=False, timeout=15)
        return r.status_code == 200
    except Exception as e:
        print(f"❌ Bulk Delete rTorrent Error: {e}")
        return False

def smart_reclaim_process(node, required_gb, is_emergency=False, node_type="qbit"):
    """
    เวอร์ชัน Hardcoded Bypass + คืนค่า Log รายละเอียดสูง 
    [UPGRADED]: เสริมเกราะป้องกันลบงานแอดใหม่ (Safety Lock Age-Gate) ป้องกันอาการลบผิดพลาด 100%
    """
    try:
        node.refresh_status()
        buffer_gb = 5.0 if is_emergency else 2.5
        target_free = required_gb + buffer_gb  
        current_free = float(node.free_gb) if getattr(node, 'free_gb', None) is not None else 0.0
        
        if current_free >= target_free and not is_emergency:
            print(f"✅ [{node.name}] พื้นที่ดิสก์จริงเพียงพออยู่แล้ว: {current_free:.2f} GB")
            return True

        raw_torrents_data = []
        current_ts = int(time.time())
        
        if node_type == "qbit":
            try:
                url = f"{node.url}/api/v2/torrents/info"
                r = node.s.get(url, params={"filter": "all"}, auth=node.auth, timeout=12, verify=False)
                if r.status_code == 200:
                    raw_torrents_data = r.json()
                    print(f"👁️ [{node.name}] Hardcoded-Bypass กวาดงานตรงจาก qBitสำเร็จ: {len(raw_torrents_data)} ตัว")
            except Exception as e:
                print(f"⚠️ บายพาส qBit ตรงพลาด ถอยไปใช้โหมดเดิม: {e}")
                raw_torrents_data = node.get_all_torrents_info()
        
        else:
            try:
                # เพิิ่มการดึง d.timestamp.init= (v[8]) และ d.creation_date= (v[9]) เผื่อมาพิจารณาร่วมกรณีสถิติ rTorrent รวน
                xml = '''<?xml version="1.0"?><methodCall><methodName>d.multicall2</methodName><params>
                        <param><value><string></string></value></param><param><value><string>main</string></value></param>
                        <param><value><string>d.hash=</string></value></param><param><value><string>d.ratio=</string></value></param>
                        <param><value><string>d.complete=</string></value></param><param><value><string>d.name=</string></value></param>
                        <param><value><string>d.size_bytes=</string></value></param><param><value><string>d.left_bytes=</string></value></param>
                        <param><value><string>d.timestamp.finished=</string></value></param><param><value><string>d.state=</string></value></param>
                        <param><value><string>d.timestamp.init=</string></value></param>
                        </params></methodCall>'''
                req_headers = getattr(node, 'headers', {}).copy()
                req_headers["Connection"] = "close"
                r = node.s.post(node.url, data=xml, auth=node.auth, headers=req_headers, timeout=15, verify=False)
                if r.status_code == 200:
                    import xml.etree.ElementTree as ET
                    root = ET.fromstring(r.text)
                    nodes_data = root.findall(".//value/array/data/value/array/data")
                    
                    for item in nodes_data:
                        v = item.findall("./value")
                        if len(v) < 7: continue
                        
                        def _fast_text(vn):
                            if vn is None: return ""
                            for tag in ["./string", "./i4", "./int"]:
                                tg = vn.find(tag)
                                if tg is not None and tg.text is not None: return tg.text.strip()
                            return vn.text.strip() if vn.text else ""
                        
                        raw_torrents_data.append({
                            'hash': _fast_text(v[0]),
                            'ratio': int(_fast_text(v[1])) / 1000.0 if _fast_text(v[1]).isdigit() else 0.0,
                            'is_rt_complete': _fast_text(v[2]) == "1",
                            'name': _fast_text(v[3]),
                            'total_size': int(_fast_text(v[4])) if _fast_text(v[4]).isdigit() else 0,
                            'amount_left': int(_fast_text(v[5])) if _fast_text(v[5]).isdigit() else 0,
                            'ts_finished': int(_fast_text(v[6])) if _fast_text(v[6]).isdigit() else 0,
                            'rt_state': _fast_text(v[7]) if len(v) > 7 else "1",
                            'ts_init': int(_fast_text(v[8])) if len(v) > 8 and _fast_text(v[8]).isdigit() else 0
                        })
                    print(f"👁️ [{node.name}] Hardcoded-Bypass กวาดงานตรงจาก rTorrent สำเร็จ: {len(raw_torrents_data)} ตัว")
            except Exception as e:
                print(f"⚠️ บายพาส rTorrent ตรงพลาด ถอยไปใช้โหมดเดิม: {e}")
                raw_torrents_data = node.get_all_torrents_info()

        if not raw_torrents_data:
            print(f"⚠️ [{node.name}] ไม่มีข้อมูลงานจากการยิงตรง")
            return False

        scannable_torrents = []
        leeching_backups = []

        for t in raw_torrents_data:
            try:
                t_name = t.get('name', 'Unknown')
                raw_ratio = t.get('ratio', 0.0)
                t_ratio = float(raw_ratio) if raw_ratio is not None else 0.0
                if t_ratio < 0: t_ratio = 0.0
                
                progress_val = float(t.get('progress', 1.0 if t.get('is_rt_complete') else 0.0))
                state = str(t.get('state', t.get('status', 'seeding' if t.get('is_rt_complete') else 'downloading'))).lower()
                amt_left = float(t.get('amount_left', 0 if t.get('is_rt_complete') else -1))
                
                seeded_time_str = "N/A"
                if node_type == "qbit":
                    seeding_time = t.get('seeding_time', t.get('time_active', 0))
                    if 'seeding_time' in t and seeding_time > 0:
                        hours = seeding_time / 3600
                        seeded_time_str = f"{hours:.1f}h" if hours < 24 else f"{hours/24:.1f}d"
                    else:
                        seeded_time_str = "Active"
                else:
                    ts_fin = t.get('ts_finished', 0)
                    if ts_fin > 0 and current_ts > ts_fin:
                        diff_sec = current_ts - ts_fin
                        hours = diff_sec / 3600
                        seeded_time_str = f"{hours:.1f}h" if hours < 24 else f"{hours/24:.1f}d"
                    else:
                        seeded_time_str = "Stopped/No-Ts"

                is_completed = any(x in state for x in ['seed', 'upload', 'stalledup', 'completed']) or t.get('is_rt_complete') is True
                if not is_completed:
                    is_completed = (0.99 <= progress_val <= 1.0) or (progress_val >= 99.0) or (amt_left == 0)
                if not is_completed and any(x in state for x in ['paused', 'error', 'checking', 'stalled']):
                    if progress_val >= 1.0 or progress_val >= 99.0 or amt_left == 0:
                        is_completed = True

                size_bytes = float(t.get('total_size', t.get('size', t.get('size_bytes', 0))))
                t_size_gb = size_bytes / (1024**3)
                if t_size_gb == 0 and 'size' in t:
                    t_size_gb = float(t['size'])

                t['_calculated_size_gb'] = t_size_gb
                t['_calculated_ratio'] = t_ratio
                t['_calculated_seed_time'] = seeded_time_str

                # 🔥 [🛡️ HARDENED SAFETY LOCK AGE-GATE]: เกราะป้องกันสอยงานแอดใหม่ด่วนพิเศษ
                if node_type == "qbit":
                    added_on = t.get('added_on', 0)
                    seeding_time = t.get('seeding_time', 0)
                    # ถ้าแอดเข้าเครื่องยังไม่ถึง 45 นาที (2700 วินาที) หรือ เริ่มปล่อยงานจริงยังไม่ถึง 5 นาที (300 วินาที) -> ข้ามทันที ห้ามแตะ!
                    if (current_ts - added_on) < 2700 or (seeding_time > 0 and seeding_time < 300):
                        continue
                else:
                    ts_fin = t.get('ts_finished', 0)
                    ts_init = t.get('ts_init', 0)
                    # ป้องกันกรณี rTorrent คืนค่าเป็น 0 ตอนแอดใหม่ หรือเวลาเช็กน้อยกว่า 45 นาที
                    if ts_fin == 0 or (current_ts - ts_fin) < 2700:
                        continue
                    # ป้องกันงานแอดใหม่เอี่ยมที่ตัวแอปยังสลับสเตตัสไม่นิ่ง (เช็กอายุการสร้างก้อนงานในเครื่องขั้นต่ำ 45 นาที)
                    if ts_init > 0 and (current_ts - ts_init) < 2700:
                        continue

                # สิ้นสุดส่วน Safe Guard (เงื่อนไขดั้งเดิมจะประมวลผลต่อด้านล่างอย่างปลอดภัย)
                if is_completed:
                    if is_emergency:
                        if t_size_gb >= 1.0: scannable_torrents.append(t)
                    else:
                        if t_ratio >= 1.0 and t_size_gb >= 1.0: scannable_torrents.append(t)
                else:
                    if t_size_gb >= 2.0 and not 'allocating' in state:
                        leeching_backups.append(t)
                        
            except Exception:
                continue

        if not scannable_torrents and is_emergency and leeching_backups:
            print(f"☣️ [{node.name}] มาตรการขั้นสุดยอด! ดึงงานดาวน์โหลดค้างมาทำลายเพื่อคืนพื้นที่ดิสก์")
            leeching_backups.sort(key=lambda x: -x['_calculated_size_gb'])
            scannable_torrents = leeching_backups[:2]

        if not scannable_torrents:
            print(f"⚠️ [{node.name}] ตรวจวิเคราะห์ข้อมูลตรงแล้ว ไม่พบไฟล์ตรงตามเงื่อนไขกู้ภัย")
            return False

        scannable_torrents.sort(key=lambda x: (-x['_calculated_size_gb'], x['_calculated_ratio']))

        virtual_free_gb = current_free
        targets_to_delete = []
        target_hashes = []
        reclaimed_logs = []
        
        max_delete_limit = 15 if is_emergency else 6

        for t in scannable_torrents:
            if virtual_free_gb >= target_free or len(targets_to_delete) >= max_delete_limit:
                break
            targets_to_delete.append(t)
            target_hashes.append(t.get('hash'))
            virtual_free_gb += t['_calculated_size_gb']

        if not targets_to_delete:
            return False

        print(f"🧹 [{node.name}] บายพาสล็อกเป้าหมายเตรียมกวาดล้าง {len(targets_to_delete)} รายการ -> ยิงคำสั่งทำลายข้อมูลจริง")
        
        # ยิงคำสั่งลบแบบกลุ่ม (Bulk 3-Step Sequence)
        bulk_success = False
        if node_type == "qbit":
            bulk_success = _bulk_delete_qbit(node, target_hashes)
        else:
            bulk_success = _bulk_delete_rtorrent(node, target_hashes)

        if bulk_success:
            for t in targets_to_delete:
                t_name = t.get('name', 'Unknown')
                name_safeguard = t_name[:28] + "..." if len(t_name) > 28 else t_name
                log_entry = f"  🔥 [Purged] {name_safeguard} | ขนาด: {t['_calculated_size_gb']:.2f}GB | เรโช: {t['_calculated_ratio']:.2f} | เวลาปล่อย: {t['_calculated_seed_time']}"
                reclaimed_logs.append(log_entry)
                print(log_entry)
        else:
            print("⚠️ Bulk purge failed, falling back to sequential 3-step hardened delete.")
            for t in targets_to_delete:
                h = t.get('hash')
                try:
                    if node_type == "qbit":
                        node.s.post(f"{node.url}/api/v2/torrents/reannounce", data={"hashes": h}, auth=node.auth, verify=False, timeout=3)
                        node.s.post(f"{node.url}/api/v2/torrents/pause", data={"hashes": h}, auth=node.auth, verify=False, timeout=3)
                        time.sleep(0.2)
                        deleted = node.delete_torrent(h)
                    else:
                        node.s.post(node.url, data=f'<?xml version="1.0"?><methodCall><methodName>d.tracker_announce</methodName><params><param><value><string>{h}</string></value></param></params></methodCall>', auth=node.auth, verify=False, timeout=3)
                        node.s.post(node.url, data=f'<?xml version="1.0"?><methodCall><methodName>d.stop</methodName><params><param><value><string>{h}</string></value></param></params></methodCall>', auth=node.auth, verify=False, timeout=3)
                        time.sleep(0.2)
                        deleted = node.delete_torrent(h)
                        
                    if deleted:
                        t_name = t.get('name', 'Unknown')
                        name_safeguard = t_name[:28] + "..." if len(t_name) > 28 else t_name
                        log_entry = f"  ⚠️ [Fallback-Seq] {name_safeguard} | ขนาด: {t['_calculated_size_gb']:.2f}GB | เรโช: {t['_calculated_ratio']:.2f} | เวลาปล่อย: {t['_calculated_seed_time']}"
                        reclaimed_logs.append(log_entry)
                        print(log_entry)
                except Exception as seq_err:
                    print(f"❌ Fallback ลบไฟล์เดี่ยวติดปัญหา: {seq_err}")
                time.sleep(0.1)

        if reclaimed_logs and callable(globals().get('send_notify')):
            header_str = f"🚨 <b>[Emergency Bypass Smart Reclaim]</b> [{node.name}]\n" if is_emergency else f"🧹 <b>[Normal Smart Reclaim]</b> [{node.name}]\n"
            msg = header_str + "ระบบทำการกวาดล้างและทวงคืนพื้นที่ดิสก์เสร็จสิ้น รายละเอียดไฟล์:\n" + "\n".join(reclaimed_logs)
            try:
                asyncio.create_task(safe_send_notify(msg))
            except Exception as e:
                print(f"⚠️ การแจ้งเตือนล้มเหลวหรือช้าเกินไป: {e}")

        print(f"⏳ [{node.name}] รอฮาร์ดแวร์จัดสรรบล็อกดิสก์คืน...")
        for attempt in range(20): 
            time.sleep(0.5) 
            node.refresh_status()
            final_free = float(node.free_gb) if getattr(node, 'free_gb', None) is not None else 0.0
            if final_free >= target_free:
                print(f"✅ [{node.name}] พื้นที่ระบบคืนกลับมาสมบูรณ์: {final_free:.2f} GB")
                return True

        return final_free >= required_gb

    except Exception as e:
        print(f"❌ Critical Reclaim Error: {str(e)}")
        return False

# ========================= Global FUNCTIONS =========================

stealth_args = {
    "ignore_https_errors": True,
    "accept_downloads": True,
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "locale": "th-TH",
    "timezone_id": "Asia/Bangkok",
    "viewport": {'width': 1920, 'height': 1080}, # เพิ่ม Viewport ให้เหมือนคนจริง
    "extra_http_headers": {
        "Accept-Language": "th-TH,th;q=0.9,en-US;q=0.8,en;q=0.7",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache"
    }
}

async def safe_await(coro, context_name="Unknown"):
    try:
        result = await coro
        if result is None:
            print(f"⚠️ Warning: {context_name} returned NoneType")
        return result
    except Exception as e:
        print(f"🚨 Critical inside {context_name}: {e}")
        return None
        
def safe_goto(page, url, retries=3, **kwargs):
    # กำหนดค่าเริ่มต้นถ้าไม่ได้ส่งมาใน kwargs
    if 'wait_until' not in kwargs:
        kwargs['wait_until'] = 'load'
    if 'timeout' not in kwargs:
        kwargs['timeout'] = 60000

    for i in range(retries):
        try:
            # ใช้ **kwargs เพื่อรับค่าอย่าง wait_until หรือ timeout มาจากภายนอก
            response = page.goto(url, **kwargs)
            
            # ตรวจสอบว่าโหลดสำเร็จ (HTTP 200) หรือหน้าเว็บมีการตอบกลับ
            if response and (response.status == 200 or response.status == 0): # status 0 บางทีเกิดกับ cache/local file
                return True
                
        except Exception as e:
            print(f"⚠️ [Attempt {i+1}] Goto Error: {str(e)}")
            if i < retries - 1: # ถ้ายังไม่ถึงครั้งสุดท้าย ให้รอแล้วลองใหม่
                time.sleep(random.uniform(2.0, 5.0))
                
    return False

def clear_and_fill(page, selector, text):
    # 1. ย้าย Focus ไปที่ช่องนั้นและคลิกเพื่อความชัวร์
    page.click(selector)
    
    # 2. ล้างค่าแบบเบ็ดเสร็จ (Select All + Backspace)
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    
    # 3. กรอกค่าใหม่ลงไป
    # แนะนำให้ใช้ .type พร้อม delay เล็กน้อยเพื่อให้ระบบเว็บตรวจจับการพิมพ์ได้ทัน
    page.locator(selector).type(text, delay=50)
    
    # 4. ⚡️ คลิกที่ว่างเพื่อรอระบบตรวจสอบ (Trigger Validation)
    # เราจะคลิกไปที่ตัวรูปภาพ Captcha หรือพื้นที่ข้างๆ ช่องกรอก
    page.mouse.click(0, 0) # คลิกที่มุมซ้ายบนของหน้าจอ หรือ
    # page.keyboard.press("Tab") # หรือใช้การกด Tab เพื่อเลื่อน Focus ออกจากช่องกรอก
    
    # รอจังหวะให้ระบบหมุนตรวจสอบแป๊บหนึ่ง
    page.wait_for_timeout(1000)

async def ensure_site_logged_in(page: uc.Tab, site_cfg: dict) -> bool:
    site_key = site_cfg['name']
    base_url = site_cfg.get('base_url', '').rstrip('/')
    target_list = site_cfg.get('target_urls', [])
    
    # 1. การเลือก URL อย่างปลอดภัย (เหมือนเดิม)
    chosen_item = random.choice(target_list) if target_list else ""
    check_url = chosen_item.get('url', chosen_item.get('link', '')) if isinstance(chosen_item, dict) else str(chosen_item)
    final_url = check_url if check_url.startswith('http') else f"{base_url}/{check_url.lstrip('/')}"

    print(f"🔍 [{site_key}] กำลังตรวจสอบ Session ที่: {final_url}")
    
    try:
        await load_cookies_to_browser(page, site_cfg)
        await page.get(final_url)
        # รอให้ Body โหลด (หรืออย่างน้อยให้ Cloudflare ตัดสินใจว่าจะขึ้น Challenge ไหม)
        await asyncio.sleep(3)
        
        # --- [PASSIVE WAITING LOGIC] ---
        # วนลูปเช็คว่าหน้าเว็บยังเป็นหน้า Cloudflare Challenge อยู่ไหม
        for attempt in range(10): # รอสูงสุด 50 วินาที (10 * 5s)
            content = await page.get_content()
            content_lower = content.lower()
            
            # เช็คคำที่มักปรากฏบนหน้า Cloudflare
            is_cf = any(k in content_lower for k in ["just a moment", "กำลังทำการตรวจสอบความปลอดภัย", "cf-chl-widget"])
            
            if is_cf:
                print(f"🛡️ [{site_key}] ตรวจพบ Cloudflare... รอ (Passive Waiting) รอบที่ {attempt+1}...")
                await page.send(
                    uc.cdp.input_.dispatch_mouse_event(
                        type_='mouseMoved',
                        x=random.randint(100, 500),
                        y=random.randint(100, 500)
                    )
                )
                await asyncio.sleep(5)
            else:
                break # หลุดจากด่านแล้ว ไปเช็ค Session ต่อ
        # -------------------------------
        
        # หลังจากรอผ่านด่านแล้ว จึงค่อยเช็ค Session ตามปกติ
        content = await page.get_content()
        
        has_logout_link = False
        if "logout.php" in content or "/user/account/logout" in content:
            has_logout_link = True

        is_logged_in = (site_cfg['username'].lower() in content.lower()) or has_logout_link
        
        if is_logged_in and len(content) > 1000:
            print(f"✅ [{site_key}] Session ยังคงใช้งานได้อยู่")
            return True

        print(f"🔑 [{site_key}] ไม่พบ Session ที่ถูกต้อง -> เริ่มกระบวนการ Login")
        return await universal_login(page, site_cfg)

    except Exception as e:
        print(f"⚠️ Error เช็คหน้าเว็บ ({site_key}): {e}")
        await page.reload()
        await asyncio.sleep(5)
        return await universal_login(page, site_cfg)

async def universal_login(page: uc.Tab, site_cfg: dict) -> bool:
    site_key = site_cfg['name']
    base_url = site_cfg.get('base_url', '').rstrip('/')
    
    # 1. จัดการเลือก Login URL
    login_url = site_cfg.get('login_url')
    if not login_url:
        if "bitsuse" in base_url.lower():
            login_url = f"{base_url}/bs_login.php"
        elif site_key.upper() == "DEDBIT":
            login_url = base_url
        else:
            login_url = f"{base_url}/login.php"

    try:
        print(f"🔐 [{site_key}] กำลังเข้าหน้า Login...: {login_url}")
        await page.get(login_url)
        await asyncio.sleep(4.5)  # รอโครงสร้างเครือข่ายและ DOM ตั้งหลัก
        
        # ดึง Content ล่าสุดมาเช็กสถานะการคงอยู่ของเซสชัน
        page_content = await page.get_content()
        
        # 🎯 ตรวจสอบปุ่ม Logout สไตล์ nodriver (ค้นหาคำดิบในซอร์สโค้ด ปลอดภัยและเร็วกว่าพึ่ง Selector)
        has_logout_link = False
        if "logout.php" in page_content or "/user/account/logout" in page_content:
            has_logout_link = True

        is_logged_in = (site_cfg['username'].lower() in page_content.lower()) or has_logout_link
        
        if is_logged_in and len(page_content) > 1000:
            print(f"✅ [{site_key}] ยืนยันสถานะ: ล็อกอินอยู่แล้ว (Skip Login) ✨")
            return True
            
        # ดักจับเคสหน้าขาว/อินเทอร์เน็ตหลุดชั่วคราว
        if len(page_content) < 500:
            print(f"⚠️ [{site_key}] ตรวจพบหน้าว่าง ({len(page_content)} บิต) -> กำลังลอง Reload...")
            await asyncio.sleep(5)
            await page.reload()
            await asyncio.sleep(5)
            page_content = await page.get_content()
            
        print(f"🔑 [{site_key}] สถานะ: ยังไม่ได้ล็อกอิน หรือ Session หลุด -> เริ่มกระบวนการกรอกฟอร์ม")

        # 2. เริ่มต้นลูปพยายามล็อกอิน (สูงสุด 10 รอบ)
        for attempt in range(10):
            print(f"🔎 [{site_key}] กำลังพยายามล็อกอิน รอบที่ {attempt+1}/10...")
            
            target_form = 'form[action*="takelogin"],form[action*="/user/account/login/"]'
            u_sel = 'input[name="username"], input[name="user"], input#username'
            p_sel = 'input[name="password"], input[name="pass"], input#password'
            
            try:
                # ค้นหาอีเลเมนต์กล่องข้อมูล (ใช้คู่กับคำสั่งดักจับ Exception ของ nodriver เผื่อหาไม่เจอ)
                u_input = await page.select(u_sel)
                p_input = await page.select(p_sel)
                
                if not u_input or not p_input:
                    raise ValueError("หาช่องกรอกข้อมูลไม่พบในโครงสร้าง DOM")
                
                # ✍️ จำลองพิมพ์ Username
                await u_input.click()
                await page.evaluate(f"try {{ document.querySelector('{u_sel}').value = ''; }} catch(e) {{}}")
                await u_input.send_keys(site_cfg['username'])
                await asyncio.sleep(random.uniform(0.4, 0.7))

                # ✍️ จำลองพิมพ์ Password
                await p_input.click()
                await page.evaluate(f"try {{ document.querySelector('{p_sel}').value = ''; }} catch(e) {{}}")
                await p_input.send_keys(site_cfg['password'])
                await asyncio.sleep(random.uniform(0.4, 0.7))
                
                print(f"🚀 [{site_key}] กรอกข้อมูลสิทธิ์ผู้ใช้งานผ่าน Emulator สำเร็จ")

            except Exception as e:
                print(f"❌ [{site_key}] กรอกแบบจำลองไม่สำเร็จ: {e} -> สลับไปใช้แผนสำรอง Legacy JS")
                try:
                    await page.evaluate(f"""
                        const u = document.querySelector('{u_sel}');
                        const p = document.querySelector('{p_sel}');
                        if(u) u.value = '{site_cfg['username']}';
                        if(p) p.value = '{site_cfg['password']}';
                    """)
                except Exception as js_err:
                    print(f"❌ Legacy JS พังเช่นกัน: {js_err}")
                    continue

            # --- 🛡️ ส่วนจัดการ Captcha สไตล์ nodriver ---
            captcha_img = None
            captcha_input = None
            try:
                # ค้นหาภาพสัญลักษณ์และกล่องส่งรหัสแคปชา
                captcha_img = await page.select('img.cimage, img[src*="captcha"], #captcha_img')
                captcha_input = await page.select('input[name="captcha"], #captcha')
            except:
                pass  # ค่ายไหนไม่มี Captcha ปล่อยไหลผ่าน

            if captcha_img and captcha_input:
                try:
                    # 📸 บันทึกภาพลงดิสก์ชั่วคราวก่อนส่งให้ OCR แกะ (เนื่องจากเซฟเป็นไบต์ตรงๆ บน nodriver มีปัญหาโครงสร้างภายใน)
                    tmp_captcha_path = f"tmp_captcha_{site_key}.png"
                    await captcha_img.save_screenshot(tmp_captcha_path)
                    
                    if os.path.exists(tmp_captcha_path):
                        with open(tmp_captcha_path, 'rb') as f:
                            img_bytes = f.read()
                        
                        raw_text = ocr.classification(img_bytes)
                        captcha_text = re.sub(r'[^a-zA-Z0-9]', '', raw_text).upper()
                        print(f"🤖 AI Solve [{site_key}]: {captcha_text} (รอยืนยันผล...)")
                        
                        await captcha_input.click()
                        await captcha_input.send_keys(captcha_text)
                        await asyncio.sleep(1.0)
                        
                        # ลบไฟล์ภาพชั่วคราวออกเพื่อสุขอนามัยของระบบ
                        try: os.remove(tmp_captcha_path)
                        except: pass
                    
                    # ตรวจสอบการผ่านด่านเบื้องต้น (ValidGreen)
                    try:
                        valid_green = await page.select('img[src*="ValidGreen.png"]')
                        if valid_green:
                            print(f"✅ [{site_key}] ระบบตรวจสอบ Captcha เบื้องต้นผ่านฉลุย")
                    except:
                        print(f"🔄 [{site_key}] Captcha ไม่ตรงล็อก -> กำลังสั่ง Refresh ภาพรหัสใหม่...")
                        try:
                            refresh_btn = await page.select('a[title="refresh"], a[onclick*="refreshimg"], img[src*="Refresh.png"]')
                            await refresh_btn.click()
                        except:
                            await page.evaluate("if(typeof refreshimg === 'function') refreshimg();")
                        
                        await asyncio.sleep(2.5)
                        continue  # วนลูปเริ่มกรอกใหม่ในรอบถัดไป
                        
                except Exception as captcha_err:
                    print(f"⚠️ บั๊กระบบ Captcha ย่อย: {captcha_err}")

            # --- 🛫 การส่งฟอร์มล็อกอิน (Submit) ---
            await page.evaluate(f"""
                (() => {{
                    const form = document.querySelector('{target_form}');
                    if (form) {{
                        HTMLFormElement.prototype.submit.call(form);
                    }} else {{
                        const passInput = document.querySelector('{p_sel}');
                        if (passInput) passInput.dispatchEvent(new KeyboardEvent('keydown', {{'key': 'Enter'}}));
                    }}
                }})()
            """)

            await asyncio.sleep(8.5) # รอเซิร์ฟเวอร์บิทประมวลผลเซสชันและพาวาร์ปหน้าเว็บ
            
            # 🏁 ตรวจสอบผลลัพธ์หลังส่งข้อมูล
            curr_content = await page.get_content()
            curr_url = page.url.lower()
            
            if "logout" in curr_content.lower() or "login" not in curr_url:
                print(f"🎉 [{site_key}] ล็อกอินเข้าสู่ระบบสำเร็จเรียบร้อยแล้ว!")
                # หมายเหตุ: ไม่ต้องเซฟไฟล์ storage_state แล้ว เพราะ nodriver บันทึกเซสชันลง User Data Dir ให้เองแบบถาวรครับ
                return True

        return False
        
    except Exception as global_login_err:
        print(f"❌ [{site_key}] กระบวนการ Universal Login เกิดข้อผิดพลาดรุนแรง: {global_login_err}")
        return False

    except Exception as e:
        print(f"❌ [{site_key}] Login Error: {str(e)}")
        return False

async def auto_click_thanks(page, details_url: str) -> bool:
    if not details_url:
        return False

    print(f"🔍 [nodriver] เข้าสู่หน้า: {details_url}")
    
    try:
        await page.get(details_url)
        # รอสักครู่เพื่อให้หน้าเว็บโหลด DOM ที่เปลี่ยนไปหลังจากกดขอบคุณเสร็จแล้ว
        await asyncio.sleep(2) 

        js_code = """
        (() => {
            // ค้นหา container
            const td = document.querySelector('td#saythanks');
            if (!td) return "container_not_found"; // ถ้าไม่มี td นี้เลย
            
            // ค้นหาปุ่มภายใน
            const btn = td.querySelector('a[onclick*="sndReq"]');
            
            if (!btn) {
                // ถ้าไม่เจอ a[onclick] อาจเป็นเพราะกดไปแล้ว หรือเปลี่ยนสถานะไปแล้ว
                return "already_thanked_or_no_button";
            }
            
            // ถ้าเจอ ให้คลิก
            btn.click();
            return "clicked";
        })()
        """
        
        status = await page.evaluate(js_code)

        if status == "clicked":
            print("✅ กดขอบคุณสำเร็จ")
            return True
        elif status == "already_thanked_or_no_button":
            print("⏭️ ไม่พบปุ่ม (อาจจะเคยกดไปแล้ว หรือสถานะการขอบคุณปิดอยู่)")
            return False
        else:
            print("⚠️ ไม่พบส่วนประกอบการขอบคุณในหน้าเว็บ")
            return False
            
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def format_size(size_gb):
    if size_gb == 0: return "0.00 GB"
    
    units = ("B", "KB", "MB", "GB", "TB", "PB", "EB")
    current_size = float(abs(size_gb))
    unit_index = 3 
    
    while current_size >= 1024 and unit_index < len(units) - 1:
        current_size /= 1024
        unit_index += 1
    while current_size < 1 and unit_index > 0:
        current_size *= 1024
        unit_index -= 1
        
    # คืนค่าแค่ตัวเลขและหน่วย (ลบ sign ออก)
    return f"{current_size:.2f} {units[unit_index]}"

def save_hourly_snapshot(site_name, current_data):
    """
    บันทึกข้อมูลแบบแยกระบบ Site และเก็บเป็นตัวเลข (Parsed)
    - [Data Integrity] เพิ่มระบบดักจับและเปลี่ยนประเภทข้อมูล (Cast Type) เป็นตัวเลขที่แท้จริงก่อนลงดิสก์
    - [Retention Fix] ปรับปรุงการคำนวณขอบเขตลบข้อมูล 31 วัน (744 ชั่วโมง) ป้องกันประวัติหายจากบอทรันซ้ำในชั่วโมงเดิม
    """
    try:
        all_history = {}
        
        # 1. โหลดข้อมูลเดิมอย่างปลอดภัย
        if os.path.exists(STATS_HISTORY_FILE):
            with open(STATS_HISTORY_FILE, 'r', encoding='utf-8') as f:
                try:
                    all_history = json.load(f)
                except Exception: 
                    all_history = {}

        if site_name not in all_history:
            all_history[site_name] = {}
        
        now = get_now()
        timestamp_key = now.strftime("%Y-%m-%d %H:00")
        
        # 2. 🛡️ Data Type Safeguard: แปลงค่าให้แน่ใจว่าเป็นตัวเลขก่อนบันทึกลง JSON
        # ป้องกันกรณีต้นทางส่งมาเป็น string เช่น "1,500" หรือติดคอมมามา
        def clean_float(val):
            if isinstance(val, (int, float)):
                return float(val)
            try:
                return float(str(val).replace(',', '').strip())
            except (ValueError, TypeError):
                return 0.0

        # บันทึกข้อมูล Snapshot ประจำชั่วโมง
        all_history[site_name][timestamp_key] = {
            'username': current_data.get('username', 'N/A'),
            'ratio': clean_float(current_data.get('ratio', 0)),
            'up': clean_float(current_data.get('up', 0)),
            'dl': clean_float(current_data.get('dl', 0)),
            'bonus': clean_float(current_data.get('bonus', 0)),
            'raw_time': now.strftime("%Y-%m-%d %H:%M:%S")
        }

        # 3. ⏱️ ปรับปรุงระบบ Retention (จำกัดความยาวประวัติ 31 วัน)
        # โพซิชันเดิม site_keys[:-744] หากคีย์มีน้อยกว่า 744 ตัว มันจะส่งลิสต์เปล่าออกมา ซึ่งปลอดภัย
        # แต่เปลี่ยนใช้ลูปจำกัดจำนวนแบบตรงไปตรงมา เพื่อความแม่นยำในการคัดทิ้งคีย์ที่เก่าที่สุด
        site_keys = sorted(all_history[site_name].keys())
        while len(site_keys) > 744:
            oldest_key = site_keys.pop(0)  # ดึงคีย์ที่เก่าที่สุดออกทีละตัว
            del all_history[site_name][oldest_key]

        # 4. Save แบบ Atomic ป้องกันไฟล์พังเมื่อบอทโดนตัดการทำงาน
        tmp_file = STATS_HISTORY_FILE + ".tmp"
        with open(tmp_file, 'w', encoding='utf-8') as f:
            json.dump(all_history, f, indent=4, ensure_ascii=False)
            
        # ตรวจสอบขนาดไฟล์ชั่วคราวก่อนแทนที่ เพื่อความชัวร์ว่าข้อมูลไม่ว่างเปล่า
        if os.path.getsize(tmp_file) > 0:
            os.replace(tmp_file, STATS_HISTORY_FILE)
        else:
            raise ValueError("สร้างไฟล์ชั่วคราวสำเร็จแต่ขนาดไฟล์เป็น 0 KB (ระงับการ Replace เพื่อเซฟไฟล์หลัก)")

    except Exception as e:
        print(f"❌ Snapshot Error [{site_name}]: {e}")
        # เคลียร์ไฟล์ขยะเผื่อค้างคา
        if os.path.exists(STATS_HISTORY_FILE + ".tmp"):
            try: os.remove(STATS_HISTORY_FILE + ".tmp")
            except: pass

async def async_get_stats_diff(site_name, current_data):
    return await asyncio.to_thread(get_stats_diff, site_name, current_data)

def get_stats_diff(site_name, current_data):
    """
    เปรียบเทียบค่าปัจจุบันกับ Cache โดยแยกตาม site_name 
    และส่งคืนข้อความส่วนต่างที่กระชับ
    """
    diff_msg = ""
    all_cache = {}

    # 1. โหลด Cache ทั้งหมด (ถ้ามี)
    if os.path.exists(STATS_CACHE_FILE):
        try:
            with open(STATS_CACHE_FILE, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if content:
                    all_cache = json.loads(content)
        except Exception as e:
            print(f"⚠️ [{site_name}] Cache Read Error: {e}")

    # 2. ดึงข้อมูลเก่าเฉพาะของ Site นี้
    old_data = all_cache.get(site_name)

    if old_data:
        try:
            # ใช้ parse_size ที่เราทำไว้ เพื่อความแม่นยำของหน่วย (GB)
            curr_up = parse_size(current_data.get('up', '0'))
            old_up = parse_size(old_data.get('up', '0'))
            
            curr_dl = parse_size(current_data.get('dl', '0'))
            old_dl = parse_size(old_data.get('dl', '0'))

            # คำนวณ Bonus (ดึงตัวเลขออกมาลบกันตรงๆ)
            def clean_float(val):
                try: return float(str(val).replace(',', ''))
                except: return 0.0

            curr_bonus = clean_float(current_data.get('bonus', 0))
            old_bonus = clean_float(old_data.get('bonus', 0))

            changes = []
            
            # ส่วนต่าง Upload
            diff_up = curr_up - old_up
            if diff_up > 0.001: # Precision 1MB
                changes.append(f"Up: (+{format_size(diff_up)})")

            # ส่วนต่าง Download
            diff_dl = curr_dl - old_dl
            if diff_dl > 0.001:
                changes.append(f"Dl: (+{format_size(diff_dl)})")

            # ส่วนต่าง Bonus
            diff_b = curr_bonus - old_bonus
            if abs(diff_b) > 0.1:
                symbol = "+" if diff_b > 0 else ""
                changes.append(f"💰 {symbol}{diff_b:,.1f}")

            if changes:
                diff_msg = "<b>Changes:</b> " + " | ".join(changes)

        except Exception as e:
            print(f"⚠️ [{site_name}] Calc Diff Error: {e}")

    # 3. อัปเดต Cache เฉพาะส่วนของ Site นี้ และบันทึกกลับแบบ Atomic
    all_cache[site_name] = current_data
    try:
        temp_file = STATS_CACHE_FILE + ".tmp"
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(all_cache, f, indent=4, ensure_ascii=False)
        os.replace(temp_file, STATS_CACHE_FILE)
    except Exception as e:
        print(f"❌ [{site_name}] Cache Save Error: {e}")

    return diff_msg

async def ensure_dedbit_logged_in(page):
    # 1. ดึง Config
    site_cfg = next((s for s in CFG.get('SITE', []) if s['name'] in ["DEDBIT", "BITSUSE"]), None)
    if not site_cfg: return False

    # 2. เช็ค URL โดยใช้ page.url (nodriver เก็บเป็น property)
    target_url = "https://www.dedbit.com/index.php"
    if "dedbit.com" not in page.url:
        print("🔗 [System] กำลังไปหน้าแรก DEDBIT...")
        await page.get(target_url)
        await asyncio.sleep(2) 
    
    # 3. เช็คสถานะการ Login
    # nodriver ใช้ .find() ในการหา element แทน .locator()
    my_user = site_cfg.get('username')
    
    # ใช้ xpath หรือ css selector เพื่อหา element
    logout_btn = await page.find('a[href*="logout.php"]', timeout=3)
    user_text = await page.find(my_user, timeout=3)
    
    is_logged_in = logout_btn is not None or user_text is not None

    if not is_logged_in:
        print(f"🔑 [DEDBIT] พบว่า Session หลุด -> เริ่มการล็อกอินใหม่ที่หน้าแรก")
        temp_cfg = site_cfg.copy()
        temp_cfg['login_url'] = target_url
        return await universal_login(page, temp_cfg)

    print(f"✅ [DEDBIT] ล็อกอินเรียบร้อยแล้ว")
    return True

async def get_site_stats(page: uc.Tab, site_cfg: dict) -> str:
    """
    เวอร์ชัน Universal (List-based): ปลอดภัยสูงสุดจากการสะดุดของฟังก์ชันย่อย
    """
    site = site_cfg['name'] 
    
    try:
        if site in ["DEDBIT", "BITSUSE"]:
            base_url = "https://www.dedbit.com"
        else:
            base_url = site_cfg.get('base_url', "https://bearbit.org").rstrip('/')

        # -------------------------------------------------------------------------
        # 🎯 1. ทำภารกิจกวาดล้าง (Clear Notifications & Auto-Vote) ครอบสิทธิ์เซฟตี้แยกส่วน
        # -------------------------------------------------------------------------
        if 'bearbit' in site.lower():
            try:
                # 🛡️ ครอบ Safety แยกต่างหาก ไม่ว่าสองฟังก์ชันนี้จะส่ง None หรือระเบิด บอทหลักจะไม่พัง!
                if 'clear_bearbit_notifications' in globals():
                    await clear_bearbit_notifications(page, base_url, site_name=site)
            except Exception as notif_sub_err:
                print(f"⚠️ [{site}] ระบบเคลียร์แจ้งเตือนขัดข้องชั่วคราว: {notif_sub_err}")

            try:
                if 'auto_vote_snatched' in globals():
                    await auto_vote_snatched(page, base_url, site_name=site)
            except Exception as vote_sub_err:
                print(f"⚠️ [{site}] ระบบ Auto-Vote ขัดข้องชั่วคราว: {vote_sub_err}")
                
            # ⚡ กลับมาตั้งหลักที่หน้าแรกสุดเสมอ
            index_url = f"{base_url}/index.php" if not base_url.endswith('/') else f"{base_url}index.php"
            await page.get(index_url)
            await asyncio.sleep(2)

        # -------------------------------------------------------------------------
        # 🎯 2. สแกนสดหา User ID ณ วินาทีปัจจุบัน (True Fresh Soup)
        # -------------------------------------------------------------------------
        page_source = await page.get_content()
        soup = BeautifulSoup(page_source, 'html.parser')
        index_soup = soup 
        
        if site in ["DEDBIT", "BITSUSE"]:
            current_url = page.url
            user_tag = soup.find("a", href=re.compile(r"userdetails\.php\?id=\d+"))
            
            if not user_tag or "dedbit.com" not in current_url:
                print(f"🔄 [{site}] เซสชันมีปัญหา กำลังตรวจสอบสิทธิ์และเข้าสู่ระบบ DEDBIT ใหม่...")
                await ensure_dedbit_logged_in(page) 
                
                # ดึงตารางโครงสร้างใหม่หลังจากล็อกอินสำเร็จ
                page_source = await page.get_content()
                soup = BeautifulSoup(page_source, 'html.parser')
                index_soup = soup
                # ⚡ [Bug Fixed] ต้องหาตำแหน่งไอดีผู้ใช้ซ้ำอีกรอบในตารางที่โหลดมาใหม่
                user_tag = soup.find("a", href=re.compile(r"userdetails\.php\?id=\d+"))
        else:
            user_tag = soup.find("a", href=re.compile(r"userdetails\.php\?id=\d+"))
        
        if not user_tag:
            return f"⚠️ [{site}] ไม่พบข้อมูลผู้ใช้ (Login อาจหลุดออกจากระบบ)"

        username = user_tag.get_text(strip=True)
        href = user_tag['href']
        profile_url = f"{base_url}/{href.lstrip('/')}" if not href.startswith('http') else href
        
        print(f"📊 [{site}] เซสชันเสถียร กำลังดึงสถิติจากหน้าโปรไฟล์: {profile_url}")

        # -------------------------------------------------------------------------
        # 🎯 3. เข้าหน้าโปรไฟล์เพื่อคว้าสถิติเชิงลึก
        # -------------------------------------------------------------------------
        # ปรับมาใช้คำสั่งเดินทางของ nodriver ตรงๆ (หรือใช้ safe_goto ที่ปรับปรุงเป็น async แล้ว)
        await page.get(profile_url)
        await asyncio.sleep(2) # แทนที่ page.wait_for_timeout(2000)
        
        page_source = await page.get_content()
        soup = BeautifulSoup(page_source, 'html.parser')
        
        # 🎯 [เกราะป้องกันชั้นที่ 1]: สกัดแกะไอเทมทันที ณ วินาทีนี้ เก็บใส่ตัวแปรไว้ก่อนเลย!
        bearbit_item_cache = "NONE"
        if 'bearbit' in site.lower():
            bearbit_item_cache = get_bearbit_item_status(soup)

        text = soup.get_text(separator=" ")

        # ฟังก์ชันสกัด Regex ภายใน
        def extract(pattern, source, default=None):
            m = re.search(pattern, source, re.I)
            return m.group(1) if m else default

        curr_ratio = extract(r"Ratio:?\s*([\d\.,]+)", text, None)
        curr_up    = extract(r"(?:Uploaded|Upload):?\s*([\d\.,]+\s*[KMGTP]B)", text, None)
        curr_dl    = extract(r"(?:Downloaded|Download):?\s*([\d\.,]+\s*[KMGTP]B)", text, None)
        curr_bonus = extract(r"(?:Bonus):?\s*([\d\.,]+)", text, "0")

        if not all([curr_ratio, curr_up, curr_dl]):
            return f"⚠️ [{site}] สถิติไม่ครบถ้วน (หน้าเว็บเรนเดอร์ข้อมูลไม่สมบูรณ์)"

        curr_data = {
            'username': username,
            'ratio': curr_ratio,
            'up': curr_up,
            'dl': curr_dl,
            'bonus': curr_bonus
        }

        # -------------------------------------------------------------------------
        # 🎯 4. บันทึก Snapshot และประวัติความเปลี่ยนแปลง (Diff History)
        # -------------------------------------------------------------------------
        try:
            diff_text = await async_get_stats_diff(site, curr_data)
            snapshot_data = {
                'username': username,
                'ratio': float(curr_data['ratio'].replace(',', '')),
                'up': parse_size(curr_data['up']), 
                'dl': parse_size(curr_data['dl']), 
                'bonus': float(curr_data['bonus'].replace(',', ''))
            }
            await asyncio.to_thread(save_hourly_snapshot, site, snapshot_data)
        except Exception as save_err:
            print(f"⚠️ [{site}] ระบบบันทึกประวัติขัดข้อง: {save_err}")
            diff_text = "No changes" # กำหนดค่าปลอดภัยไว้ถ้าบันทึกไม่สำเร็จ

        # -------------------------------------------------------------------------
        # 🎯 5. ประกอบร่างรายงานสรุปผล
        # -------------------------------------------------------------------------
        display_site = "DED/BITS" if site in ["BITSUSE", "DEDBIT"] else site
        msg = [f"👤 <b>{username}</b> ({display_site}) | Ratio: {curr_data['ratio']}"]
        msg.append(f"📤 Up: {curr_data['up']} | 📥 Dl: {curr_data['dl']}")
        
        if 'bearbit' in site.lower():
            # 🎯 [เกราะป้องกันชั้นที่ 2]: ดึงข้อมูลจากแคชตัวแปรที่ดักสกัดไว้ด้านบนมาพ่นออกรายงานตรงๆ
            msg.append(f"💰 Bonus: {curr_data['bonus']} | 🎁 Item: {bearbit_item_cache}")
        else:
            msg.append(f"💰 Bonus: {curr_data['bonus']}")

        if diff_text and diff_text != "No changes":
            msg.append(f"🔄 {diff_text}")

        return "\n".join(msg)

    except Exception as e:
        return f"❌ Stats Error [{site}]: {str(e)}"

def extract_digit(tag):
    """
    ฟังก์ชันช่วยสกัดตัวเลขออกจาก Tag HTML 
    จัดการเรื่องคอมม่า (เช่น 1,200) และตัวอักษรแปลกปลอม
    """
    if not tag:
        return 0
    try:
        # ดึง Text ออกมา -> ลบทุกอย่างที่ไม่ใช่ตัวเลข -> แปลงเป็น int
        txt = tag.get_text(strip=True)
        # ลบคอมม่าออกก่อน (เช่น 1,200 -> 1200)
        txt = txt.replace(',', '')
        val = re.sub(r'\D', '', txt) 
        return int(val) if val else 0
    except:
        return 0

async def extract_torrent_data(row, base_url, dl_session=None, headers=None):
    if row is None: return None
    
    # --- 1. สกัด ID & Title ---
    title_tag = row.find("a", href=re.compile(r"details\.php\?id=(\d+)", re.I))
    t_id, title, details_url = None, "Unknown File", None
    if title_tag:
        title = title_tag.get_text(strip=True)
        t_id = re.search(r"id=(\d+)", title_tag.get('href', '')).group(1)
        details_url = f"{base_url.rstrip('/')}/details.php?id={t_id}"

    # --- 2. สกัดข้อมูล (รองรับโครงสร้างตาราง Bearbit) ---
    all_tds = row.find_all("td")
    s, l, c = 0, 0, 0
    t_size_str, raw_date_str = "0 GB", ""

    # Bearbit Index:
    # [7]: Date, [8]: Size, [9]: Completed, [10]: Seeders, [11]: Leechers
    if len(all_tds) >= 11:
        raw_date_str = all_tds[7].get_text(separator=' ', strip=True)
        t_size_str = all_tds[8].get_text(separator=' ', strip=True)
        c = extract_digit(all_tds[9])
        s = extract_digit(all_tds[10])
        l = extract_digit(all_tds[11])

    # 3. สกัดลิงก์ดาวน์โหลด (ฉบับคัดกรอง)
    download_url = None
    
    # ดึงจากส่วน Action บนหน้าหลัก
    action_div = row.find("div", class_="bb-file-actions")
    if action_div:
        # Regex ตัวเดียวจบ ครอบคลุมทั้ง download.php และ downloadnew.php
        btn_dl = action_div.find("a", href=re.compile(r"download(new)?\.php|nDonatedN\.php", re.I))
        if btn_dl:
            path = btn_dl.get('href', '')
            download_url = path if path.startswith('http') else f"{base_url.rstrip('/')}/{path.lstrip('/')}"

    # Fallback ถ้าไม่เจอใน div action
    if not download_url:
        btn_dl = row.find("a", href=re.compile(r"download(new)?\.php\?id=" + str(t_id), re.I))
        if btn_dl:
            download_url = f"{base_url.rstrip('/')}/{btn_dl.get('href', '').lstrip('/')}"

    # [จุดสำคัญ] กรองลิงก์ VIP/Donate ทิ้งทันที
    if download_url and any(bad in download_url.lower() for bad in ['ndonatedn', 'vip', 'donate']):
        #print(f"⚠️ [{t_id}] ตรวจพบลิงก์หน้า VIP, บังคับทำ Deep Scan เพื่อหาลิงก์จริง...")
        download_url = None # บังคับให้ข้ามไปทำ Deep Scan ในข้อ 4

    # --- 4. Deep Scan (ฉบับอัปเกรด) ---
    if not download_url and details_url and dl_session:
        #print(f"🔍 [{t_id}] ไม่พบลิงก์หน้าหลัก ทำ Deep Scan...")
        try:
            local_headers = headers.copy() if headers else {}
            local_headers['Referer'] = details_url
            
            # ดึงข้อมูลผ่าน session
            resp = await dl_session.get(details_url, headers=local_headers, timeout=20)
            
            if resp and resp.status_code == 200:
                soup_details = BeautifulSoup(resp.content, 'html.parser')
                
                if is_cloudflare(soup_details):
                    return {"id": t_id, "status": "cf_blocked"}
                
                # [แก้ไข] ค้นหาลิงก์ดาวน์โหลดที่รองรับทั้ง download.php และ downloadnew.php
                # และหลีกเลี่ยงลิงก์ที่เป็น VIP/Donate
                dl_tag = soup_details.find("a", href=re.compile(r"download(new)?\.php\?.*id=" + str(t_id), re.I))
                
                if dl_tag:
                    action_url = dl_tag.get('href', '').strip()
                    # ตรวจสอบลิงก์ที่ได้ว่าไม่ใช่หน้า VIP
                    if any(bad in action_url.lower() for bad in ['ndonatedn', 'vip', 'donate']):
                        print(f"🚩 [{t_id}] ตรวจพบลิงก์ VIP/Donate, ข้าม...")
                    else:
                        download_url = action_url if action_url.startswith('http') else f"{base_url.rstrip('/')}/{action_url.lstrip('/')}"
        
        except Exception as e:
            print(f"⚠️ [{t_id}] Error ในการ Deep Scan: {e}")

    return {
        "id": t_id, "title": title, "seeders": s, "leechers": l,
        "completed": c, "size_str": t_size_str, "raw_date": raw_date_str,
        "download_url": download_url, "details_url": details_url
    }

class ResponseWrapper:
    def __init__(self, status, content, url):
        self.status_code = status # ปรับชื่อให้ตรงกับที่คุณใช้
        self.content = content
        self.url = url
        self.headers = {} # ถ้าต้องใช้ headers ให้ดึงมาใส่ด้วย

async def download_torrent_via_browser(tab, details_url, download_url):
    # 1. ไปหน้า details เพื่อให้ Browser สร้าง Session/Cookie ให้สมบูรณ์
    await tab.get(details_url)
    await tab.wait(2) # รอแค่แป๊บเดียวพอ
    
    # 2. ดึงคุกกี้จาก Browser ผ่าน JS
    cookie_str = await tab.evaluate("document.cookie")
    cookies_dict = {}
    if isinstance(cookie_str, str):
        for item in cookie_str.split("; "):
            if '=' in item:
                key, val = item.split("=", 1)
                cookies_dict[key.strip()] = val.strip()

    # 3. ใช้ download_url ที่ได้รับมา (แทนที่จะไปกดปุ่ม)
    # เราใช้ download_url ที่บอทส่งมาให้ในตัวแปร details_url หรือพารามิเตอร์อื่น
    # ตรงนี้สมมติว่า download_url คือ URL ของปุ่มโหลดที่คุณมีอยู่แล้ว
    # หากยังไม่มี ต้องดึงออกมาจาก details_url หรือระบุเข้ามา
    
    print(f"🚀 เริ่มดาวน์โหลดตรงจาก URL: {download_url}") # ใช้ download_url ที่คุณมี

    import aiohttp
    async with aiohttp.ClientSession(cookies=cookies_dict) as session:
        headers = {
            'Referer': details_url,
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/120.0.0.0'
        }
        async with session.get(download_url, headers=headers) as resp:
            if resp.status == 200:
                return await resp.read()
            else:
                print(f"❌ ดาวน์โหลดตรงล้มเหลว Status: {resp.status}")
    
    return None
                
def format_site_stats_report(all_nodes):
    # โครงสร้างใหม่: { 'Site_Name': { 'Node_Name': { 'up_gb': X, 'speed_mb': Y, 'count': Z } } }
    combined_stats = {}
    errors = []

    for node in all_nodes:
        node_name = getattr(node, 'name', 'Unknown Node').strip()
        try:
            node_data = node.get_stats_by_site()
            if not node_data: 
                continue

            for site, data in node_data.items():
                site_name = str(site).strip() if site else "Unknown"
                # กรอง Category ขยะ
                if site_name.lower() in ['', 'none', 'uncategorized', 'default']: 
                    site_name = "Other"
                
                # ตรวจสอบและประกาศ Dict รองรับชั้น Tracker
                if site_name not in combined_stats:
                    combined_stats[site_name] = {}
                
                # แปลงหน่วยเก็บราย Node
                up_gb = data.get('total_up_bytes', 0) / (1024**3)
                speed_mb = data.get('current_speed_bytes', 0) / (1024**2)
                file_count = data.get('count', 0)

                # ถ้า Node นี้เคยมีข้อมูลใน Tracker นี้แล้ว (เผื่อเคสซ้ำ) ให้บวกเพิ่ม
                if node_name in combined_stats[site_name]:
                    combined_stats[site_name][node_name]['up_gb'] += up_gb
                    combined_stats[site_name][node_name]['speed_mb'] += speed_mb
                    combined_stats[site_name][node_name]['count'] += file_count
                else:
                    combined_stats[site_name][node_name] = {
                        'up_gb': up_gb,
                        'speed_mb': speed_mb,
                        'count': file_count
                    }

        except Exception as e:
            errors.append(f"{node_name}: {str(e)}")

    # เริ่มสร้างข้อความหัวรายงาน
    current_time = datetime.now().strftime("%H:%M:%S")
    msg = f"📊 <b>Universal Auto-Pilot Stats</b>\n"
    msg += f"🕒 <i>Last Sync: {current_time} | 🛰 Nodes: {len(all_nodes)}</i>\n\n"
    
    if not combined_stats:
        return msg + "⚠️ No active data from nodes."

    # วนลูปสร้างตารางแยกตาม Tracker (เรียงชื่อ Tracker ตามลำดับอักษร หรือปรับเปลี่ยนได้)
    for site_name, nodes_data in sorted(combined_stats.items()):
        
        # กรองเอาเฉพาะ Node ที่มีไฟล์วิ่งอยู่ในเว็บนี้จริงๆ
        active_nodes = {n: d for n, d in nodes_data.items() if d['count'] > 0}
        if not active_nodes:
            continue

        # เปิดหัวข้อ Tracker
        msg += f"Tracker: <b>{site_name.upper()}</b>\n"
        msg += "```bash\n"
        msg += f"{'Node':<10} | {'Upload':<10} | {'Speed':<10}\n"
        msg += "-" * 37 + "\n"

        track_total_up = 0
        track_total_speed = 0
        node_count = len(active_nodes)

        # วนลูปแสดงสถิติราย Node ภายใต้ Tracker นี้
        for n_name, stat in active_nodes.items():
            # ปรับหน่วยความเร็ว (ถ้าวิ่งเกิน 1024 MB/s ให้ตัดขึ้นเป็น GB/s)
            if stat['speed_mb'] >= 1024:
                speed_str = f"{stat['speed_mb']/1024:>5.2f} GB/s"
            else:
                speed_str = f"{stat['speed_mb']:>5.1f} MB/s"

            msg += f"{n_name[:10]:<10} | {stat['up_gb']:>5.1f} GB | {speed_str}\n"
            
            # สมทบยอดรวมใน Tracker นี้
            track_total_up += stat['up_gb']
            track_total_speed += stat['speed_mb']

        msg += "-" * 37 + "\n"
        
        # คำนวณยอดรวม (TOTAL) ของ Tracker นี้
        if track_total_speed >= 1024:
            total_speed_str = f"{track_total_speed/1024:>5.2f} GB/s"
        else:
            total_speed_str = f"{track_total_speed:>5.1f} MB/s"
        msg += f"{'TOTAL':<10} | {track_total_up:>5.1f} GB | {total_speed_str}\n"

        # คำนวณค่าเฉลี่ย (AVG) ต่อ Node ของ Tracker นี้
        avg_up = track_total_up / node_count if node_count > 0 else 0
        avg_speed = track_total_speed / node_count if node_count > 0 else 0
        if avg_speed >= 1024:
            avg_speed_str = f"{avg_speed/1024:>5.2f} GB/s"
        else:
            avg_speed_str = f"{avg_speed:>5.1f} MB/s"
        msg += f"{'AVG':<10} | {avg_up:>5.1f} GB | {avg_speed_str}\n"
        
        msg += "```\n"

    # แสดงผล Error ด้านล่างสุด (ถ้ามี)
    if errors:
        msg += f"❌ <b>Errors ({len(errors)}):</b> <code>{errors[0][:40]}...</code>"

    return msg
    
# ========================= BearBit STATUS =========================

async def clear_bearbit_notifications(page: uc.Tab, base_url: str, site_name: str = "BEARBIT") -> bool:
    """
    ฟังก์ชันสำหรับลูปกวาดกล่องข้อความแจ้งเตือนสีเขียว (inbox.php?type=ตัวเลขใดๆ) ของ BEARBIT จนกว่าจะหมด
    - [Strict Filter] คัดกรองชื่อเมนูคงที่ออกด้วย Regex มั่นใจได้ว่าบอทจับเฉพาะแถบแจ้งเตือนจริงเท่านั้น
    - [AJAX Loop Engine] ปรับปรุงสถาปัตยกรรมเข้าหา nodriver เพื่อประมวลผลด่วนแบบไม่ต้องรีโหลดหน้าเว็บ
    - [Detached Safeguard] ใช้ JavaScript ตรวจเช็กการสลายตัวของ Element ใน DOM ก่อนขยับลูปถัดไป
    - [Hybrid Fallback] มีระบบสำรองอัตโนมัติ หากเกิดอาการเครือข่ายหน่วงและปุ่มไม่ยอมหายไปใน 3 วินาที
    - [Full-Report Edition] ส่งรายงานผลลัพธ์เข้า Discord แยกเคสเคลียร์สำเร็จ และเคสกล่องสะอาดเรียบร้อยดี
    """
    print(f"📥 [{site_name}] เริ่มระบบ Auto-Clear Notification (High-Speed AJAX Engine via nodriver)...")
    
    cleared_messages = []
    loop_count = 0
    max_loops = 35
    is_clean_at_start = True  # ตัวแปรดักจับว่าหน้าเว็บคลีนตั้งแต่เริ่มต้นหรือไม่
    
    if not base_url.endswith('/'):
        base_url += '/'
    index_url = f"{base_url}index.php"
    
    try:
        # เช็กหน้าปัจจุบันก่อน ถ้าอยู่ที่หน้าหลักอยู่แล้วไม่ต้องสั่งโหลดซ้ำ
        if page.url != index_url:
            await page.get(index_url)
            await asyncio.sleep(2)
    except Exception as e:
        print(f"⚠️ [{site_name}] ไม่สามารถโหลดหน้าแรกได้: {e}")
        return False

    # Regex สำหรับกรองเมนูคงที่ที่ไม่ใช่ข้อความแจ้งเตือนจริง
    static_menu_pattern = re.compile(r"^(?:กล่องข้อความ|Inbox|ข้อความส่วนตัว|My Inbox|Messages)$", re.I)

    while loop_count < max_loops:
        try:
            # 🎯 ดักจับลิงก์แจ้งเตือนทั้งหมดในตาราง #pms หรือ td ด้วย CSS Selector
            all_links = await page.select('table[id="pms"] a[href*="inbox.php?type="], td a[href*="inbox.php?type="]')
            
            # กรอง Element หาตัวแรกที่แมตช์เงื่อนไขแจ้งเตือนจริง (ไม่ใช่ปุ่มเมนูสเตติก)
            current_noti = None
            clean_text = ""
            target_href = ""
            
            if all_links:
                # ดึงมาเป็นลิสต์ในกรณีที่เจอหลายตัว
                if not isinstance(all_links, list):
                    all_links = [all_links]
                    
                for link in all_links:
                    text_val = link.text.strip() if link.text else ""
    
                    if text_val and not static_menu_pattern.match(text_val):
                        current_noti = link
                        clean_text = text_val
        
                        # [FIX] วิธีเข้าถึง attribute ที่ปลอดภัยที่สุดใน nodriver
                        # บางครั้ง attributes ถูกเก็บเป็น property หรือ method
                        try:
                            # พยายามดึงผ่าน dictionary ถ้ามี
                            if hasattr(link, 'attributes') and isinstance(link.attributes, dict):
                                target_href = link.attributes.get('href', '')
                            else:
                                # ถ้าไม่มี ให้ใช้ .get_attribute ซึ่งเป็นวิธีมาตรฐานของโพรโตคอล
                                target_href = await link.get_attribute('href') or ''
                        except Exception:
                            target_href = ''
            
                        break

            # 🛑 ประเมินจุดจบของลูป: ถ้าไม่เจอแจ้งเตือนใหม่แล้วให้เบรกออกจากลูปทันที
            if not current_noti:
                if loop_count == 0:
                    print(f"✨ [{site_name}] Inbox คลีน! ไม่มีข้อความแจ้งเตือนค้างอยู่")
                    is_clean_at_start = True
                else:
                    print(f"✅ [{site_name}] เคลียร์แจ้งเตือนระบบผ่าน AJAX ทุกประเภทเรียบร้อย! (รวม {loop_count} ฉบับ)")
                    is_clean_at_start = False
                break
            
            # สกัดค่า Type จาก Attribute href เพื่อพิมพ์ Log บันทึกประวัติ
            type_match = re.search(r"type=(\d+)", target_href)
            noti_type = type_match.group(1) if type_match else "Unknown"
            
            print(f"🔔 [{site_name}] ตรวจพบ Type [{noti_type}]: {clean_text} -> กำลังคลิกอ่าน (รอบที่ {loop_count + 1})")
            cleared_messages.append(f"• รอบที่ {loop_count + 1} (Type {noti_type}): {clean_text}")
            
            # ⚡ สั่งคลิกอ่านเพื่อเคลียร์แจ้งเตือน (ส่งสัญญาณ AJAX ไปยังเซิร์ฟเวอร์)
            await current_noti.click()
            loop_count += 1
            is_clean_at_start = False  # มีการเคลียร์ แปลว่าหน้าเว็บไม่ได้คลีนตั้งแต่แรก
            
            # 🎯 [AJAX Optimization] จำลองพฤติกรรม wait_for(state="detached") ใน nodriver
            # โดยดักเช็กว่าตัวแปรอิลิเมนต์นี้ได้ถูกถอดถอน (Remove) ออกจากหน้าเว็บไปแล้วหรือยัง
            is_detached = False
            for _ in range(15):  # ลูปเช็กย่อยรอบละ 0.2 วินาที (รวมเป็น 3 วินาทีสูงสุด)
                await asyncio.sleep(0.2)
                try:
                    # ถ้าระบบ AJAX ทำงานเสร็จสิ้น ปุ่มนี้จะหายไปจาก DOM สั่งเช็กผ่าน JavaScript
                    # (ใช้การเปรียบเทียบในฝั่งเพจว่าอิลิเมนต์ตัวนี้หลุดออกจากโครงสร้างหน้าหลักหรือยัง)
                    still_connected = await page.evaluate(
                        f"document.body.contains(document.querySelector('a[href=\"{target_href}\"]'))"
                    )
                    if not still_connected:
                        is_detached = True
                        break
                except:
                    is_detached = True
                    break
            
            if is_detached:
                await asyncio.sleep(0.3)  # หน่วงสั้นๆ เพื่อให้ DOM เรียงตัวใหม่เสร็จสิ้น
            else:
                # 🔄 [Fallback Safeguard] หากปุ่มไม่ยอมหายไปใน 3 วินาที (AJAX หน่วงหรือค้าง)
                print(f"🔄 [{site_name}] AJAX ตอบสนองช้า สั่งรีโหลดหน้าแรกเพื่ออัปเดตแผงควบคุม...")
                await page.get(index_url)
                await asyncio.sleep(2)
                
        except Exception as e:
            print(f"⚠️ [{site_name}] เกิดข้อผิดพลาดในลูปเคลียร์แจ้งเตือน: {e}")
            break

    # -------------------------------------------------------------------------
    # 🎯 ระบบสรุปยอดรายงานผลส่งเข้า Discord ท้ายฟังก์ชัน
    # -------------------------------------------------------------------------
    has_notify_func = False
    try:
        # ดึงฟังก์ชันเช็กจาก Global scope ทั่วไป
        notify_fn = globals().get('send_notify')
        if notify_fn and callable(notify_fn):
            has_notify_func = True
    except:
        pass

    if has_notify_func:
        if cleared_messages:
            # 📝 เคสที่ 1: มีข้อความที่ถูกล้างไป
            report_msg = [f"📥 <b>[{site_name}] ล้างกล่องแจ้งเตือน (AJAX Mode) สำเร็จ!</b>"]
            report_msg.append(f"🧹 เคลียร์สตรีมมิ่งไปทั้งหมด <b>{len(cleared_messages)}</b> ฉบับ:")
            report_msg.extend(cleared_messages[:10]) 
            if len(cleared_messages) > 10:
                report_msg.append(f"<i>... และรายการอื่น ๆ อีก {len(cleared_messages) - 10} ฉบับ</i>")
            
            # ตรวจสอบว่าเป็นโค้ดแบบ Async หรือไม่
            if asyncio.iscoroutinefunction(notify_fn):
                await notify_fn("\n".join(report_msg))
            else:
                notify_fn("\n".join(report_msg))
                
        elif is_clean_at_start:
            # ✨ เคสที่ 2: ไม่มีแจ้งเตือนใดๆ ค้างตั้งแต่แรก
            msg = f"📥 <b>[{site_name}]</b> ตรวจสอบแล้ว <u>ไม่มีข้อความแจ้งเตือนใหม่</u> กล่องข้อความสะอาดเรียบร้อยดี"
            if asyncio.iscoroutinefunction(notify_fn):
                await notify_fn(msg)
            else:
                notify_fn(msg)
    else:
        print(f"📢 [{site_name}] เสร็จสิ้นกระบวนการเคลียร์แจ้งเตือน (ไม่ได้ส่งรายงาน Discord เนื่องจากไม่พบฟังก์ชัน send_notify)")
        
    return True

def check_item_urgency(exp_time_str):
    try:
        if exp_time_str == "N/A": return False
        now = get_now()

        # แก้ไข Format: BearBit มักใช้ วัน-เดือน-ปี (ค.ศ.)
        if "-" in exp_time_str:
            # ใช้ %d-%m-%Y แทน %Y-%m-%d ตามลักษณะเว็บไทย
            exp_dt = datetime.strptime(exp_time_str, "%d-%m-%Y %H:%M:%S")
        else:
            exp_dt = datetime.strptime(exp_time_str, "%H:%M:%S").replace(
                year=now.year, month=now.month, day=now.day
            )

        diff = (exp_dt - now).total_seconds() / 60
        # แจ้งเตือนถ้าเหลือน้อยกว่า 30 นาที
        return 0 < diff <= 30
    except:
        return False

RE_ITEM_ROW = re.compile(r"Item\s*Status|สถานะ\s*ไอเทม", re.I)
RE_EXP_DATE = re.compile(r"(\d{2}[-/]\d{2}[-/]\d{4}\s+\d{2}:\d{2}:\d{2})")

def get_bearbit_item_status(soup):
    try:
        active_item = "NONE"
        display_exp = "N/A"
        
        # 1. ค้นหาเนื้อหาแบบจำกัดขอบเขต
        target_element = soup.find(string=RE_ITEM_ROW)
        clean_text = ""
        
        # ค้นหาภาพไอเทมในขอบเขตเดียวกัน
        img_src = ""
        if target_element:
            parent = target_element.find_parent(["tr", "table"])
            if parent:
                clean_text = " ".join(parent.get_text(" ", strip=True).split())
                # ค้นหารูปภาพภายใน parent นั้น
                img_tag = parent.find("img", src=re.compile(r"pic/item/item\d+\.gif", re.I))
                if img_tag:
                    img_src = img_tag.get('src', '')

        # 2. ถ้าไม่เจอค่อยใช้ Fallback (ตรวจสอบภาพในทั้งหน้า)
        if not img_src:
            img_tag = soup.find("img", src=re.compile(r"pic/item/item\d+\.gif", re.I))
            if img_tag:
                img_src = img_tag.get('src', '')

        # 3. ลอจิกไอเทม (รวมทั้ง Keyword และ Image Path)
        # ตรวจสอบจาก img_src ก่อน ถ้าเจอให้ข้ามไปเลย
        if "item1.gif" in img_src:
            active_item = "FREELOAD_100"
        elif "item3.gif" in img_src:
            active_item = "FREELOAD_50"
        elif "item5.gif" in img_src:
            active_item = "FREELOAD_10"
        elif "item6.gif" in img_src:
            active_item = "FREELOAD_15"
        else:
            # ถ้าไม่เจอจากรูป ให้เช็คจาก Keyword (Fallback)
            item_map = {
                "FREELOAD_100": ["ซานตาคลอส", "100%", "Santa Claus"],
                "FREELOAD_50": ["ตุ๊กตาซานต้า", "50%", "Santa Doll"], 
                "FREELOAD_15": ["หยินหยาง", "15%", "Yin Yang"],
                "FREELOAD_10": ["แหวนครองพิภพ", "10%", "One Ring"]
            }
            for key, keywords in item_map.items():
                if any(k.lower() in clean_text.lower() for k in keywords):
                    active_item = key
                    break

        # 4. ดึงวันหมดอายุ (คงเดิม)
        exp_match = RE_EXP_DATE.search(clean_text)
        if exp_match:
            display_exp = exp_match.group(1).replace("/", "-")
            try:
                if 'check_item_urgency' in globals() and check_item_urgency(display_exp):
                    display_exp += " ⚠️"
            except Exception: pass

        # 5. อัปเดต Bot Config (คงเดิม)
        if active_item != "NONE" and 'update_bot_config' in globals():
            try:
                update_bot_config(active_item)
            except Exception as e:
                print(f"⚠️ [Config Update Warning] {e}")

        return f"{active_item} ({display_exp})"

    except Exception as e:
        print(f"❌ [Critical Error in Parser] {e}")
        return "NONE"

def update_bot_config(active_item):
    global CFG
    if not CFG or 'SETTING' not in CFG: return

    discounts = {
        "FREELOAD_100": 100,
        "FREELOAD_50": 50,
        "FREELOAD_30": 30,
        "FREELOAD_15": 15,
        "FREELOAD_10": 10
    }

    current_discount = discounts.get(active_item, 0)
    CFG['SETTING']['CURRENT_DISCOUNT'] = current_discount

    if current_discount == 100:
        # โหมดฟรี 100%: ไม่ต้องสนหน้าเว็บ ไม่ต้องสน Pending เพราะเราฟรีแน่นอน
        CFG['SETTING']['FREELOAD_ENABLE'] = True
        CFG['SETTING']['MIN_FREE_PERCENT'] = 0
        CFG['SETTING']['EXCLUDE_WEB_FREE'] = False # ไม่ต้องเลี่ยงไฟล์ฟรี เพราะยังไงเราก็ฟรี
        print("🚀 [FREE 100% MODE]: กวาดทุกไฟล์ไม่สนหน้าเว็บ (เน้นเก็บยอดอัปโหลด)")

    elif current_discount > 0:
        # โหมดมีส่วนลด (เช่น 50%): ต้องใช้ลอจิกคัดกรองความคุ้มค่า
        CFG['SETTING']['FREELOAD_ENABLE'] = True
        CFG['SETTING']['MIN_FREE_PERCENT'] = 0
        CFG['SETTING']['EXCLUDE_WEB_FREE'] = True
        print(f"⚠️ [DISCOUNT {current_discount}% MODE]: เน้นไฟล์ที่ใช้ไอเทมแล้วคุ้มกว่าหน้าเว็บ")

    else:
        # โหมดปกติ: ไอเทมหมดอายุ
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                new_cfg = json.load(f)
                CFG['SETTING'].update(new_cfg.get('SETTING', {}))

            # --- เสริมกำแพงป้องกัน ---
            CFG['SETTING']['CURRENT_DISCOUNT'] = 0

            print("🛡️ [NORMAL MODE]: กลับสู่โหมดปกติ")
        except Exception as e:
            # กรณีโหลดไฟล์ไม่สำเร็จ ให้ใช้ค่า Hard-coded ที่ปลอดภัยที่สุด
            CFG['SETTING']['CURRENT_DISCOUNT'] = 0
            print(f"❌ Error reloading config: {e} | Switching to Emergency Safety Mode")

async def auto_vote_snatched(page: uc.Tab, base_url: str, site_name: str = "BEARBIT") -> bool:
    try:
        max_p = 5
        total_voted = 0
        snatch_url = f"{base_url.rstrip('/')}/snatchdown.php"
        
        print(f"🗳️ [{site_name}] เริ่มระบบ Auto-Vote...")
        
        # 1. บังคับเปลี่ยน URL และรอจนโหลดเสร็จจริงๆ
        await page.get(snatch_url)
        await asyncio.sleep(3) # รอให้แน่ใจว่าหน้าโหลดครบ
        
        vote_img_selector = 'img[src*="v5.1.1.png"], img[title="ยอดเยี่ยม"]'
        
        for p_idx in range(1, max_p + 1):
            # 2. ป้องกันกรณีหลุดไปหน้าอื่น (เช็ค URL ทุกครั้งก่อนเริ่ม Loop)
            current_url = await page.evaluate("window.location.href")
            if "snatchdown.php" not in current_url:
                print(f"⚠️ [{site_name}] ตรวจพบการแทรกแซง! กำลังดึง Tab กลับมาที่ Snatchdown...")
                await page.get(snatch_url)
                await asyncio.sleep(3)

            all_vote_btns = await page.select_all(vote_img_selector)
            
            if not all_vote_btns:
                print(f"✅ [{site_name}] หน้า {p_idx} ไม่มีรายการค้าง - ตรวจสอบเสร็จสิ้น")
                break
                
            print(f"🔍 [{site_name}] พบ {len(all_vote_btns)} รายการ (หน้า {p_idx})")
            
            for vote_btn in all_vote_btns:
                try:
                    await vote_btn.click()
                    total_voted += 1
                    await asyncio.sleep(random.uniform(0.8, 1.5))
                except Exception:
                    continue
            
            # 3. เช็คปุ่มถัดไป
            next_btn = await page.select('img[src*="nextpage.gif"]')
            if not next_btn or p_idx >= max_p:
                break
            
            print(f"➡️ ไปหน้า {p_idx + 1}...")
            await next_btn.click()
            await asyncio.sleep(4.0) 
        
        # ส่วน Notify
        notify_fn = globals().get('send_notify')
        if callable(notify_fn):
            msg = f"🗳️ <b>[{site_name}]</b> Auto-Vote สำเร็จ: <b>{total_voted}</b> รายการ" if total_voted > 0 else f"🗳️ <b>[{site_name}]</b> Auto-Vote : สถานะสะอาดเรียบร้อย ✨"
            
            if asyncio.iscoroutinefunction(notify_fn):
                await notify_fn(msg)
            else:
                notify_fn(msg)
                
    except Exception as e:
        print(f"❌ [{site_name}] Vote Error: {e}")
        return False
    return True
    
# ========================= Smart Node Controller =========================

def calculate_task_weight(size_gb):
    """คำนวณน้ำหนักไฟล์: ปรับให้ไฟล์เล็กมีน้ำหนักน้อยลงเพื่ออัดงานได้มากขึ้น"""
    if size_gb < 5: return 0.5  # ไฟล์จิ๋ว ให้ความสำคัญต่ำมาก อัดได้รัวๆ
    elif size_gb < 15: return 1.5
    elif size_gb < 40: return 2.5
    return 4.0 # ไฟล์ใหญ่เบิ้ม กิน Disk I/O สูง

def get_node_dynamic_cap(node, disk_type):
    """คำนวณ Cap แบบสายซิ่ง: เน้นอัดงานเข้า Disk ให้เต็มประสิทธิภาพ"""
    # ปรับ Base Caps ให้สูงขึ้น (NVME/SSD อัดได้มากกว่าเดิม 2 เท่า)
    base_caps = {
        'NVME': 30,   # เดิม 15
        'SSD': 20,    # เดิม 10
        'HYBRID': 12,
        'HDD': 8
    }
    base = base_caps.get(disk_type, 5)
    start = time.time()
    
    try:
        node.refresh_status()
        latency = (time.time() - start) * 1000
        # ปรับตัวหาร Latency ให้ 'ใจดี' ขึ้น บอทจะได้ไม่กลัวความหน่วงเล็กน้อย
        div = 150 if disk_type == 'HDD' else 80
        proxy_wait = max(0, (latency - 150) / div) # ยืดหยุ่น latency จาก 200 เป็น 150
    except Exception as e:
        node.is_connected = False
        proxy_wait = 10 # ลด penalty เมื่อ API error เพื่อให้กลับมาทำงานไวขึ้น
        print(f"⚠️ [{node.name}] API Error: {e}")

    # --- [ปรับปรุง Space Factor: สายเปย์] ---
    # ยอมให้รับงานหนักได้จนถึงพื้นที่เหลือ 30%
    free_percent = (node.free_gb / (node.quota_gb or 1000)) * 100

    if free_percent > 60:
        space_factor = 6.0   # เร่งเครื่องเต็มสูบ
    elif free_percent > 30:
        space_factor = 4.0   # ยังซิ่งได้
    elif free_percent > 15:
        space_factor = 2.0   # เริ่มคุมความเร็ว
    elif free_percent > 5:
        space_factor = 1.0   # ประคองตัว
    else:
        space_factor = 0.0   # Safety Stop ที่ 5% (ประหยัดพื้นที่กว่าเดิม)

    # --- [Pending Brake: ปรับให้ยืดหยุ่นขึ้น] ---
    pending_gb = getattr(node, 'pending_gb', 0)
    # ถ้า Disk แรง (NVME/SSD) ไม่ต้องเบรกแรง แม้มีงานค้างเยอะ
    pending_limit = 250 if disk_type in ['NVME', 'SSD'] else 100
    if pending_gb > pending_limit:
        space_factor *= 0.7 # ลดลงแค่ 30% พอ ไม่ต้องหารครึ่ง งานจะได้ไม่ขาดช่วง
        print(f"⚠️ [{node.name}] Pending Warning: {pending_gb:.1f}GB | Soft Brake Active")

    # คำนวณ Final Capacity
    reduction_factor = {'NVME': 15, 'SSD': 10, 'HYBRID': 7, 'HDD': 5}.get(disk_type, 5)
    latency_cap = int(base / (1 + (proxy_wait / reduction_factor)))

    # ปรับจังหวะสุดท้าย: การันตีขั้นต่ำ 3 งานเสมอสำหรับ Node ที่ยังไม่ตาย
    return max(3, int(latency_cap * space_factor)), proxy_wait

def get_node_current_weight(node):
    active_torrents = node.get_active_downloads()
    total_weight = 0
    now = time.time()

    for t in active_torrents:
        # ดึงค่าสถานะตรงๆ จาก qBittorrent
        state = t.get('state', '').lower()
        dl_speed = t.get('dlspeed', 0)
        progress = t.get('progress', 0)
        size_gb = t.get('size_bytes', 0) / (1024**3)
        
        # คำนวณ Weight มาตรฐานตามขนาดไฟล์
        weight = calculate_task_weight(size_gb)

        # --- [NEW] Logic สำหรับจัดการไฟล์กั๊กที่ (Stalled / Queued) ---
        
        # ถ้าสถานะเป็น Stalled หรือโดน Queue ไว้ (ยังไม่วิ่ง)
        # หรือความเร็วเป็น 0 นานเกิน 5 นาที (idle_time)
        last_activity = t.get('last_activity', now)
        idle_time = now - last_activity

        # 1. ระดับรุนแรงที่สุด: Stalled หรือนิ่งสนิท
        if 'stalled' in state or (dl_speed == 0 and idle_time > 300):
            weight *= 0.1 
            
        # 2. ระดับกั๊กหนัก: วิ่งต่ำกว่า 500 KB/s (พวกกั๊กเม็ดชัดเจน)
        elif dl_speed < (500 * 1024) and progress > 0.05:
            weight *= 0.2
            print(f"🕵️ [Anti-Gak] พบไฟล์กั๊กสปีดต่ำมาก: {t.get('name')[:20]}... | Weight -> {weight:.2f}")

        # 3. ระดับกั๊กปานกลาง: วิ่งต่ำกว่า 2 MB/s (สปีดไม่สมศักดิ์ศรี Seedbox)
        elif dl_speed < (2 * 1024 * 1024) and progress > 0.01:
            weight *= 0.4 # ปรับเพิ่มจาก 0.3 เป็น 0.4 เพื่อไม่ให้รับงานใหม่รัวจน Disk พัง

        total_weight += weight
            
    return total_weight

# ========================= MAIN FUNCTIONS =========================

def clean_name(text):
    if not text: 
        return ""
    # ลบ HTML Tags (ถ้ามี)
    text = re.sub(r'<[^>]*>', '', text)
    # ลบอักขระที่ห้ามใช้ตั้งชื่อไฟล์: \ / : * ? " < > |
    text = re.sub(r'[\\/*?:"<>|]', '', text)
    # ลบช่องว่างที่ซ้ำซ้อน
    text = " ".join(text.split())
    return text.strip()

def is_fresh_and_racing(data, max_age_hours=24):
    try:
        if not data or not data.get('id'): return False
        
        now = get_now()
        short_title = data['title'][:30]
        raw_text = data.get('raw_text', '') 
        
        # 1. เช็ค Locked
        if data.get('is_locked'):
            print(f" ⏭️ ข้าม: [Locked/Banned] {short_title}")
            return False

        # 2. 🔥 ยุทธศาสตร์สกัดเวลา: โฟกัสตารางวันลงของระบบเว็บอย่างเดียว (Pure System Date)
        time_str = ""
        
        # กฎข้อที่ 1: ตรวจจับ 'วันนี้/เมื่อวาน' ของระบบบอร์ดก่อน (เป็นระเบียบและแน่นอนที่สุด)
        if 'วันนี้' in raw_text:
            t_match = re.search(r'(\d{2}:\d{2}:\d{2})', raw_text)
            time_part = t_match.group(1) if t_match else now.strftime('%H:%M:%S')
            time_str = f"{now.strftime('%Y-%m-%d')} {time_part}"
        elif 'เมื่อวาน' in raw_text:
            yesterday = now - timedelta(days=1)
            t_match = re.search(r'(\d{2}:\d{2}:\d{2})', raw_text)
            time_part = t_match.group(1) if t_match else "00:00:00"
            time_str = f"{yesterday.strftime('%Y-%m-%d')} {time_part}"
        
        # กฎข้อที่ 2: ใช้ค่า raw_date จากคอลัมน์วันลงในระบบตารางที่คัดกรองชื่อไฟล์ออกไปแล้ว
        if not time_str:
            time_str = data.get('raw_date', '')

        # กฎข้อที่ 3: มาตรการเซฟตี้สุดท้าย
        if not time_str:
            time_str = now.strftime('%Y-%m-%d %H:%M:%S')

        # 3. 🛠 จัดการฟอร์แมตและแปลงเป็น datetime
        try:
            clean_time = time_str.strip().replace('/', '-').replace('.', '-')
            
            if ' ' not in clean_time and len(clean_time) <= 10:
                clean_time += " 00:00:00"
            
            clean_time = clean_time[:19]
            
            date_blocks = clean_time.split(' ')[0].split('-')
            if len(date_blocks[0]) == 4:
                fmt = '%Y-%m-%d %H:%M:%S'
            elif len(date_blocks[-1]) == 4:
                fmt = '%d-%m-%Y %H:%M:%S'
            elif len(date_blocks[0]) == 2 and int(date_blocks[0]) > 12:
                fmt = '%y-%m-%d %H:%M:%S'
            else:
                fmt = '%d-%m-%y %H:%M:%S'
            
            naive_time = datetime.strptime(clean_time, fmt)
            upload_time = tz.localize(naive_time)
            
        except Exception:
            upload_time = now

        # 4. Racing Logic (คำนวณอายุจากตารางวันลงที่แท้จริง)
        age_delta = now - upload_time
        total_hours = age_delta.total_seconds() / 3600
        
        if age_delta.total_seconds() < -300: return False

        demand_ratio = data['leechers'] / max(1, data['seeders'])

        print(f" 📊 System Date: {short_title}.. (S:{data['seeders']} L:{data['leechers']} Age:{total_hours:.1f}ชม.)")

        # --- เงื่อนไขการกรอง ---
        if total_hours > max_age_hours:
            print(f" ⏭️ ข้าม: [เก่าเกิน {max_age_hours} ชม.]")
            return False

        if data['leechers'] < 1:
            print(f" ⏭️ ข้าม: [ไม่มีคนโหลด]")
            return False
            
        return True
    except Exception as e:
        print(f" ⚠️ Filter Error: {e}")
        return False

def generate_main_status(config, site_name=""):
    SET = config.get('SETTING', {})
    
    # ดึงค่าพื้นฐานจาก Config เดียวกัน
    min_gb = SET.get('MIN_SIZE_GB', 5.0)
    max_gb = SET.get('MAX_SIZE_GB', 150.0)
    is_freeload = SET.get('FREELOAD_ENABLE', True)
    min_percent = SET.get('MIN_FREE_PERCENT', 0)

    status_text = "เปิด" if is_freeload else "ปิด"
    
    # --- Logic แยกการแสดงผลเฉพาะบางเว็บ ---
    # รายชื่อเว็บที่ต้องการให้แสดง % ฟรีโหลด (ตัวอย่างเช่น UnlimitZ)
    sites_with_percent = ['bearbit'] 
    
    # ตรวจสอบเงื่อนไข:
    # 1. ต้องเปิด FREELOAD
    # 2. ต้องมีค่าเปอร์เซ็นต์ > 0
    # 3. ชื่อเว็บต้องอยู่ในลิสต์ที่กำหนด (ใช้ .lower() เพื่อป้องกันความผิดพลาด)
    if is_freeload and min_percent > 0 and any(s in site_name.lower() for s in sites_with_percent):
        freeload_info = f" (ขั้นต่ำ: {min_percent}%)"
    else:
        # สำหรับเว็บอื่นๆ หรือถ้าค่าเป็น 0 จะไม่แสดง % ให้รก
        freeload_info = ""  
    
    return f"⚙️ เงื่อนไข: ขนาด {min_gb:.1f}-{max_gb:.1f}GB | ฟรีโหลด {status_text}{freeload_info}"
    
async def get_valid_tab(browser):
    try:
        # พยายามปิด Tab เก่าทิ้งก่อนสร้างใหม่เสมอ
        return await browser.get('about:blank')
    except Exception:
        return None

async def ensure_active_page(browser, page, site_cfg):
    """ ปรับปรุงให้ทนทานต่อสถานะ Page ที่พังไปแล้ว """
    
    # ถ้าตัวแปร page พังไปแล้วหรือเป็น None ต้องสร้างใหม่ทันที
    if page is None:
        return await browser.get("about:blank", new_tab=True)
    
    try:
        # ใช้การทดสอบเบาที่สุด: เช็คว่า page มีการตอบสนองไหม
        # ถ้า page พัง มันจะโยน error ออกมาให้เราจับใน except ทันที
        await page.evaluate("document.title") 
        return page
    except:
        # ถ้าเข้าตรงนี้ แปลว่า page พังแน่นอนแล้ว
        print(f"⚠️ [{site_cfg['name']}] ตรวจพบ Tab พัง กำลังคืนค่า None เพื่อให้ลูปสร้างใหม่...")
        return None

def get_val(obj, key, default=None):
    """ฟังก์ชันช่วยดึงค่าจากทั้ง Dictionary และ Object"""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)

def is_cloudflare(soup):
    # 1. เช็ค Title (ครอบคลุมกรณี "Just a moment..." หรือ "Attention Required!")
    title = soup.title.string.strip() if soup.title else ""
    if any(text in title for text in ["Just a moment", "Attention Required"]):
        return True
        
    # 2. เช็คจาก Selector ที่ Cloudflare ใช้เรียก Challenge
    # เพิ่ม 'cf-wrapper' หรือ 'challenge-running' ซึ่งมักจะติดมาด้วย
    if soup.find(id="cf-content") or soup.find("div", {"class": ["cf-browser-verification", "cf-wrapper"]}):
        return True
        
    # 3. เช็คจากข้อความใน Body (เน้นคำสำคัญ)
    body_text = soup.get_text(" ", strip=True).lower()
    cloudflare_keywords = ["cloudflare", "checking if the site connection is secure"]
    if all(keyword in body_text for keyword in cloudflare_keywords):
        return True
    
    # 4. เช็คความผิดปกติ (หน้าเว็บว่างเปล่าแต่มี meta robots noindex, nofollow)
    # หน้า Cloudflare มักใส่ tag นี้ไว้เพื่อไม่ให้ Google Index หน้า Challenge
    if soup.find("meta", attrs={"name": "robots", "content": "noindex,nofollow"}):
        # ถ้าเจอ meta นี้ร่วมกับความยาว body ที่สั้นผิดปกติ ให้สงสัยไว้ก่อนว่าเป็น CF
        if len(body_text) < 500:
            return True
            
    return False

class BrowserSessionWrapper:
    def __init__(self, browser_instance):
        self.browser = browser_instance
        
    async def get_cookies(self):
        try:
            # ดึงคุกกี้ทั้งหมดจาก Browser Instance
            return await self.browser.get_cookies()
        except Exception:
            return []
    class MockResponse:
                def __init__(self, raw_bytes):
                    self.content = raw_bytes
                    self.text = raw_bytes.decode('utf-8', errors='ignore')
                    self.status_code = 200

    async def get(self, url, headers=None, timeout=15):
        try:
            if not url:
                return None

            tab = self.browser.main_tab
        
            # 1. ใช้ handler ดัก Request ขาออกเพื่อแทรก Headers
            # วิธีนี้ปลอดภัยที่สุดเพราะไม่ต้องเรียกใช้ cdp.network ที่มีปัญหา
            async def add_headers(event: uc.cdp.network.RequestWillBeSent):
                if headers:
                    event.request.headers.update(headers)
        
            # ลงทะเบียน Handler (ทำก่อนสั่ง get)
            tab.add_handler(uc.cdp.network.RequestWillBeSent, add_headers)

            # 2. โหลด URL
            await tab.get(url)
            await tab.wait(timeout)
        
            # 3. ดึง Content
            content = await tab.get_content()
        
            if not content:
                return None
            
            return self.MockResponse(content.encode('utf-8', errors='ignore'))

        except Exception as e:
            print(f"❌ Error ในการดึงข้อมูล: {e}")
            return None

async def safe_timeout(coro, timeout_sec):
    if sys.version_info >= (3, 11):
        # ถ้าเป็น Python 3.11+ ใช้ syntax ใหม่ที่ดูสวยกว่า
        async with asyncio.timeout(timeout_sec):
            return await coro
    else:
        # ถ้าเป็นเวอร์ชันเก่า ใช้ wait_for
        return await asyncio.wait_for(coro, timeout=timeout_sec)

async def main():
    global browser_instance
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(handle_exit(s)))
        
    startup_msg = "🚀 Universal Auto-Pilot (nodriver Edition) : Started"
    print(startup_msg)
    
    asyncio.create_task(safe_send_notify(startup_msg))
    browser_instance = None
    resp = None
    consecutive_errors = 0
    
    while not stop_event.is_set():
        try:
            if stop_event.is_set(): break
            global CFG
            CFG = load_full_config()
            SET = CFG.get('SETTING', {})
            global_clean = CFG.get('GLOBAL_CLEAN', {})
            active_nodes = []
            node_status_buffer = []

            # =================================================================
            # 1. NODE SECTION (Checking & Cleanup & Update Trackers)
            # =================================================================
            print("\n🔌 NODE STATUS CHECKING...")
            for n_cfg in CFG['NODES']:
                if stop_event.is_set(): break
                if not n_cfg.get('enable'): 
                    continue
                
                node = RtorrentNode(n_cfg) if n_cfg.get("type") == "rtorrent" else QbitNode(n_cfg)

                if node.login():
                    node.refresh_status()
                    pre_free = node.free_gb

                    is_system_emergency = pre_free < 15.0 

                    NodeCleaner(node, n_cfg.get('clean_settings', {}), global_clean).process(force_emergency=is_system_emergency)
                    await asyncio.sleep(2)  

                    if hasattr(node, '_sweeper_force_start'):
                        node._sweeper_force_start()

                    node.reannounce_all()
                    node.refresh_status()

                    gained = node.free_gb - pre_free
                    if gained > 0.01:
                        print(f"✨ [{node.name}] Cleaned up: {gained:.2f} GB recovered!")

                    active_nodes.append((node, n_cfg))
                    icon = "🟢"
                else: 
                    icon = "❌"
                
                line = f"{icon} [{node.name}] {getattr(node, 'stat_msg', 'N/A')}"
                print(line)
                node_status_buffer.append(line)
                update_trackers(node)

            if active_nodes:
                print("⏳ Waiting 5s for trackers to sync with All Trackers")
                await asyncio.sleep(5)

            if node_status_buffer:
                msg = "🔌 <b>Node Status Report</b>\n" + "\n".join(node_status_buffer)
                asyncio.create_task(safe_send_notify(msg))

            if not active_nodes:
                print("⚠️ [Warning] ไม่มี Node ไหนพร้อมใช้งานในรอบนี้ ข้ามไปรอรอบถัดไป")
                await asyncio.sleep(60)
                continue

            # =================================================================
            # 2. BROWSER SECTION (nodriver Implementation)
            # =================================================================

            # ตรวจสอบว่ามี instance หรือไม่ และยังเชื่อมต่ออยู่หรือไม่ (Is connected?)
            is_browser_healthy = False
            if browser_instance is not None:
                try:
                    # ใช้การดึงข้อมูลสั้นๆ เพื่อทดสอบว่า Browser ยังตอบสนองไหม
                    await browser_instance.target.get_targets()
                    is_browser_healthy = True
                except Exception:
                    print("⚠️ ตรวจพบการเชื่อมต่อ Browser ขัดข้อง, กำลังรีเซ็ต...")
                    browser_instance = None # สั่งรีเซ็ต

            if not is_browser_healthy:
                print("🌐 กำลังเริ่ม Browser instance ใหม่...")
                try:
                    browser_instance = await launch_any_browser(stealth_args)
                except Exception as e:
                    print(f"❌ ไม่สามารถเปิด Browser ได้: {e}")
                    await asyncio.sleep(30) # รอถ้าเปิดไม่ได้
                    continue # ข้ามรอบนี้ไป
            
            target_sites_cfg = [s for s in CFG.get('SITE', []) if s.get('enable', True)]
            print(f"📡 Detected Sites: {[s['name'] for s in target_sites_cfg]}")
            site_page = await browser_instance.get("about:blank", new_tab=True)
            dl_session = BrowserSessionWrapper(browser_instance) 

            for site_cfg in target_sites_cfg:
                if stop_event.is_set(): break
                site = site_cfg['name']
                current_site_seen_file = get_seen_file(site)
                seen_ids = load_data(current_site_seen_file) 
                current_site_hash_file = get_hash_file(site)
                seen_hashes = load_data(current_site_hash_file)
                data_saved = False
        
                try:
                    if site_page is None:
                        print(f"❌ [{site}] ไม่สามารถสร้าง Tab ใหม่ได้")
                        continue
                    login_result = await safe_await(ensure_site_logged_in(site_page, site_cfg), "SiteLogin")
                    if login_result is True:
                        try:
                            cookies = await site_page.send(cdp.network.get_cookies())
                            # ในบาง library ผลลัพธ์ที่ได้อาจอยู่ใน ['cookies']
                            if isinstance(cookies, dict) and 'cookies' in cookies:
                                cookies = cookies['cookies']

                            target_domain = site_cfg.get('base_url').split('//')[-1].split('/')[0]

                            for cookie in cookies:
                                # 1. จัดการข้อมูลให้เป็น dictionary เสมอ
                                # ถ้า cookie เป็น object ให้แปลงเป็น dict ด้วย .__dict__ หรือเข้าถึงแบบ dict
                                c_data = cookie if isinstance(cookie, dict) else getattr(cookie, '__dict__', {})
    
                                c_name = c_data.get('name')
                                c_value = c_data.get('value')
                                c_domain = c_data.get('domain', '')
                                c_path = c_data.get('path', '/')
                                c_secure = c_data.get('secure', False)

                                if target_domain in c_domain:
                                    # หาก dl_session เป็น requests.Session
                                    if hasattr(dl_session, 'cookies'):
                                        dl_session.cookies.set(c_name, c_value, domain=c_domain, path=c_path, secure=c_secure)
            
                            print(f"✅ [{site}] ดึงคุกกี้สดเข้า Session สำเร็จ ({len(cookies)} cookies)")

                            # 3. บันทึกไฟล์
                            auth_file = get_auth_file(site)
                            with open(auth_file, "w") as f:
                                cookie_list = [{
                                    'name': (c.name if hasattr(c, 'name') else c['name']),
                                    'value': (c.value if hasattr(c, 'value') else c['value']),
                                    'domain': (c.domain if hasattr(c, 'domain') else c['domain']),
                                    'path': (c.path if hasattr(c, 'path') else c.get('path', '/')),
                                    'secure': (c.secure if hasattr(c, 'secure') else c.get('secure', False))
                                } for c in cookies if target_domain in (c.domain if hasattr(c, 'domain') else c['domain'])]
                                json.dump(cookie_list, f)

                        except Exception as cookie_err:
                            print(f"⚠️ [{site}] ไม่สามารถดึงคุกกี้: {cookie_err}")
                            
                        stats_data = await get_site_stats(site_page, site_cfg)
                        print(stats_data)

                        if stats_data and isinstance(stats_data, str):
                            asyncio.create_task(safe_send_notify(stats_data))
                        else:
                            print(f"⚠️ [{site}] ข้อมูลสถิติไม่สมบูรณ์ หรือได้ NoneType, ข้ามการส่ง Notification")

                        base_url = site_cfg.get('base_url')
                        site_target_urls = site_cfg.get('target_urls', [])
                            
                        for target_item in site_target_urls:
                            if stop_event.is_set(): break
                            site_page = await ensure_active_page(browser_instance, site_page, site_cfg)
                            if not site_page:
                                # พยายามสร้างใหม่แค่ครั้งเดียวต่อโซน ถ้าไม่ได้ให้ข้ามโซนนี้ไป ไม่ใช่ข้ามทั้งเว็บ
                                print(f"⚠️ [{site}] Tab พัง พยายามสร้างใหม่...")
                                site_page = await browser_instance.get("about:blank", new_tab=True)
                                await site_page.get(site_cfg['url'])

                            if isinstance(target_item, dict):
                                if not target_item.get('enable', True): continue
                                target_url = target_item.get('url')
                                display_zone = target_item.get('name', "Zone")
                            else:
                                target_url, display_zone = target_item, "Zone"

                            if target_url.startswith('/') or not target_url.startswith('http'):
                                target_url = f"{base_url.rstrip('/')}/{target_url.lstrip('/')}"

                            try:
                                print(f"\n🌐 [{site}] Scanning: [{display_zone}]")
                                    
                                if site_page is None: continue
                                    
                                await site_page.get(target_url)
                                await asyncio.sleep(2.5)
                                    
                                page_source = await site_page.get_content()
                                if not page_source:
                                    print(f"⚠️ [{site}] ได้หน้าว่างเปล่า... พยายามกู้คืน Tab")
                                    try:
                                        site_page = await browser_instance.get("about:blank", new_tab=True)
                                    except:
                                        site_page = None # หากกู้คืนไม่ได้จริงๆ ถึงค่อยยอมให้เป็น None
                                    continue

                                soup = BeautifulSoup(page_source, "html.parser")
                                
                                if is_cloudflare(soup):
                                    print(f"🛡️ [{site}] ตรวจพบ Cloudflare! กำลังเข้าสู่กระบวนการกู้คืน...")
                                    # ใส่ logic การรอ หรือแก้ Challenge ที่นี่
                                    await asyncio.sleep(10) 
                                    continue
    
                                if "ไม่สามารถเปิดลิงก์จากภายนอกได้" in soup.text:
                                    print(f"⚠️ [{site}] ติด Hotlink... กำลังใช้มาตรการย้ำหน้ากระตุ้นระบบ Referer")
                                    index_url = f"{base_url.rstrip('/')}/index.php"
                                    await site_page.get(index_url)
                                    await asyncio.sleep(1.5)
                                    print(f"DEBUG: กำลังเรียก site_page.get({target_url})")    
                                    await site_page.get(target_url)
                                    await asyncio.sleep(2.5)
                                        
                                    page_source = await site_page.get_content()
                                    soup = BeautifulSoup(page_source, "html.parser")
                                    
                                if "ไม่สามารถเปิดลิงก์จากภายนอกได้" in soup.text:
                                    print(f"❌ [{site}] ระบบความปลอดภัยเข้มงวดเกินไป ข้ามโซน [{display_zone}] ไปก่อน")
                                    continue
                            except Exception as e:
                                print(f"❌ [{site}] Error ระหว่างเข้าหน้า {display_zone}: {e}")
                                continue

                            added_in_zone = [] 
                            full_nodes_in_zone = []
                            error_logs = []
                            count_skip = 0    
                                
                            all_details = soup.find_all("a", href=re.compile(r"details(new)?\.php\?id=\d+"))
                            rows = []

                            for a in all_details:
                                if stop_event.is_set(): break
                                t_text = a.get_text(strip=True)
                                if len(t_text) <= 5: 
                                    continue
                                    
                                parent_tr = a.find_parent("tr")
                                if parent_tr and parent_tr not in rows:
                                    row_raw_text = parent_tr.get_text().lower()
                                    user_stat_keywords = ['ratio:', 'bonus:', 'upload:', 'download:', 'อัพโหลด:', 'ดาวน์โหลด:']
                        
                                    if any(key in row_raw_text for key in user_stat_keywords):
                                        continue
                                    rows.append(parent_tr)

                            for row in rows:
                                if stop_event.is_set(): break
                                t_id = "UNKNOWN"
                                try:
                                    local_headers = {
                                        'User-Agent': stealth_args["user_agent"],
                                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                                        'Accept-Language': 'th-TH,th;q=0.9,en-US;q=0.8,en;q=0.7',
                                        'Connection': 'keep-alive',
                                        'Referer': f"{target_url}"
                                    }
                                    data = await extract_torrent_data(row, base_url, dl_session, local_headers)
                                    
                                    if not data or not data.get('id'):
                                        print(f" ⚠️ ข้าม: สกัดข้อมูล ID ไม่สำเร็จ")
                                        continue

                                    t_id = str(data['id']) 
                                    download_url = data['download_url']
                                    details_url = data['details_url']
                                    raw_title = data.get('title', 'Unknown')

                                    if not download_url:
                                        with open(f"debug_failed_{t_id}.html", "w", encoding="utf-8") as f:
                                            f.write(resp.text)
                                        print(f" ⚠️ [{t_id}] ข้าม: ไม่พบลิงก์ดาวน์โหลด")
                                        continue

                                    if not is_fresh_and_racing(data):
                                        count_skip += 1
                                        continue  

                                    if t_id in seen_ids:
                                        print(f" ❌ ข้าม: เคยเพิ่มไปแล้ว (ใน {site})")
                                        count_skip += 1
                                        continue

                                    safe_title = clean_name(raw_title)
                                    is_stat = any(word in safe_title.lower() for word in ['ratio', 'bonus', 'upload', 'download'])
                                        
                                    if not is_stat and len(safe_title) >= 10:
                                        t_name = safe_title
                                    else:
                                        t_name = f"Torrent_ID_{t_id}"

                                    print(f"🔍 [{site.upper()}] Checking: {t_name[:50]}... (ID: {t_id})")

                                    t_size_gb = parse_size(data['size_str'])
                                    if not (SET.get('MIN_SIZE_GB', 0) <= t_size_gb <= SET.get('MAX_SIZE_GB', 999)):
                                        print(f" ❌ ข้าม: ขนาด {t_size_gb:.2f}GB ไม่ตรงเงื่อนไข")
                                        seen_ids.add(t_id) 
                                        count_skip += 1
                                        continue

                                    is_free_to_go = False
                                    is_use_item = False

                                    freeload_enable = SET.get('FREELOAD_ENABLE', True)
                                    item_discount = SET.get('CURRENT_DISCOUNT', 0)     
                                    min_free_req = SET.get('MIN_FREE_PERCENT', 0)      
                                    site_name = site.lower()
                                        
                                    if details_url and dl_session:
                                        if await check_pending_status(dl_session, details_url):
                                            print(f" ⏳ ข้าม: ไฟล์นี้ยังอยู่ในสถานะ (รอการอนุมัติ) -> {details_url}")
                                            count_skip += 1
                                            continue

                                    if not freeload_enable:
                                        is_free_to_go = True
                                        is_use_item = False
                                    elif "bearbit" in site_name:
                                        free_p = check_freeload_status(row)
                                        if item_discount > 0:
                                            if free_p > item_discount:
                                                print(f" ❌ ข้าม: หน้าเว็บฟรี {free_p}% ซึ่งดีกว่าไอเทม {item_discount}% (เก็บไอเทมไว้ก่อน)")
                                                count_skip += 1
                                                continue
                                            else:
                                                is_use_item = True
                                                is_free_to_go = True
                                                print(f" 🎫 [ITEM MODE] บังคับใช้ไอเทม {item_discount}% (หน้าเว็บฟรีแค่ {free_p}%)")
                                        else:
                                            if free_p >= min_free_req:
                                                is_free_to_go = True
                                                is_use_item = False
                                                print(f" ✅ [NORMAL MODE] หน้าเว็บฟรี {free_p}% ผ่านเกณฑ์ขั้นต่ำ ({min_free_req}%)")
                                            else:
                                                print(f" ❌ ข้าม: ไม่มีไอเทม และหน้าเว็บ ({free_p}%) ต่ำกว่าเกณฑ์ที่กำหนด ({min_free_req}%)")
                                                count_skip += 1
                                                continue
                                    else:
                                        free_p_others = check_freeload_status(row)
                                        if free_p_others == 100:
                                            is_free_to_go = True
                                        else:
                                            print(f" ❌ ข้าม: ไฟล์นี้ไม่ฟรี 100% (หน้าเว็บแจ้ง {free_p_others}%)")
                                            count_skip += 1
                                            continue

                                    if not is_free_to_go:
                                        continue

                                    current_url = "unknown"
                                    try:
                                        print(f"🚀 เริ่มดาวน์โหลดไฟล์: {t_id}")
    
                                        # 1. ใช้ Wrapper .get() ซึ่งจัดการ Browser Tab และคืนค่า MockResponse
                                        # MockResponse นี้จะมี .text (สำหรับเช็ค HTML) และ .content (สำหรับไฟล์ทอร์เรนต์)
                                        try:
                                            r_dl = await safe_timeout(dl_session.get(download_url, headers=local_headers), 30)
                                        except asyncio.TimeoutError:
                                            print("หมดเวลา!")
    
                                        if not r_dl:
                                            raise Exception("ไม่สามารถดึงข้อมูลจาก BrowserSessionWrapper ได้")

                                        # 2. ใช้ค่าจาก r_dl แทนการเรียกเมธอดที่ไม่มีอยู่
                                        current_url = dl_session.browser.main_tab.url
                                        raw_data_bytes = r_dl.content
                                        raw_data_text = r_dl.text
                                        is_torrent = raw_data_bytes.startswith(b'd8:')

                                        t_hash = None
                                        download_ready = False

                                        # 3. Logic ตรวจสอบและดาวน์โหลด
                                        if is_torrent:
                                            t_hash = extract_info_hash(raw_data_bytes)
                                            if t_hash:
                                                download_ready = True
                                                print(f"✅ พบไฟล์ทอร์เรนต์ Hash: {t_hash}")
                                        else:
                                            print(f"🔄 พบปัญหาการดาวน์โหลด (URL: {current_url}), กำลังเข้าสู่โหมดกู้คืนการคลิกผ่าน Browser...")
    
                                            # เรียกใช้ฟังก์ชันคลิกปุ่มผ่าน tab ที่มี Session ล็อกอินอยู่แล้ว
                                            raw_content = await download_torrent_via_browser(
                                                dl_session.browser.main_tab, 
                                                details_url, 
                                                download_url
                                            )
    
                                            if raw_content and raw_content.startswith(b'd8:'):
                                                raw_data_bytes = raw_content
                                                t_hash = extract_info_hash(raw_data_bytes)
                                                if t_hash:
                                                    download_ready = True
                                                    print(f"✅ กู้คืนการดาวน์โหลดสำเร็จ! Hash: {t_hash}")
                                            else:
                                                print("❌ ไม่สามารถดาวน์โหลดไฟล์ได้แม้จะลองคลิกผ่าน Browser แล้ว")

                                        # 4. ส่วนการส่งเข้า Node (เหมือนเดิม)
                                        if download_ready:
                                            # ตรวจสอบ Hash ซ้ำ
                                            if t_hash in seen_hashes:
                                                print(f" ❌ ข้าม: Hash {t_hash} ซ้ำในระบบ")
                                                seen_ids.add(t_id)
                                                download_ready = False
                                            else:
                                                is_already_in_node = False
                                                target_node_name = ""
                                                for node_obj, _ in active_nodes:
                                                    if node_obj.is_torrent_exists(t_hash):
                                                        is_already_in_node = True
                                                        target_node_name = node_obj.name
                                                        break
        
                                                if is_already_in_node:
                                                    print(f" ❌ ข้าม: ตรวจพบ Hash [...{t_hash[-5:]}] วิ่งอยู่ใน {target_node_name}")
                                                    seen_ids.add(t_id)
                                                    count_skip += 1
                                                    download_ready = False

                                        if download_ready:
                                            print(f"✅ [{site}] พร้อมส่งไฟล์เข้า Client (Hash: {t_hash})")
                                            active_nodes.sort(key=lambda x: x[0].free_gb, reverse=True)
                                            success_node = None
                                            task_weight = calculate_task_weight(t_size_gb)

                                            for node_obj, n_cfg in active_nodes:
                                                if stop_event.is_set(): break
                                                d_type = n_cfg.get('disk_type', 'HDD')
                                                dynamic_max_cap, _ = get_node_dynamic_cap(node_obj, d_type)
                                                current_load = round(get_node_current_weight(node_obj), 1)

                                                print(f"📡 Check [{node_obj.name}]: Load {current_load:.1f}/{dynamic_max_cap}")
                                                if (current_load + task_weight) > dynamic_max_cap:
                                                    print(f" ⏳ [Queue Full] {node_obj.name} ลอง Node ถัดไป")
                                                    continue

                                                effective_free_gb = node_obj.free_gb - node_obj.get_downloading_size()
                                                if effective_free_gb < (t_size_gb + 15.0):
                                                    smart_reclaim_process(node_obj, required_gb=(t_size_gb + 15.0), is_emergency=False)
                                                    node_obj.refresh_status()
                                                    effective_free_gb = node_obj.free_gb - node_obj.get_downloading_size()
                                                    if effective_free_gb < (t_size_gb + 5.0): continue

                                                try:
                                                    
                                                    # 1. ตรวจสอบให้แน่ใจว่า raw_data_bytes มีข้อมูลอยู่จริงก่อนเรียกใช้งาน
                                                    if 'raw_data_bytes' in locals() and raw_data_bytes:
        
                                                        # 2. ส่งไฟล์เข้า Seedbox โดยใช้ตัวแปรที่ถูกต้อง
                                                        result = safe_add_torrent(node_obj, raw_data_bytes, site)
                                                        if result:
                                                            success_msg = f"📥 [Success] {node_obj.name} | {t_size_gb:.1f}GB | {t_name[:40]}"
                                                            print(success_msg)
            
                                                            # อัปเดตสถานะ Node
                                                            node_obj.free_gb = max(0.0, node_obj.free_gb - (t_size_gb + 0.1))
                                                            added_in_zone.append(success_msg)
                                                            seen_ids.add(t_id)
                                                            seen_hashes.add(t_hash)
                                                            success_node = node_obj
            
                                                            # กดปุ่ม Thanks
                                                            if details_url:
                                                                new_tab = None 
                                                                try:
                                                                    # nodriver ใช้ .get(url, new_tab=True) เพื่อสร้าง Tab ใหม่และไปที่ URL ทันที
                                                                    new_tab = await browser_instance.get(details_url, new_tab=True)
        
                                                                    # ส่ง new_tab เข้าไปทำงานต่อ
                                                                    await auto_click_thanks(new_tab, details_url)
        
                                                                except Exception as e:
                                                                    print(f"⚠️ ไม่สามารถกดปุ่ม Thanks ได้: {e}")
        
                                                                finally:
                                                                    # ปิด Tab หลังจากทำงานเสร็จ
                                                                    if new_tab:
                                                                        await new_tab.close()
            
                                                            break # ส่งเข้า Node สำเร็จแล้ว ให้หยุด Loop
                                                    else:
                                                        print(f"❌ [Error] ข้อมูลไฟล์ทอร์เรนต์ (raw_data_bytes) ว่างเปล่า ไม่สามารถส่งเข้า {node_obj.name}")

                                                except Exception as e:
                                                    import traceback
                                                    print(f"❌ [Connect Error] {node_obj.name}: {str(e)}")
                                                    traceback.print_exc()

                                            if not success_node:
                                                full_nodes_in_zone.append(f"❌ [Full] {t_name[:30]}...")

                                    except asyncio.TimeoutError:
                                        print(f"⚠️ [Timeout] {t_id} ค้างนานเกินไป")
                                        continue 
                                    except Exception as e:
                                        print(f"❌ [Error] เกิดปัญหาที่ {t_id}: {str(e)}")
                                        continue

                                    
                                except Exception as e:
                                    if consecutive_errors > 3:
                                        print("⚠️ พบ Error ติดต่อกันเกิน 3 ครั้ง! กำลังรีเซ็ต Browser ด้วย nodriver...")
                                        
                                        # 1. ทำลายซาก Browser อย่างระมัดระวัง
                                        if dl_session is not None:
                                            try:
                                                # ใช้ getattr เพื่อความปลอดภัยสูงสูด
                                                browser_obj = getattr(dl_session, 'browser', None)
                                                if browser_obj is not None:
                                                    await browser_obj.stop()
                                            except Exception as stop_err:
                                                print(f"⚠️ ปิด Browser เดิมไม่สมบูรณ์: {stop_err}")
                                            finally:
                                                # ล้างค่าทิ้งเพื่อป้องกันการเรียกใช้ซ้ำ
                                                dl_session = None 
                                        
                                        # 2. สร้าง Browser ใหม่ (ต้องมั่นใจว่าฟังก์ชันนี้ไม่มีการอ้างอิงของเก่า)
                                        try:
                                            print("🚀 กำลังสร้าง Browser Instance ใหม่...")
                                            new_browser = await launch_any_browser()
                                            dl_session = BrowserSessionWrapper(new_browser)
                                            consecutive_errors = 0
                                            await asyncio.sleep(10)
                                        except Exception as launch_err:
                                            print(f"❌ ล้มเหลวในการสร้าง Browser ใหม่: {launch_err}")
                                            break
                                    else:
                                        # ถ้ายังไม่ถึง 3 ครั้ง ให้พักสั้นๆ
                                        await asyncio.sleep(5)
    
                                    continue

                                if len(added_in_zone) >= SET.get('MAX_NEW_PER_ZONE', 5): 
                                    break

                            # Summary Section
                            if len(added_in_zone) > 0 or count_skip > 0 or len(full_nodes_in_zone) > 0 or len(error_logs) > 0:
                                condition_header = generate_main_status(CFG)
                                summary_msg = (
                                    f"⚙️ <b>{condition_header}</b>\n"
                                    f"🌐 <b>Scanning:</b> [{display_zone}] {target_url}\n\n"
                                )
                                if added_in_zone: 
                                    summary_msg += "✅ <b>Added:</b>\n" + "\n".join(added_in_zone) + "\n\n"
                                
                                if full_nodes_in_zone: 
                                    summary_msg += "⚠️ <b>Queue Full:</b>\n" + "\n".join(full_nodes_in_zone) + "\n\n"
                                
                                if error_logs: 
                                    summary_msg += "🚨 <b>System Errors:</b>\n" + "\n".join(error_logs) + "\n\n"
                                
                                if not added_in_zone and not full_nodes_in_zone and not error_logs:
                                    summary_msg += "❌ ไม่มีไฟล์เข้าเงื่อนไข\n\n"

                                footer = f"📊 <b>สรุป {display_zone}:</b> เพิ่ม {len(added_in_zone)} | เต็ม {len(full_nodes_in_zone)} | ข้าม {count_skip}"
                                summary_msg += footer
                                print(f"\n{footer}")
                                    
                                await safe_send_notify(summary_msg)

                            # ✅ [FIX 3] ย้ายการเซฟประวัติเข้ามาบันทึกในจบลูปโซนย่อยทันที ข้อมูลสดใหม่ตลอดเวลา ไม่สูญหาย
                            save_data(current_site_seen_file, seen_ids)
                            save_data(current_site_hash_file, seen_hashes)
                            data_saved = True
                    else:
                        # ถ้าเป็น None (จาก Warning) หรือเป็น False (จาก logic ของฟังก์ชัน)
                        # ให้ข้ามไซต์นี้ไปทันที
                        print(f"❌ [{site}] Login ไม่สำเร็จหรือข้อมูลตอบกลับผิดพลาด")
                        continue
                except Exception as site_err:
                    print(f"🚨 [Site System Error] พังทั้งเว็บ {site}: {site_err}")
                finally:
                    # 1. เซฟข้อมูล (เผื่อกรณี error ก่อนเซฟข้อมูล)
                    if not data_saved:
                        save_data(current_site_seen_file, seen_ids)
                        save_data(current_site_hash_file, seen_hashes)
                    # ปิด Tab ของไซต์นี้ทันทีเมื่อสแกนจบ (ไม่ว่าจะพังหรือไม่)
                    if site_page:
                        await site_page.close()
                        print(f"📂 ปิด Tab ของ {site_name} เรียบร้อย")

            # ปิด Browser หลังจากปิด Tab แล้ว
            active_browser = browser_instance 
            
            if active_browser is not None:
                try:
                    if hasattr(active_browser, 'stop'):
                        # ตรวจสอบว่าเป็น coroutine หรือไม่ก่อนจะ await
                        import inspect
                        if inspect.iscoroutinefunction(active_browser.stop):
                            await asyncio.shield(active_browser.stop())
                        else:
                            active_browser.stop() # เรียกแบบปกติถ้าไม่ใช่ async
                except Exception as e:
                    print(f"⚠️ Warning during stop(): {e}")
                finally:
                    # ไม่ว่า stop() จะพังหรือไม่ ให้ใช้ kill_specific_browser 
                    # ซึ่งใช้ PID ในการสั่งปิดจริง (OS Level) 
                    # เพื่อให้แน่ใจว่า Browser ตายแน่นอน
                    kill_specific_browser()
                    
                    # ล้างค่าใน Instance และลบ profile
                    browser_instance = None
                    await cleanup_profile() # ฟังก์ชันลบโฟลเดอร์ที่เราคุยกัน
                    
                    print("🔒 [System] ปิด Browser และเคลียร์หน่วยความจำแล้ว")
            else:
                print("ℹ️ Browser instance ไม่มีอยู่แล้ว")

            #รันรายงานสถิติ (ยิง api ตรง)
            stats_report = format_site_stats_report([n[0] for n in active_nodes])
            if stats_report:
                print(stats_report)
                await safe_send_notify(stats_report) # แนะนำให้ใช้ await ถ้าเป็นไปได้
            
            #Cycle complete (เข้าสู่ช่วงพัก)
            wait_sec = random.randint(SET.get('MIN_WAIT_MINUTES', 2)*60, SET.get('MAX_WAIT_MINUTES', 10)*60)
            wait_msg = f"\n💤 Cycle finished. Waiting {wait_sec//60} minutes for next scan..."
            print(wait_msg)
            await safe_send_notify(wait_msg)
        
            #ช่วงเวลาคอย
            for s in range(wait_sec, 0, -1):
                if stop_event.is_set(): break
                sys.stdout.write(f"\r⏳ Next cycle in: {s//60}m {s%60}s...   ")
                sys.stdout.flush()
                await asyncio.sleep(1)
        
            print("\n🚀 Starting next cycle...")

        except Exception as global_err:
            print(f"🚨 [Global Critical Error] บอทหลุดนอกลูปหลัก: {global_err}")
            await asyncio.sleep(30)
            
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # หากกด Ctrl+C แล้วติดตรงนี้ ไม่ต้องทำอะไร ปล่อยให้มันจบเอง
        pass
