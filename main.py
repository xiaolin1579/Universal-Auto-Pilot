import random
import threading
import chardet
import gzip
import time
import os
import re
import hashlib
import json
import requests
import urllib3
import base64
import signal
import sys
import platform
import shutil
import pytz
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from requests.auth import HTTPBasicAuth, HTTPDigestAuth
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
import ddddocr
import io
from xml.sax.saxutils import escape
import functools
print = functools.partial(print, flush=True)

ocr = ddddocr.DdddOcr(show_ad=False)

def handle_exit(sig, frame):
    reasons = {
        signal.SIGINT: "User Interrupted (Ctrl+C)",
        signal.SIGHUP: "Terminal Closed (SIGHUP)",
        signal.SIGTERM: "Process Killed (SIGTERM)"
    }
    reason = reasons.get(sig, f"Signal {sig}")
    stop_msg = f"🛑 Universal Auto-Pilot : Stopped\nReason: {reason}"
    print(f"\n{stop_msg}")
    try: send_notify(stop_msg)
    except: pass
    os._exit(0)

signal.signal(signal.SIGINT, handle_exit)
signal.signal(signal.SIGTERM, handle_exit)
if sys.platform != "win32":
    signal.signal(signal.SIGHUP, handle_exit)
    import codecs
    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer)

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

def send_notify(msg, raw_data=None):
    """
    ส่งแจ้งเตือนผ่าน Messaging API, Telegram และ Discord DM
    """
    cfg = load_full_config()
    msg = msg.strip()

    # เตรียมข้อความ
    discord_msg = msg.replace('<b>', '**').replace('</b>', '**')
    line_clean_msg = msg.replace('<b>', '').replace('</b>', '')

    # 1. LINE Messaging API (คงเดิม)
    line_cfg = cfg.get('LINE_CONFIG', {})
    if line_cfg.get('enable') and line_cfg.get('access_token'):
        url = "https://api.line.me/v2/bot/message/push"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {line_cfg.get('access_token')}"
        }
        payload = {
            "to": line_cfg.get('user_id'),
            "messages": [{"type": "text", "text": line_clean_msg}]
        }
        try: requests.post(url, json=payload, headers=headers, timeout=10)
        except: pass

    # 2. Telegram Bot (คงเดิม)
    tele_cfg = cfg.get('TELEGRAM_CONFIG', {})
    if tele_cfg.get('notify_enable') and tele_cfg.get('main_bot_token'):
        try:
            requests.post(
                f"https://api.telegram.org/bot{tele_cfg.get('main_bot_token')}/sendMessage",
                json={'chat_id': tele_cfg.get('chat_id'), 'text': msg, 'parse_mode': 'HTML'},
                timeout=10
            )
        except: pass

    # 3. Discord DM (ปรับปรุงใหม่)
    disc_cfg = cfg.get('DISCORD_CONFIG', {})
    bot_token = disc_cfg.get('remote_bot_token') # ใช้ Token เดียวกับบอทรีโมท
    admin_id = disc_cfg.get('admin_id')

    if disc_cfg.get('notify_enable') and bot_token and admin_id:
        try:
            # ขั้นตอนที่ 1: สร้าง DM Channel กับ Admin
            create_dm_url = "https://discord.com/api/v10/users/@me/channels"
            headers = {
                "Authorization": f"Bot {bot_token}",
                "Content-Type": "application/json"
            }
            # ส่ง recipient_id (Admin ID) เพื่อขอเปิดห้องแชท
            dm_channel_res = requests.post(create_dm_url, json={"recipient_id": str(admin_id)}, headers=headers, timeout=10)

            if dm_channel_res.status_code == 200:
                channel_id = dm_channel_res.json().get('id')
                # ขั้นตอนที่ 2: ส่งข้อความเข้าไปใน Channel ID ที่ได้มา
                send_msg_url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
                payload = {
                    "content": f"🔔 **[Universal Notification]**\n{discord_msg}"
                }
                requests.post(send_msg_url, json=payload, headers=headers, timeout=10)
        except Exception as e:
            print(f"⚠️ Discord DM Notify Error: {e}")

# กำหนด Timezone ไทย
tz = pytz.timezone('Asia/Bangkok')

def get_now():
    """ฟังก์ชันกลางสำหรับดึงเวลาไทยปัจจุบัน"""
    return datetime.now(tz)
    
def load_data(path):
    if not os.path.exists(path): return set()
    with open(path, "r", encoding='utf-8') as f: return set(x.strip().lower() for x in f if x.strip())

def get_auth_file(site_key):
    """ส่งกลับชื่อไฟล์ auth แยกตามเว็บ เช่น auth_UNLIMITZ.json"""
    try:
        # สร้างโฟลเดอร์สำหรับเก็บประวัติถ้ายังไม่มี
        auth_dir = os.path.join(BASE_DIR, "auth")
        if not os.path.exists(auth_dir):
            os.makedirs(auth_dir, exist_ok=True) # ใช้ exist_ok=True เพื่อป้องกัน Error กรณี Race Condition
            
        # ทำความสะอาด site_key ป้องกันอักขระพิเศษที่มีผลกับชื่อไฟล์
        clean_key = "".join(c for c in site_key if c.isalnum() or c in (' ', '.', '_')).rstrip()
        filename = f"auth_{clean_key.upper()}.json"
        
        return os.path.join(auth_dir, filename)
    except Exception as e:
        print(f"❌ เกิดข้อผิดพลาดในการสร้างเส้นทางไฟล์: {e}")
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
        start = torrent_content.find(b'4:info') + 6
        if start < 6: return None
        return hashlib.sha1(torrent_content[start:-1]).hexdigest().lower()
    except: return None

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

def check_pending_status(session, details_url):
    """
    ตรวจสอบสถานะ (รอการอนุมัติ) โดยเข้าหน้ารายละเอียดโดยตรง
    """
    try:
        # ใช้ details_url ที่รับมาได้เลย
        r = session.get(details_url, timeout=10, verify=False)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, 'html.parser')
            page_text = soup.get_text()
            
            # เช็ค Keyword สำหรับ TorrentDD และเว็บทั่วไป
            if "(รอการอนุมัติ)" in page_text or "รอการอนุมัติ" in page_text:
                return True
        return False
    except Exception as e:
        print(f"      ⚠️ Error checking pending status: {e}")
        return False

# ========================= BROWSER ENGINE =========================

def get_universal_browser():
    current_os = platform.system().lower()
    if current_os == "windows":
        search_map = {
            "chromium": [os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"), os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe")],
            "firefox": [os.path.expandvars(r"%ProgramFiles%\Mozilla Firefox\firefox.exe")]
        }
    else:
        search_map = {
            "chromium": ["/usr/bin/google-chrome-stable", "/usr/bin/google-chrome", "/usr/bin/chromium-browser"],
            "firefox": ["/usr/bin/firefox", "/usr/bin/firefox-esr"]
        }
    for path in search_map["chromium"]:
        if os.path.exists(path): return {"type": "chromium", "path": path}
    for path in search_map["firefox"]:
        if os.path.exists(path): return {"type": "firefox", "path": path}
    return None

def launch_any_browser(p):
    info = get_universal_browser()
    
    # เพิ่ม Argument สำหรับ Google DNS และลดภาระระบบ
    common_args = [
        "--no-sandbox", 
        "--disable-gpu", 
        "--mute-audio",
        # --- บังคับใช้ Google DNS ผ่าน DoH ---
        "--dns-over-https-urls=https://dns.google/dns-query",
        "--ignore-certificate-errors"
    ]
    
    if not info: 
        return p.chromium.launch(headless=True, args=common_args), "Default Playwright"
    
    if info["type"] == "chromium":
        # เพิ่ม --disable-dev-shm-usage เพื่อป้องกัน Crash บน Docker/VPS
        return p.chromium.launch(
            executable_path=info["path"], 
            headless=True, 
            args=common_args + ["--disable-dev-shm-usage"]
        ), info["path"]
    else:
        # สำหรับ Firefox จะใช้ config ต่างกันเล็กน้อย (ถ้าจำเป็น)
        return p.firefox.launch(
            executable_path=info["path"], 
            headless=True, 
            args=common_args
        ), info["path"]

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
            # เพิ่ม auth และ verify เพื่อความเสถียร
            r = self.s.get(
                f"{self.url}/api/v2/torrents/info", 
                params={'filter': 'completed'}, 
                auth=self.auth, 
                verify=False, 
                timeout=15 # ข้อมูลเยอะอาจใช้เวลาดึงนานขึ้นเล็กน้อย
            )
            
            if r.status_code == 200:
                try:
                    data = r.json()
                except:
                    return []

                # เรียงลำดับตาม Ratio (มากไปน้อย) เพื่อให้ไฟล์ที่ "ทำกำไร" ได้มากที่สุดถูกลบก่อน
                data.sort(key=lambda x: x.get('ratio', 0), reverse=True)

                results = []
                for t in data:
                    # เลือกใช้ total_size ถ้าไม่มีให้ถอยไปใช้ size
                    size_bytes = t.get('total_size', t.get('size', 0))
                    
                    results.append({
                        'hash': t.get('hash'),
                        'ratio': t.get('ratio', 0),
                        'name': t.get('name', 'Unknown'),
                        'size': size_bytes / (1024**3), # แปลงเป็น GB
                        'added_on': t.get('added_on'),
                        'category': t.get('category') # เก็บไว้เผื่อเช็คว่ามาจากเว็บไหน (BEARBIT/TDD)
                    })
                return results
            
            elif r.status_code in [401, 403]:
                self.is_connected = False # แจ้งให้ระบบรู้ว่าต้อง Login ใหม่
                
            return []
        except Exception as e:
            # print(f"⚠️ [{self.name}] Error fetching torrent info: {e}")
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
            </params>
            </methodCall>'''

            r = self.s.post(self.url, data=xml, auth=self.auth, headers=self.headers, timeout=20, verify=False)
            if r.status_code != 200: return []

            root = ET.fromstring(r.text)
            # rTorrent XML-RPC คืนค่าเป็น nested arrays
            data = root.findall(".//value/array/data/value/array/data")

            results = []
            for item in data:
                values = item.findall("./value")
                # values[0]=hash, [1]=ratio, [2]=complete, [3]=name
                is_complete = values[2].find("./i4").text == "1"

                if is_complete:
                    results.append({
                        'hash': values[0].find("./string").text,
                        'ratio': int(values[1].find("./i4").text) / 1000.0,
                        'name': values[3].find("./string").text
                    })
            return results
        except Exception as e:
            print(f"❌ rTorrent Reclaim Error: {e}")
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
        if info_hash and self._verify_torrent_in_client(info_hash):
            print(f"ℹ️ [{self.name}] Torrent already exists, skipping load.raw and starting.")
            self._force_start_torrent(info_hash)
            return True
            
        encoded_content = base64.b64encode(content).decode('ascii')
        safe_site = self.safe_xml_escape(site_name)
        
        # 🎯 ใช้ load.raw_start ฝังคำสั่ง d.custom1.set ไร้อาการหลุดรวนบนระบบ Shared
        xml_payload = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<methodCall>'
            '<methodName>load.raw_start</methodName>'
            '<params>'
            '<param><value><string></string></value></param>'
            '<param><value><base64>{}</base64></value></param>'
            f'<param><value><string>d.custom1.set={safe_site}</string></value></param>' 
            '</params>'
            '</methodCall>'
        ).format(encoded_content).encode('utf-8')
        
        # 🔥 [เสริมเกราะป้องกันตาย]: ประกาศตัวแปรรอรับ Response ด้านนอกลูป
        r = None
        
        # วนลูปให้โอกาสยิงแก้ตัวทั้งหมด 3 รอบ (รอบแรก + Retry 2 รอบ)
        for attempt in range(3):
            try:
                # ลด timeout เหลือ 15 วินาทีต่อรอบ เพื่อให้บอทตัดใจมาลองรอบใหม่ได้เร็วขึ้น ไม่ค้างคาจนเธรดหลักหลุด
                r = self.s.post(self.url, data=xml_payload, auth=self.auth, headers=self.headers, timeout=15, verify=False)
                break  # ถ้ายิงผ่านและได้ Response กลับมาสำเร็จ ให้หลุดออกจากลูป Retry ทันที
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as net_err:
                if attempt < 2:
                    print(f"🔄 [{self.name}] Network Timeout/Error ({net_err}) กำลังลองใหม่อีกครั้ง... (รอบที่ {attempt+1}/2)")
                    time.sleep(1.5)  # นอนรอให้เน็ตเวิร์กหรือ I/O ของเซิร์ฟเวอร์แชร์เคลียร์ตัวเองสักครู่
                else:
                    print(f"❌ [{self.name}] Exception during upload หลังพยายามครบ 3 รอบ: {net_err}")
                    return False
            except Exception as e:
                print(f"❌ [{self.name}] Fatal Exception during upload: {e}")
                return False
        
        # ประมวลผลผลลัพธ์จากการโพสต์ (กระบวนการหลังจากหลุดลูป Retry)
        try:
            if r is None or r.status_code != 200 or "fault" in r.text.lower():
                print(f"⚠️ [{self.name}] Load failed: {r.text[:50] if r else 'No Response'}")
                return False

            print(f"✅ [{self.name}] Added. Syncing with rTorrent...")
            
            found = False
            for i in range(20):  
                if i < 5:      time.sleep(0.5)
                elif i < 12:   time.sleep(1.0)
                else:          time.sleep(2.0)
                
                if self._verify_torrent_in_client(info_hash):
                    found = True
                    break
            
            if not found:
                print(f"⚠️ [{self.name}] Detection Delay: บอทตรวจไม่เจอแต่คาดว่าไฟล์กำลังจองดิสก์ ส่งคำสั่งเผื่อไว้...")
                self._force_start_torrent(info_hash) 
                return True 

            print(f"🚀 [{self.name}] Torrent verified และติดป้าย Label เรียบร้อย")
            return True

        except Exception as e:
            print(f"❌ [{self.name}] Exception during response parsing: {e}")
            return False

    def _force_start_torrent(self, info_hash):
        """ส่งคำสั่งตรวจสอบและสั่งรันซ้ำเพื่อป้องกันเคสไฟล์ค้าง ตกหล่น"""
        target_hash = info_hash.strip().lower()
        sequence = ['d.open', 'd.start']
        for method in sequence:
            xml_cmd = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<methodCall><methodName>{}</methodName>'
                '<params><param><value><string>{}</string></value></param></params>'
                '</methodCall>'
            ).format(method, target_hash).encode('utf-8')
            try:
                self.s.post(self.url, data=xml_cmd, auth=self.auth, headers=self.headers, timeout=5, verify=False)
                time.sleep(0.1)
            except:
                pass

    def _verify_torrent_in_client(self, info_hash):
        target_hash = info_hash.strip().lower() 
        
        xml_check = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<methodCall>'
            '<methodName>d.multicall2</methodName>'
            '<params>'
            '<param><value><string></string></value></param>'
            f'<param><value><string>{target_hash}</string></value></param>' 
            '<param><value><string>d.name=</string></value></param>'
            '</params>'
            '</methodCall>'
        ).encode('utf-8')
    
        try:
            headers = {**self.headers, 'Content-Type': 'text/xml; charset=utf-8'}
            r = self.s.post(self.url, data=xml_check, auth=self.auth, headers=headers, timeout=6, verify=False)
            
            response_text_clean = r.text.lower()
            if r.status_code == 200 and "fault" not in response_text_clean:
                if "value" in response_text_clean and len(response_text_clean) > 150:
                    return True
        except:
            pass
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
        if not self.is_connected: self.login()
        try:
            # 1. การสร้าง Proxy: หากเซิร์ฟเวอร์ใช้ Digest ให้ใช้ Requests เข้ามาช่วย (ตามที่เคยแนะนำ)
            # แต่ถ้ามั่นใจว่าเป็น Basic Auth หรือรันใน Local Network ใช้แบบเดิมที่คุณทำได้เลยครับ
            auth_url = self.url.replace("://", f"://{self.user}:{self.pw}@")
            proxy = xmlrpc.client.ServerProxy(auth_url)

            # 2. ดึงข้อมูล: d.custom1 (Label), d.get_up_total (ยอดรวม), d.get_up_rate (ความเร็ว)
            # ใช้ view "main" เพื่อความครอบคลุม
            response = proxy.d.multicall2("", "main", "d.custom1=", "d.get_up_total=", "d.get_up_rate=")

            site_stats = {}
            for t in response:
                # ล้างชื่อ Site ให้สะอาด
                raw_site = t[0] if t[0] else "Uncategorized"
                site = unquote(raw_site).strip()
            
                # rTorrent ส่งค่าเป็น Bytes มาอยู่แล้ว
                total_up = int(t[1])
                up_speed = int(t[2])

                if site not in site_stats:
                    site_stats[site] = {
                        'total_up_bytes': 0, 
                        'current_speed_bytes': 0, 
                        'count': 0
                    }
        
                site_stats[site]['total_up_bytes'] += total_up
                site_stats[site]['current_speed_bytes'] += up_speed
                site_stats[site]['count'] += 1
            
            return site_stats
        except Exception as e:
            # หาก Proxy พัง ให้ลองสั่ง login ใหม่ในรอบถัดไป
            self.is_connected = False
            # print(f"⚠️ [{self.name}] rTorrent Stats Error: {e}")
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

# ========================= AUTO CLEAN =========================

class NodeCleaner:
    def __init__(self, node_obj, node_clean_cfg, global_clean_cfg):
        self.node = node_obj
        self.node_cfg = node_clean_cfg or {}
        self.global_cfg = global_clean_cfg or {}

    def process(self, force_emergency=False):
        """
        ระบบตรวจสอบและลบทอร์เรนต์ที่หมดอายุ
        :param force_emergency: บังคับใช้โหมดลบด่วน (ใช้เมื่อต้องการคืนพื้นที่ทันที)
        """
        node_enable = self.node_cfg.get('enable')
        global_enable = self.global_cfg.get('enable', False)

        # ตรวจสอบสิทธิ์การใช้งาน (Node Priority > Global)
        is_enabled = node_enable or global_enable
        if not is_enabled:
            return

        # เช็คสภาวะดิสก์เต็ม (Emergency) ถ้าพื้นที่เหลือน้อยกว่า 10GB หรือถูกสั่ง Force
        # โดยอ้างอิงจากค่า free_gb ของ Node นั้นๆ
        is_emergency = force_emergency or (self.node.free_gb < 10.0)
        if is_emergency:
            print(f"🚨 [EMERGENCY CLEAN] [{self.node.name}] พื้นที่วิกฤตเหลือ {self.node.free_gb:.2f}GB")

        print(f"🔍 Debug: [{self.node.name}] Starting cleanup process... (Emergency: {is_emergency})")

        try:
            removed_list = []
            if isinstance(self.node, QbitNode):
                removed_list = self._clean_qbit(is_emergency)
            elif isinstance(self.node, RtorrentNode):
                removed_list = self._clean_rtorrent(is_emergency)

            if removed_list:
                status_title = "🚨 EMERGENCY Cleanup" if is_emergency else "🧹 Cleanup Summary"
                msg = f"{status_title} [{self.node.name}]:\n" + "\n".join(removed_list)
                send_notify(msg)
        except Exception as e:
            print(f"⚠️ [{self.node.name}] Clean Error: {e}")

    def _should_remove(self, ratio, age_hours, is_emergency=False):
        """
        Logic การตัดสินใจลบไฟล์
        """
        # หากอยู่ในโหมดฉุกเฉิน จะลดเกณฑ์การลบลงครึ่งหนึ่ง (ลบง่ายขึ้น) เพื่อรีบคืนพื้นที่
        threshold_div = 2 if is_emergency else 1

        use_node_cfg = self.node_cfg.get('enable', False)
        cfg = self.node_cfg if use_node_cfg else self.global_cfg

        # ถ้าพื้นที่เป็น 0.0GB จริงๆ ให้ใช้เกณฑ์ "ล้างป่าช้า"
        if self.node.free_gb <= 0.01:
            # ลบไฟล์ที่อยู่เกิน 2 ชม. ทิ้งทันทีเพื่อกู้ชีพ Node
            if age_hours >= 2: return True

        # ดึงค่า Config (Ratio / Min Time / Max Time)
        min_ratio = (cfg.get('min_ratio', 1.0)) / threshold_div
        min_time = (cfg.get('min_time', 360) / 60) / threshold_div
        max_time = (cfg.get('max_time', 1440) / 60) / threshold_div

        # 1. ลบถ้าอยู่มานานเกิน Max Time
        if age_hours >= max_time:
            return True

        # 2. ลบถ้าอยู่เกิน Min Time และ Ratio ถึงเป้าหมาย
        if age_hours >= min_time and ratio >= min_ratio:
            return True

        return False

    def _clean_qbit(self, is_emergency):
        res = []
        # ดึงข้อมูลจาก qBittorrent Web API
        r = self.node.s.get(f"{self.node.url}/api/v2/torrents/info", auth=self.node.auth, verify=False, timeout=15)
        if r.status_code != 200: return []

        torrents = r.json()
        now = time.time()
        for t in torrents:
            # ข้ามถ้ายังโหลดไม่เสร็จ
            if t.get('progress', 0) < 1: continue

            completion_on = t.get('completion_on', 0)
            if completion_on <= 0: continue

            age_hours = (now - completion_on) / 3600
            ratio = t.get('ratio', 0)

            if self._should_remove(ratio, age_hours, is_emergency):
                if self.node.delete_torrent(t['hash']):
                    line = f"  🗑️ {t['name'][:30]} (R:{ratio:.2f}, {age_hours:.1f}h)"
                    print(line); res.append(line)
        return res

    def _clean_rtorrent(self, is_emergency):
        res = []
        # XML-RPC สำหรับ rTorrent multicall
        xml = (
            '<?xml version="1.0"?><methodCall><methodName>d.multicall2</methodName>'
            '<params><param><value><string></string></value></param>'
            '<param><value><string>main</string></value></param>'
            '<param><value><string>d.hash=</string></value></param>'
            '<param><value><string>d.ratio=</string></value></param>'
            '<param><value><string>d.timestamp.finished=</string></value></param>'
            '<param><value><string>d.name=</string></value></param></params></methodCall>'
        )

        try:
            r = self.node.s.post(
                self.node.url, 
                data=xml, 
                auth=self.node.auth, 
                headers=self.node.headers, # แนะนำให้ดึง headers มาด้วย
                verify=False, 
                timeout=15
            )
            if r.status_code != 200: return []

            soup = BeautifulSoup(r.text, "xml")
            response = soup.find('methodResponse')
            if not response: return []

            torrent_entries = response.find_all('data')
            now = time.time()

            for entry in torrent_entries:
                vals = [v.get_text().strip() for v in entry.find_all('value', recursive=False)]
                if len(vals) < 4: continue

                t_hash, t_ratio_raw, t_finish, t_name = vals[0], vals[1], vals[2], vals[3]

                if not t_finish.isdigit() or int(t_finish) <= 0: continue

                ratio = int(t_ratio_raw) / 1000 if t_ratio_raw.isdigit() else 0
                age_hours = (now - int(t_finish)) / 3600

                if self._should_remove(ratio, age_hours, is_emergency):
                    if self.node.delete_torrent(t_hash):
                        line = f"  🗑️ {t_name[:30]} (R:{ratio:.2f}, {age_hours:.1f}h)"
                        print(line); res.append(line)

        except Exception as e:
            print(f"⚠️ [{self.node.name}] rTorrent Clean Error: {str(e)}")
        return res

# ========================= Smart Reclaim Space =========================

def smart_reclaim_process(node, required_gb):
    """
    เวอร์ชันหยุดบอทคลั่ง: คำนวณพื้นที่ในแรม + ดักห้ามลบงานขาดทุน (Ratio < 1.0)
    """
    try:
        # อัปเดตพื้นที่ปัจจุบันก่อนเริ่ม
        node.refresh_status()
        
        # ปรับ Buffer สุทธิหน้างานให้กระชับพอดี (คลาสพี่มี safety_buffer 15GB ข้างในอยู่แล้ว)
        target_free = required_gb + 2.0 
        
        if node.free_gb >= target_free:
            print(f"✅ [{node.name}] พื้นที่เพียงพออยู่แล้ว: {node.free_gb:.2f} GB")
            return True

        # 1. ดึงข้อมูลงานทั้งหมด
        all_torrents = node.get_all_torrents_info()
        if not all_torrents:
            print(f"⚠️ [{node.name}] ไม่มีงานให้เคลียร์")
            return False

        # 2. คัดกรองงานที่ปลอดภัยที่จะลบ
        # 2. คัดกรองงานที่เสร็จสมบูรณ์แล้วเท่านั้น
        scannable_torrents = []
        for t in all_torrents:
            t_ratio = t.get('ratio', 0.0)
            
            # 1. ดึงค่าดิบ
            raw_p = t.get('progress', t.get('percent', t.get('progress_raw', 0.0)))
            
            # 2. ปรับให้เป็นทศนิยม 0.0 - 1.0 เสมอ
            progress = raw_p / 100.0 if raw_p > 1.0 else raw_p
            
            # 3. คัดกรอง
            if progress < 1.0:
                continue # ข้ามงานที่ยังโหลดไม่เสร็จ
                
            if t_ratio < 1.0:
                continue # ข้ามงานที่ยังขาดทุน
                
            scannable_torrents.append(t)

        if not scannable_torrents:
            print(f"⚠️ [{node.name}] ไม่มีงานที่ Ratio >= 1.00 เหลือให้ลบแล้ว! (ระบบเซฟชีวิตเพื่อรักษาค่าเรติโอ)")
            return False

        # 3. เรียงลำดับ: ลบตัวที่กำไรสูงสุด (Ratio เยอะสุด) ออกก่อน
        scannable_torrents.sort(key=lambda x: x.get('ratio', 0), reverse=True)

        print(f"🧹 [{node.name}] เริ่มทวงคืนพื้นที่... เป้าหมายสะสม: {target_free - node.free_gb:.2f} GB")

        # ตัวแปรจำลองพื้นที่ในแรม (แก้ปัญหาเซิร์ฟเวอร์คืนพื้นที่ช้า)
        virtual_free_gb = node.free_gb
        delete_count = 0
        max_delete_limit = 5  # 🔥 [เกราะป้องกันชั้นที่ 2]: ลบสูงสุดไม่เกิน 5 ตัวต่อรอบพื่อความปลอดภัย

        for t in scannable_torrents:
            # ถ้าพื้นที่จำลองคำนวณแล้วว่าผ่านเป้า ให้หยุดลบทันที! ไม่ต้องรอ API เซิร์ฟเวอร์
            if virtual_free_gb >= target_free:
                print(f"🛑 [{node.name}] หยุดลบ! คำนวณพื้นที่จำลองในแรมเพียงพอต่อการลงไฟล์ใหม่แล้ว")
                break
                
            if delete_count >= max_delete_limit:
                print(f"🛑 [{node.name}] หยุดลบชั่วคราว! ชนเพดานจำกัดการลบสูงสุด {max_delete_limit} ตัวต่อรอบ")
                break

            t_name = t.get('name', 'Unknown')
            t_ratio = t.get('ratio', 0.0)
            t_hash = t.get('hash')
            
            # คำนวณขนาด (รองรับโครงสร้าง size ข้อมูลของพี่)
            t_size_gb = t.get('size', 0) / (1024**3) if t.get('size', 0) > 1000000 else t.get('size_gb', 0)

            print(f"🧹 [{node.name}] กำลังลบ: {t_name[:30]} (Ratio: {t_ratio:.2f} | ปลดปล่อยพื้นที่ทิพย์: +{t_size_gb:.2f} GB)")

            # สั่งลบจริง
            node.delete_torrent(t_hash)
            
            # อัปเดตค่าในแรมทันที! (จุดนี้แหละที่จะช่วยชีวิตพี้ ไม่ให้บอทลบรัวๆ)
            virtual_free_gb += t_size_gb
            delete_count += 1
            
            # หน่วงสั้นๆ ให้เครื่องหายใจสำหรับเลเยอร์ Racing
            time.sleep(1.0)

        # จบลูป ยิงตรวจสอบจริงรอบเดียวท้ายงาน
        time.sleep(2.0)
        node.refresh_status()
        return node.free_gb >= target_free

    except Exception as e:
        print(f"❌ Reclaim Error on {node.name}: {str(e)}")
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

def apply_stealth(page):
    page.add_init_script("""
        # แก้ทางระบบดีเทค Automation
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        
        # เพิ่มความเนียนของ Chrome พร็อพเพอร์ตี้
        window.chrome = { runtime: {} };
        
        # หลอกเรื่องภาษาและคุกกี้
        Object.defineProperty(navigator, 'languages', { get: () => ['th-TH', 'th', 'en-US', 'en'] });
        
        # ป้องกันการเช็คผ่านสแต็กของการเรียกฟังก์ชัน (สำคัญ!)
        const newProto = Navigator.prototype;
        delete newProto.webdriver;
        Object.setPrototypeOf(navigator, newProto);
    """)

def sync_playwright_cookies(context, session):
    for cookie in context.cookies():
        session.cookies.set(cookie['name'], cookie['value'], domain=cookie['domain'])
        
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

def ensure_site_logged_in(page, site_cfg):
    site_key = site_cfg['name']
    target_list = site_cfg.get('target_urls', [])
    
    check_url = ""
    if target_list:
        # สุ่มเลือกมา 1 Item
        chosen_item = random.choice(target_list)
        
        # ตรวจสอบว่า item ที่สุ่มมาเป็น Dictionary หรือ String
        if isinstance(chosen_item, dict):
            # ถ้าเป็น dict ให้ดึง key 'url' ออกมา (ปรับชื่อ key ให้ตรงกับ JSON ของคุณ)
            check_url = chosen_item.get('url', chosen_item.get('link', ''))
        else:
            # ถ้าเป็น string อยู่แล้วก็ใช้ได้เลย
            check_url = chosen_item
            
    # Fallback ถ้าหา URL ไม่ได้
    if not check_url or not isinstance(check_url, str):
        base_url = site_cfg.get('base_url', '').rstrip('/')
        check_url = f"{base_url}"

    print(f"🔍 [{site_key}] สุ่มตรวจสอบ Session ที่: {check_url}")
    
    try:
        # ป้องกัน Error .startswith ถ้า check_url หลุดเป็น None
        final_url = check_url if check_url.startswith('http') else f"{site_cfg.get('base_url').rstrip('/')}/{check_url.lstrip('/')}"
        
        page.goto(final_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000) 
        
        content = page.content()
        
        # 3. ตรวจสอบสถานะการล็อกอิน
        # เช็คชื่อ User หรือคำว่า logout ในหน้าเว็บ
        is_logged_in = (
            (site_cfg['username'] in content) or 
            ("logout.php" in content.lower())
        )
        # เช็คว่าหน้าปัจจุบันไม่ใช่หน้า Login (ไม่มีช่องกรอก Password)
        is_login_page = any(k in content for k in ['type="password"', 'name="password"'])

        if is_logged_in and not is_login_page:
            print(f"✅ [{site_key}] Session ยัง OK (สุ่มผ่าน)")
            return True
            
        print(f"🔑 [{site_key}] พบว่า Session หลุด -> กำลังส่งไป universal_login")
        return universal_login(page, site_cfg)

    except Exception as e:
        print(f"⚠️ [{site_key}] เข้าหน้าเช็คไม่สำเร็จ: {str(e)} -> ลอง Login ใหม่")
        return universal_login(page, site_cfg)

def universal_login(page, site_cfg):
    # 1. ดึงชื่อ Site และข้อมูลเบื้องต้นจาก Object โดยตรง
    site_key = site_cfg['name']
    
    # --- [Stealth Injection] ---
    page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    """)

    base_url = site_cfg.get('base_url').rstrip('/')
    # ปรับ Logic URL ให้ยืดหยุ่นขึ้น
    login_url = site_cfg.get('login_url')
    if not login_url:
        if "bitsuse" in base_url.lower():
            login_url = f"{base_url}/bs_login.php"
        elif site_key.upper() == "DEDBIT":
            login_url = base_url
        else:
            login_url = f"{base_url}/login.php"

    try:
        print(f"🔐 [{site_key}] กำลังเข้าหน้า Login...")
        if not safe_goto(page, login_url, wait_until="networkidle", timeout=45000):
            return False
        
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(5000) # เผื่อเวลาให้สคริปต์หน้าเว็บ Render ชื่อ User
        page_content = page.content() 
        
        is_logged_in = (
            (site_cfg['username'] in page_content) or 
            (page.locator('a[href*="logout.php"], a[href*="/user/account/logout"]').count() > 0)
        )
        
        if is_logged_in and len(page_content) > 1000:
            print(f"✅ [{site_key}] ยืนยันสถานะ: ล็อกอินอยู่แล้ว (Skip Login)")
            return True
            
        # ถ้าหน้าขาวเกินไป ให้ลอง Reload (ป้องกันเคส image_da63b3.png)
        if len(page_content) < 500:
            page.screenshot(path=f"debug_{site_key}_blank.png")
            print(f"⚠️ [{site_key}] ตรวจพบหน้าว่าง (Blank Page) -> บันทึกรูป debug แล้ว -> กำลังลอง Reload...")
            page.wait_for_timeout(10000)
            page.reload(wait_until="networkidle")
            page.wait_for_timeout(10000)
            
        print(f"🔑 [{site_key}] ตรวจพบว่ายังไม่ได้ล็อกอิน หรือ Session หลุด -> เริ่มกรอกข้อมูล")

        for attempt in range(10):
            print(f"🔎 [{site_key}] รอบที่ {attempt+1}...")
            
            # 1. ปรับ Selector ให้เจาะจงที่ฟอร์มที่มี action="takelogin.php" 
            # ซึ่งเป็นมาตรฐานของ BEARBIT/TB Source
            target_form = 'form[action*="takelogin"],form[action*="/user/account/login/"]'
            u_sel = 'input[name="username"], input[name="user"], input#username'
            p_sel = 'input[name="password"], input[name="pass"], input#password'
            
            try:
                # 1. รอจนกว่าช่อง Username จะปรากฏ
                u_input = page.locator(u_sel).first
                p_input = page.locator(p_sel).first
                
                u_input.wait_for(state="visible", timeout=10000)

                # 2. ใช้วิธี "คลิกแล้วพิมพ์" (Simulator) แทนการ Fill
                u_input.click(delay=random.randint(100, 200))
                page.keyboard.press("Control+A")
                page.keyboard.press("Backspace")
                # พิมพ์ชื่อผู้ใช้ช้าๆ เหมือนคน
                page.keyboard.type(site_cfg['username'], delay=random.randint(50, 150))
                
                page.wait_for_timeout(random.randint(300, 600))

                # 3. ใช้ Tab เพื่อเลื่อนไปช่อง Password (สำคัญมากสำหรับ BEARBIT)
                page.keyboard.press("Tab")
                page.wait_for_timeout(200)
                page.keyboard.type(site_cfg['password'], delay=random.randint(50, 150))
                
                # 4. กด Enter เพื่อ Login
                page.keyboard.press("Enter")
                
                print(f"🚀 [{site_key}] ส่งข้อมูล Login ผ่าน Keyboard Emulator แล้ว")

            except Exception as e:
                print(f"❌ [{site_key}] กรอกข้อมูลไม่สำเร็จ: {str(e)}")
                # แผนสำรอง: ถ้าหาแบบเจาะจงไม่เจอ ให้ลองหาแบบกว้าง (Legacy Mode)
                try:
                    page.locator('input[name="username"]').first.fill(site_cfg['username'])
                    page.locator('input[name="password"]').first.fill(site_cfg['password'])
                except: continue

            # --- ส่วนจัดการ Captcha (ปรับให้ข้ามได้ถ้าไม่มี) ---
            captcha_img = page.locator('img.cimage, img[src*="captcha"], #captcha_img').first
            captcha_input = page.locator('input[name="captcha"], #captcha').first

            if captcha_img.is_visible(timeout=3000) and captcha_input.is_visible():
                img_bytes = captcha_img.screenshot()
                raw_text = ocr.classification(img_bytes)
                captcha_text = re.sub(r'[^a-zA-Z0-9]', '', raw_text).upper()
                
                print(f"🤖 AI Solve: {captcha_text} (รอยืนยัน ✅)")
                captcha_input.fill(captcha_text)

                # --- [เพิ่มส่วนนี้: สุ่มคลิกที่ว่างเพื่อ Trigger ระบบตรวจสอบ] ---
                page.wait_for_timeout(random.randint(500, 1000))
                
                # วิธีที่ 1: คลิกที่พื้นที่ว่าง (สุ่มพิกัดเล็กน้อยที่ไม่โดนปุ่มอื่น)
                # เลือกจุดที่มักจะว่าง เช่น แถวๆ ขอบซ้ายหรือขวาของฟอร์ม
                page.mouse.click(random.randint(10, 50), random.randint(10, 50)) 
                
                # วิธีที่ 2: กด Tab เพื่อออกจากช่อง (มักจะได้ผลดีกับระบบตรวจ Checkmark)
                # page.keyboard.press("Tab") 
                
                # วิธีที่ 3: บังคับให้ช่อง Captcha หลุด Focus (Blur) ผ่าน JS
                page.evaluate("() => document.activeElement.blur()")
                
                page.wait_for_timeout(2000) # รอให้ระบบ Verify สักครู่
                # -------------------------------------------------------
                # ตรวจสอบ Checkmark (เฉพาะ Unlimitz)
                try:
                    page.wait_for_selector('img[src*="ValidGreen.png"]', state="visible", timeout=4000)
                    print(f"✅ [{site_key}] Captcha ถูกต้อง")
                except:
                    print(f"🔄 [{site_key}] Captcha ไม่ผ่าน -> กำลังกด Refresh")
                    
                    # --- [แก้ไขจุดนี้] ---
                    # แผน A: คลิกที่ปุ่ม Refresh (หาจาก title หรือ src ของรูป)
                    refresh_btn = page.locator('a[title="refresh"], a[onclick*="refreshimg"], img[src*="Refresh.png"]').first
                    
                    if refresh_btn.is_visible():
                        refresh_btn.click()
                    else:
                        # แผน B: ถ้าหาปุ่มไม่เจอ ให้สั่งรันฟังก์ชัน JavaScript refreshimg() โดยตรง
                        page.evaluate("() => { if(typeof refreshimg === 'function') refreshimg(); }")
                    
                    page.wait_for_timeout(2000) # รอให้รูปใหม่โหลด
                    continue 

            # --- การส่งฟอร์ม (ปรับให้รองรับ ID ฟอร์มของ TL) ---
            page.evaluate(f"""
                () => {{
                    const form = document.querySelector('{target_form}');
                    if (form) {{
                        // พยายามเรียก submit ตรงๆ
                        HTMLFormElement.prototype.submit.call(form);
                    }} else {{
                        // ถ้าหาฟอร์มไม่เจอ ให้กด Enter ที่ช่อง Password
                        const passInput = document.querySelector('input[name="password"]');
                        if (passInput) passInput.dispatchEvent(new KeyboardEvent('keydown', {{'key': 'Enter'}}));
                    }}
                }}
            """)

            # ตรวจสอบผลลัพธ์
            page.wait_for_timeout(8000) 
            curr_content = page.content().lower()
            if "logout" in curr_content or "login" not in page.url.lower():
                print(f"🎉 [{site_key}] Login Success!") 
                page.context.storage_state(path=get_auth_file(site_key))
                return True

        return False

    except Exception as e:
        print(f"❌ [{site_key}] Login Error: {str(e)}")
        return False

def auto_click_thanks(page, details_url):
    """
    ตรวจจับและกดปุ่มขอบคุณอัตโนมัติ โดยวิ่งเข้าหา details_url โดยตรง
    รองรับ UnlimitZ, BearBit และ Tracker ทั่วไป
    """
    if not details_url:
        return False

    print(f"🔍 กำลังเข้าสู่หน้ารายละเอียดเพื่อเช็คปุ่มขอบคุณ... \n🔗 {details_url}")
    
    try:
        # 1. สั่งให้ Page เดินทางไปยัง URL ที่ได้รับมา
        # wait_until="domcontentloaded" เพื่อความรวดเร็ว ไม่ต้องรอโหลดรูปภาพจนเสร็จ
        page.goto(details_url, timeout=15000, wait_until="domcontentloaded")
        
        # 2. นิยาม Selector แบบกลุ่ม
        selectors = [
            'form[action="thanks.php"] input[type="submit"]', 
            'td#saythanks img', 
            'a[onclick*="action=say_thanks"]',
            'input[id="say_thanks_button"]',
            '#saythanks > a' # เพิ่มเติมสำหรับบางเวอร์ชั่น
        ]
        combined_selector = ", ".join(selectors)
        
        # 3. ค้นหา Element
        thanks_btn = page.locator(combined_selector).first
        
        # ตรวจสอบเบื้องต้นว่าพบปุ่มหรือไม่
        if thanks_btn.count() > 0:
            # --- ตรวจสอบว่าปุ่มกดได้หรือไม่ (Disabled Check) ---
            if not thanks_btn.is_enabled():
                print("⏭️ ปุ่มขอบคุณถูกปิดใช้งาน (Disabled) -> น่าจะเคยกดไปแล้ว")
                return False

            if thanks_btn.is_visible(timeout=3000):
                print(f"💖 พบปุ่มขอบคุณ! กำลังดำเนินการกด...")
                thanks_btn.scroll_into_view_if_needed()
                page.wait_for_timeout(500) # ให้เวลา UI เซตตัวนิดนึง
                
                # ใช้ force=True เพื่อแก้ปัญหาโดน Element อื่นบัง
                thanks_btn.click(timeout=5000, force=True) 
                
                print("✅ กดขอบคุณเรียบร้อยแล้ว")
                return True
        
        # 4. [แผนสำรอง] หากวิธีปกติไม่ได้ผล ให้ใช้ JavaScript (จัดการพวก AJAX หรือรูปภาพใน BearBit)
        executed = page.evaluate("""() => {
            const btn = document.querySelector('a[onclick*="say_thanks"]') || 
                        document.querySelector('form[action="thanks.php"] input[type="submit"]') ||
                        document.querySelector('td#saythanks img') ||
                        document.querySelector('input[id="say_thanks_button"]');
            if (btn) {
                // เช็คสถานะ Disabled ของปุ่มที่เป็น Input
                if (btn.tagName === 'INPUT' && btn.disabled) return "already_pressed";
                
                // เช็คว่าเคยกดไปแล้วหรือยัง (บางเว็บจะเปลี่ยนข้อความเป็น "ขอบคุณแล้ว")
                if (btn.innerText && btn.innerText.includes("ขอบคุณแล้ว")) return "already_pressed";
                
                btn.click();
                return "success";
            }
            return "not_found";
        }""")
        
        if executed == "success":
            print("🚀 กดขอบคุณผ่าน JavaScript สำเร็จ (แผนสำรอง)")
            return True
        elif executed == "already_pressed":
            print("⏭️ ระบบ JS แจ้งว่าเคยกดไปแล้ว -> ข้าม")
            return False

        print("ℹ️ ไม่พบปุ่มขอบคุณในหน้านี้ (อาจจะไม่มี หรือกดไปแล้ว)")
        return False
            
    except Exception as e:
        # ดักจับเคสที่กดแล้วหน้า Refresh/Redirect ทันที (Navigation Error)
        if any(msg in str(e).lower() for msg in ["context was destroyed", "navigation", "load"]):
            print("⏩ กดสำเร็จแล้วและหน้ากำลังเปลี่ยนไป...")
            return True
        print(f"❌ เกิดข้อผิดพลาดที่หน้าขอบคุณ: {e}")
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
    """บันทึกข้อมูลแบบแยกระบบ Site และเก็บเป็นตัวเลข (Parsed)"""
    try:
        all_history = {}
        if os.path.exists(STATS_HISTORY_FILE):
            with open(STATS_HISTORY_FILE, 'r', encoding='utf-8') as f:
                try:
                    all_history = json.load(f)
                except: all_history = {}

        if site_name not in all_history:
            all_history[site_name] = {}
        
        now = get_now()
        # ใช้ Key ที่เรียงลำดับตามเวลาได้ง่าย
        timestamp_key = now.strftime("%Y-%m-%d %H:00")
        
        # สำคัญ: ต้อง parse ค่าให้เป็นตัวเลขก่อนบันทึก
        all_history[site_name][timestamp_key] = {
            'username': current_data.get('username', 'N/A'),
            'ratio': current_data.get('ratio', 0),
            'up': current_data.get('up', 0),
            'dl': current_data.get('dl', 0),
            'bonus': current_data.get('bonus', 0),
            'raw_time': now.strftime("%Y-%m-%d %H:%M:%S")
        }

        # จำกัดจำนวนข้อมูล (31 วัน)
        site_keys = sorted(all_history[site_name].keys())
        if len(site_keys) > 744:
            for k in site_keys[:-744]:
                del all_history[site_name][k]

        # Save แบบ Atomic
        with open(STATS_HISTORY_FILE + ".tmp", 'w', encoding='utf-8') as f:
            json.dump(all_history, f, indent=4)
        os.replace(STATS_HISTORY_FILE + ".tmp", STATS_HISTORY_FILE)

    except Exception as e:
        print(f"❌ Snapshot Error [{site_name}]: {e}")

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

def ensure_dedbit_logged_in(page):
    # 1. ดึง Config
    site_cfg = next((s for s in CFG.get('SITE', []) if s['name'] in ["DEDBIT", "BITSUSE"]), None)
    if not site_cfg: return False

    # 2. เช็คว่าอยู่ที่หน้า DEDBIT หรือยัง ถ้าไม่ใช่ให้ไปหน้าแรก
    target_url = "https://www.dedbit.com/index.php"
    if "dedbit.com" not in page.url:
        print("🔗 [System] กำลังไปหน้าแรก DEDBIT...")
        page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
    
    # 3. เช็คสถานะการ Login
    my_user = site_cfg.get('username')
    # เช็คจาก Link Logout หรือ ชื่อ User (ทนทานกว่า)
    is_logged_in = page.locator('a[href*="logout.php"]').count() > 0 or \
                   page.locator(f"text={my_user}").count() > 0

    if not is_logged_in:
        print(f"🔑 [DEDBIT] พบว่า Session หลุด -> เริ่มการล็อกอินใหม่ที่หน้าแรก")
        temp_cfg = site_cfg.copy()
        temp_cfg['login_url'] = target_url
        return universal_login(page, temp_cfg)

    # --- ลบบรรทัด page.wait_for_selector("td:has-text('Uploaded')") ออกไปเลย ---
    print(f"✅ [DEDBIT] ล็อกอินเรียบร้อยแล้ว")
    return True

def get_site_stats(page, site_cfg):
    """
    เวอร์ชัน Universal (List-based): รองรับโครงสร้าง JSON ใหม่
    - [Flow Re-Ordered] ย้ายจุดสร้าง index_soup ไปไว้หลังจากการเคลียร์แจ้งเตือน/โหวตเสร็จสิ้น เพื่อให้ดึงสถานะไอเทมได้แม่นยำ 100%
    - [Session Guard] อัปเดตโครงสร้างดักจับ user_tag ของค่าย DEDBIT หลัง Re-Login ป้องกันการดีดหลุดโดยใช่เหตุ
    - [Resilient] เพิ่มการคุมจังหวะหน้าเว็บด้วยระบบตรวจสอบเส้นทาง URL ปลายทางอย่างมั่นคง
    """
    site = site_cfg['name'] 
    
    try:
        # กำหนด Base URL ของแต่ละค่าย
        if site in ["DEDBIT", "BITSUSE"]:
            base_url = "https://www.dedbit.com"
        else:
            base_url = site_cfg.get('base_url', "https://bearbit.org").rstrip('/')

        # -------------------------------------------------------------------------
        # 🎯 1. ทำภารกิจกวาดล้าง (Clear Notifications & Auto-Vote) ให้เรียบร้อยก่อน
        # -------------------------------------------------------------------------
        if 'bearbit' in site.lower():
            # รันระบบล้างกล่องข้อความสีเขียวและระเบิดโหวตทอร์เรนต์ผ่านสคริปต์ Master Engine
            clear_bearbit_notifications(page, base_url, site_name=site)
            auto_vote_snatched(page, base_url, site_name=site)
            
            # ⚡ [Crucial Fix] บังคับให้บอทกลับมาตั้งหลักที่หน้าแรกสุดหลังจากทำภารกิจข้างบนเสร็จสิ้น
            index_url = f"{base_url}/index.php" if not base_url.endswith('/') else f"{base_url}index.php"
            page.goto(index_url, timeout=20000, wait_until="domcontentloaded")

        # -------------------------------------------------------------------------
        # 🎯 2. สแกนสดหา User ID ณ วินาทีปัจจุบัน (True Fresh Soup)
        # -------------------------------------------------------------------------
        soup = BeautifulSoup(page.content(), 'html.parser')
        
        # เก็บ Soup หน้าแรกที่อัปเดตล่าสุดไว้สำหรับแกะสเตตัสไอเทมซานต้าตอนท้าย
        index_soup = soup 
        
        if site in ["DEDBIT", "BITSUSE"]:
            current_url = page.url
            user_tag = soup.find("a", href=re.compile(r"userdetails\.php\?id=\d+"))
            
            if not user_tag or "dedbit.com" not in current_url:
                print(f"🔄 [{site}] เซสชันมีปัญหา กำลังตรวจสอบสิทธิ์และเข้าสู่ระบบ DEDBIT ใหม่...")
                ensure_dedbit_logged_in(page) 
                # ดึงตารางโครงสร้างใหม่หลังจากล็อกอินสำเร็จ
                soup = BeautifulSoup(page.content(), 'html.parser')
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
        if not safe_goto(page, profile_url, wait_until="domcontentloaded", timeout=30000):
            return f"❌ [{site}] เข้าหน้าโปรไฟล์ไม่สำเร็จ (โครงข่ายหน่วง)"

        page.wait_for_timeout(2000)
        soup = BeautifulSoup(page.content(), 'html.parser')
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
        diff_text = get_stats_diff(site, curr_data)
        
        save_hourly_snapshot(site, {
            'username': username,
            'ratio': float(curr_data['ratio'].replace(',', '')),
            'up': parse_size(curr_data['up']), 
            'dl': parse_size(curr_data['dl']), 
            'bonus': float(curr_data['bonus'].replace(',', ''))
        })

        # -------------------------------------------------------------------------
        # 🎯 5. ประกอบร่างรายงานสรุปผล
        # -------------------------------------------------------------------------
        display_site = "DED/BITS" if site in ["BITSUSE", "DEDBIT"] else site
        msg = [f"👤 <b>{username}</b> ({display_site}) | Ratio: {curr_data['ratio']}"]
        msg.append(f"📤 Up: {curr_data['up']} | 📥 Dl: {curr_data['dl']}")
        
        if 'bearbit' in site.lower():
            # 🎯 [Fixed True-Source] ส่ง index_soup หน้าแรกเวอร์ชันอัปเดตสดๆ ไปแกะไอเทมซานต้า รับประกันความแม่นยำ 100%
            item_info = get_bearbit_item_status(index_soup) 
            msg.append(f"💰 Bonus: {curr_data['bonus']} | 🎁 Item: {item_info}")
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

def extract_torrent_data(row, base_url, dl_session=None, headers=None):
    if row is None: return None
    
    row_str = str(row)
    row_text = row.get_text(separator=' ', strip=True)
    all_tds = row.find_all("td")
    
    # --- 1. สกัด ID & Title (คงเดิม) ---
    title_tag = row.find("a", href=re.compile(r"details(?:new)?\.php\?id=(\d+)", re.I))
    t_id, title, details_url = None, "Unknown File", None
    if title_tag:
        title = title_tag.get_text(strip=True)
        href = title_tag.get('href', '')
        match = re.search(r"id=(\d+)", href)
        if match:
            t_id = match.group(1)
            details_url = f"{base_url.rstrip('/')}/details.php?id={t_id}"

    # --- 2. สถานะ Locked/Sticky (คงเดิม) ---
    is_hard_locked = any(x in row_str for x in ['Locked !!', 'fa-ban'])
    is_sticky = any(x in row_str for x in ['📌', 'sticky', 'Auto Sticky:'])

    # --- 3. 🔥 กลยุทธ์สแกนข้อมูลแบบ Hybrid (เน้นความแม่นยำสูง) ---
    l, s, c = 0, 0, 0
    t_size_str = "0 GB"
    raw_date_str = ""

    # วนลูปเช็คข้อมูลทีละคอลัมน์ โดยเลือกเฉพาะคอลัมน์ที่ไม่ใช่ส่วน Responsive (มือถือ)
    for td in all_tds:
        # กรองคอลัมน์ที่ซ้ำซ้อนออก (TorrentDD ใช้ dp-show สำหรับมือถือ)
        td_class = str(td.get('class', []))
        if 'dp-show' in td_class:
            continue
            
        txt = td.get_text(separator=' ', strip=True)
        
        # 1. สกัด Size: มองหาหน่วยวัด (Priority 1)
        if t_size_str == "0 GB":
            size_match = re.search(r'(\d+(?:\.\d+)?)\s*(GB|MB|TB|KB)', txt, re.I)
            if size_match:
                t_size_str = f"{size_match.group(1)} {size_match.group(2).upper()}"
        
        # 2. สกัด Date: มองหาแพทเทิร์นวันที่
        if not raw_date_str:
            date_match = re.search(r'(\d{2,4}[-/]\d{2}[-/]\d{2,4})', txt)
            if date_match:
                time_match = re.search(r'(\d{2}:\d{2}:\d{2})', txt)
                raw_date_str = f"{date_match.group(1)} {time_match.group(1) if time_match else '00:00:00'}"

    # --- 4. สกัด Peers (Seeders/Leechers) ---
    try:
        # กรองคอลัมน์มือถือออกเหมือนเดิม
        clean_tds = [td for td in all_tds if 'dp-show' not in str(td.get('class', []))]
        
        if len(clean_tds) >= 8:
            # ขยับตำแหน่งมาทางซ้าย 1 ช่อง เพราะช่องสุดท้ายคือ "ผู้อัพ"
            s = extract_digit(clean_tds[-3]) # ปล่อย (Seeders)
            l = extract_digit(clean_tds[-2]) # โหลด (Leechers)
            c = extract_digit(clean_tds[-4]) # เสร็จ (Completed)
            
        # ตรวจสอบความสมเหตุสมผล (Sanity Check)
        # ถ้า Leechers มากเกินปกติ หรือชื่อไฟล์ดันมีตัวเลขที่ทำให้ Regex หลุดมา
        if l > 10000: 
            l = 0 # ป้องกันค่าปี ค.ศ. หรือ ID หลุดมาเป็นจำนวนคนโหลด
            
    except Exception as e:
        print(f"⚠️ Error parsing Peers: {e}")

    # --- 5. ค้นหาลิงก์ดาวน์โหลด ---
    download_url = None

    # STEP A: หาจากหน้า List (TorrentDD Version)
    # 1. หาแบบปุ่มทั่วไป (<a>)
    dl_pattern = re.compile(rf"(download(?:new)?|d)\.php\?.*(id|keyalert1)={t_id or ''}", re.I)
    btn_dl = row.find("a", href=dl_pattern)
    
    if not btn_dl:
        # 2. หาจากปุ่ม <button> (โครงสร้างเฉพาะของ TorrentDD)
        # มองหาปุ่มที่มีคำสั่ง download.php ใน onclick
        btn_dl = row.find("button", onclick=re.compile(rf"download\.php/{t_id or ''}", re.I))

    if btn_dl:
        if btn_dl.name == "button":
            # สกัด URL จาก onclick: document.location = 'url'
            onclick_str = btn_dl.get('onclick', '')
            url_match = re.search(r"'(.*?)'", onclick_str)
            path = url_match.group(1).lstrip('/') if url_match else ""
        else:
            path = btn_dl.get('href', '').lstrip('/')
            
        if path:
            download_url = f"{base_url.rstrip('/')}/{path}"

    # STEP B: Deep Scan (มุดเข้าหน้า Details)
    if not download_url and details_url and dl_session:
        try:
            local_headers = headers.copy() if headers else {}
            local_headers['Accept-Encoding'] = 'gzip, deflate'
            local_headers['Referer'] = base_url
            
            resp = dl_session.get(details_url, headers=local_headers, timeout=15)
            
            if resp.status_code == 200:
                raw_c = resp.content
                
                # 1. จัดการ Gzip
                if raw_c.startswith(b'\x1f\x8b'):
                    try: raw_c = gzip.decompress(raw_c)
                    except: pass
                
                # 2. จัดการ Encoding
                det = chardet.detect(raw_c)
                enc = det.get('encoding') or 'tis-620'
                try:
                    decoded_html = raw_c.decode(enc, errors='replace')
                except:
                    decoded_html = raw_c.decode('tis-620', errors='replace')

                soup_details = BeautifulSoup(decoded_html, 'html.parser')

                # --- กลยุทธ์ค้นหาปุ่มดาวน์โหลด (รองรับทั้ง <a> และ <button>) ---
                dl_tag = None
                
                # แบบที่ 1: หาจาก <a> มาตรฐาน (BearBit / Unlimitz)
                # dl_pattern ควรนิยามไว้ก่อนเข้า Step B หรือใช้ re.compile(rf"(download(?:new)?|d)\.php\?.*(id|keyalert1)={t_id}", re.I)
                dl_tag = soup_details.find("a", href=dl_pattern)
                
                # แบบที่ 2: หาจาก <button> onclick (TorrentDD)
                if not dl_tag:
                    # มองหา button ที่มีคำว่า download.php และตามด้วย ID
                    dl_tag = soup_details.find("button", onclick=re.compile(rf"download\.php/{t_id}", re.I))

                # แบบที่ 3: ไม้ตายสุดท้าย (Class หรือ .torrent)
                if not dl_tag:
                    dl_tag = soup_details.find("a", class_=re.compile(r"bb-dl-btn|index", re.I)) or \
                             soup_details.find("a", href=re.compile(rf"\.torrent.*{t_id}", re.I))

                # --- การสกัด URL จริงจาก Tag ที่พบ ---
                if dl_tag:
                    action_url = ""
                    if dl_tag.name == "button":
                        # สกัดค่าระหว่าง '...' ใน onclick
                        onclick_str = dl_tag.get('onclick', '')
                        url_match = re.search(r"'(.*?)'", onclick_str)
                        if url_match:
                            action_url = url_match.group(1).strip()
                    else:
                        action_url = dl_tag.get('href', '').strip()

                    if action_url:
                        action_url = action_url.lstrip('/')
                        download_url = action_url if action_url.startswith('http') else f"{base_url.rstrip('/')}/{action_url}"

        except Exception as e:
            print(f"⚠️ [{t_id}] Error scanning Details: {e}")

    return {
        "id": t_id,
        "title": title,
        "is_locked": is_hard_locked,
        "is_sticky": is_sticky,
        "seeders": s,
        "leechers": l,
        "completed": c,
        "size_str": t_size_str,
        "raw_date": raw_date_str,
        "download_url": download_url,
        "details_url": details_url,
        "raw_text": row_text
    }

def format_site_stats_report(all_nodes):
    combined_stats = {}
    errors = []
    total_speed = 0
    total_upload = 0
    total_files = 0

    for node in all_nodes:
        try:
            node_data = node.get_stats_by_site()
            if not node_data: continue

            for site, data in node_data.items():
                site_name = str(site).strip() if site else "Unknown"
                # กรองพวก Category ขยะ (ถ้ามี)
                if site_name.lower() in ['', 'none', 'uncategorized', 'default']: 
                    site_name = "Other"
                
                if site_name not in combined_stats:
                    combined_stats[site_name] = {'up_gb': 0, 'speed_mb': 0, 'count': 0}
                
                up_gb = data.get('total_up_bytes', 0) / (1024**3)
                speed_mb = data.get('current_speed_bytes', 0) / (1024**2)
                file_count = data.get('count', 0)

                combined_stats[site_name]['up_gb'] += up_gb
                combined_stats[site_name]['speed_mb'] += speed_mb
                combined_stats[site_name]['count'] += file_count
                
                # เก็บยอดรวมสะสม
                total_speed += speed_mb
                total_upload += up_gb
                total_files += file_count

        except Exception as e:
            errors.append(f"{getattr(node, 'name', 'Node')}: {str(e)}")

    # สร้างข้อความ
    current_time = datetime.now().strftime("%H:%M:%S")
    msg = f"📊 <b>Universal Auto-Pilot Stats</b>\n"
    msg += f"🕒 <i>Last Sync: {current_time} | 🛰 Nodes: {len(all_nodes)}</i>\n\n"
    
    if not combined_stats:
        return msg + "⚠️ No active data from nodes."

    # ตารางสถิติ
    msg += "```bash\n"
    msg += f"{'Tracker':<12} | {'Upload':<9} | {'Speed':<9}\n"
    msg += "-" * 35 + "\n"

    # เรียงตาม Speed
    sorted_sites = sorted(combined_stats.items(), key=lambda x: x[1]['speed_mb'], reverse=True)

    for site, stat in sorted_sites:
        if stat['count'] > 0:
            # ปรับการแสดงผล Speed ถ้ามากกว่า 1024 MB/s ให้โชว์เป็น GB/s
            speed_str = f"{stat['speed_mb']:>6.1f} M"
            if stat['speed_mb'] >= 1024:
                speed_str = f"{stat['speed_mb']/1024:>6.2f} G"

            msg += f"{site[:12]:<12} | {stat['up_gb']:>6.1f}G | {speed_str}/s\n"

    msg += "-" * 35 + "\n"
    # บรรทัดสรุปยอดรวม
    msg += f"{'TOTAL':<12} | {total_upload:>6.1f}G | {total_speed/1024 if total_speed >= 1024 else total_speed:>6.1f} {'GB/s' if total_speed >= 1024 else 'MB/s'}\n"
    msg += "```"

    if errors:
        msg += f"\n❌ <b>Errors ({len(errors)}):</b> <code>{errors[0][:40]}...</code>"

    return msg
    
# ========================= BearBit STATUS =========================

def clear_bearbit_notifications(page, base_url, site_name="BEARBIT"):
    """
    ฟังก์ชันสำหรับลูปกวาดกล่องข้อความแจ้งเตือนสีเขียว (inbox.php?type=ตัวเลขใดๆ) ของ BEARBIT จนกว่าจะหมด
    - [Strict Filter] มองข้ามปุ่มเมนูหลักของระบบตั้งแต่ระดับ Selector มั่นใจได้ว่าบอทจับเฉพาะแถบแจ้งเตือนจริงเท่านั้น
    - [AJAX Loop Engine] เปลี่ยนมาใช้สถาปัตยกรรมไม่รีโหลดหน้าเว็บ (No-Reload) กวาดล้างข้อความได้เร็วขึ้นสูงสุด 5 เท่า
    - [Detached Safeguard] ใช้ .wait_for(state="detached") มั่นใจได้ว่าปุ่มเดิมถูกทำลายไปแล้วก่อนขยับลูปถัดไป
    - [Hybrid Fallback] มีระบบ Goto สำรองอัตโนมัติ หากเกิดอาการเครือข่ายหน่วงและปุ่มไม่ยอมหายไปใน 3 วินาที
    - [Full-Report Edition] ส่งรายงานผลลัพธ์เข้า Discord แยกเคสเคลียร์สำเร็จ และเคสกล่องสะอาดเรียบร้อยดี
    """
    print(f"📥 [{site_name}] เริ่มระบบ Auto-Clear Notification (High-Speed AJAX Engine)...")
    
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
            page.goto(index_url, timeout=20000, wait_until="domcontentloaded")
    except Exception as e:
        print(f"⚠️ [{site_name}] ไม่สามารถโหลดหน้าแรกได้: {e}")
        return False

    while loop_count < max_loops:
        try:
            # 🎯 ดักจับลิงก์แจ้งเตือนในตาราง #pms หรือ td โดยคัดกรองชื่อเมนูคงที่ออกจากการสแกน
            noti_locator = page.locator('table[id="pms"] a[href*="inbox.php?type="], td a[href*="inbox.php?type="]').filter(
                has_not_text=re.compile(r"^(?:กล่องข้อความ|Inbox|ข้อความส่วนตัว|My Inbox|Messages)$", re.I)
            )
            
            # ชี้เป้าไปที่อิลิเมนต์ตัวแรกเสมอ (เพราะเมื่อตัวแรกหาย ตัวถัดไปจะขยับขึ้นมาเป็น .first แทน)
            current_noti = noti_locator.first
            
            try:
                # รอดูปุ่มแจ้งเตือนระบบแสดงผลจริง 2 วินาที (ถ้าไม่มีแล้วจะดีดออกเพื่อจบลูป)
                current_noti.wait_for(state="visible", timeout=2000)
            except:
                if loop_count == 0:
                    print(f"✨ [{site_name}] Inbox คลีน! ไม่มีข้อความแจ้งเตือนค้างอยู่")
                    is_clean_at_start = True
                else:
                    print(f"✅ [{site_name}] เคลียร์แจ้งเตือนระบบผ่าน AJAX ทุกประเภทเรียบร้อย! (รวม {loop_count} ฉบับ)")
                    is_clean_at_start = False
                break
            
            # ดึงเนื้อหาข้อความมาวิเคราะห์
            noti_text = current_noti.text_content()
            clean_text = noti_text.strip() if noti_text else ""
            
            # [Safety Guard] สกัดกั้นเผื่อกรณีเมนูหลักหลุดรอดมา
            if clean_text in ["กล่องข้อความ", "Inbox", "ข้อความส่วนตัว", "My Inbox", "Messages"] or not clean_text:
                print(f"🛑 [{site_name}] ตัวกรองตรวจพบเมนูสเตติกหลุดรอด ({clean_text or 'Empty'}) -> ปิดลูปเพื่อความปลอดภัย")
                break
            
            # สกัดค่า Type จาก Attribute href
            target_href = current_noti.get_attribute("href") or ""
            type_match = re.search(r"type=(\d+)", target_href)
            noti_type = type_match.group(1) if type_match else "Unknown"
            
            print(f"🔔 [{site_name}] ตรวจพบ Type [{noti_type}]: {clean_text} -> กำลังคลิกอ่าน (รอบที่ {loop_count + 1})")
            cleared_messages.append(f"• รอบที่ {loop_count + 1} (Type {noti_type}): {clean_text}")
            
            # ⚡ สั่งคลิกอ่านเพื่อเคลียร์แจ้งเตือน (ส่งสัญญาณ AJAX ไปยังเซิร์ฟเวอร์)
            current_noti.click(timeout=10000)
            loop_count += 1
            is_clean_at_start = False  # มีการเคลียร์ แปลว่าหน้าเว็บไม่ได้คลีนตั้งแต่แรก
            
            # 🎯 [AJAX Optimization] รอให้อิลิเมนต์เดิมถูกถอดถอนออกจากหน้าจอ (หายไปจาก DOM)
            try:
                # ระบบ AJAX ที่ดีจะลบแท็กทิ้งทันที รอตรวจจับภายใน 3 วินาที
                current_noti.wait_for(state="detached", timeout=3000)
                # หน่วงสั้นๆ เพื่อให้ DOM เรียงตัวใหม่เสร็จสิ้น (ไม่ต้องรีโหลดหน้าเว็บ)
                page.wait_for_timeout(300)
            except:
                # 🔄 [Fallback Safeguard] หากปุ่มไม่ยอมหายไปใน 3 วินาที (AJAX หน่วงหรือค้าง) 
                # ให้สั่งรีโหลดหน้าแรกเพื่อแก้เกมและอัปเดตสเตตัสสดทันที
                print(f"🔄 [{site_name}] AJAX ตอบสนองช้า สั่งรีโหลดหน้าแรกเพื่ออัปเดตแผงควบคุม...")
                page.goto(index_url, timeout=15000, wait_until="domcontentloaded")
                page.wait_for_timeout(1000)
                
        except Exception as e:
            print(f"⚠️ [{site_name}] เกิดข้อผิดพลาดในลูปเคลียร์แจ้งเตือน: {e}")
            break

    # -------------------------------------------------------------------------
    # 🎯 ระบบสรุปยอดรายงานผลส่งเข้า Discord ท้ายฟังก์ชัน
    # -------------------------------------------------------------------------
    has_notify_func = False
    try:
        if callable(globals().get('send_notify')) or callable(locals().get('send_notify')):
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
            send_notify("\n".join(report_msg))
            
        elif is_clean_at_start:
            # ✨ เคสที่ 2: ไม่มีแจ้งเตือนใดๆ ค้างตั้งแต่แรก
            send_notify(f"📥 <b>[{site_name}]</b> ตรวจสอบแล้ว <u>ไม่มีข้อความแจ้งเตือนใหม่</u> กล่องข้อความสะอาดเรียบร้อยดี")
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

def get_bearbit_item_status(soup):
    try:
        active_item = "NONE"
        display_exp = "N/A"

        # ปรับ Regex ให้ดักจับได้กว้างขึ้น (ครอบคลุมทั้งตารางรายละเอียด)
        item_row = soup.find("td", string=re.compile(r"สถานะไอเทม|Item Status|หมดอายุ Item", re.I))
        if item_row:
            target_td = item_row.find_next_sibling("td")
            full_text = target_td.get_text(" ", strip=True)

            # --- แมปปิ้งไอเทมแบบละเอียดยิ่งขึ้น ---
            item_map = {
                "FREELOAD_100": ["ซานตาคลอส", "100%", "Santa Claus"],
                "FREELOAD_50": ["ตุ๊กตาซานต้า", "50%", "Santa Doll"],
                "FREELOAD_15": ["หยินหยาง", "15%", "Yin Yang"],
                "FREELOAD_10": ["แหวนครองพิภพ", "10%", "One Ring"]
            }

            for key, keywords in item_map.items():
                if any(k in full_text for k in keywords):
                    active_item = key
                    break

            # ดึงวันเวลาหมดอายุ (ดักจับทั้งแบบมีขีด - และแบบสแลช /)
            exp_match = re.search(r"(\d{2}[-/]\d{2}[-/]\d{4}\s+\d{2}:\d{2}:\d{2})", full_text)
            if exp_match:
                raw_exp = exp_match.group(1).replace("/", "-") # Normalize format
                display_exp = raw_exp
                
                # เช็คด่วน: ถ้าจะหมดอายุใน 30 นาที ให้แจ้งเตือน (Urgency Check)
                if check_item_urgency(raw_exp):
                    display_exp += " ⚠️ ใกล้หมดอายุ!"

            if active_item != "NONE":
                update_bot_config(active_item)
                return f"<b>{active_item}</b> ({display_exp})"
                
        return "NONE"
    except Exception as e:
        return f"ERROR ({str(e)[:20]})"

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

def auto_vote_snatched(page, base_url, site_name="BEARBIT"):
    """
    ฟังก์ชันช่วยโหวตทอร์เรนต์ที่ดาวน์โหลดไปแล้ว (snatchdown.php)
    - [Multi-Site Fully Tested] รองรับพารามิเตอร์ site_name สอดประสานกับระบบ Log และ Discord ครบทุกจุด
    - [High-Speed AJAX Engine] คลิกรัวผ่านระบบสตรีมมิ่ง คุมลูปด้วยสถานะปุ่มหาย (Detached) โดยไม่ต้องรีโหลดหน้าเว็บ
    - [Count-Based Resilient] เปลี่ยนมาใช้ .count() แทน .is_visible() ป้องกันอาการบอทตาถั่วข้ามปุ่มเนื่องจากหน้าเว็บเรนเดอร์ภาพไม่ทัน
    - [Safe Callback] ออกแบบระบบเรียกใช้ send_notify ให้ยืดหยุ่นสูง รองรับการทำงานแบบแยกไฟล์โมดูล
    - [Edge-Case Fixed] เพิ่มระบบตรวจสอบ URL ป้องกันบอทติดลูปหน้าสุดท้ายกรณีปุ่ม Next Page ไม่ยอมหายไป
    - [Latency Resilient] ปรับระบบเปลี่ยนหน้าให้รอการเรนเดอร์ปุ่ม Next จริง ๆ ก่อนตรวจสอบสถานะ ป้องกันบอทจบลูปก่อนกำหนด
    """
    try:
        max_p = 5
        total_voted = 0
        last_page_url = ""  # ตัวแปรสำหรับดักจับดัชนี URL ป้องกันการโหลดหน้าซ้ำซ้อนซ้ำซาก
        
        # ประกอบ URL จาก base_url หลัก
        if not base_url.endswith('/'):
            base_url += '/'
        snatch_url = f"{base_url}snatchdown.php"
        
        print(f"🗳️ [{site_name}] เริ่มระบบ Auto-Vote (สแกนสูงสุด {max_p} หน้าผ่าน High-Speed AJAX)...")
        
        # โหลดหน้าแรกเพื่อตั้งหลัก
        page.goto(snatch_url, timeout=25000, wait_until="domcontentloaded")
        
        for p_idx in range(1, max_p + 1):
            current_url = page.url
            # 🛑 [Safeguard] ถ้า URL ปัจจุบันซ้ำกับหน้าเดิม แปลว่ากดเปลี่ยนหน้าไม่ไป หรือเป็นหน้าสุดท้ายแล้ว ให้จบลูปทันที
            if current_url == last_page_url:
                print(f"🏁 [{site_name}] ตรวจพบอาการ URL ซ้ำซ้อน (หน้าสุดท้ายจริง) สั่งจบลูปทำงานอย่างปลอดภัย")
                break
            last_page_url = current_url
            
            print(f"📖 [{site_name}] กำลังสแกนตรวจสอบรายการในหน้าทำงานที่ {p_idx}...")
            page.wait_for_timeout(1000) # รอหน้าเว็บ Render ไอคอนให้พร้อม
            
            # 🎯 ประกาศ Selector เจาะจงไอคอนปุ่มกดโหวต "ยอดเยี่ยม"
            vote_selector = 'img[title="ยอดเยี่ยม"], img[src*="v5.1.1.png"]'
            
            voted_in_page = 0
            max_attempts_per_page = 40
            
            for _ in range(max_attempts_per_page):
                # ดักจับ Target Locator สดๆ ณ วินาทีนี้
                noti_locator = page.locator(vote_selector)
                
                # ⚡ เช็กจำนวนปุ่มที่เหลืออยู่ใน DOM จริง ณ วินาทีนี้ 
                if noti_locator.count() == 0:
                    break
                    
                current_vote_target = noti_locator.first
                
                try:
                    # เลื่อนหน้าจอไปหาไอคอนเบาๆ เพื่อให้ Playwright โฟกัสพิกัดได้อย่างแม่นยำ
                    current_vote_target.scroll_into_view_if_needed()
                    page.wait_for_timeout(50)
                    
                    # 🚀 ส่งคำสั่งคลิกเพื่อทำรายการโหวตผ่านระบบ AJAX ของเว็บบิท
                    current_vote_target.click(timeout=4000)
                    total_voted += 1
                    voted_in_page += 1
                    print(f"    🔹 [{site_name}] กดโหวตสำเร็จแล้วเรียบร้อย (ยอดสะสมรวม: {total_voted} รายการ)")
                    
                    # 🎯 [AJAX Optimization] รอให้ปุ่มเดิมเปลี่ยนสถานะหรือหายไป (Detached/Hidden)
                    try:
                        current_vote_target.wait_for(state="detached", timeout=1500)
                    except:
                        # หากปุ่มไม่หายใน 1.5 วินาที หน่วงสั้นๆ แล้ว Re-scan เพื่อเช็กสเตตัสรอบถัดไป
                        page.wait_for_timeout(500)
                    
                    # หน่วงเวลาสุ่มสั้นๆ เลียนแบบพฤติกรรมมนุษย์ ป้องกันเซิร์ฟเวอร์ตัดการเชื่อมต่อ (Rate Limit)
                    page.wait_for_timeout(int(random.uniform(600, 1200)))
                    
                except Exception as click_err:
                    print(f"    ⚠️ [{site_name}] เกิดความล่าช้าในการส่ง Request (กำลังลองใหม่): {click_err}")
                    page.wait_for_timeout(1000)
                    continue
            
            if voted_in_page == 0:
                print(f"✨ [{site_name}] ไม่พบปุ่มโหวตที่ค้างคาในหน้า {p_idx}")
            else:
                print(f"🧹 [{site_name}] เคลียร์การโหวตในหน้า {p_idx} เสร็จสิ้น (ทำรายการไป {voted_in_page} ตัว)")
            
            # 🎯 [Page Transition Fix] ตรวจสอบปุ่ม Next Page สำหรับเคลื่อนไปหน้าถัดไป
            if p_idx < max_p:
                next_btn = page.locator('img[src*="nextpage.gif"]').first
                try:
                    # ⏳ หน่วงรอให้ปุ่ม Next Page แสดงสถานะเด่นชัดขึ้นบนจอก่อน 1.5 วินาที เผื่อเน็ตเวิร์กหน่วง
                    next_btn.wait_for(state="visible", timeout=1500)
                    print(f"➡️ [{site_name}] กำลังเปลี่ยนไปหน้าถัดไป (หน้า {p_idx + 1})...")
                    next_btn.click(timeout=5000)
                    page.wait_for_load_state("domcontentloaded")
                except:
                    print(f"🏁 [{site_name}] ไม่พบปุ่ม Next Page ในหน้าปัจจุบัน สั่งจบลูปทำงานอย่างสมบูรณ์")
                    break
            else:
                print(f"🏁 [{site_name}] สแกนครบตามโควตาหน้าสูงสุด ({max_p} หน้า) เรียบร้อยแล้ว")
                break
                
        # -------------------------------------------------------------------------
        # 🎯 ระบบสรุปยอดรายงานผลส่งเข้า Discord เมื่อเสร็จสิ้นภารกิจ
        # -------------------------------------------------------------------------
        has_notify_func = False
        try:
            if callable(globals().get('send_notify')) or callable(locals().get('send_notify')):
                has_notify_func = True
        except:
            pass

        if has_notify_func:
            if total_voted > 0:
                print(f"✅ [{site_name}] [🗳️ Vote System] ทำการโหวตสำเร็จทั้งหมด {total_voted} รายการ")
                send_notify(f"🗳️ <b>[{site_name}]</b> ทำการ Auto-Vote ทอร์เรนต์สำเร็จเรียบร้อย รวม <b>{total_voted}</b> รายการ")
            else:
                print(f"✨ [{site_name}] [🗳️ Vote System] ไม่มีทอร์เรนต์ค้างโหวต ระบบสะอาดเรียบร้อย")
                send_notify(f"🗳️ <b>[{site_name}]</b> ตรวจสอบแล้ว ไม่มีทอร์เรนต์ค้างโหวต ระบบสะอาดเรียบร้อย")
        else:
            print(f"📢 [{site_name}] เสร็จสิ้นภารกิจ Auto-Vote รวมทั้งสิ้น {total_voted} รายการ (ไม่ได้ส่งรายงาน Discord เนื่องจากไม่พบฟังก์ชัน send_notify)")
            
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

        # 2. 🔥 ยุทธศาสตร์สกัดเวลา (Priority Search)
        time_str = ""
        
        # กฎข้อที่ 1: ถ้าเจอ 'วันนี้' หรือ 'เมื่อวาน' ให้เชื่ออันนี้ก่อน (เพราะสดใหม่แน่นอน)
        if 'วันนี้' in raw_text:
            t_match = re.search(r'(\d{2}:\d{2}:\d{2})', raw_text)
            time_part = t_match.group(1) if t_match else now.strftime('%H:%M:%S')
            time_str = f"{now.strftime('%Y-%m-%d')} {time_part}"
        elif 'เมื่อวาน' in raw_text:
            yesterday = now - timedelta(days=1)
            t_match = re.search(r'(\d{2}:\d{2}:\d{2})', raw_text)
            time_part = t_match.group(1) if t_match else "00:00:00"
            time_str = f"{yesterday.strftime('%Y-%m-%d')} {time_part}"
        
        # กฎข้อที่ 2: ถ้าไม่เจอ ให้ใช้ raw_date ที่สกัดมาจากคอลัมน์วันที่ (ถ้ามี)
        if not time_str:
            time_str = data.get('raw_date', '')

        # กฎข้อที่ 3: ถ้ายังไม่มีอีก (เช่น Sticky) ให้ใช้เวลาปัจจุบันไปเลย
        if not time_str:
            time_str = now.strftime('%Y-%m-%d %H:%M:%S')

        # 3. 🛠 จัดการฟอร์แมตและแปลงเป็น datetime (แก้ปัญหา Error Time Format)
        try:
            # ล้างคราบอักขระและเปลี่ยน / เป็น - ให้หมด
            clean_time = time_str.strip().replace('/', '-')
            
            # กรณีเจอวันที่ในชื่อไฟล์ที่ไม่มีเวลา (เช่น 08-05-26) ให้เติมเวลาหลอก
            if ' ' not in clean_time and len(clean_time) <= 10:
                clean_time += " 00:00:00"
            
            clean_time = clean_time[:19] # เอาแค่ YYYY-MM-DD HH:MM:SS
            
            # แยกเช็คปี 2 หลัก หรือ 4 หลัก
            date_part = clean_time.split(' ')[0]
            year_part = date_part.split('-')[-1]
            
            # ตรวจสอบ Format: dd-mm-yy หรือ yyyy-mm-dd
            if len(year_part) == 2:
                # ปี 2 หลัก (เช่น 26)
                fmt = '%d-%m-%y %H:%M:%S' if clean_time[2] == '-' else '%y-%m-%d %H:%M:%S'
            else:
                # ปี 4 หลัก (เช่น 2026)
                fmt = '%d-%m-%Y %H:%M:%S' if clean_time[2] == '-' else '%Y-%m-%d %H:%M:%S'
            
            naive_time = datetime.strptime(clean_time, fmt)
            upload_time = tz.localize(naive_time)
            
        except Exception:
            # ไม้ตายสุดท้าย: ถ้าแปลงไม่ได้จริงๆ ให้ถือเป็นเวลาปัจจุบัน (เพื่อ Racing)
            upload_time = now

        # 4. Racing Logic (คำนวณอายุและ Ratio)
        age_delta = now - upload_time
        total_hours = age_delta.total_seconds() / 3600
        
        if age_delta.total_seconds() < -300: return False # เวลามั่ว (ล้ำหน้าปัจจุบัน)

        demand_ratio = data['leechers'] / max(1, data['seeders'])

        # Log สรุปสถานะ
        print(f" 📊 Peers: {short_title}.. (S:{data['seeders']} L:{data['leechers']} C:{data['completed']} Ratio:{demand_ratio:.2f} Age:{total_hours:.1f}ชม.)")

        # --- เงื่อนไขการกรอง ---
        if data.get('is_sticky') and total_hours > 12:
            print(f" ⏭️ ข้าม: [Sticky เก่า]")
            return False

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
    
def main():
    startup_msg = "🚀 Universal Auto-Pilot : Started"
    print(startup_msg); send_notify(startup_msg)
    
    while True:
        try:
            global CFG
            CFG = load_full_config()
            SET = CFG.get('SETTING', {})
            global_clean = CFG.get('GLOBAL_CLEAN', {})
            active_nodes = []
            node_status_buffer = []

            # 1. Node Section (Checking & Cleanup & Update Trackers)
            print("\n🔌 NODE STATUS CHECKING...")
            for n_cfg in CFG['NODES']:
                if not n_cfg.get('enable'): continue
                
                # สร้าง Object ตามประเภทของ Node
                node = RtorrentNode(n_cfg) if n_cfg.get("type") == "rtorrent" else QbitNode(n_cfg)

                if node.login():
                    # 1. ต้อง refresh ก่อนเพื่อให้ NodeCleaner รู้ค่าพื้นที่ที่แท้จริง
                    node.refresh_status()
                    pre_free = node.free_gb  # เก็บค่าพื้นที่ "ก่อนลบ"

                    # 2. เริ่ม Cleanup (ส่งแรงกระตุ้นให้โหมด Emergency ทำงาน)
                    # ตรวจสอบให้แน่ใจว่าได้อัปเดตคลาส NodeCleaner ให้รับค่า is_emergency แล้ว
                    NodeCleaner(node, n_cfg.get('clean_settings', {}), global_clean).process()

                    # 3. ให้เวลาระบบไฟล์คืนพื้นที่ และอัปเดต Tracker
                    time.sleep(2)

                    # ทำการกวาดและบังคับ Start งานค้าง (Sweeper)
                    if hasattr(node, '_sweeper_force_start'):
                        node._sweeper_force_start()

                    node.reannounce_all()

                    # 4. refresh อีกครั้งเพื่อดูค่าพื้นที่ "หลังลบ"
                    node.refresh_status()

                    # 5. คำนวณและแสดงผลพื้นที่ที่กู้คืนมาได้
                    gained = node.free_gb - pre_free
                    if gained > 0.01:
                        print(f"✨ [{node.name}] Cleaned up: {gained:.2f} GB recovered!")

                    active_nodes.append((node, n_cfg))
                    icon = "🟢"
                else: icon = "❌"
                line = f"{icon} [{node.name}] {getattr(node, 'stat_msg', 'N/A')}"
                print(line)
                node_status_buffer.append(line)
                update_trackers(node)

            if active_nodes:
                print("⏳ Waiting 5s for trackers to sync with All Trackers")
                time.sleep(5)

            if node_status_buffer:
                send_notify("🔌 <b>Node Status Report</b>\n" + "\n".join(node_status_buffer))

            # 2. Browser Section
            with sync_playwright() as p:
                browser, browser_path = launch_any_browser(p)
                # ดึง Config ของ Site ที่ Enable
                target_sites_cfg = [s for s in CFG.get('SITE', []) if s.get('enable', True)]
                print(f"📡 Detected Sites: {[s['name'] for s in target_sites_cfg]}")

                for site_cfg in target_sites_cfg:
                    site = site_cfg['name']
                    
                    current_site_seen_file = get_seen_file(site)
                    seen_ids = load_data(current_site_seen_file) 
                    current_site_hash_file = get_hash_file(site)
                    seen_hashes = load_data(current_site_hash_file)
                    
                    auth_file = get_auth_file(site)
                    dl_session = requests.Session()

                    # 1. สร้าง Context ใหม่ทุกครั้งภายใน Loop (เพื่อให้ได้ Session สดใหม่)
                    # ถ้ามีไฟล์คุกกี้ให้โหลด ถ้าไม่มีให้เริ่มจากว่างเปล่า
                    current_context = browser.new_context(
                        storage_state=auth_file if os.path.exists(auth_file) else None,
                        user_agent=stealth_args["user_agent"],
                        viewport=stealth_args["viewport"],
                        locale=stealth_args["locale"],
                        timezone_id=stealth_args["timezone_id"],
                        extra_http_headers=stealth_args["extra_http_headers"],
                        ignore_https_errors=stealth_args["ignore_https_errors"]
                    )
                    current_context.set_default_timeout(30000)
                    
                    try:
                        # 2. สร้างหน้าเพจและฉีด Stealth
                        site_page = current_context.new_page()
                        apply_stealth(site_page)
                    
                        # เข้าสู่กระบวนการ Universal Login และ Scan ตามปกติ
                        if ensure_site_logged_in(site_page, site_cfg):
                            sync_playwright_cookies(site_page.context, dl_session)
                            # 1. ดึงสถิติและส่ง Report (แยกตาม site_name)
                            stats_data = get_site_stats(site_page, site_cfg)
                            send_notify(stats_data)
                            print(stats_data)

                            # --- วนลูปสแกนแต่ละโซน (ดึง config ตามชื่อ site ปัจจุบัน) ---
                            base_url = site_cfg.get('base_url')
                            site_target_urls = site_cfg.get('target_urls', [])
                            
                            for target_item in site_target_urls:
                                if site_page.is_closed(): break 

                                # 1. เตรียมข้อมูลโซน
                                if isinstance(target_item, dict):
                                    if not target_item.get('enable', True): continue
                                    target_url = target_item.get('url')
                                    display_zone = target_item.get('name', "Zone")
                                else:
                                    target_url, display_zone = target_item, "Zone"

                                print(f"\n🌐 [{site}] Scanning: [{display_zone}]")

                                # แสดงสถานะไอเทมล่าสุด (เช่น ซานตาคลอสจาก BearBit)
                                status_line = generate_main_status(CFG, site_name=site) 
                                print(status_line)                        
            
                                # 2. ไปยังหน้าเป้าหมาย
                                try:
                                    # พยายามเข้าครั้งแรกด้วย Referer หน้าหลัก
                                    site_page.goto(target_url, referer=base_url, wait_until="networkidle", timeout=60000)
            
                                    # ดึง Content มาเช็คทันที
                                    soup = BeautifulSoup(site_page.content(), "html.parser")

                                    # ตรวจสอบว่าติดหน้า Hotlink หรือไม่
                                    if "ไม่สามารถเปิดลิงก์จากภายนอกได้" in soup.text:
                                        print(f"⚠️ [{site}] ติด Hotlink... กำลังใช้แผน B (Double-Referer)")
                                        # แผน B: เข้าซ้ำโดยใช้ URL ตัวเองเป็น Referer (วิธีนี้ได้ผลดีกับระบบ Anti-Bot หลายที่)
                                        site_page.goto(target_url, referer=target_url, wait_until="networkidle")
                                        soup = BeautifulSoup(site_page.content(), "html.parser")
                
                                    # ถ้าหลังจากแผน B แล้วยังติดหน้าเดิมอยู่ (อาจจะเพราะ Cookie หลุด)
                                    if "ไม่สามารถเปิดลิงก์จากภายนอกได้" in soup.text:
                                        print(f"❌ [{site}] แผน B ล้มเหลว ข้ามโซนนี้ไปก่อน")
                                        continue

                                except Exception as e:
                                    print(f"❌ [{site}] Error ระหว่างเข้าหน้า {display_zone}: {e}")
                                    continue

                                # --- เริ่มกระบวนการสแกน Torrent ตามปกติ ---
                                added_in_zone = [] # เก็บ msg รายการที่เพิ่มสำเร็จ
                                full_nodes_in_zone = []
                                error_logs = []
                                count_skip = 0    # นับจำนวนที่ข้าม
                        
                                # ดึงรายการ Torrent
                                all_details = soup.find_all("a", href=re.compile(r"details(new)?\.php\?id=\d+"))
                                rows = []

                                # --- วนลูปสกัดเฉพาะรายการไฟล์จริง ---
                                for a in all_details:
                                    # 1. เช็คชื่อไฟล์เบื้องต้น
                                    t_text = a.get_text(strip=True)
                                    if len(t_text) <= 5: continue
    
                                    # 2. ป้องกันการดึงสถิติผู้ใช้ (Ratio, Bonus, User Profile)
                                    # ปกติสถิติพวกนี้มักจะมีคำเฉพาะ หรืออยู่ใน ID/Class ที่ต่างออกไป
                                    parent_tr = a.find_parent("tr")
    
                                    if parent_tr and parent_tr not in rows:
                                        # ตรวจสอบว่าในแถว (row) นั้นมีคำบ่งชี้ว่าเป็นข้อมูลส่วนตัวหรือไม่
                                        row_raw_text = parent_tr.get_text().lower()
                                        user_stat_keywords = ['ratio:', 'bonus:', 'upload:', 'download:', 'อัพโหลด:', 'ดาวน์โหลด:']
        
                                        # ถ้าในแถวมีคำพวกนี้ ให้ข้ามไปเลย เพราะไม่ใช่แถวของ Torrent
                                        if any(key in row_raw_text for key in user_stat_keywords):
                                            continue
            
                                        rows.append(parent_tr)
                                # --- วนลูปรายไฟล์ในโซน ---
                                for row in rows:
                                    try:
                                        local_headers = {
                                            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                                            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                                            'Accept-Language': 'th-TH,th;q=0.9,en-US;q=0.8,en;q=0.7',
                                            'Accept-Encoding': 'gzip, deflate, br',
                                            'Connection': 'keep-alive',
                                            'Upgrade-Insecure-Requests': '1',
                                            'Referer': f"{target_url}", # หรือหน้าหลักที่บอทใช้ดึงข้อมูล
                                            'Cache-Control': 'max-age=0'
                                        }
                                        # 1. สกัดข้อมูลพื้นฐานก่อน
                                        data = extract_torrent_data(row, base_url, dl_session, local_headers)

                                        if not data or not data.get('id'):
                                            print(f" ⚠️ ข้าม: สกัดข้อมูล ID ไม่สำเร็จ")
                                            continue

                                        t_id = data['id']
                                        download_url = data['download_url']
                                        details_url = data['details_url']
                                        # ดึงชื่อดิบมาเตรียมทำความสะอาด
                                        raw_title = data.get('title', 'Unknown')


                                        if not download_url:
                                            print(f" ⚠️ [{t_id}] ข้าม: ไม่พบลิงก์ดาวน์โหลด")
                                            continue

                                        # 2. เช็คความสดและโอกาสทำ Ratio ก่อนเลย
                                        if not is_fresh_and_racing(data):
                                            #print(f" ⏭️ ข้าม: ไฟล์ไม่อยู่ในเงื่อนไข Racing (เก่าเกินไปหรือ Peer ไม่คุ้ม)")
                                            count_skip += 1
                                            continue  # ข้ามไฟล์ที่ "ไม่คุ้ม" ที่จะใช้โหลดและพื้นที่                                        

                                        # 3. เช็คประวัติการเพิ่ม (Seen ID)
                                        if str(t_id) in seen_ids:
                                            print(f" ❌ ข้าม: เคยเพิ่มไปแล้ว (ใน {site})")
                                            count_skip += 1
                                            continue

                                        # 4. สกัดชื่อไฟล์แบบปลอดภัยยิ่งขึ้น
                                        safe_title = clean_name(raw_title)
                
                                        # Logic การตัดสินใจใช้ชื่อ: ถ้าชื่อมีคำพวก Stat หรือสั้นไป ให้ใช้ ID แทน
                                        is_stat = any(word in safe_title.lower() for word in ['ratio', 'bonus', 'upload', 'download'])
                
                                        if not is_stat and len(safe_title) >= 10:
                                            t_name = safe_title
                                        else:
                                            t_name = f"Torrent_ID_{t_id}"

                                        print(f"🔍 [{site.upper()}] Checking: {t_name[:50]}... (ID: {t_id})")

                                        # ลิงก์ดาวน์โหลด (ตรวจสอบซ้ำอีกครั้ง)
                                        if not download_url:
                                            print(f" ⚠️ [{t_id}] ข้าม: ไม่พบลิงก์ดาวน์โหลด")
                                            continue
                                        # 5. เช็คขนาดไฟล์ (ป้องกัน Error กรณี t_size_str เป็น None)
                                        t_size_gb = parse_size(data['size_str'])
                                        if not (SET.get('MIN_SIZE_GB', 0) <= t_size_gb <= SET.get('MAX_SIZE_GB', 999)):
                                            print(f" ❌ ข้าม: ขนาด {t_size_gb:.2f}GB ไม่ตรงเงื่อนไข")
                                            count_skip += 1
                                            continue

                                        # ##################################################
                                        # # 6. Logic ฟรีโหลดและไอเทม (ฉบับสมบูรณ์)
                                        # ##################################################
                                        is_free_to_go = False
                                        is_use_item = False

                                        freeload_enable = SET.get('FREELOAD_ENABLE', True) # เช็กว่าเปิดระบบกรองฟรีโหลดไหม
                                        item_discount = SET.get('CURRENT_DISCOUNT', 0)     # เช่น 50%
                                        min_free_req = SET.get('MIN_FREE_PERCENT', 0)      # เช่น 10%
                                        site_name = site.lower()
                                        
                                        # ตรวจสอบสถานะ (รอการอนุมัติ) 
                                        if details_url and dl_session:
                                            if check_pending_status(dl_session, details_url):
                                                print(f" ⏳ ข้าม: ไฟล์นี้ยังอยู่ในสถานะ (รอการอนุมัติ) -> {details_url}")
                                                count_skip += 1
                                                continue

                                        # --- กรณีที่ 1: ปิดระบบฟรีโหลด (ไม่สนฟรี % ลุยดาวน์โหลดทุกไฟล์) ---
                                        if not freeload_enable:
                                            is_free_to_go = True
                                            is_use_item = False
                                            # print(f" 🎯 [ALL-IN MODE] ปิดระบบกรองฟรีโหลด: ลุยดาวน์โหลดทุกไฟล์โดยไม่เช็กเปอร์เซ็นต์")

                                        # --- กรณีที่ 2: เปิดระบบฟรีโหลด (กรองตามเงื่อนไขเว็บและไอเทม) ---
                                        elif "bearbit" in site_name:
                                            # 1. เช็คหน้าเว็บก่อนว่าให้ฟรีเท่าไหร่
                                            free_p = check_freeload_status(row)

                                            # 2. เข้าสู่ Logic ตัดสินใจโดยยึดไอเทมเป็นเกณฑ์หลัก
                                            if item_discount > 0:
                                                # ถ้าหน้าเว็บฟรี "มากกว่า" ไอเทม -> ข้ามทันที (เพื่อประหยัดไอเทมไปใช้กับไฟล์ไม่ฟรี)
                                                if free_p > item_discount:
                                                    print(f" ❌ ข้าม: หน้าเว็บฟรี {free_p}% ซึ่งดีกว่าไอเทม {item_discount}% (เก็บไอเทมไว้ก่อน)")
                                                    count_skip += 1
                                                    continue
                                
                                                # กรณีที่หน้าเว็บฟรี "น้อยกว่าหรือเท่ากับ" ไอเทม -> บังคับใช้ไอเทมลุยเลย!
                                                else:
                                                    is_use_item = True
                                                    is_free_to_go = True
                                                    print(f" 🎫 [ITEM MODE] บังคับใช้ไอเทม {item_discount}% (หน้าเว็บฟรีแค่ {free_p}%)")

                                            # 3. ถ้าไม่มีไอเทม หรือไม่ได้ตั้งค่าไอเทม ค่อยกลับมาพึ่งเกณฑ์ขั้นต่ำบนหน้าเว็บ
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
                                            # --- เว็บอื่น (Unlimitz, TorrentDD) ---
                                            # ถ้าหลุดมาถึงตรงนี้ แปลว่า freeload_enable = True (เปิดระบบกรองฟรีโหลด)
                                            # ดังนั้นเว็บอื่นจะต้องบังคับเช็กเฉพาะไฟล์ที่ฟรี 100% เท่านั้นตามลอจิกเดิมของคุณ
                                            free_p_others = check_freeload_status(row)
                                            
                                            if free_p_others == 100:
                                                is_free_to_go = True
                                                # print(f" ✅ [OTHERS] ผ่าน: ไฟล์ฟรี 100% (จากระบบสแกนละเอียด)")
                                            else:
                                                print(f" ❌ ข้าม: ไฟล์นี้ไม่ฟรี 100% (หน้าเว็บแจ้ง {free_p_others}%)")
                                                count_skip += 1
                                                continue

                                        # ตรวจสอบด่านสุดท้าย
                                        if not is_free_to_go:
                                            continue

                                        # --- 7. เริ่มทำการดาวน์โหลดไฟล์ .torrent ---
                                        r_dl = dl_session.get(download_url, headers=local_headers, timeout=20)
                
                                        if r_dl.status_code == 200:
                                            content_type = r_dl.headers.get('Content-Type', '').lower()
                    
                                            # ตรวจสอบว่าเป็น HTML หรือไฟล์ที่เล็กผิดปกติ (หน้าแจ้งเตือน/หน้าใช้ไอเทม)
                                            if 'html' in content_type or len(r_dl.content) < 800:
                                                detected = chardet.detect(r_dl.content)
                                                encoding_type = detected['encoding'] or 'tis-620'
                                                soup_error = BeautifulSoup(r_dl.content, 'html.parser', from_encoding=encoding_type)

                                                # ค้นหาปุ่มยืนยัน (dcI.php หรือ dI.php)
                                                confirm_link_tag = soup_error.find("a", href=re.compile(r"d[c]?I\.php", re.I))
                        
                                                if not confirm_link_tag:
                                                    img_btn = soup_error.find("img", {"src": re.compile(r"DL|download|DL5", re.I)})
                                                    if img_btn: confirm_link_tag = img_btn.find_parent("a")

                                                if confirm_link_tag and confirm_link_tag.get("href"):
                                                    action_url = confirm_link_tag.get("href")
                                                    # ประกอบ URL ให้สมบูรณ์
                                                    final_dl_url = action_url if action_url.startswith('http') else f"{base_url.rstrip('/')}/{action_url.lstrip('/')}"
                            
                                                    # สำคัญ: อัปเดต Referer เป็นหน้าปัจจุบันก่อนกดยืนยัน
                                                    local_headers['Referer'] = r_dl.url 
                                                    print(f"🔄 [{site}] กดยืนยันดาวน์โหลด -> {final_dl_url}")
                            
                                                    r_final = dl_session.get(final_dl_url, headers=local_headers, timeout=20)
                                                    r_dl = r_final # แทนที่ด้วยผลลัพธ์ใหม่

                                            # --- ตรวจสอบไฟล์ที่ได้รับมาจริง (Final Check) ---
                                            raw_data = r_dl.content
                                            if raw_data.startswith(b'd'): # เช็ค Bencode
                                                t_hash = extract_info_hash(raw_data)
                        
                                                if t_hash and t_hash in seen_hashes:
                                                    print(f" ❌ ข้าม: Hash {t_hash} ซ้ำในระบบ")
                                                    continue
                                
                                                # >>> [ขั้นตอนถัดไป] ส่งเข้า Client: qBittorrent/rTorrent <<<
                                                print(f"✅ [{site}] พร้อมส่งไฟล์เข้า Client (Hash: {t_hash})")
                                            else:
                                                print(f"🚩 ⚠️ ข้าม: ไม่ใช่ไฟล์ทอร์เรนต์ (อาจติดหน้าล็อคอินหรือเรโชต่ำ)")
                                                # เก็บ Log กรณีพลาด
                                                with open(f"debug_{site}_{t_id}.html", "wb") as f:
                                                    f.write(r_dl.content)
                                
                                            is_already_in_node = False
                                            target_node_name = ""

                                            for node_obj, _ in active_nodes:
                                                # เช็คตรงตัวกับ API ของ Node (qBittorrent/rTorrent)
                                                if node_obj.is_torrent_exists(t_hash):
                                                    is_already_in_node = True
                                                    target_node_name = node_obj.name
                                                    break

                                            if is_already_in_node:
                                                # พิมพ์ Log ให้ชัดเจนว่าเจอที่เครื่องไหน และโชว์ Hash 5 ตัวท้ายเพื่อตรวจสอบ
                                                print(f" ❌ ข้าม: ตรวจพบ Hash [...{t_hash[-5:]}] วิ่งอยู่ใน {target_node_name}")
    
                                                # บันทึก ID ลงประวัติเว็บปัจจุบัน (กันโหลด .torrent ซ้ำ)
                                                seen_ids.add(t_id)
    
                                                # ⚠️ ห้ามเอาเข้า seen_hashes ตรงๆ ถ้าพี่อยากให้มันเช็ค Node จริงทุกครั้ง
                                                # หรือถ้าจะเอาเข้า ต้องมั่นใจว่าใน seen_hashes มีค่าตรงกับในเครื่องจริงๆ เท่านั้น
                                                count_skip += 1
                                                continue

                                            # --- [ส่วนเลือก Node และสั่งดาวน์โหลด] ---
                                            active_nodes.sort(key=lambda x: x[0].free_gb, reverse=True)

                                            success_node = None # ใช้มาร์คว่าแอดไฟล์สำเร็จหรือยัง
                                            task_weight = calculate_task_weight(t_size_gb)

                                            for node_obj, n_cfg in active_nodes:
                                                d_type = n_cfg.get('disk_type', 'HDD')
                                                dynamic_max_cap, p_wait = get_node_dynamic_cap(node_obj, d_type)
                                                current_load = round(get_node_current_weight(node_obj), 1)  # ปัดเศษให้เหลือ 1 ตำแหน่งตั้งแต่ตอนคำนวณ

                                                print(f"📡 Check [{node_obj.name}]: Load {current_load:.1f}/{dynamic_max_cap} (Wait: {p_wait:.1f})")

                                                # 1. เช็ค Capacity
                                                if (current_load + task_weight) > dynamic_max_cap:
                                                    print(f" ⏳ [Queue Full] {node_obj.name} ลอง Node ถัดไป")
                                                    continue

                                                # 2. เช็คพื้นที่สุทธิ
                                                effective_free_gb = node_obj.free_gb - node_obj.get_downloading_size()
                                                if effective_free_gb < (t_size_gb + 15.0):
                                                    print(f" 🧹 พื้นที่น้อยไป... พยายาม Reclaim")
                                                    smart_reclaim_process(node_obj, t_size_gb)
                                                    node_obj.refresh_status() # อัปเดตหลังลบ

                                                    if node_obj.free_gb < (t_size_gb + 2.0):
                                                        print(f" ❌ พื้นที่ยังไม่พอ... ลอง Node ถัดไป")
                                                        continue

                                                # ✅ 3. ดำเนินการ Add ไฟล์ทันทีที่เจอ Node ที่เหมาะสม
                                                try:
                                                    if node_obj.add(r_dl.content,site_name=site):
                                                        success_msg = f"📥 [Success] {node_obj.name} | {t_size_gb:.1f}GB | {t_name[:40]}"
                                                        print(success_msg)

                                                    # จัดการจองพื้นที่และบันทึกประวัติ
                                                        booking_size = t_size_gb + 0.1
                                                        node_obj.free_gb = max(0.0, node_obj.free_gb - booking_size)
                                                        node_obj.stat_msg = f"Used: (Updating...) | Avail: {node_obj.free_gb:.1f}GB"

                                                        added_in_zone.append(success_msg)
                                                        seen_ids.add(str(t_id))
                                                        if t_hash: seen_hashes.add(t_hash)

                                                        success_node = node_obj
                                                        if details_url:
                                                            try:
                                                                auto_click_thanks(site_page,details_url)
                                                                time.sleep(random.uniform(0.8, 1.5))
                                                            except Exception as e:
                                                                print(f" ⚠️ ไม่สามารถขอบคุณ ID {t_id} ได้: {e}")

                                                        break
                                                    else:
                                                        # กรณี API ตอบกลับมาเป็น False (เช่น ดิสก์ในโปรแกรมเต็ม หรือไฟล์ซ้ำ)
                                                        print(f" ⚠️ [API Reject] {node_obj.name} ปฏิเสธงาน (Disk Full/Dup)")
                                                except Exception as e:
                                                    # 🚨 จุดสำคัญ: จะโชว์ว่า Password ผิด, Timeout หรือ Server Down
                                                    print(f"❌ [Connect Error] {node_obj.name}: {str(e)}")
                                            if not success_node:
                                                node_summary = []
                                                for n_obj, _ in active_nodes:
                                                    c_load = get_node_current_weight(n_obj)
                                                    d_max, _ = get_node_dynamic_cap(n_obj, n_obj.disk_type if hasattr(n_obj, 'disk_type') else 'HDD')
                                                    node_summary.append(f"📡 {n_obj.name}: {c_load}/{d_max}")

                                                error_detail = "\n".join(node_summary)
                                                full_alert_msg = f"❌ **ไม่มีโหนดว่าง**\n\n**สถานะโหลดปัจจุบัน:**\n{error_detail}"
                                                full_nodes_in_zone.append(f"❌ [Full] {t_name[:30]}...")
                                                print(f"🚩 สรุปผล: {full_alert_msg}")

                                    except Exception as e: # <--- ถ้าข้างในพัง มันจะเด้งมาที่นี่
                                        print(f" ❌ Error ในแถวนี้: {e}")
                                        continue # แล้วไปทำแถวถัดไปทันที

                                    if len(added_in_zone) >= SET.get('MAX_NEW_PER_ZONE', 5): 
                                        print(f" ⚠️ ครบโควตา {len(added_in_zone)} ไฟล์แล้ว")
                                        break

                                # ======================================================
                                # 📊 สรุปหลังจบแต่ละโซน (อยู่นอก Row loop แต่อยู่ใน Zone loop)
                                # ======================================================
                                # ตรวจสอบเงื่อนไขการส่งแจ้งเตือน (เพิ่ม len(error_logs) > 0)
                                if len(added_in_zone) > 0 or count_skip > 0 or len(full_nodes_in_zone) > 0 or len(error_logs) > 0:
                                    condition_header = generate_main_status(CFG)
                                    summary_msg = (
                                        f"⚙️ <b>{condition_header}</b>\n"
                                        f"🌐 <b>Scanning:</b> [{display_zone}] {target_url}\n\n"
                                    )

                                    # 1. แสดงไฟล์ที่เพิ่มสำเร็จ
                                    if added_in_zone:
                                        summary_msg += "✅ <b>Added:</b>\n" + "\n".join(added_in_zone) + "\n\n"

                                    # 2. แสดงไฟล์ที่พลาดเพราะโหนดเต็ม
                                    if full_nodes_in_zone:
                                        summary_msg += "⚠️ <b>Queue Full (ไม่มีโหนดว่าง):</b>\n" + "\n".join(full_nodes_in_zone) + "\n\n"

                                    # 3. แสดง Error ที่เกิดขึ้นระหว่างสแกน (เช่น Login หลุด)
                                    if error_logs:
                                        summary_msg += "🚨 <b>System Errors:</b>\n" + "\n".join(error_logs) + "\n\n"

                                    # 4. กรณีไม่มีไฟล์เข้าเงื่อนไข และไม่มี Error
                                    if not added_in_zone and not full_nodes_in_zone and not error_logs:
                                        summary_msg += "❌ ไม่มีไฟล์เข้าเงื่อนไข\n\n"            
    
                                    # ส่วนท้ายสรุปสถิติ
                                    footer = (f"📊 <b>สรุป {display_zone}:</b> "
                                                f"เพิ่ม {len(added_in_zone)} | "
                                                f"เต็ม {len(full_nodes_in_zone)} | "
                                                f"ข้าม {count_skip}" + 
                                                (f" | Error {len(error_logs)}" if error_logs else ""))

                                    summary_msg += footer
    
                                    print(f"\n{footer}") # แสดงในหน้าจอ Log
                                    send_notify(summary_msg) # ส่งแจ้งเตือนไปยัง Discord
                                # ======================================================

                                # บันทึกข้อมูลหลังจบ "ทุกโซน" 
                                save_data(current_site_seen_file, seen_ids)
                                save_data(current_site_hash_file, seen_hashes)
                    except Exception as e:
                        print(f"❌ Error at {site}: {e}")

                    finally:
                        # ✅ 1. ปิด Page และ Context "ทันที" เมื่อจบแต่ละ Site
                        # เพื่อเคลียร์คุกกี้และรอยนิ้วมือ ไม่ให้ปนกับ Site ถัดไป
                        if 'site_page' in locals() and not site_page.is_closed():
                            site_page.close()
                        if 'site_context' in locals():
                            site_context.close() 
                        print(f"🧹 [{site}] เคลียร์ Session เรียบร้อย\n")

                # ปิด Browser เมื่อรันครบทุกโซนแล้วเท่านั้น
                browser.close()
                print("🔒 [System] ปิด Browser และจบการทำงานทั้งหมด")

            stats_report = format_site_stats_report([n[0] for n in active_nodes])
            print(stats_report)
            send_notify(stats_report)
            # จบรอบ เข้าสู่ช่วงพัก
            wait_sec = random.randint(SET.get('MIN_WAIT_MINUTES', 2)*60, SET.get('MAX_WAIT_MINUTES', 10)*60)
            wait_msg = f"💤 Cycle finished. Waiting {wait_sec//60} minutes for next scan..."
            
            # พิมพ์ลง Log แค่ครั้งเดียวว่ากำลังรอ
            print(wait_msg) 
            send_notify(wait_msg) 

            for s in range(wait_sec, 0, -1):
                # ใช้ \r เพื่อให้พิมพ์ทับบรรทัดเดิมใน Terminal 
                # และใช้ sys.stdout โดยตรงจะช่วยลดการเขียนลง Log ไฟล์ได้ในบางการตั้งค่า
                sys.stdout.write(f"\r⏳ Next cycle in: {s//60}m {s%60}s...   ")
                sys.stdout.flush()
                time.sleep(1)
            
            # เมื่อรอเสร็จค่อยพิมพ์ขึ้นบรรทัดใหม่
            cycle_msg = "\n🚀 Starting next cycle..."
            print(cycle_msg); send_notify(cycle_msg)

        except Exception as e:
            print(f"❌ Global Error: {e}"); time.sleep(60)

if __name__ == "__main__":
    main()
