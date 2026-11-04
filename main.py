from fake_useragent import UserAgent
from urllib.parse import urljoin, unquote
import xmlrpc.client
import logging
import aiohttp
import random
import inspect
import httpx
import cloudscraper
from qbittorrentapi import Client
import threading
import chardet
import gzip
import time
import os
import re
from functools import lru_cache
import hashlib
import json
import bcoding
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
import gc
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
from pathlib import Path
import traceback
import urllib.parse

print = functools.partial(print, flush=True)

ocr = ddddocr.DdddOcr(show_ad=False)

stop_event = asyncio.Event()

# GRACEFUL_SHUTDOWN_PATCH_V1
# Signal Handler มีหน้าที่ร้องขอหยุดเท่านั้น
# Main Loop เป็นเจ้าของการ Cleanup ทั้งหมด

def handle_exit(sig):
    if stop_event.is_set():
        return

    print(
        f"\n🛑 ได้รับสัญญาณ {sig}; "
        "กำลังรอ Main Loop หยุดอย่างปลอดภัย..."
    )
    stop_event.set()


urllib3.disable_warnings()

# ========================= CONFIGURATION =========================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
STATS_CACHE_FILE = os.path.join(BASE_DIR, "stats_cache.json")
STATS_HISTORY_FILE = os.path.join(BASE_DIR, "stats_history.json")
MAPPING_FILE = os.path.join(BASE_DIR, "torrent_mapping.json")

# UAP_PHASE_F_RESUME_RECLAIM_GUARDS_V1
# Automatic resume is opt-in per node; absent/false means keep paused.
# PURGE_AUTH_DRYRUN_PATCH_V1
# Phase A: Read-only authorization; deletion remains disabled.
PURGE_AUTH_DRY_RUN = True

# Phase G Controlled Runtime Test: fail-closed defaults.
PHASE_G_ALLOW_STARTUP_REANNOUNCE = False
PHASE_G_ALLOW_NORMAL_TORRENT_ADD = False
PHASE_G_ALLOW_MAPPING_WRITES = False

# Exact tracker hostname aliases supplied and verified by operator.
PURGE_TRACKER_HOST_ALIASES = {
    "files.bit-sv.net": "BEARBIT",
    "tracker.cab": "DEDBIT",
}


DB_DIR = os.path.join(BASE_DIR, "db")
CFG = {} 
ORIGINAL_SETTING = None

def load_full_config():
    if not os.path.exists(CONFIG_PATH):
        print(f"❌ Error: ไม่พบไฟล์ {CONFIG_PATH}")
        sys.exit(1)
        
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"❌ Error: ไฟล์ {CONFIG_PATH} มีรูปแบบ JSON ไม่ถูกต้อง (Syntax Error: {e})")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error: ไม่สามารถอ่านไฟล์ config ได้ ({e})")
        sys.exit(1)

async def send_notify(msg, *args, **kwargs):
    """
    เรียก _send_notify_sync โดยตรงผ่าน asyncio.to_thread
    ไม่ต้องพึ่งพา globals() เพื่อป้องกันปัญหา Name Resolution
    """
    try:
        # เรียกฟังก์ชันซิงค์โดยตรง ถ้ามันอยู่ในไฟล์เดียวกันหรือ Import มา
        await asyncio.to_thread(_send_notify_sync, msg, *args, **kwargs)
        
    except Exception as e:
        error_msg = f"🚨 [Notification Error]: {e}"
        print(error_msg)
        with open("bot_error.log", "a", encoding="utf-8") as f:
            f.write(f"{get_now()} - {error_msg}\n")
        return False
    return True

def _send_notify_sync(msg):
    """
    ฟังก์ชันสำหรับจัดการการส่งแจ้งเตือนแบบ Blocking (Sync)
    """
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
        
        return True
        
    except Exception as e:
        print(f"Critical _send_notify_sync Error: {e}")
        return False

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

def get_db_path(site_key):
    """สร้าง Path สำหรับไฟล์ {site}_link_db.json"""
    if not os.path.exists(DB_DIR):
        os.makedirs(DB_DIR)
    return os.path.join(DB_DIR, f"{site_key}_link_db.json")

def load_db(site_key):
    """โหลดข้อมูลจากไฟล์ database ของไซต์นั้นๆ"""
    db_path = get_db_path(site_key)
    if not os.path.exists(db_path):
        return {}
    with open(db_path, 'r', encoding='utf-8') as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def save_db(site_key, data):
    """บันทึกข้อมูลลงไฟล์ database ของไซต์นั้นๆ"""
    db_path = get_db_path(site_key)
    with open(db_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def load_all_db():
    """
    โหลดข้อมูลจากทุกไฟล์ {site}_link_db.json ในโฟลเดอร์ DB_DIR
    คืนค่าเป็น Dict: { 'site_key': { 'hash': {ข้อมูลไฟล์} } }
    """
    all_dbs = {}
    if not os.path.exists(DB_DIR):
        print(f"⚠️ ไม่พบโฟลเดอร์ฐานข้อมูล: {DB_DIR}")
        return all_dbs

    for filename in os.listdir(DB_DIR):
        if filename.endswith("_link_db.json"):
            # ดึง site_key ออกมาจากชื่อไฟล์ (เช่น "thabit_link_db.json" -> "thabit")
            site_key = filename.replace("_link_db.json", "")
            
            # โหลดข้อมูลผ่านฟังก์ชัน load_db เดิมที่มีอยู่
            data = load_db(site_key)
            all_dbs[site_key] = data
            
    return all_dbs

# เพิ่ม Lock ไว้ที่ระดับ Global
db_lock = asyncio.Lock()

async def async_save_db(site_key, data):
    """เวอร์ชัน Async ที่ปลอดภัยต่อการเขียนไฟล์พร้อมกัน"""
    async with db_lock:
        await asyncio.to_thread(save_db, site_key, data)

async def async_load_db(site_key):
    """เวอร์ชัน Async สำหรับโหลดข้อมูล"""
    async with db_lock:
        return await asyncio.to_thread(load_db, site_key)

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

# MAPPING_ZONE_SCHEMA_PHASE1_V1

def _build_mapping_site_zone_registry():
    """Build a safe registry from configured site endpoints; no listing inference."""
    import json
    import os
    import unicodedata
    from urllib.parse import urlsplit

    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    with open(config_path, "r", encoding="utf-8") as handle:
        config = json.load(handle)

    domain_map = {
        "bearbit.org": "BEARBIT",
        "bitsuse.com": "BITSUSE",
        "dedbit.com": "DEDBIT",
        "torrentdd.com": "TORRENTDD",
        "unlimitz.biz": "UNLIMITZ",
    }
    result = []
    for site_cfg in config.get("SITE", []):
        if not isinstance(site_cfg, dict):
            continue
        base_url = str(site_cfg.get("base_url") or "").strip()
        host = (urlsplit(base_url).hostname or "").lower()
        site_key = next((key for domain, key in domain_map.items()
                         if host == domain or host.endswith("." + domain)), None)
        if not site_key:
            continue
        site_group = "DEDBIT" if site_key in {"BITSUSE", "DEDBIT"} else site_key
        for zone in (site_cfg.get("target_urls") or []):
            if not isinstance(zone, dict):
                continue
            raw_url = str(zone.get("url") or "").strip()
            if not raw_url:
                continue
            name = unicodedata.normalize("NFKC", str(zone.get("name") or "")).casefold()
            path = urlsplit(raw_url).path
            path_lower = path.casefold()
            if ("ปกติ" in name or "no 18" in name or "no18" in name
                    or "viewno18" in path_lower):
                zone_id = "normal"
            elif ("พิเศษ" in name or "18+" in name or "browse18" in path_lower
                    or "clip18" in path_lower or "viewbrsb" in path_lower):
                zone_id = "special"
            elif site_key == "BITSUSE":
                zone_id = "special"
            elif site_key == "DEDBIT":
                zone_id = "normal"
            elif site_key in {"TORRENTDD", "UNLIMITZ"} and path_lower.endswith("/browse.php"):
                zone_id = "normal"
            else:
                zone_id = "unknown"
            result.append({
                "site_group": site_group,
                "site_key": site_key,
                "zone_id": zone_id,
                "zone_name": str(zone.get("name") or "Unknown"),
                "enabled": bool(zone.get("enable", True)),
                "host": host,
                "url_path": path,
            })
    if not result:
        raise RuntimeError("ไม่สามารถสร้าง Site Zone Registry จาก config.json ได้")
    return result


def _ensure_mapping_zone_schema(data):
    """Additive defaults only. Never infer historical source/listing zones."""
    import copy

    if not isinstance(data, dict) or not isinstance(data.get("torrents"), list):
        raise ValueError("Invalid Mapping structure; refusing to write")
    version = data.get("zone_metadata_version", 0)
    try:
        if int(version or 0) > 1:
            raise ValueError(f"Unsupported zone metadata version: {version}")
    except (TypeError, ValueError):
        raise ValueError(f"Invalid zone_metadata_version: {version!r}")

    for index, record in enumerate(data["torrents"]):
        if not isinstance(record, dict):
            raise ValueError(f"Invalid Mapping record at index {index}")
        ci = record.get("client_info")
        if not isinstance(ci, dict) or not re.fullmatch(
            r"[0-9A-Fa-f]{40}", str(ci.get("hash") or "")
        ):
            raise ValueError(f"Invalid/missing v1 infohash at Mapping index {index}")
        record.setdefault("content_group_id", None)
        record.setdefault("source_zone_id", None)
        record.setdefault("source_zone_verified", False)
        record.setdefault("source_zone_evidence", None)
        if record.get("source_zone_evidence") is not None and not isinstance(record.get("source_zone_evidence"), dict):
            raise ValueError(f"Invalid source_zone_evidence at Mapping index {index}")
        record.setdefault("zone_metadata_version", 1)
        variant = record.get("variant_info")
        if variant is None:
            variant = {}
            record["variant_info"] = variant
        if not isinstance(variant, dict):
            raise ValueError(f"Invalid variant_info at Mapping index {index}")
        variant.setdefault("role", "unclassified")
        variant.setdefault("original_infohash", None)
        variant.setdefault("target_site", None)
        variant.setdefault("target_zone_id", None)
        variant.setdefault("verified", False)
        listings = record.get("site_listings")
        if listings is None:
            listings = []
            record["site_listings"] = listings
        if not isinstance(listings, list) or any(not isinstance(x, dict) for x in listings):
            raise ValueError(f"Invalid site_listings at Mapping index {index}")

    data.setdefault("zone_metadata_version", 1)
    registry = _build_mapping_site_zone_registry()
    current = data.get("site_zone_registry")
    if current is None:
        data["site_zone_registry"] = copy.deepcopy(registry)
    elif not isinstance(current, list):
        raise ValueError("Invalid site_zone_registry; refusing to write")
    else:
        merged = {}
        for item in current:
            if not isinstance(item, dict):
                raise ValueError("Invalid site_zone_registry entry")
            key = (item.get("site_group"), item.get("site_key"), item.get("zone_id"))
            if key in merged:
                raise ValueError(f"Duplicate site_zone_registry key: {key}")
            merged[key] = item
        for item in registry:
            key = (item["site_group"], item["site_key"], item["zone_id"])
            merged.setdefault(key, {}).update(item)
        data["site_zone_registry"] = sorted(
            merged.values(),
            key=lambda x: (str(x.get("site_group")), str(x.get("site_key")), str(x.get("zone_id")))
        )
    return data



# MAPPING_PERSISTENCE_TRANSACTION_V1
import contextvars
import functools
from contextlib import asynccontextmanager

mapping_lock = asyncio.Lock()

_mapping_transaction_owner = contextvars.ContextVar(
    "uap_mapping_transaction_owner",
    default=None,
)


@asynccontextmanager
async def mapping_transaction():
    current_task = asyncio.current_task()

    if current_task is None:
        raise RuntimeError(
            "Mapping transaction requires an active asyncio task"
        )

    # Re-entrant only for the same asyncio Task.
    if _mapping_transaction_owner.get() is current_task:
        yield
        return

    async with mapping_lock:
        token = _mapping_transaction_owner.set(current_task)
        try:
            yield
        finally:
            _mapping_transaction_owner.reset(token)


def _mapping_transactional(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        async with mapping_transaction():
            return await func(*args, **kwargs)

    return wrapper


def _atomic_save_mapping_file(data):
    """Atomic JSON replacement with fsync and original mode/ownership preservation."""
    if globals().get("PHASE_G_ALLOW_MAPPING_WRITES", False) is not True:
        raise RuntimeError(
            "Mapping file write blocked by Phase G safety gate"
        )
    import json
    import os
    import stat
    import tempfile

    mapping_path = os.path.realpath(
        os.path.abspath(os.fspath(MAPPING_FILE))
    )
    mapping_dir = os.path.dirname(mapping_path) or "."
    original_stat = os.stat(mapping_path)
    fd, temp_path = tempfile.mkstemp(prefix="torrent_mapping.", suffix=".tmp", dir=mapping_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=4, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        if original_stat:
            os.chown(temp_path, original_stat.st_uid, original_stat.st_gid)
            os.chmod(temp_path, stat.S_IMODE(original_stat.st_mode))
        else:
            os.chmod(temp_path, 0o644)
        os.replace(temp_path, mapping_path)
        try:
            dir_fd = os.open(mapping_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    except Exception:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise


async def load_mapping():
    """Load Mapping fail-closed and add in-memory schema defaults."""
    def _load():
        mapping_path = os.path.realpath(
            os.path.abspath(os.fspath(MAPPING_FILE))
        )

        if not os.path.isfile(mapping_path):
            raise RuntimeError(
                "Mapping file missing or not a regular file; "
                f"refusing empty fallback: {mapping_path}"
            )

        try:
            with open(mapping_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception as exc:
            raise RuntimeError(
                f"Mapping read/JSON failure; refusing empty fallback: {exc}"
            ) from exc
        return _ensure_mapping_zone_schema(data)

    return await asyncio.to_thread(_load)


async def save_mapping(data):
    """Persist Mapping through the shared transaction and atomic writer."""
    if globals().get("PHASE_G_ALLOW_MAPPING_WRITES", False) is not True:
        print("🔒 [Mapping] Save blocked by Phase G; read-only mode.")
        return False
    if not isinstance(data, dict) or not isinstance(data.get("torrents"), list):
        raise ValueError("Invalid Mapping structure; refusing write")

    def _save():
        _ensure_mapping_zone_schema(data)
        _atomic_save_mapping_file(data)

    async with mapping_transaction():
        await asyncio.to_thread(_save)

    return True


def extract_info_hash(torrent_content: bytes) -> str | None:
    if not torrent_content:
        return None

    # 1. คลายบีบอัดกรณีข้อมูลติด Gzip (\x1f\x8b)
    if torrent_content.startswith(b'\x1f\x8b'):
        try:
            torrent_content = gzip.decompress(torrent_content)
        except Exception as e:
            print(f"DEBUG: คลาย Gzip ไม่สำเร็จ: {e}")
            return None

    # 2. ป้องกันกรณีได้ HTML Response (ติด Referer Check หรือ Session หมดอายุ)
    lowered = torrent_content[:200].lower()
    if b'<html' in lowered or b'<!doctype' in lowered or b'<script' in lowered:
        print("⚠️ [BEARBIT ERROR] ข้อมูลที่ได้เป็น HTML (ติด Referer Check หรือ Session หมดอายุ)")
        return None

    # 3. ตรวจสอบว่าขึ้นต้นด้วย 'd' ตามมาตรฐาน Bencode
    if not torrent_content.startswith(b'd'):
        print(f"DEBUG: ไม่ใช่ Bencode Data (Header: {torrent_content[:15]!r})")
        return None

    try:
        # Decode ด้วย bcoding
        metadata = bcoding.bdecode(torrent_content)

        if not isinstance(metadata, dict):
            return None

        # 4. ค้นหาคีย์ 'info' (bcoding มักใช้ bytes keys)
        info_data = metadata.get(b'info') or metadata.get('info')
        if not info_data:
            for k, v in metadata.items():
                k_str = k.decode('utf-8', errors='ignore') if isinstance(k, bytes) else str(k)
                if k_str == 'info':
                    info_data = v
                    break

        if not info_data:
            keys_found = [k.decode('utf-8', errors='ignore') if isinstance(k, bytes) else str(k) for k in metadata.keys()]
            print(f"⚠️ DEBUG: ไม่พบ info ใน Dictionary! Keys ที่พบ: {keys_found}")
            return None

        # Re-encode เฉพาะส่วน info กลับเป็น bytes แล้วทำ SHA-1
        info_bytes = bcoding.bencode(info_data)
        return hashlib.sha1(info_bytes).hexdigest().lower()

    except Exception as e:
        print(f"DEBUG: bcoding Parsing Error: {e}")
        return None

def parse_size(size_str):
    """แปลง Text สถิติจากหน้าเว็บ (PB, TB, GB, MB) ให้เป็นตัวเลขหน่วย GB"""
    try:
        if not size_str: return 0.0
        size_str = size_str.upper().replace(',', '').strip()
        
        # เพิ่ม PB และ PIB เข้าไปใน Regex
        match = re.search(r"([0-9.]+)\s*(PB|PIB|TB|TIB|GB|GIB|MB|MIB|KB|KIB|B)", size_str)
        if not match: return 0.0
        
        num = float(match.group(1))
        unit = match.group(2)
        
        # กำหนด Factor โดยให้ GB = 1 (1 PB = 1024 TB = 1,048,576 GB)
        factors = {
            "PB": 1024 * 1024, "PIB": 1024 * 1024,
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
    # 1. เช็ค TorrentDD: มองหา <label class="badge badge-outline-success">Free</label>
    torrentdd_badge = row.find("label", class_="badge-outline-success")
    if torrentdd_badge and "free" in torrentdd_badge.get_text().lower():
        return 100

    # 2. เช็ค BearBIT (Badge/Promo Col)
    badge = row.find("span", class_="bb-badge bb-free")
    if badge:
        text = badge.get_text().lower()
        pct_match = re.search(r"(\d+)\s*%", text)
        if pct_match:
            return int(pct_match.group(1))
    
    # 3. กรณีไม่มี Badge ให้เช็คจาก td ที่มี class="bb-promo-col" (ส่วนแสดง % ชัดเจน)
    promo_col = row.find("td", class_="bb-promo-col")
    if promo_col:
        text = promo_col.get_text().strip()
        pct_match = re.search(r"(\d+)\s*%", text)
        if pct_match:
            return int(pct_match.group(1))

    # 4. Fallback: ใช้รูปภาพ (Icon)
    row_html_lower = str(row).lower()
    if any(icon in row_html_lower for icon in ["freeload.png", "free.gif", "free.png"]):
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

def _canonical_mapping_site(value):
    """
    Accept only exact configured SITE names.
    Never infer a tracker from torrent name or seedbox node.
    """
    candidate = str(value or "").strip()

    if not candidate:
        return ""

    cfg = globals().get("CFG", {})
    site_configs = (
        cfg.get("SITE", [])
        if isinstance(cfg, dict)
        else []
    )

    for site_cfg in site_configs:
        if not isinstance(site_cfg, dict):
            continue

        configured_name = str(
            site_cfg.get("name") or ""
        ).strip()

        if (
            configured_name
            and configured_name.casefold() == candidate.casefold()
        ):
            return configured_name

    return ""



# MAPPING_DUPLICATE_TRI_STATE_PHASE_C_V1
def _classify_hash_presence_state(
    seen_hash_duplicate,
    matched_node_count,
    inventory_check_failed,
):
    """
    History is a per-site processing ledger, not Node presence evidence.
    A positive live match is decisive even if another Node is unavailable.
    """
    if matched_node_count > 0:
        return "live_duplicate"

    if inventory_check_failed:
        return "inventory_incomplete"

    if seen_hash_duplicate:
        return "history_duplicate"

    return "eligible_for_add"


# MAPPING_NODE_LOCATIONS_PHASE_B_V1
def _merge_mapping_node_location(record, node_data):
    """
    Add/update one explicitly observed Node location.

    Trust levels:
      live_qbittorrent_inventory = live inventory confirmed
      successful_add_response    = add response accepted, not inventory confirmed

    Historical node_info is intentionally left unchanged by this helper.
    """
    if not isinstance(record, dict):
        return False

    if not isinstance(node_data, dict):
        return False

    host = str(node_data.get("seedbox_host") or "").strip()
    if not host:
        return False

    method = str(
        node_data.get("verification_method") or ""
    ).strip()

    trust_rank = {
        "successful_add_response": 1,
        "live_qbittorrent_inventory": 2,
    }

    incoming_rank = trust_rank.get(method, 0)
    if incoming_rank == 0:
        return False

    save_path = str(node_data.get("save_path") or "").strip()
    if not save_path:
        return False

    locations = record.get("node_locations")

    if locations is None:
        locations = []
    elif not isinstance(locations, list):
        raise ValueError(
            "Invalid node_locations structure; refusing overwrite"
        )

    if any(not isinstance(item, dict) for item in locations):
        raise ValueError(
            "Invalid node_locations entry; refusing overwrite"
        )

    matches = [
        item for item in locations
        if str(item.get("seedbox_host") or "").strip().casefold()
        == host.casefold()
    ]

    if len(matches) > 1:
        raise ValueError(
            f"Duplicate node_locations entries for Node {host!r}"
        )

    inventory_verified = (
        method == "live_qbittorrent_inventory"
    )

    if not matches:
        location = {
            "seedbox_host": host,
            "save_path": save_path,
            "inventory_verified": inventory_verified,
            "verification_method": method,
        }
        locations.append(location)

    else:
        location = matches[0]

        previous_method = str(
            location.get("verification_method") or ""
        ).strip()

        previous_rank = trust_rank.get(
            previous_method,
            2 if location.get("inventory_verified") is True else 0
        )

        # Weaker evidence must never downgrade or replace a
        # stronger verified Node location.
        if incoming_rank < previous_rank:
            return False

        location["verification_method"] = method
        location["inventory_verified"] = inventory_verified
        location["seedbox_host"] = host
        location["save_path"] = save_path

    record["node_locations"] = locations
    return True

@_mapping_transactional
async def add_or_update_torrent(client_data, web_data=None, node_data=None):
    """
    Mapping provenance rules:

    source_site:
        Only assigned from explicitly verified successful web additions.

    known_sites:
        Sites where a manifest-verified candidate has been observed.
        This never establishes source-site provenance.

    Unknown source metadata is never promoted from observed_site.
    """
    if globals().get("PHASE_G_ALLOW_MAPPING_WRITES", False) is not True:
        print("🔒 [Mapper] Update blocked by Phase G; read-only mode.")
        return False

    data = await load_mapping()
    torrents = data.get("torrents", [])

    if not isinstance(client_data, dict):
        print("⚠️ [Mapper] Invalid client_data")
        return False

    target_hash = str(
        client_data.get("hash") or ""
    ).strip().upper()

    if not target_hash:
        print(
            "⚠️ [Mapper]: ไม่พบค่า Hash "
            "ไม่สามารถดำเนินการต่อได้"
        )
        return False

    web_data = (
        web_data if isinstance(web_data, dict) else {}
    )
    node_data = (
        node_data if isinstance(node_data, dict) else {}
    )

    client_type = client_data.get(
        "client_type", "unknown"
    )

    # An explicit verification flag is mandatory.
    incoming_verified = (
        web_data.get("source_site_verified") is True
    )

    incoming_source = (
        _canonical_mapping_site(
            web_data.get("source_site")
            or web_data.get("site_name")
        )
        if incoming_verified
        else ""
    )

    if not incoming_source:
        incoming_verified = False

    observed_site = _canonical_mapping_site(
        web_data.get("observed_site")
    )

    existing = next(
        (
            record
            for record in torrents
            if isinstance(record, dict)
            and isinstance(record.get("client_info"), dict)
            and str(
                record["client_info"].get("hash") or ""
            ).strip().upper() == target_hash
        ),
        None
    )

    if existing:
        # ----------------------------------------------------
        # 1. Client metadata
        # ----------------------------------------------------

        if not isinstance(existing.get("client_info"), dict):
            existing["client_info"] = {}

        existing["client_info"].update({
            "state": client_data.get(
                "state",
                existing["client_info"].get("state")
            ),
            "ratio": client_data.get(
                "ratio",
                existing["client_info"].get("ratio")
            ),
            "name": client_data.get(
                "name",
                existing["client_info"].get("name")
            )
        })

        # ----------------------------------------------------
        # 2. Node metadata
        # ----------------------------------------------------

        if not isinstance(existing.get("node_info"), dict):
            existing["node_info"] = {}

        current_host = str(
            node_data.get("seedbox_host") or ""
        ).strip()

        if current_host:
            existing["node_info"]["seedbox_host"] = current_host

        if node_data.get("save_path"):
            existing["node_info"]["save_path"] = (
                node_data["save_path"]
            )
        elif client_data.get("save_path"):
            existing["node_info"]["save_path"] = (
                client_data["save_path"]
            )

        # ----------------------------------------------------
        # 3. Merge known_sites without changing source_site
        # ----------------------------------------------------

        raw_known = existing.get("known_sites", [])

        if not isinstance(raw_known, (list, tuple, set)):
            raw_known = []

        known_sites = []

        for value in raw_known:
            canonical = _canonical_mapping_site(value)
            if canonical and canonical not in known_sites:
                known_sites.append(canonical)

        for value in (observed_site, incoming_source):
            if value and value not in known_sites:
                known_sites.append(value)

        existing["known_sites"] = known_sites

        # ----------------------------------------------------
        # 4. Preserve previously verified source metadata
        # ----------------------------------------------------

        current_source_verified = (
            existing.get("source_site_verified") is True
        )

        current_source = (
            _canonical_mapping_site(
                existing.get("source_site")
            )
            if current_source_verified
            else ""
        )

        if current_source:
            existing["source_site"] = current_source
            existing["source_site_verified"] = True
            existing["site_name"] = current_source

        elif incoming_verified:
            # First confirmed source attribution.
            existing["source_site"] = incoming_source
            existing["source_site_verified"] = True
            existing["site_name"] = incoming_source

            if (
                web_data.get("torrent_id")
                and not existing.get("torrent_id")
            ):
                existing["torrent_id"] = (
                    web_data["torrent_id"]
                )

            if (
                web_data.get("details_url")
                and not existing.get("details_url")
            ):
                existing["details_url"] = (
                    web_data["details_url"]
                )

            if (
                web_data.get("release_name")
                and existing.get("release_name")
                in ("", None, "Unknown")
            ):
                existing["release_name"] = (
                    web_data["release_name"]
                )

        else:
            # Preserve legacy site_name for reference, but do not
            # treat it as verified provenance.
            existing["source_site"] = "Unknown"
            existing["source_site_verified"] = False

        print(
            f"🔄 [Mapper]: อัปเดต ({client_type}) "
            f"{existing.get('release_name', target_hash)} "
            f"| source={existing.get('source_site', 'Unknown')} "
            f"| verified={existing.get('source_site_verified', False)} "
            f"| known_sites={existing.get('known_sites', [])}"
        )

    else:
        # ----------------------------------------------------
        # 5. New record
        # ----------------------------------------------------

        known_sites = []

        for value in (observed_site, incoming_source):
            if value and value not in known_sites:
                known_sites.append(value)

        source_name = (
            incoming_source if incoming_verified else "Unknown"
        )

        new_record = {
            "torrent_id": (
                web_data.get("torrent_id")
                if incoming_verified
                else None
            ),
            "site_name": source_name,
            "source_site": source_name,
            "source_site_verified": bool(incoming_verified),
            "known_sites": known_sites,
            "release_name": (
                web_data.get("release_name")
                if incoming_verified
                else client_data.get("name", "Unknown")
            ),
            "details_url": (
                web_data.get("details_url", "")
                if incoming_verified
                else ""
            ),
            "web_size": (
                web_data.get(
                    "web_size",
                    client_data.get("size_bytes", 0)
                )
                if incoming_verified
                else client_data.get("size_bytes", 0)
            ),
            "client_info": {
                "client_type": client_type,
                "hash": target_hash,
                "name": client_data.get("name"),
                "size_bytes": client_data.get("size_bytes", 0),
                "state": client_data.get("state", "unknown"),
                "ratio": client_data.get("ratio", 0.0)
            },
            "node_info": {
                "seedbox_host": node_data.get(
                    "seedbox_host", "Localhost"
                ),
                "save_path": node_data.get("save_path", "")
            },
            "cross_seed_links": [],
            "status": "active"
        }

        torrents.append(new_record)

        print(
            f"✨ [Mapper]: เพิ่มเรกคอร์ด ({client_type}) "
            f"{new_record['release_name']} "
            f"| source={source_name} "
            f"| verified={incoming_verified} "
            f"| known_sites={known_sites}"
        )

    target_record = (
        existing if existing is not None else new_record
    )

    effective_source = (
        _canonical_mapping_site(target_record.get("source_site"))
        if target_record.get("source_site_verified") is True
        else ""
    )
    incoming_zone_evidence = _validate_mapping_source_zone_evidence(
        web_data.get("source_zone_evidence"),
        effective_source,
        web_data.get("torrent_id"),
        web_data.get("details_url"),
    ) if effective_source else None

    if incoming_zone_evidence:
        existing_zone_id = str(
            target_record.get("source_zone_id") or ""
        ).strip().casefold()

        # Never trust the historical Boolean flag alone.
        # Existing evidence must independently validate against
        # the current source site, torrent ID and details URL.
        existing_zone_evidence = (
            _validate_mapping_source_zone_evidence(
                target_record.get("source_zone_evidence"),
                effective_source,
                target_record.get("torrent_id"),
                target_record.get("details_url"),
            )
            if effective_source
            else None
        )

        existing_source_zone_is_valid = (
            target_record.get("source_zone_verified") is True
            and existing_zone_evidence is not None
            and existing_zone_id == existing_zone_evidence["zone_id"]
        )

        if existing_source_zone_is_valid:
            if existing_zone_id != incoming_zone_evidence["zone_id"]:
                # Two independently valid proofs conflict.
                # Preserve the existing verified provenance.
                print(
                    "⚠️ [Mapper] Conflicting verified Source Zone; "
                    f"preserving existing zone={existing_zone_id!r}"
                )
            # Same verified zone: preserve the existing evidence and
            # its matching torrent ID/details URL.
        else:
            # Existing zone metadata is absent, stale, or unverifiable.
            # Adopt the new independently validated evidence as one
            # consistent provenance bundle.
            target_record["source_zone_id"] = (
                incoming_zone_evidence["zone_id"]
            )
            target_record["source_zone_verified"] = True
            target_record["source_zone_evidence"] = (
                incoming_zone_evidence
            )
            target_record["torrent_id"] = (
                incoming_zone_evidence["torrent_id"]
            )
            target_record["details_url"] = (
                incoming_zone_evidence["details_url"]
            )

            print(
                "🧭 [Mapper] Source Zone verified/reconciled: "
                f"{effective_source}/"
                f"{incoming_zone_evidence['zone_id']}"
            )

    _merge_mapping_node_location(target_record, node_data)

    data["torrents"] = torrents
    await save_mapping(data)
    return True


async def handle_new_torrent_grabbed(web_item, client_response, host_name):
    """
    Called after a successful torrent addition.

    Only this confirmed-add path can establish source provenance.
    """
    web_item = (
        dict(web_item)
        if isinstance(web_item, dict)
        else {}
    )

    source_name = _canonical_mapping_site(
        web_item.get("site_name")
    )

    web_item["source_site"] = source_name
    web_item["source_site_verified"] = bool(source_name)

    # Source Zone is proven only when the current enabled Browse endpoint,
    # the exact details URL/torrent ID, and the successful add all agree.
    web_item.pop("source_zone_evidence", None)
    zone_meta = _validate_mapping_target_zone_meta(
        web_item.get("source_zone_meta")
    )
    if zone_meta and source_name and zone_meta.get("site_key") == source_name:
        from datetime import datetime, timezone
        source_zone_evidence = {
            **zone_meta,
            "torrent_id": str(web_item.get("torrent_id") or "").strip(),
            "details_url": str(web_item.get("details_url") or "").strip(),
            "verified": True,
            "verification_status": "successful_add_from_verified_zone",
            "verification_method": "successful_add_from_verified_zone",
            "verified_by": [
                "configured_enabled_zone_registry",
                "matching_search_result_details_url",
                "successful_add_response",
            ],
            "verified_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        validated_evidence = _validate_mapping_source_zone_evidence(
            source_zone_evidence,
            source_name,
            web_item.get("torrent_id"),
            web_item.get("details_url"),
        )
        if validated_evidence:
            web_item["source_zone_evidence"] = validated_evidence
        else:
            print(
                "⚠️ [Mapper] Source Zone evidence rejected: "
                "details URL / torrent ID / registry mismatch"
            )
    elif web_item.get("source_zone_meta") is not None:
        print("⚠️ [Mapper] Source Zone metadata rejected: site/registry mismatch")

    client_data = {
        "client_type": client_response.get("type", "qbittorrent"),
        "hash": client_response["hash"],
        "name": client_response["name"],
        "size_bytes": client_response.get("size_bytes", 0),
        "state": client_response.get("state", "downloading"),
        "ratio": 0.0
    }

    node_data = {
        "seedbox_host": host_name,
        "save_path": client_response.get("save_path", ""),
        "verification_method": "successful_add_response"
    }

    await add_or_update_torrent(
        client_data=client_data,
        web_data=web_item,
        node_data=node_data
    )



# MAPPING_ZONE_PHASE1C_V1

def _build_mapping_target_zone_meta(site_cfg, zone):
    """Resolve one configured target endpoint to its persisted Zone Registry row."""
    from urllib.parse import urlsplit

    if not isinstance(site_cfg, dict) or not isinstance(zone, dict):
        return None

    base_url = str(site_cfg.get("base_url") or "").strip()
    raw_url = str(zone.get("url") or "").strip()
    if not base_url or not raw_url:
        return None

    host = (urlsplit(base_url).hostname or "").casefold()
    path = urlsplit(raw_url).path or raw_url.split("?", 1)[0]
    if not host or not path:
        return None

    try:
        registry = _build_mapping_site_zone_registry()
    except Exception as exc:
        print(
            "⚠️ [Zone Metadata] Registry resolution failed: "
            f"{type(exc).__name__}"
        )
        return None

    matches = [
        row for row in registry
        if isinstance(row, dict)
        and str(row.get("host") or "").casefold() == host
        and str(row.get("url_path") or "") == path
    ]
    if len(matches) != 1:
        print(
            f"⚠️ [Zone Metadata] Endpoint is ambiguous/unregistered: "
            f"host={host!r} path={path!r} matches={len(matches)}"
        )
        return None

    row = matches[0]
    return {
        "site_group": row.get("site_group"),
        "site_key": row.get("site_key"),
        "zone_id": row.get("zone_id"),
        "zone_name": row.get("zone_name"),
        "host": row.get("host"),
        "url_path": row.get("url_path"),
        "enabled": row.get("enabled") is True,
    }


def _validate_mapping_target_zone_meta(meta):
    """Fail-closed validation against config-derived registry; only enabled known zones."""
    if not isinstance(meta, dict):
        return None

    site_group = str(meta.get("site_group") or "").strip().upper()
    site_key = str(meta.get("site_key") or "").strip().upper()
    zone_id = str(meta.get("zone_id") or "").strip().casefold()
    host = str(meta.get("host") or "").strip().casefold()
    url_path = str(meta.get("url_path") or "").strip()

    if zone_id not in {"normal", "special"} or not host or not url_path:
        return None
    expected_group = "DEDBIT" if site_key in {"BITSUSE", "DEDBIT"} else site_key
    if not site_key or site_group != expected_group:
        return None
    if meta.get("enabled") is not True:
        return None

    try:
        registry = _build_mapping_site_zone_registry()
    except Exception:
        return None

    matches = [
        row for row in registry
        if isinstance(row, dict)
        and str(row.get("site_group") or "").upper() == site_group
        and str(row.get("site_key") or "").upper() == site_key
        and str(row.get("zone_id") or "").casefold() == zone_id
        and str(row.get("host") or "").casefold() == host
        and str(row.get("url_path") or "") == url_path
        and row.get("enabled") is True
    ]
    if len(matches) != 1:
        return None

    row = matches[0]
    return {
        "site_group": site_group,
        "site_key": site_key,
        "zone_id": zone_id,
        "zone_name": str(row.get("zone_name") or "Unknown"),
        "host": host,
        "url_path": url_path,
        "enabled": True,
    }


def _validate_mapping_source_zone_evidence(evidence, source_site, torrent_id, details_url):
    """Validate source-zone proof against the enabled registry and exact details URL."""
    from urllib.parse import parse_qs, urlsplit

    if not isinstance(evidence, dict) or evidence.get("verified") is not True:
        return None
    if evidence.get("verification_method") != "successful_add_from_verified_zone":
        return None

    meta = _validate_mapping_target_zone_meta(evidence)
    canonical_source = _canonical_mapping_site(source_site)
    if not meta or not canonical_source or meta["site_key"] != canonical_source:
        return None

    expected_id = str(torrent_id or "").strip()
    evidence_id = str(evidence.get("torrent_id") or "").strip()
    expected_url = str(details_url or "").strip()
    evidence_url = str(evidence.get("details_url") or "").strip()
    if (
        not re.fullmatch(r"[0-9]{1,20}", expected_id)
        or evidence_id != expected_id
        or not expected_url
        or evidence_url != expected_url
    ):
        return None

    parsed = urlsplit(expected_url)
    host = (parsed.hostname or "").casefold().rstrip(".")
    if parsed.scheme.casefold() not in {"http", "https"} or host != meta["host"].casefold().rstrip("."):
        return None
    if not re.search(r"(?:^|/)(?:bs_)?details(?:new)?\.php$", parsed.path, re.I):
        return None

    query = parse_qs(parsed.query)
    ids = {
        str(value).strip()
        for field in ("id", "tid", "torrentid")
        for value in query.get(field, [])
    }
    if expected_id not in ids:
        return None

    verified_by = evidence.get("verified_by")
    if not isinstance(verified_by, list) or not {
        "configured_enabled_zone_registry",
        "matching_search_result_details_url",
        "successful_add_response",
    }.issubset(set(verified_by)):
        return None
    verified_at = str(evidence.get("verified_at") or "").strip()
    if not verified_at:
        return None

    return {
        **meta,
        "torrent_id": expected_id,
        "details_url": expected_url,
        "verified": True,
        "verification_status": "successful_add_from_verified_zone",
        "verification_method": "successful_add_from_verified_zone",
        "verified_by": [
            "configured_enabled_zone_registry",
            "matching_search_result_details_url",
            "successful_add_response",
        ],
        "verified_at": verified_at,
    }


@_mapping_transactional
async def record_cross_seed_site_listings(updates):
    """Persist only exact-manifest, complete-inventory, tracker-verified listings."""
    if globals().get("PHASE_G_ALLOW_MAPPING_WRITES", False) is not True:
        print("🔒 [Site Listings] Persistence blocked by Phase G.")
        return False
    from datetime import datetime, timezone
    from urllib.parse import parse_qs, urlsplit

    if not isinstance(updates, dict) or not updates:
        print("ℹ️ [Cross-Seed Site Listings] No valid updates")
        return False

    normalized = {}
    for source_hash, raw_listings in updates.items():
        source_hash = str(source_hash or "").strip().upper()
        if not re.fullmatch(r"[0-9A-F]{40}", source_hash):
            continue
        if not isinstance(raw_listings, (list, tuple)):
            continue

        for raw in raw_listings:
            if not isinstance(raw, dict):
                continue
            zone_meta = _validate_mapping_target_zone_meta(raw.get("target_zone_meta"))
            candidate_hash = str(raw.get("infohash") or "").strip().upper()
            torrent_id = str(raw.get("torrent_id") or "").strip()
            details_url = str(raw.get("details_url") or "").strip()
            parsed_details = urlsplit(details_url)
            details_host = (parsed_details.hostname or "").casefold()
            detail_query = parse_qs(parsed_details.query)
            detail_ids = {
                str(value).strip()
                for field in ("id", "tid", "torrentid")
                for value in detail_query.get(field, [])
            }

            if not zone_meta:
                continue
            if not re.fullmatch(r"[0-9A-F]{40}", candidate_hash):
                continue
            if not re.fullmatch(r"[0-9]{1,20}", torrent_id):
                continue
            if parsed_details.scheme.casefold() not in {"http", "https"}:
                continue
            if torrent_id not in detail_ids:
                continue
            allowed_domains = {
                "BEARBIT": "bearbit.org",
                "BITSUSE": "bitsuse.com",
                "DEDBIT": "dedbit.com",
                "TORRENTDD": "torrentdd.com",
                "UNLIMITZ": "unlimitz.biz",
            }
            expected_domain = allowed_domains.get(zone_meta["site_key"])
            if not expected_domain or not (
                details_host == expected_domain
                or details_host.endswith("." + expected_domain)
            ):
                continue

            listing = {
                **zone_meta,
                "torrent_id": torrent_id,
                "infohash": candidate_hash,
                "details_url": details_url,
                "verified": True,
                "verification_status": "live_inventory_exact_manifest_tracker_verified",
                "verified_by": [
                    "exact_source_candidate_file_manifest",
                    "live_qbittorrent_complete_inventory",
                    "candidate_tracker_api_all_present",
                ],
                "verified_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            key = (
                listing["site_group"], listing["site_key"],
                listing["zone_id"], listing["infohash"],
            )
            normalized.setdefault(source_hash, {})[key] = listing

    if not normalized:
        print("ℹ️ [Cross-Seed Site Listings] No listing passed zone/hash/URL validation")
        return False

    data = await load_mapping()
    if not isinstance(data, dict) or not isinstance(data.get("torrents"), list):
        print("🛑 [Cross-Seed Site Listings] Invalid Mapping; no write")
        return False

    matched_sources = set()
    changed = 0
    for record in data["torrents"]:
        if not isinstance(record, dict):
            continue
        client_info = record.get("client_info")
        if not isinstance(client_info, dict):
            continue
        source_hash = str(client_info.get("hash") or "").strip().upper()
        requested = normalized.get(source_hash)
        if not requested:
            continue

        matched_sources.add(source_hash)
        listings = record.get("site_listings")
        if not isinstance(listings, list) or any(not isinstance(x, dict) for x in listings):
            raise ValueError(
                f"Invalid site_listings for source hash {source_hash[:12]}; refusing write"
            )

        index_by_key = {}
        for index, old in enumerate(listings):
            old_key = (
                str(old.get("site_group") or "").upper(),
                str(old.get("site_key") or "").upper(),
                str(old.get("zone_id") or "").casefold(),
                str(old.get("infohash") or "").upper(),
            )
            if old_key in index_by_key:
                raise ValueError(
                    f"Duplicate site listing key for {source_hash[:12]}: {old_key}"
                )
            index_by_key[old_key] = index

        for key, listing in requested.items():
            if key in index_by_key:
                old = listings[index_by_key[key]]
                # Never downgrade an already verified listing.
                if old.get("verified") is True:
                    continue
                old.update(listing)
            else:
                listings.append(listing)
            changed += 1

    missing_sources = set(normalized) - matched_sources
    if missing_sources:
        print(
            "⚠️ [Cross-Seed Site Listings] Source hashes not found in Mapping: "
            f"{len(missing_sources)}; those entries skipped"
        )

    if not changed:
        print("ℹ️ [Cross-Seed Site Listings] No new verified listing to persist")
        return not missing_sources

    await save_mapping(data)
    verified_data = await load_mapping()
    verified_index = {}
    for record in verified_data.get("torrents", []):
        ci = record.get("client_info") if isinstance(record, dict) else None
        if isinstance(ci, dict):
            h = str(ci.get("hash") or "").strip().upper()
            verified_index[h] = record

    failed = []
    for source_hash, requested in normalized.items():
        record = verified_index.get(source_hash)
        if not record:
            failed.append(source_hash)
            continue
        actual = record.get("site_listings") or []
        actual_keys = {
            (
                str(x.get("site_group") or "").upper(),
                str(x.get("site_key") or "").upper(),
                str(x.get("zone_id") or "").casefold(),
                str(x.get("infohash") or "").upper(),
            )
            for x in actual if isinstance(x, dict) and x.get("verified") is True
        }
        if not set(requested).issubset(actual_keys):
            failed.append(source_hash)

    if failed:
        print(
            f"❌ [Cross-Seed Site Listings] Persistence verification failed: "
            f"{len(failed)} source records"
        )
        return False

    print(
        f"✅ [Cross-Seed Site Listings] Verified listings persisted: "
        f"{changed}; source records={len(normalized) - len(missing_sources)}"
    )
    return True


@_mapping_transactional
async def record_cross_seed_known_sites(updates):
    """
    Persist verified candidate-site observations against SOURCE hashes.

    Only known_sites is modified.
    Source provenance and torrent/tracker state are never changed.
    """
    if globals().get("PHASE_G_ALLOW_MAPPING_WRITES", False) is not True:
        print("🔒 [Known Sites] Persistence blocked by Phase G.")
        return False

    normalized = {}

    for source_hash, raw_sites in (updates or {}).items():
        source_hash = str(source_hash or "").strip().upper()

        if not re.fullmatch(r"[0-9A-F]{40}", source_hash):
            continue

        if isinstance(raw_sites, str):
            raw_sites = [raw_sites]

        if not isinstance(raw_sites, (list, tuple, set)):
            continue

        for raw_site in raw_sites:
            canonical = _canonical_mapping_site(raw_site)

            if canonical:
                normalized.setdefault(source_hash, set()).add(canonical)

    if not normalized:
        print("ℹ️ [Cross-Seed Known Sites] No valid updates")
        return False

    if not os.path.isfile(MAPPING_FILE):
        print("🛑 [Cross-Seed Known Sites] Mapping file missing; aborted")
        return False

    data = await load_mapping()

    if not isinstance(data, dict) or not isinstance(data.get("torrents"), list):
        print("🛑 [Cross-Seed Known Sites] Invalid Mapping structure; aborted")
        return False

    matched_records = 0
    changed_records = 0

    for record in data["torrents"]:
        if not isinstance(record, dict):
            continue

        client_info = record.get("client_info")

        if not isinstance(client_info, dict):
            continue

        record_hash = str(
            client_info.get("hash") or ""
        ).strip().upper()

        requested_sites = normalized.get(record_hash)

        if not requested_sites:
            continue

        matched_records += 1

        raw_known = record.get("known_sites")

        if isinstance(raw_known, list):
            known_sites = list(raw_known)
        elif isinstance(raw_known, (tuple, set)):
            known_sites = list(raw_known)
        else:
            known_sites = []

        known_keys = {
            canonical.casefold()
            for value in known_sites
            if (canonical := _canonical_mapping_site(value))
        }

        changed = False

        for site in sorted(requested_sites):
            if site.casefold() not in known_keys:
                known_sites.append(site)
                known_keys.add(site.casefold())
                changed = True

        if changed:
            record["known_sites"] = known_sites
            changed_records += 1

    if not matched_records:
        print(
            "⚠️ [Cross-Seed Known Sites] No source hash matched Mapping; "
            "no write performed"
        )
        return False

    if changed_records:
        try:
            await save_mapping(data)
        except Exception as exc:
            print(
                "❌ [Cross-Seed Known Sites] "
                "Shared Mapping write failed: "
                f"{type(exc).__name__}: {exc}"
            )
            return False

    # Verify persistence by reading the actual file again.
    verified_data = await load_mapping()

    if not isinstance(verified_data, dict):
        print("❌ [Cross-Seed Known Sites] Reload failed")
        return False

    verified_records = {}

    for record in verified_data.get("torrents", []):
        if not isinstance(record, dict):
            continue

        client_info = record.get("client_info")

        if not isinstance(client_info, dict):
            continue

        record_hash = str(
            client_info.get("hash") or ""
        ).strip().upper()

        if record_hash in normalized:
            verified_records.setdefault(record_hash, []).append(record)

    failed = []
    verified_count = 0

    for source_hash, sites in normalized.items():
        records = verified_records.get(source_hash, [])

        if not records:
            failed.append(source_hash)
            continue

        for site in sites:
            site_key = site.casefold()

            all_records_verified = all(
                site_key in {
                    canonical.casefold()
                    for value in (record.get("known_sites") or [])
                    if (canonical := _canonical_mapping_site(value))
                }
                for record in records
            )

            if all_records_verified:
                verified_count += 1
            else:
                failed.append(f"{source_hash}:{site}")

    if failed:
        print(
            f"⚠️ [Cross-Seed Known Sites] Verification incomplete: {failed!r}"
        )
        return False

    print(
        f"✅ [Cross-Seed Known Sites] "
        f"verified associations={verified_count} | "
        f"source records={len(verified_records)} | "
        f"updated records={changed_records}"
    )
    print(
        "🔒 [Cross-Seed Known Sites] "
        "Source provenance and qBittorrent/tracker state unchanged"
    )

    return True


def process_cross_seed_linking(mapping_data):
    """
    Conservative linker:
    never infer identical content from save_path + size_bytes alone.
    Keep explicitly verified links; archive legacy unverified associations.
    """
    torrents = mapping_data.get("torrents", [])
    archived_count = 0
    kept_verified_count = 0

    def _archive_key(item):
        return json.dumps(
            item, sort_keys=True, ensure_ascii=False, default=str
        )

    for record in torrents:
        if not isinstance(record, dict):
            continue

        current = record.get("cross_seed_links")
        if not isinstance(current, list):
            current = []

        verified = []
        legacy = []

        for link in current:
            if (
                isinstance(link, dict)
                and link.get("verified") is True
                and str(link.get("verification_method") or "").strip()
            ):
                verified.append(link)
                kept_verified_count += 1
            else:
                legacy.append(link)

        old_archive = record.get("cross_seed_links_legacy_unverified")
        if not isinstance(old_archive, list):
            old_archive = []

        archive = list(old_archive)
        known = {_archive_key(item) for item in archive}

        for link in legacy:
            key = _archive_key(link)
            if key not in known:
                archive.append(link)
                known.add(key)
                archived_count += 1

        record["cross_seed_links"] = verified

        if archive:
            record["cross_seed_links_legacy_unverified"] = archive

    print(
        "[Cross-Seed] Conservative linking: "
        f"verified links retained={kept_verified_count}, "
        f"legacy unverified links archived={archived_count}; "
        "no links inferred from path/size."
    )
    return mapping_data



# ========================= BROWSER ENGINE =========================

def get_browser_path_or_fail(override_path=None):
    path = get_universal_browser_path(override_path)
    if not path:
        raise FileNotFoundError(
            "ไม่พบ Browser (Chromium/Chrome/Edge/Brave) ในระบบนี้ "
            "กรุณาติดตั้ง Browser หรือระบุ path ผ่านตัวแปร BROWSER_PATH"
        )
    return path

def get_universal_browser_path(override_path=None):
    # 1. ลำดับความสำคัญสูงสุด: Override หรือ Environment Variable
    if override_path and os.path.exists(override_path):
        return override_path
    
    env_path = os.environ.get("BROWSER_PATH")
    if env_path and os.path.exists(env_path):
        return env_path

    current_os = platform.system().lower()
    
    # 2. กรณี Windows (เรียงจาก Google Chrome เป็นหลัก ตามด้วย Edge และ Brave)
    if current_os == "windows":
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        local_app_data = os.environ.get("LOCALAPPDATA", os.path.expanduser(r"~\AppData\Local"))
        
        search_paths = [
            os.path.join(local_app_data, "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(program_files, "Google", "Application", "chrome.exe"),
            os.path.join(program_files_x86, "Google", "Application", "chrome.exe"),
            os.path.join(program_files, "Microsoft", "Edge", "Application", "msedge.exe"),
            os.path.join(program_files_x86, "Microsoft", "Edge", "Application", "msedge.exe"),
            os.path.join(local_app_data, "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
            os.path.join(program_files, "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
        ]
        
        for path in search_paths:
            if os.path.exists(path):
                return path

    # 3. กรณี Linux/Unix (เรียงลำดับความเสถียรและเบาสุด: Google Chrome ตัวเต็ม -> Stable -> Chromium)
    explicit_linux_paths = [
        "/opt/google/chrome/chrome",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser"
    ]
    for path in explicit_linux_paths:
        if os.path.exists(path) and os.path.isfile(path) and os.access(path, os.X_OK):
            return path

    # ค้นหาผ่านระบบ PATH โดยตัด Snap ออกเพื่อความเสถียรและประหยัดแรม
    executables = ["chrome", "google-chrome-stable", "google-chrome", "chromium", "chromium-browser", "brave-browser"]
    
    for exe in executables:
        path = shutil.which(exe)
        if path:
            real_path = os.path.realpath(path)
            if "snap" in real_path:
                continue 
            if os.access(real_path, os.X_OK):
                return real_path
    
    # สำรองกรณีสุดท้าย (หลีกเลี่ยง Snap ถ้าไม่จำเป็น)
    extra_paths = [
        "/usr/lib/chromium-browser/chromium-browser",
        "/snap/bin/chromium"
    ]
    for path in extra_paths:
        if os.path.exists(path) and os.path.isfile(path) and os.access(path, os.X_OK):
            if "snap" in path and not any(name in path.lower() for name in ["chromium", "chrome", "brave"]):
                continue
            return path

    return None

_global_display = None
_active_browser_instance = None #ตัวแปรเพื่อติดตาม instance
_current_profile_path = None #ตัวแปรเก็บ pathpath
os.environ["CHROME_DEVEL_SANDBOX"] = ""

def load_xvfb_config():
    """ฟังก์ชันช่วยอ่านค่า Xvfb_enable จาก config.json (รองรับทั้งแบบอยู่ใน SETTING และระดับนอกสุด)"""
    try:
        config_data = load_full_config()
        
        # 1. ลองเช็คจากก้อน "SETTING" ดูก่อน (ตามโครงสร้างจริงของคุณ)
        if isinstance(config_data, dict):
            setting_section = config_data.get("SETTING", {})
            if isinstance(setting_section, dict) and "Xvfb_enable" in setting_section:
                return bool(setting_section.get("Xvfb_enable", False))
            
            # 2. เผื่อกรณีไฟล์ config วางไว้นอกสุดระดับ Root
            if "Xvfb_enable" in config_data:
                return bool(config_data.get("Xvfb_enable", False))
                
        return False
    except SystemExit:
        print("⚠️ [Config] ไม่พบหรือโหลด config ไม่สำเร็จ ใช้ค่าเริ่มต้น Xvfb_enable: False")
        return False
    except Exception as e:
        print(f"⚠️ [Config] เกิดข้อผิดพลาดในการอ่าน Xvfb_enable ({e}), ใช้ค่าเริ่มต้น: False")
        return False

async def launch_any_browser(sitename="default", custom_args=None):
    global _global_display, _active_browser_instance, _current_profile_path
    
    # 1. ปิดเฉพาะ Browser ของ profile ที่ติดตามได้; หากไม่สำเร็จให้หยุดแบบ fail-closed
    if "kill_specific_browser" in globals():
        if kill_specific_browser() is False:
            raise RuntimeError("ยกเลิก Browser launch: ปิด Browser เก่าอย่างปลอดภัยไม่สำเร็จ")
    if "kill_xvfb" in globals():
        kill_xvfb()
    if "cleanup_profile" in globals():
        if await cleanup_profile() is False:
            raise RuntimeError("ยกเลิก Browser launch: ยังลบ profile เก่าอย่างปลอดภัยไม่ได้")

    # 2. อ่านค่าคอนฟิก Xvfb_enable จาก config.json ผ่านฟังก์ชันที่คุณมี
    xvfb_enabled_in_config = load_xvfb_config() if "load_xvfb_config" in globals() else False

    # 3. Xvfb & Headless Setup ตามเงื่อนไข
    headless_mode = True  # ค่าเริ่มต้นเป็น True

    if xvfb_enabled_in_config:
        xvfb_exists = shutil.which("Xvfb") is not None
        if xvfb_exists:
            print("🖥️ [System] เปิดใช้งาน Xvfb ตาม config (Xvfb_enable: true)")
            from pyvirtualdisplay import Display
            _global_display = Display(visible=0, size=(1920, 1080))
            _global_display.start()
            await asyncio.sleep(2)
            headless_mode = False  # มี Xvfb และเปิดใช้งาน -> ตั้งค่า headless=False
        else:
            print("⚠️ [System] config สั่งเปิด Xvfb แต่ไม่พบแพ็กเกจ Xvfb ในระบบ -> Fallback เป็น headless=True")
            headless_mode = True
    else:
        print("🖥️ [System] ปิดการใช้งาน Xvfb ตาม config (Xvfb_enable: false) -> ตั้งค่า headless=True")
        headless_mode = True

    # 4. กำหนด Profile Path ใหม่
    _current_profile_path = os.path.abspath(f"./profiles/{sitename}_uc_profile")

    # Clean stale profile from an earlier Python process before reusing this path.
    # Do not call cleanup_profile() here: it clears _current_profile_path on success.
    if os.path.exists(_current_profile_path):
        profile_to_reset = _current_profile_path
        if not _terminate_chrome_for_profile(profile_to_reset):
            raise RuntimeError(
                f"ยกเลิก Browser launch: ยังมี Chrome ใช้ profile นี้อยู่: "
                f"{profile_to_reset}"
            )
        await asyncio.sleep(0.5)
        try:
            shutil.rmtree(profile_to_reset, ignore_errors=False)
        except Exception as exc:
            raise RuntimeError(
                f"ยกเลิก Browser launch: ลบ stale profile ไม่สำเร็จ: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        if os.path.exists(profile_to_reset):
            raise RuntimeError(
                f"ยกเลิก Browser launch: profile ยังอยู่หลัง cleanup: "
                f"{profile_to_reset}"
            )

    os.makedirs(_current_profile_path, exist_ok=True)

    # 5. ตั้งค่า Config ของ nodriver
    config = uc.Config()
    config.user_data_dir = _current_profile_path
    config.headless = headless_mode
    
    # 🔴 บังคับปิด Sandbox สำหรับสิทธิ์ Root (แก้ปัญหา Failed to connect to browser)
    config.sandbox = False
    
    if "get_browser_path_or_fail" in globals():
        config.browser_executable_path = get_browser_path_or_fail()

    # บล็อกอาร์กิวเมนต์รีดประสิทธิภาพ
    performance_args = [
        # --- รีดประสิทธิภาพและการใช้งานหน่วยความจำ (Performance & Memory) ---
        "--disable-dev-shm-usage",
        "--disable-breakpad",
        "--disable-component-update",
        "--disable-infobars",
        "--no-zygote",
        "--disable-gpu",
        "--blink-settings=imagesEnabled=false",
        "--disable-gl-extensions",
        "--disable-software-rasterizer",
        "--disable-extensions",
        "--disable-background-networking",
        "--mute-audio",
        "--disable-features=VizDisplayCompositor",
        "--disk-cache-dir=/dev/null",
        
        # --- ปิดระบบ Background และการประมวลผลที่ไม่จำเป็น ---
        "--disable-background-timer-throttling", 
        "--disable-backgrounding-occluded-windows", 
        "--disable-renderer-backgrounding", 
        "--disable-ipc-flooding-protection", 
        
        # --- ปิดฟีเจอร์และบริการเสริมเพื่อประหยัด RAM ---
        "--disable-translate", 
        "--disable-extensions-http-throttling", 
        "--disable-sync", 
        "--metrics-recording-only", 
        "--no-first-run", 
        "--disable-default-apps", 
        
        # --- จัดการหน่วยความจำ (Memory Management) ---
        "--js-flags=--max-old-space-size=512",
    ]
    
    for arg in performance_args:
        config.add_argument(arg)
    
    if isinstance(custom_args, list):
        for arg in custom_args:
            config.add_argument(arg)

    # 6. รัน Browser Instance ใหม่ด้วย nodriver
    try:
        selected_path = config.browser_executable_path or "System Default"
        print(f"📂 [System] Path ที่ตรวจพบและใช้งาน: {selected_path}")

        # nodriver ignores extra start kwargs when an existing Config is supplied.
        # Explicitly disable sandbox on the Config object before launching as root.
        config.sandbox = False
        # Keep the Browser object before start(); start() may raise after Chrome spawned.
        from nodriver.core.browser import Browser as NodriverBrowser
        browser_candidate = NodriverBrowser(config)
        _active_browser_instance = browser_candidate
        await browser_candidate.start()
        
        print(f"🚀 [System] Browser (nodriver) รันสำเร็จ (Headless: {headless_mode})")
        return _active_browser_instance

    except Exception as e:
        print(f"❌ [Critical] Nodriver Start Error: {type(e).__name__}: {e}")
        cleanup_ok = False
        try:
            cleanup_ok = (
                kill_specific_browser()
                if "kill_specific_browser" in globals()
                else False
            )
        except Exception as cleanup_error:
            print(
                f"⚠️ [Browser Cleanup] {type(cleanup_error).__name__}: "
                f"{cleanup_error}"
            )

        # Keep Xvfb alive if Chrome cleanup cannot be verified.
        if cleanup_ok and "kill_xvfb" in globals():
            kill_xvfb()
        elif not cleanup_ok:
            print("🔒 [Browser Cleanup] คง Xvfb ไว้ เพราะยังยืนยันการปิด Chrome ไม่ได้")
        raise


def _normalise_browser_profile_path(value):
    """Return a canonical profile path, or None when it cannot be resolved."""
    try:
        if value is None:
            return None
        return os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(value))))
    except Exception:
        return None


def _cmdline_profile_path(cmdline):
    """Extract Chrome's --user-data-dir argument (supports both CLI forms)."""
    if not isinstance(cmdline, (list, tuple)):
        return None
    for index, arg in enumerate(cmdline):
        if not isinstance(arg, str):
            continue
        if arg.startswith("--user-data-dir="):
            return arg.split("=", 1)[1]
        if arg == "--user-data-dir" and index + 1 < len(cmdline):
            return cmdline[index + 1]
    return None


def _terminate_chrome_for_profile(profile_path, timeout=5.0):
    """Stop only Chrome processes whose exact user-data-dir matches profile_path.

    Returns True only when no matching Chrome process remains. This function
    never uses pkill and never terminates browsers using another profile.
    """
    expected = _normalise_browser_profile_path(profile_path)
    if not expected:
        print("🔒 [Browser Cleanup] ไม่มี profile path ที่ยืนยันได้; ปฏิเสธการปิด")
        return False

    try:
        import psutil
    except Exception as exc:
        print(f"🔒 [Browser Cleanup] โหลด psutil ไม่ได้: {type(exc).__name__}: {exc}")
        return False

    allowed_names = {"chrome", "chromium", "chromium-browser", "brave"}

    def find_matching_processes():
        matches = []
        uncertain = []
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                name = (proc.info.get("name") or "").lower()
                if name not in allowed_names:
                    continue
                cmdline = proc.info.get("cmdline") or proc.cmdline()
                actual_path = _normalise_browser_profile_path(
                    _cmdline_profile_path(cmdline)
                )
                if actual_path == expected:
                    matches.append(proc)
            except psutil.NoSuchProcess:
                continue
            except psutil.AccessDenied:
                # As root this should be unusual. Fail closed rather than
                # deleting a profile while a Chrome process cannot be checked.
                try:
                    name = (proc.name() or "").lower()
                except Exception:
                    name = ""
                if name in allowed_names:
                    uncertain.append(getattr(proc, "pid", "?"))
            except Exception as exc:
                print(
                    f"⚠️ [Browser Cleanup] ตรวจ PID {getattr(proc, 'pid', '?')} ไม่ได้: "
                    f"{type(exc).__name__}: {exc}"
                )
                uncertain.append(getattr(proc, "pid", "?"))
        return matches, uncertain

    matches, uncertain = find_matching_processes()
    if uncertain:
        print(
            "🔒 [Browser Cleanup] ตรวจ Chrome บาง process ไม่ได้ "
            f"(PID: {uncertain}); ปฏิเสธการลบ Profile"
        )
        return False

    if not matches:
        return True

    pids = sorted({proc.pid for proc in matches})
    print(
        f"🧹 [Browser Cleanup] พบ Chrome {len(pids)} process ที่ใช้ Profile นี้: "
        f"{pids}"
    )

    # Terminate only processes whose command line proves exact profile ownership.
    for proc in matches:
        try:
            proc.terminate()
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied as exc:
            print(f"🔒 [Browser Cleanup] ปิด PID {proc.pid} ไม่ได้: {exc}")
            return False
        except Exception as exc:
            print(
                f"⚠️ [Browser Cleanup] terminate PID {proc.pid} ไม่สำเร็จ: "
                f"{type(exc).__name__}: {exc}"
            )
            return False

    _, alive = psutil.wait_procs(matches, timeout=max(1.0, float(timeout)))
    for proc in alive:
        try:
            # Recheck ownership immediately before force-kill.
            cmdline = proc.cmdline()
            actual_path = _normalise_browser_profile_path(
                _cmdline_profile_path(cmdline)
            )
            if actual_path != expected:
                print(
                    f"🔒 [Browser Cleanup] PID {proc.pid} เปลี่ยน profile; "
                    "ปฏิเสธการ force-kill"
                )
                return False
            proc.kill()
        except psutil.NoSuchProcess:
            pass
        except Exception as exc:
            print(
                f"🔒 [Browser Cleanup] kill PID {proc.pid} ไม่สำเร็จ: "
                f"{type(exc).__name__}: {exc}"
            )
            return False

    if alive:
        psutil.wait_procs(alive, timeout=2.0)

    remaining, uncertain = find_matching_processes()
    if uncertain or remaining:
        remain_pids = [proc.pid for proc in remaining]
        print(
            "🔒 [Browser Cleanup] ยังมี Chrome ใช้ Profile อยู่ "
            f"(remaining={remain_pids}, uncertain={uncertain}); ห้ามลบ Profile"
        )
        return False

    print(f"✅ [Browser Cleanup] ไม่มี Chrome ใช้ Profile นี้แล้ว: {profile_path}")
    return True


def kill_specific_browser():
    """Stop the tracked Browser and all Chrome processes using its exact profile."""
    global _active_browser_instance

    browser = _active_browser_instance
    if browser is None:
        return True

    config = getattr(browser, "config", None)
    profile_path = getattr(config, "user_data_dir", None)
    if not profile_path:
        print("🔒 [System] Browser ไม่มี user_data_dir ที่ยืนยันได้; ไม่ลบ reference")
        return False

    # If nodriver already created a PID, verify it belongs to this exact profile.
    process_obj = getattr(browser, "_process", None)
    pid = (
        getattr(browser, "_process_pid", None)
        or getattr(process_obj, "pid", None)
        or getattr(browser, "browser_pid", None)
    )
    if pid:
        try:
            import psutil
        except Exception as exc:
            print(
                f"🔒 [System] โหลด psutil ไม่ได้; คง Browser reference ไว้: "
                f"{type(exc).__name__}: {exc}"
            )
            return False
        try:
            proc = psutil.Process(int(pid))
            cmdline = proc.cmdline()
            actual = _normalise_browser_profile_path(_cmdline_profile_path(cmdline))
            expected = _normalise_browser_profile_path(profile_path)
            if actual != expected:
                print(
                    f"🔒 [System] ปฏิเสธปิด PID {pid}: "
                    f"profile ไม่ตรง/ยืนยันไม่ได้ ({actual})"
                )
                return False
        except psutil.NoSuchProcess:
            pass
        except Exception as exc:
            print(
                f"🔒 [System] ตรวจสอบ PID {pid} ไม่ได้; คง Browser reference ไว้: "
                f"{type(exc).__name__}: {exc}"
            )
            return False

    if not _terminate_chrome_for_profile(profile_path):
        return False

    _active_browser_instance = None
    return True


async def cleanup_profile():
    """Remove the current profile only after all matching Chrome processes exit."""
    global _current_profile_path

    profile_path = _current_profile_path
    if not profile_path:
        return True

    if not os.path.exists(profile_path):
        _current_profile_path = None
        return True

    # Also catches orphan Chrome processes left by an earlier Python process.
    if not _terminate_chrome_for_profile(profile_path):
        print(
            f"🔒 [System] เก็บ Profile ไว้ก่อน เพราะยังยืนยันการปิด Chrome ไม่ได้: "
            f"{profile_path}"
        )
        return False

    await asyncio.sleep(0.5)

    for attempt in range(3):
        try:
            if os.path.exists(profile_path):
                shutil.rmtree(profile_path, ignore_errors=False)
            if os.path.exists(profile_path):
                raise RuntimeError("โฟลเดอร์ยังคงอยู่หลัง rmtree")

            print(f"🧹 [System] ลบ Profile สำเร็จ: {profile_path}")
            _current_profile_path = None
            return True
        except Exception as exc:
            print(
                f"⚠️ [System] ลบ Profile ไม่สำเร็จ "
                f"(Retry {attempt + 1}/3): {type(exc).__name__}: {exc}"
            )
            if attempt < 2:
                await asyncio.sleep(1.0)

    # Keep the path so the next launch can retry safely.
    print(
        f"🔒 [System] ไม่ล้าง current profile reference เพื่อป้องกันการเปิดทับ: "
        f"{profile_path}"
    )
    return False


def kill_xvfb():
    global _global_display

    display = _global_display

    if display is None:
        return

    pid = getattr(display, "pid", None)

    try:
        # ให้ pyvirtualdisplay จัดการปิด Instance ที่ตัวเองเปิดขึ้น
        stop = getattr(display, "stop", None)

        if callable(stop):
            stop()
            print(f"✅ [System] ปิด Xvfb Instance แล้ว (PID: {pid})")

        elif pid:
            # Fallback เฉพาะ PID ที่เราบันทึกไว้
            os.kill(int(pid), signal.SIGTERM)
            print(f"⚠️ [System] ส่ง SIGTERM ไปยัง Xvfb PID {pid}")

    except ProcessLookupError:
        print(f"ℹ️ [System] Xvfb PID {pid} ปิดไปแล้ว")

    except Exception as e:
        print(f"⚠️ [System] ปิด Xvfb ไม่สำเร็จ: {type(e).__name__}: {e}")

        # ลอง fallback เฉพาะเมื่อยืนยันตัว Process ได้
        if pid:
            try:
                import psutil

                proc = psutil.Process(int(pid))

                if (
                    proc.name().lower() == "xvfb"
                    and proc.ppid() == os.getpid()
                ):
                    proc.kill()

            except Exception as fallback_error:
                print(
                    f"⚠️ [System] Xvfb fallback ล้มเหลว: "
                    f"{fallback_error}"
                )

    finally:
        _global_display = None

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
    
class ForcedCloseAdapter(HTTPAdapter):
    def send(self, request, *args, **kwargs):
        # บังคับเพิ่ม header Connection: close ทุกครั้งในระดับต่ำสุด
        request.headers['Connection'] = 'close'
        return super().send(request, *args, **kwargs)

# ========================= NODE CLASSES =========================

class QbitNode:
    def __init__(self, cfg):
        self.name, self.url = cfg["name"], cfg["url"].rstrip("/")
        self.user, self.pw = cfg["qb_user"], cfg["qb_pass"]
        self.quota_gb = cfg.get("quota_gb", 0)

        # Storage telemetry is selected per node. Existing nodes retain the
        # historical 15 GiB safety buffer unless explicitly configured.
        self.storage_api = str(cfg.get("storage_api") or "").strip().lower()
        self.storage_mount = str(cfg.get("storage_mount") or "/").strip() or "/"
        self.storage_safety_buffer_gb = cfg.get(
            "storage_safety_buffer_gb", 15.0
        )
        self.allow_space_reclaim = cfg.get("allow_space_reclaim", True) is True

        # Provider quota telemetry (GiB, binary units).
        # None means no verified provider quota sample yet.
        self.quota_total_gb = None
        self.quota_used_gb = None
        self.quota_free_gb = None
        
        self.provider_sso = cfg.get("provider_sso") is True
        self.preallocated_downloads_accounted_in_free_space = cfg.get("preallocated_downloads_accounted_in_free_space") is True
        self.provider_user = str(cfg.get("provider_user") or "").strip()
        self.provider_pw = str(cfg.get("provider_pass") or "")

        self.auth = (
            (self.user, self.pw)
            if cfg.get("nginx") and not self.provider_sso
            else None
        )
        
        self.s = requests.Session()
        self.s.auth = self.auth 
        
        # --- แก้ตรงนี้ครับ ---
        # ต้องใช้ ForcedCloseAdapter แทน HTTPAdapter ปกติ
        adapter = ForcedCloseAdapter() 
        self.s.mount('https://', adapter)
        self.s.mount('http://', adapter)
        # --------------------
        
        self.s.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })
        
        self.is_connected = False
        self.jobs = 0
        self.stat_msg = "API UNKNOWN: Not authenticated"
    
    def _execute_request(self, method, url, **kwargs):
        # 1. จัดการ Headers
        headers = kwargs.get('headers', {}).copy()
        headers['Connection'] = 'close'
        kwargs['headers'] = headers
        
        # 2. ยัด auth ลงไปใน kwargs ทุกครั้งถ้ามีตัวตน
        if self.auth:
            kwargs['auth'] = self.auth
            
        # 3. กำหนดค่าพื้นฐานที่จำเป็น
        kwargs.setdefault('timeout', 20)
        kwargs.setdefault('verify', False)
        
        try:
            # ใช้ self.s.request เพื่อรักษา Connection Pool ไว้ที่ 1 ตามที่ mount ไว้
            return self.s.request(method, url, **kwargs)
        except Exception as e:
            print(f"⚠️ [{self.name}] Network Error in {method} {url}: {e}")
            return None




    def _login_provider_sso(self):
        """OnlyOne Provider login -> Dashboard SSO -> qBittorrent SID."""

        self.is_connected = False
        self.s.cookies.clear()

        def fail(reason):
            self.is_connected = False
            self.free_gb = 0.0
            self.stat_msg = f"API ERROR: Provider SSO: {reason}"
            print(f"⚠️ [{self.name}] Provider SSO failed: {reason}")
            return False

        try:
            if not self.provider_user or not self.provider_pw:
                return fail("Provider credentials missing")

            parsed = urllib.parse.urlsplit(self.url)

            if not parsed.scheme or not parsed.netloc:
                return fail("Invalid qBittorrent URL")

            origin = f"{parsed.scheme}://{parsed.netloc}"
            qbit_path = parsed.path.rstrip("/")
            suffix = "/qbittorrent"

            if not qbit_path.endswith(suffix):
                return fail("URL path must end with /qbittorrent")

            account_path = qbit_path[:-len(suffix)].rstrip("/")

            if not account_path:
                return fail("Cannot determine account path")

            account_next = account_path + "/"

            login_url = (
                origin
                + "/login.php?"
                + urllib.parse.urlencode({"next": account_next})
            )

            # Stage 1: Provider login form
            page = self._execute_request(
                "GET",
                login_url,
                timeout=15,
                allow_redirects=False
            )

            if page is None or page.status_code != 200:
                return fail(
                    f"Provider login page HTTP "
                    f"{getattr(page, 'status_code', None)}"
                )

            content_type = str(
                page.headers.get("Content-Type", "")
            ).casefold()

            if "html" not in content_type:
                return fail("Provider login page is not HTML")

            soup = BeautifulSoup(page.text, "html.parser")
            form = None

            for candidate in soup.find_all("form"):
                action = urllib.parse.urljoin(
                    login_url,
                    candidate.get("action") or login_url
                )
                action_parts = urllib.parse.urlsplit(action)

                if (
                    action_parts.hostname
                    and action_parts.hostname.casefold()
                    == parsed.hostname.casefold()
                    and action_parts.path == "/login.php"
                    and candidate.get("method", "get").upper() == "POST"
                ):
                    form = candidate
                    break

            if form is None:
                return fail("Provider POST login form not found")

            fields = {}

            for item in form.find_all("input"):
                name = item.get("name")
                if name:
                    fields[name] = item.get("value", "")

            if not fields.get("_csrf") or "next" not in fields:
                return fail("Provider CSRF/next field missing")

            fields["username"] = self.provider_user
            fields["password"] = self.provider_pw

            action = urllib.parse.urljoin(
                login_url,
                form.get("action") or login_url
            )

            provider_response = self._execute_request(
                "POST",
                action,
                data=fields,
                headers={
                    "Origin": origin,
                    "Referer": login_url
                },
                timeout=20,
                allow_redirects=True
            )

            if provider_response is None:
                return fail("Provider login network failure")

            if provider_response.status_code != 200:
                return fail(
                    f"Provider login HTTP {provider_response.status_code}"
                )

            final_provider = urllib.parse.urlsplit(
                provider_response.url
            )

            if (
                final_provider.hostname.casefold()
                != parsed.hostname.casefold()
                or not (
                    final_provider.path == account_path
                    or final_provider.path.startswith(account_path + "/")
                )
            ):
                return fail("Unexpected Provider redirect")

            if not any(
                c.name == "oos_sid"
                for c in self.s.cookies
            ):
                return fail("Provider oos_sid not established")

            print(
                f"✅ [{self.name}] Provider Dashboard session established"
            )

            # Stage 2: Dashboard SSO hand-off.
            go_url = origin + account_path + "/go/qbittorrent"

            go_response = self._execute_request(
                "GET",
                go_url,
                headers={
                    "Referer": origin + account_path + "/"
                },
                timeout=20,
                allow_redirects=True
            )

            if go_response is None:
                return fail("qBittorrent SSO hand-off unavailable")

            final_qbit = urllib.parse.urlsplit(go_response.url)

            if (
                go_response.status_code != 200
                or final_qbit.hostname.casefold()
                != parsed.hostname.casefold()
                or final_qbit.path.rstrip("/") != qbit_path
            ):
                return fail(
                    f"SSO hand-off rejected "
                    f"(HTTP {go_response.status_code})"
                )

            if not any(
                c.name == "SID"
                for c in self.s.cookies
            ):
                return fail("qBittorrent SID not established")

            print(
                f"✅ [{self.name}] Dashboard SSO issued qBittorrent SID"
            )

            # Stage 3: Verify actual qBittorrent API.
            version_response = self._execute_request(
                "GET",
                f"{self.url}/api/v2/app/version",
                timeout=15,
                allow_redirects=False
            )

            if (
                version_response is None
                or version_response.status_code != 200
            ):
                return fail(
                    f"Version API HTTP "
                    f"{getattr(version_response, 'status_code', None)}"
                )

            version = str(
                version_response.text or ""
            ).strip()

            version_type = str(
                version_response.headers.get("Content-Type", "")
            ).casefold()

            if (
                "html" in version_type
                or not re.fullmatch(
                    r"v?\d+\.\d+\.\d+(?:[-+][^\s]*)?",
                    version
                )
            ):
                return fail("Invalid qBittorrent version response")

            self.is_connected = True

            print(
                f"✅ [{self.name}] Provider SSO verified: "
                f"qBittorrent={version}, Cookies=oos_sid+SID"
            )
            return True

        except Exception as e:
            return fail(f"{type(e).__name__}: {e}")

    def login(self):
        if self.provider_sso:
            return self._login_provider_sso()

        self.is_connected = False

        try:
            self.s.cookies.clear()

            parsed = urllib.parse.urlsplit(self.url)
            origin = f"{parsed.scheme}://{parsed.netloc}"
            referer = self.url.rstrip("/") + "/"

            r = self._execute_request(
                "POST",
                f"{self.url}/api/v2/auth/login",
                data={
                    "username": self.user,
                    "password": self.pw
                },
                headers={
                    "Referer": referer,
                    "Origin": origin
                },
                timeout=10,
                allow_redirects=False
            )

            if r is None:
                self.stat_msg = "API ERROR: Login network failure"
                self.free_gb = 0.0
                return False

            content_type = str(
                r.headers.get("Content-Type", "")
            ).casefold()

            body = str(r.text or "").lstrip().casefold()

            is_html = (
                "text/html" in content_type
                or body.startswith(("<!doctype html", "<html"))
            )

            cookie_names = [
                cookie.name for cookie in self.s.cookies
            ]

            # qBittorrent <= 5.1: SID
            # qBittorrent >= 5.2: QBT_SID_<WebUI port>
            has_session_cookie = any(
                name == "SID"
                or re.fullmatch(r"QBT_SID_\d+", name)
                for name in cookie_names
            )

            if (
                r.status_code not in (200, 204)
                or is_html
                or not has_session_cookie
            ):
                location_info = ""

                location = r.headers.get("Location", "")
                if location:
                    try:
                        target = urllib.parse.urlsplit(
                            urllib.parse.urljoin(
                                self.url.rstrip("/") + "/",
                                location
                            )
                        )

                        host = target.hostname or ""
                        port = (
                            f":{target.port}"
                            if target.port else ""
                        )

                        location_info = (
                            f"{host}{port}{target.path}"
                        )
                    except Exception:
                        location_info = "unparseable"

                self.is_connected = False
                self.free_gb = 0.0
                self.stat_msg = (
                    f"API ERROR: Login rejected "
                    f"(HTTP {r.status_code})"
                )

                print(
                    f"⚠️ [{self.name}] Login rejected: "
                    f"HTTP={r.status_code}, "
                    f"CookieNames={cookie_names!r}, "
                    f"Set-Cookie={bool(r.headers.get('Set-Cookie'))}, "
                    f"HTML={is_html}, "
                    f"Location={location_info!r}"
                )
                return False

            # Verify the session against a read-only qBittorrent API.
            version_response = self._execute_request(
                "GET",
                f"{self.url}/api/v2/app/version",
                timeout=10,
                allow_redirects=False
            )

            if (
                version_response is None
                or version_response.status_code != 200
            ):
                self.is_connected = False
                self.free_gb = 0.0
                self.stat_msg = (
                    "API ERROR: Session verification failed"
                )

                print(
                    f"⚠️ [{self.name}] Session verification failed: "
                    f"HTTP={getattr(version_response, 'status_code', None)}"
                )
                return False

            version_content_type = str(
                version_response.headers.get(
                    "Content-Type", ""
                )
            ).casefold()

            version_text = str(
                version_response.text or ""
            ).strip()

            if (
                "text/html" in version_content_type
                or not re.match(
                    r"^v?\d+\.\d+(?:\.\d+)?(?:[-+][^\s]*)?$",
                    version_text
                )
            ):
                self.is_connected = False
                self.free_gb = 0.0
                self.stat_msg = (
                    "API ERROR: Invalid version response"
                )

                print(
                    f"⚠️ [{self.name}] Invalid app/version response: "
                    f"Content-Type={version_content_type!r}, "
                    f"HTML={version_text[:20].casefold().startswith('<!doctype html')}"
                )
                return False

            self.is_connected = True

            print(
                f"✅ [{self.name}] Login successful: "
                f"qBittorrent={version_text}, "
                f"CookieType="
                f"{'SID' if 'SID' in cookie_names else 'QBT_SID'}"
            )
            return True

        except Exception as e:
            self.is_connected = False
            self.free_gb = 0.0
            self.stat_msg = "API ERROR: Login exception"

            print(
                f"⚠️ [{self.name}] Login Exception: "
                f"{type(e).__name__}: {e}"
            )
            return False
    @staticmethod
    def _parse_swizzin_size_gib(value):
        """Parse a dashboard size such as '10.64 GiB' into binary GiB."""
        if not isinstance(value, str):
            raise TypeError("Swizzin size must be a string with a unit")

        match = re.fullmatch(
            r"\s*(\d+(?:\.\d+)?)\s*(B|KB|MB|GB|TB|KIB|MIB|GIB|TIB)\s*",
            value,
            flags=re.IGNORECASE,
        )
        if not match:
            raise ValueError(f"Invalid Swizzin size: {value!r}")

        amount = float(match.group(1))
        unit = match.group(2).upper()
        multipliers = {
            "B": 1,
            "KB": 1000,
            "MB": 1000 ** 2,
            "GB": 1000 ** 3,
            "TB": 1000 ** 4,
            "KIB": 1024,
            "MIB": 1024 ** 2,
            "GIB": 1024 ** 3,
            "TIB": 1024 ** 4,
        }
        byte_value = amount * multipliers[unit]
        if not (0.0 <= byte_value < float("inf")):
            raise ValueError("Swizzin size is outside the valid range")
        return byte_value / (1024 ** 3)

    def _get_swizzin_storage_stats(self):
        """Read and validate ReadyIDC Swizzin quota and root-disk telemetry."""
        parsed = urllib.parse.urlsplit(self.url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError("Invalid qBittorrent URL for Swizzin API")

        origin = f"{parsed.scheme}://{parsed.netloc}"
        headers = {
            "Accept": "application/json",
            "Referer": origin + "/",
        }

        def get_json(endpoint):
            response = self._execute_request(
                "GET",
                origin + endpoint,
                headers=headers,
                timeout=15,
                allow_redirects=False,
            )
            if response is None:
                raise RuntimeError(f"Swizzin {endpoint} network failure")
            if response.status_code != 200:
                raise RuntimeError(
                    f"Swizzin {endpoint} HTTP {response.status_code}"
                )
            content_type = str(
                response.headers.get("Content-Type", "")
            ).casefold()
            if "json" not in content_type:
                raise ValueError(f"Swizzin {endpoint} response is not JSON")
            payload = response.json()
            if not isinstance(payload, dict):
                raise TypeError(f"Swizzin {endpoint} payload is invalid")
            return payload

        quota_payload = get_json("/stats/quota")
        disk_payload = get_json("/stats/disk")

        quota = quota_payload.get("quota")
        storage_mount = str(getattr(self, "storage_mount", "/") or "/").strip()
        if (
            not storage_mount.startswith("/")
            or ".." in storage_mount.split("/")
        ):
            raise ValueError("Swizzin storage_mount must be a safe absolute mount path")
        root_disk = disk_payload.get(storage_mount)
        if not isinstance(quota, dict):
            raise ValueError("Swizzin quota object is missing")
        if not isinstance(root_disk, dict):
            raise ValueError(
                f"Swizzin configured mount {storage_mount!r} is missing"
            )

        quota_free = self._parse_swizzin_size_gib(quota.get("diskfree"))
        quota_total = self._parse_swizzin_size_gib(quota.get("disktotal"))
        quota_used = self._parse_swizzin_size_gib(quota.get("diskused"))
        disk_free = self._parse_swizzin_size_gib(root_disk.get("diskfree"))
        disk_total = self._parse_swizzin_size_gib(root_disk.get("disktotal"))
        disk_used = self._parse_swizzin_size_gib(root_disk.get("diskused"))

        if quota_total <= 0 or disk_total <= 0:
            raise ValueError("Swizzin reported a zero or invalid total capacity")
        if not (0 <= quota_free <= quota_total and 0 <= quota_used <= quota_total):
            raise ValueError("Swizzin quota values are out of range")
        if not (0 <= disk_free <= disk_total and 0 <= disk_used <= disk_total):
            raise ValueError(
                f"Swizzin mount {storage_mount!r} values are out of range"
            )

        return {
            "quota_free_gb": quota_free,
            "quota_total_gb": quota_total,
            "quota_used_gb": quota_used,
            "disk_free_gb": disk_free,
            "disk_total_gb": disk_total,
            "disk_used_gb": disk_used,
            "disk_mount": storage_mount,
        }

    def _get_provider_quota_bytes(self, retry_auth=True):
        """
        Read verified account quota from Provider Dashboard.
        Returns (used_bytes, total_bytes).

        This is read-only. It does not modify torrents or storage.
        """
        if not self.provider_sso:
            raise RuntimeError("Provider SSO is not enabled")

        parsed = urllib.parse.urlsplit(self.url)

        if not parsed.scheme or not parsed.netloc:
            raise ValueError("Invalid qBittorrent URL")

        origin = f"{parsed.scheme}://{parsed.netloc}"
        qbit_path = parsed.path.rstrip("/")
        suffix = "/qbittorrent"

        if not qbit_path.endswith(suffix):
            raise ValueError(
                "Provider qBittorrent URL must end with /qbittorrent"
            )

        account_path = qbit_path[:-len(suffix)].rstrip("/")

        if not account_path:
            raise ValueError("Cannot determine Provider account path")

        endpoint = (
            origin + account_path + "/api/tenant/stats"
        )

        response = self._execute_request(
            "GET",
            endpoint,
            headers={
                "Accept": "application/json",
                "Referer": origin + account_path + "/",
            },
            timeout=15,
            allow_redirects=False,
        )

        if response is None:
            raise RuntimeError("Provider quota network failure")

        if response.status_code in (401, 403):
            if retry_auth and self.login():
                return self._get_provider_quota_bytes(
                    retry_auth=False
                )

            raise RuntimeError(
                f"Provider quota authentication HTTP "
                f"{response.status_code}"
            )

        if response.status_code != 200:
            raise RuntimeError(
                f"Provider quota HTTP {response.status_code}"
            )

        content_type = str(
            response.headers.get("Content-Type", "")
        ).casefold()

        if "json" not in content_type:
            raise ValueError(
                "Provider quota response is not JSON"
            )

        payload = response.json()

        if not isinstance(payload, dict):
            raise TypeError("Provider stats payload is invalid")

        quota = payload.get("quota")

        if not isinstance(quota, dict):
            raise ValueError("Provider quota object is missing")

        if quota.get("ok") is not True:
            raise ValueError("Provider quota status is not OK")

        used_bytes = quota.get("used")
        total_bytes = quota.get("total")

        if (
            isinstance(used_bytes, bool)
            or not isinstance(used_bytes, int)
            or isinstance(total_bytes, bool)
            or not isinstance(total_bytes, int)
        ):
            raise TypeError(
                "Provider quota values must be integer bytes"
            )

        if total_bytes <= 0:
            raise ValueError("Provider quota total is invalid")

        if used_bytes < 0 or used_bytes > total_bytes:
            raise ValueError("Provider quota used is out of range")

        return used_bytes, total_bytes


    def refresh_status(self, retry_login=True):

        def fail(message, disconnect=True):
            if disconnect:
                self.is_connected = False

            self.stat_msg = f"API ERROR: {message}"
            self.free_gb = 0.0
            if getattr(self, "storage_api", "") == "swizzin":
                # Never retain a stale provider quota sample after a failed refresh.
                self.quota_total_gb = None
                self.quota_used_gb = None
                self.quota_free_gb = None
            return False

        if not self.is_connected:
            return fail("Not authenticated")

        try:
            r_main = self._execute_request(
                "GET",
                f"{self.url}/api/v2/sync/maindata",
                timeout=10,
                allow_redirects=False
            )

            if r_main is None or r_main.status_code in (401, 403):
                self.is_connected = False

                if retry_login:
                    print(
                        f"🔄 [{self.name}] "
                        "Session unavailable; retry login"
                    )

                    if self.login():
                        return self.refresh_status(
                            retry_login=False
                        )

                return fail("maindata unavailable")

            if r_main.status_code != 200:
                return fail(
                    f"maindata HTTP {r_main.status_code}"
                )

            try:
                main_data = r_main.json()

                if not isinstance(main_data, dict):
                    raise TypeError("maindata is not a dictionary")

                server_state = main_data.get("server_state")

                if not isinstance(server_state, dict):
                    raise TypeError("server_state is invalid")

                raw_free = server_state.get("free_space_on_disk")

                if raw_free is None:
                    raise ValueError(
                        "free_space_on_disk missing"
                    )

                real_disk_free_bytes = int(raw_free)

                if real_disk_free_bytes < 0:
                    raise ValueError(
                        "free_space_on_disk is negative"
                    )

            except Exception as e:
                print(
                    f"⚠️ [{self.name}] Invalid maindata: "
                    f"{type(e).__name__}: {e}"
                )
                return fail("Invalid maindata payload")

            r_torrents = self._execute_request(
                "GET",
                f"{self.url}/api/v2/torrents/info",
                timeout=15,
                allow_redirects=False
            )

            if r_torrents is None or r_torrents.status_code in (401, 403):
                self.is_connected = False

                if retry_login:
                    print(
                        f"🔄 [{self.name}] "
                        "Torrent session unavailable; retry login"
                    )

                    if self.login():
                        return self.refresh_status(
                            retry_login=False
                        )

                return fail("torrent inventory unavailable")

            if r_torrents.status_code != 200:
                return fail(
                    f"torrents/info HTTP {r_torrents.status_code}"
                )

            try:
                torrents = r_torrents.json()

                if not isinstance(torrents, list):
                    raise TypeError(
                        "torrents/info response is not a list"
                    )

                if any(
                    not isinstance(item, dict)
                    for item in torrents
                ):
                    raise TypeError(
                        "invalid torrent entry"
                    )

            except Exception as e:
                print(
                    f"⚠️ [{self.name}] Invalid torrent response: "
                    f"{type(e).__name__}: {e}"
                )
                return fail("Invalid torrent inventory")

            active_count = len(torrents)

            real_disk_free_gb = (
                real_disk_free_bytes / (1024 ** 3)
            )

            try:
                safety_buffer = float(self.storage_safety_buffer_gb)
                if not (0.0 <= safety_buffer < float("inf")):
                    raise ValueError("safety buffer must be finite and non-negative")
            except (TypeError, ValueError, OverflowError) as buffer_error:
                return fail(
                    f"Invalid storage safety buffer: {buffer_error}",
                    disconnect=False
                )

            if self.storage_api == "swizzin":
                try:
                    storage = self._get_swizzin_storage_stats()
                except Exception as storage_error:
                    return fail(
                        "Swizzin storage unavailable: "
                        f"{type(storage_error).__name__}: {storage_error}",
                        disconnect=False
                    )

                quota_free_gb = storage["quota_free_gb"]
                quota_total_gb = storage["quota_total_gb"]
                used_gb = storage["quota_used_gb"]
                swizzin_disk_free_gb = storage["disk_free_gb"]

                self.quota_used_gb = used_gb
                self.quota_total_gb = quota_total_gb
                self.quota_free_gb = quota_free_gb

                # Use the most conservative of account quota, configured
                # Swizzin mount, and qBittorrent maindata disk-free readings.
                display_free = min(
                    quota_free_gb,
                    swizzin_disk_free_gb,
                    real_disk_free_gb,
                )

                self.stat_msg = (
                    f"FREE(Q): {quota_free_gb:.2f}GiB | "
                    f"FREE(D): {swizzin_disk_free_gb:.2f}GiB | "
                    f"Mount: {storage['disk_mount']} | "
                    f"FREE(QBT): {real_disk_free_gb:.2f}GiB | "
                    f"Total: {active_count} | "
                    f"Used: {used_gb:.2f}GiB / "
                    f"{quota_total_gb:.2f}GiB | "
                    f"Safe: {max(0.0, display_free - safety_buffer):.2f}GiB"
                )

            elif self.provider_sso:
                # Provider quota is authoritative for account usage.
                # Do not infer account usage from shared filesystem free.
                try:
                    used_bytes, total_bytes = (
                        self._get_provider_quota_bytes()
                    )
                except Exception as quota_error:
                    return fail(
                        "Provider quota unavailable: "
                        f"{type(quota_error).__name__}: {quota_error}",
                        disconnect=False
                    )

                used_gb = used_bytes / (1024 ** 3)
                quota_total_gb = total_bytes / (1024 ** 3)
                quota_free_gb = (
                    total_bytes - used_bytes
                ) / (1024 ** 3)

                self.quota_used_gb = used_gb
                self.quota_total_gb = quota_total_gb
                self.quota_free_gb = quota_free_gb

                display_free = min(
                    quota_free_gb,
                    real_disk_free_gb
                )

                self.stat_msg = (
                    f"FREE(Q): {quota_free_gb:.1f}GiB | "
                    f"FREE(D): {real_disk_free_gb:.1f}GiB | "
                    f"Total: {active_count} | "
                    f"Used: {used_gb:.1f}GiB / "
                    f"{quota_total_gb:.1f}GiB | "
                    f"Safe: "
                    f"{max(0.0, display_free - safety_buffer):.1f}GiB"
                )

            else:
                # Preserve existing accounting for other qBittorrent nodes.
                capacity_limit_gb = getattr(
                    self,
                    "total_disk_gb",
                    self.quota_gb
                )

                used_gb = max(
                    0.0,
                    capacity_limit_gb - real_disk_free_gb
                )

                quota_free_gb = max(
                    0.0,
                    self.quota_gb - used_gb
                )

                display_free = min(
                    quota_free_gb,
                    real_disk_free_gb
                )

                self.stat_msg = (
                    f"FREE(Q): {quota_free_gb:.1f}GB | "
                    f"FREE(D): {real_disk_free_gb:.1f}GB | "
                    f"Total: {active_count} | "
                    f"Used: {used_gb:.1f}G / "
                    f"{self.quota_gb:.0f}G | "
                    f"Safe: "
                    f"{max(0.0, display_free - safety_buffer):.1f}G"
                )

            self.free_gb = max(
                0.0,
                display_free - safety_buffer
            )

            return True

        except Exception as e:
            print(
                f"⚠️ [{self.name}] qBittorrent Refresh Error: "
                f"{type(e).__name__}: {e}"
            )
            return fail("refresh exception")
    def add(self, content, site_name="Universal", size=None, n_cfg=None, save_path=None, skip_checking=False, tags="AutoPilot", paused=False, cross_seed=False):
        """
        เพิ่ม Torrent เข้า qBittorrent รองรับทั้ง Normal Grab และ Cross-Seed Mode
    
        :param save_path: ตำแหน่งโฟลเดอร์สำหรับเซฟไฟล์ (จำเป็นมากสำหรับ Cross-Seed)
        :param skip_checking: หากเป็น True จะข้ามการตรวจ Hash และเริ่ม Seed ทันที
        """
        try:
            if len(content) < 1000:
                return False

            files = {"torrents": ("f.torrent", content, "application/x-bittorrent")}
        
            data = {
                "paused": "true" if paused else "false",
                "firstLastPiecePrio": "true" if not skip_checking else "false",
                "sequentialDownload": "false",
                "category": site_name,
                "tags": tags,
                "autoTMM": "false"  # ปิด TMM เพื่อให้บังคับใช้ savepath ที่ส่งไปได้แม่นยำ
            }

            if cross_seed:
                current_tags = [
                    part.strip()
                    for part in str(data.get("tags") or "").split(",")
                    if part.strip()
                ]
                normalized = {
                    tag.replace("_", "-").replace(" ", "-").casefold()
                    for tag in current_tags
                }
                if not normalized.intersection({"cross-seed", "crossseed"}):
                    current_tags.append("Cross-Seed")
                data["tags"] = ",".join(current_tags)

            # 🎯 เพิ่มพารามิเตอร์พิเศษสำหรับ Cross-Seed
            if save_path:
                data["savepath"] = save_path
        
            if skip_checking:
                data["skip_checking"] = "true"
                current_tags = [
                    part.strip()
                    for part in str(data.get("tags") or "").split(",")
                    if part.strip()
                ]
                normalized = {
                    tag.replace("_", "-").replace(" ", "-").casefold()
                    for tag in current_tags
                }
                if not normalized.intersection({"cross-seed", "crossseed"}):
                    current_tags.append("Cross-Seed")
                data["tags"] = ",".join(current_tags)

            # ส่ง Request ไปยัง qBittorrent WebUI
            r = self._execute_request(
                'POST',
                f"{self.url}/api/v2/torrents/add",
                files=files,
                data=data,
                headers={'Referer': self.url},
                auth=self.auth,
                verify=False,
                timeout=30
            )

            # 1. ตรวจสอบ Response สำเร็จ (รองรับทั้งแบบ text "Ok" และ JSON ผลลัพธ์ที่มี success_count หรือ added_torrent_ids)
            if r is not None and r.status_code == 200:
                resp_text = r.text.strip()
                if "Ok" in resp_text or "success_count" in resp_text or "added_torrent_ids" in resp_text:
                    return True
            
            # 2. กรณี Session หลุด (401/403) - Re-login แล้วลองอีกครั้ง
            if r is not None and r.status_code in [401, 403]:
                print(f" 🔄 [{self.name}] Session expired during add(), re-logging...")
                if self.login():
                    # ส่ง flag ป้องกัน loop โดยส่งพารามิเตอร์เดิมกลับไป
                    return self.add(
                        content=content, 
                        site_name=site_name, 
                        size=size, 
                        n_cfg=n_cfg, 
                        save_path=save_path, 
                        skip_checking=skip_checking,
                        tags=tags,
                        paused=paused,
                        cross_seed=cross_seed
                    )
        
            error_msg = r.text if r is not None else "No response"
            print(f"⚠️ [API Error] {self.name}: {r.status_code if r else 'None'} - {error_msg}")
            return False

        except Exception as e:
            print(f"❌ [Exception] {self.name}: {str(e)}")
            return False

    def get_all_torrents_info(self):
        """
        Return:
            []   = API สำเร็จและไม่มี torrents
            list = API สำเร็จและอ่าน inventory ได้ครบ
            None = API / Network / JSON / Processing failure
        """
        try:
            r = self._execute_request(
                'GET',
                f"{self.url}/api/v2/torrents/info",
                params={'filter': 'all'},
                auth=self.auth,
                verify=False,
                timeout=15,
                allow_redirects=False
            )

            if r is None or r.status_code != 200:
                if r is not None and r.status_code in [401, 403]:
                    self.is_connected = False

                print(
                    f"⚠️ [{self.name}] Torrent inventory API failed: "
                    f"{r.status_code if r is not None else 'No response'}"
                )
                return None

            try:
                data = r.json()
            except Exception as json_error:
                print(
                    f"⚠️ [{self.name}] Invalid inventory JSON: "
                    f"{json_error}"
                )
                return None

            if not isinstance(data, list):
                print(
                    f"⚠️ [{self.name}] Invalid inventory payload type: "
                    f"{type(data).__name__}"
                )
                return None

            if any(not isinstance(t, dict) for t in data):
                print(
                    f"⚠️ [{self.name}] Invalid torrent entry in inventory"
                )
                return None

            data.sort(
                key=lambda x: x.get('ratio', 0),
                reverse=True
            )

            results = []

            for t in data:
                size_bytes = t.get(
                    'total_size',
                    t.get('size', 0)
                )
                size_bytes = int(size_bytes or 0)

                results.append({
                    'hash': t.get('hash'),
                    'ratio': t.get('ratio', 0),
                    'name': t.get('name', 'Unknown'),
                    'size': size_bytes / (1024**3),
                    'size_bytes': size_bytes,
                    'amount_left': t.get('amount_left', t.get('left', -1)),
                    'progress': t.get('progress', 0.0),
                    'state': t.get('state', 'unknown'),
                    'added_on': t.get('added_on'),
                    'leechers': t.get('num_leechs', 0),
                    'up_speed': t.get('upspeed', 0),
                    'ts_finished': t.get('completion_on', 0),
                    'ts_init': t.get('added_on', 0),
                    'is_rt_complete': t.get('progress', 0) >= 1.0,
                    'save_path': t.get('save_path', '')
                })

            return results

        except Exception as e:
            print(
                f"⚠️ [{self.name}] Error in get_all_torrents_info: {e}"
            )
            return None

    def get_torrent_file_manifest(self, t_hash):
        """Read relative file paths and sizes through qBittorrent API."""
        clean_hash = str(t_hash or "").strip().lower()

        if len(clean_hash) not in (40, 64):
            return None
        if any(ch not in "0123456789abcdef" for ch in clean_hash):
            return None

        if not self.is_connected and not self.login():
            return None

        try:
            r = self._execute_request(
                "GET",
                f"{self.url}/api/v2/torrents/files",
                params={"hash": clean_hash},
                auth=self.auth,
                verify=False,
                timeout=15,
            )

            if r is None or r.status_code != 200:
                print(
                    f"⚠️ [{self.name}] File manifest API failure: "
                    f"{r.status_code if r is not None else 'no response'}"
                )
                return None

            payload = r.json()
            if not isinstance(payload, list) or not payload:
                return None

            result = []
            for item in payload:
                if not isinstance(item, dict):
                    return None

                file_path = str(item.get("name") or "").replace("\\", "/")
                try:
                    size_bytes = int(item.get("size"))
                except (TypeError, ValueError, OverflowError):
                    return None

                if not file_path or size_bytes < 0:
                    return None

                result.append({
                    "path": file_path,
                    "size_bytes": size_bytes,
                })

            return result

        except Exception as e:
            print(f"⚠️ [{self.name}] File manifest error: {e}")
            return None

    def get_torrent_by_hash(self, t_hash):
        """
        Return:
            dict  = Torrent found
            False = Inventory succeeded; hash not found
            None  = Inventory failure / invalid data
        """
        target_hash = str(t_hash or "").strip().lower()

        if (
            len(target_hash) not in (40, 64)
            or any(ch not in "0123456789abcdef" for ch in target_hash)
        ):
            return None

        all_torrents = self.get_all_torrents_info()

        if not isinstance(all_torrents, (list, tuple)):
            return None

        for torrent in all_torrents:
            if not isinstance(torrent, dict):
                return None

            current_hash = str(
                torrent.get("hash") or ""
            ).strip().lower()

            if (
                len(current_hash) not in (40, 64)
                or any(
                    ch not in "0123456789abcdef"
                    for ch in current_hash
                )
            ):
                return None

            if current_hash == target_hash:
                return torrent

        return False

    def is_torrent_exists(self, t_hash):
        """
        Return:
            True  = Torrent exists
            False = API succeeded; Torrent does not exist
            None  = Login / API / Network / JSON failure
        """
        clean_hash = str(t_hash or "").strip().lower()

        if (
            len(clean_hash) not in (40, 64)
            or any(ch not in "0123456789abcdef" for ch in clean_hash)
        ):
            return None

        try:
            if not self.is_connected:
                if not self.login():
                    print(
                        f"⚠️ [{self.name}] Hash check login failed"
                    )
                    return None

            r = self._execute_request(
                "GET",
                f"{self.url}/api/v2/torrents/info",
                params={"hashes": clean_hash},
                timeout=10
            )

            if r is None:
                print(
                    f"⚠️ [{self.name}] Hash check: no HTTP response"
                )
                return None

            if r.status_code in (401, 403):
                self.is_connected = False

            if r.status_code != 200:
                print(
                    f"⚠️ [{self.name}] Hash check HTTP "
                    f"{r.status_code}"
                )
                return None

            try:
                payload = r.json()
            except Exception as json_error:
                print(
                    f"⚠️ [{self.name}] Hash check JSON error: "
                    f"{json_error}"
                )
                return None

            if not isinstance(payload, list):
                print(
                    f"⚠️ [{self.name}] Hash check invalid payload"
                )
                return None

            for item in payload:
                if not isinstance(item, dict):
                    return None

                current_hash = str(
                    item.get("hash") or ""
                ).strip().lower()

                if (
                    len(current_hash) not in (40, 64)
                    or any(
                        ch not in "0123456789abcdef"
                        for ch in current_hash
                    )
                ):
                    return None

                if current_hash != clean_hash:
                    return None

                return True

            return False

        except Exception as e:
            print(
                f"⚠️ [{self.name}] Hash check exception: {e}"
            )
            return None

    def delete_torrent(self, hash_str):
        if not self.is_connected and not self.login(): 
            return False

        try:
            # ใช้ _execute_request แทนการยิงตรง
            r = self._execute_request(
                'POST', 
                f"{self.url}/api/v2/torrents/delete", 
                data={"hashes": hash_str, "deleteFiles": "true"}, 
                timeout=10
            )
            return r is not None and r.status_code == 200
        except Exception: 
            return False

    def get_downloading_size(self):
        # Preallocated bytes are already reflected in verified free-space telemetry.
        if getattr(
            self,
            'preallocated_downloads_accounted_in_free_space',
            False
        ) is True:
            return 0.0

        try:
            # ใช้ _execute_request แทน self.s.get เพื่อให้ผ่าน Adapter และ Header ที่เราตั้งไว้
            r = self._execute_request(
                'GET',
                f"{self.url}/api/v2/torrents/info", 
                params={'filter': 'downloading'}, 
                timeout=10
            )
            
            # ตรวจสอบ r เป็น None หรือ status_code ก่อนประมวลผล
            if r is not None and r.status_code == 200:
                torrents = r.json()
                total_left = sum(t.get('amount_left', 0) for t in torrents)
                return total_left / (1024**3)
            
            return 0.0
        except Exception:
            return 0.0
            


    def get_active_downloads(self):
        """ดึง torrent ที่กำลังดาวน์โหลดหรือตรวจสอบจาก qBittorrent"""
        results = []
        seen_hashes = set()
        successful_filters = 0

        for filter_type in ("downloading", "checking"):
            try:
                r = self._execute_request(
                    "GET",
                    f"{self.url}/api/v2/torrents/info",
                    params={"filter": filter_type},
                    timeout=10,
                )

                if r is None:
                    print(
                        f"⚠️ [{self.name}] {filter_type}: "
                        "ไม่มี HTTP response"
                    )
                    continue

                if r.status_code in (401, 403):
                    self.is_connected = False
                    print(
                        f"⚠️ [{self.name}] {filter_type}: "
                        f"HTTP {r.status_code}"
                    )
                    continue

                if r.status_code != 200:
                    print(
                        f"⚠️ [{self.name}] {filter_type}: "
                        f"HTTP {r.status_code}"
                    )
                    continue

                torrents = r.json()
                if not isinstance(torrents, list):
                    print(
                        f"⚠️ [{self.name}] {filter_type}: "
                        "API response ไม่ใช่ list"
                    )
                    continue

                successful_filters += 1

                for t in torrents:
                    torrent_hash = str(t.get("hash") or "").lower()

                    if not torrent_hash or torrent_hash in seen_hashes:
                        continue

                    seen_hashes.add(torrent_hash)

                    results.append({
                        "hash": torrent_hash,
                        "name": t.get("name", ""),
                        "size_bytes": int(t.get("size") or 0),
                        "state": t.get("state", ""),
                        "dlspeed": max(0, int(t.get("dlspeed") or 0)),
                        "progress": float(t.get("progress") or 0),
                        "last_activity": int(
                            t.get("last_activity") or 0
                        ),
                        "amount_left": int(t.get("amount_left") or 0),
                        "filter_type": filter_type,
                    })

            except Exception as e:
                print(
                    f"⚠️ [{self.name}] "
                    f"get_active_downloads ({filter_type}): {e}"
                )

        # แยกสถานะ API ล้มเหลวออกจากกรณีไม่มี torrent
        self.last_active_downloads_ok = successful_filters > 0

        checking_count = sum(
            1 for t in results if t["filter_type"] == "checking"
        )
        downloading_count = sum(
            1 for t in results if t["filter_type"] == "downloading"
        )

        print(
            f"[TORRENT AUDIT] [{self.name}] "
            f"api_filters_ok={successful_filters}/2 "
            f"downloading={downloading_count} "
            f"checking={checking_count} "
            f"total={len(results)}"
        )

        return results


    def _sweeper_force_start(self):
        """ระบบกวาดงานค้างอัตโนมัติ"""
        # _execute_request จะเป็นตัวจัดการการเชื่อมต่อทั้งหมด
        try:
            # 1. ดึงข้อมูลงานที่ paused
            r = self._execute_request(
                'GET', 
                f"{self.url}/api/v2/torrents/info", 
                params={'filter': 'paused'}, 
                timeout=10
            )
            
            if r is not None and r.status_code == 200:
                torrents = r.json()
                def _has_cross_seed_marker(t):
                    tags = {
                        part.strip().replace("_", "-").replace(" ", "-").casefold()
                        for part in str(t.get("tags") or "").split(",")
                        if part.strip()
                    }
                    category = (
                        str(t.get("category") or "")
                        .strip()
                        .replace("_", "-")
                        .replace(" ", "-")
                        .casefold()
                    )
                    return bool(
                        tags.intersection({"cross-seed", "crossseed"})
                        or category in {"cross-seed", "crossseed"}
                    )

                hashes_to_resume = []
                for t in torrents:
                    if t.get("state") not in [
                        "pausedUP", "pausedDL", "queuedUP", "queuedDL"
                    ]:
                        continue
                    torrent_hash = str(t.get("hash") or "").strip()
                    if torrent_hash and not _has_cross_seed_marker(t):
                        hashes_to_resume.append(torrent_hash)

                
                if hashes_to_resume:
                    # 2. สั่ง Resume ผ่าน _execute_request
                    self._execute_request(
                        'POST',
                        f"{self.url}/api/v2/torrents/resume", 
                        data={"hashes": "|".join(hashes_to_resume)},
                        headers={'Referer': self.url},
                        timeout=10
                    )
        except Exception as e:
            # print(f"⚠️ [{self.name}] Sweeper Error: {e}")
            pass

    def reannounce_torrent(self, hash_str):
        """Re-announce เฉพาะ Torrent ที่ระบุ"""
        try:
            r = self._execute_request(
                'POST',
                f"{self.url}/api/v2/torrents/reannounce",
                data={"hashes": hash_str},
                headers={'Referer': self.url},
                timeout=10
            )
            return r is not None and r.status_code == 200
        except Exception as e:
            print(f" ⚠️ [{self.name}] Re-announce Error for {hash_str}: {e}")
            return False

    def stop_torrent(self, hash_str):
        """สั่ง Stop (Pause) เฉพาะ Torrent ที่ระบุ"""
        try:
            r = self._execute_request(
                'POST',
                f"{self.url}/api/v2/torrents/pause",
                data={"hashes": hash_str},
                headers={'Referer': self.url},
                timeout=10
            )
            return r is not None and r.status_code == 200
        except Exception as e:
            print(f" ⚠️ [{self.name}] Stop Error for {hash_str}: {e}")
            return False


    def reannounce_all(self):
        """Submit re-announce request only with a valid session."""

        if not self.is_connected:
            print(
                f"⏭️ [{self.name}] Re-announce skipped: "
                "not authenticated"
            )
            return False

        try:
            r = self._execute_request(
                "POST",
                f"{self.url}/api/v2/torrents/reannounce",
                data={"hashes": "all"},
                headers={"Referer": self.url.rstrip("/") + "/"},
                timeout=15,
                allow_redirects=False
            )

            if r is None:
                return False

            if r.status_code in (401, 403):
                self.is_connected = False
                self.stat_msg = (
                    "API ERROR: Re-announce session rejected"
                )
                self.free_gb = 0.0
                return False

            content_type = str(
                r.headers.get("Content-Type", "")
            ).casefold()

            body = str(r.text or "").lstrip().casefold()

            is_html = (
                "text/html" in content_type
                or body.startswith(("<!doctype html", "<html"))
            )

            if r.status_code != 200 or is_html:
                print(
                    f"⚠️ [{self.name}] Re-announce rejected: "
                    f"HTTP={r.status_code}, "
                    f"Content-Type={content_type!r}"
                )
                return False

            # HTTP 200 means API accepted the request.
            # It does not prove tracker/peer communication succeeded.
            return True

        except Exception as e:
            print(
                f"⚠️ [{self.name}] Re-announce exception: "
                f"{type(e).__name__}: {e}"
            )
            return False
    async def sync_to_mapping(self, web_data_list=None, mapper_func=None):
        """
        Sync only verified live qBittorrent inventory into Mapping.

        None / invalid inventory = failure.
        Empty complete inventory = successful empty inventory.
        Web-target sync succeeds only when every requested hash is
        found in this Node's live inventory and every Mapper call
        returns exactly True.
        """
        if globals().get("PHASE_G_ALLOW_MAPPING_WRITES", False) is not True:
            print(
                f"🔒 [{self.name}] Mapping sync disabled by Phase G; "
                "no Mapper calls performed."
            )
            return False

        if not self.is_connected:
            if not self.login():
                return False

        all_qbt_torrents = self.get_all_torrents_info()

        if all_qbt_torrents is None:
            print(
                f"🛑 [{self.name}] Mapping sync aborted: "
                "inventory query failed"
            )
            return False

        if not isinstance(all_qbt_torrents, (list, tuple)):
            print(
                f"🛑 [{self.name}] Mapping sync aborted: "
                "invalid inventory type"
            )
            return False

        # Validate the complete inventory before writing any Mapping data.
        qbt_map = {}

        for torrent in all_qbt_torrents:
            if not isinstance(torrent, dict):
                print(
                    f"🛑 [{self.name}] Mapping sync aborted: "
                    "invalid torrent entry"
                )
                return False

            torrent_hash = str(
                torrent.get("hash") or ""
            ).strip().lower()

            if (
                len(torrent_hash) not in (40, 64)
                or any(
                    ch not in "0123456789abcdef"
                    for ch in torrent_hash
                )
            ):
                print(
                    f"🛑 [{self.name}] Mapping sync aborted: "
                    "invalid inventory infohash"
                )
                return False

            if torrent_hash in qbt_map:
                print(
                    f"🛑 [{self.name}] Mapping sync aborted: "
                    f"duplicate inventory hash {torrent_hash[:12]}"
                )
                return False

            qbt_map[torrent_hash] = torrent

        save_func = (
            mapper_func
            if mapper_func is not None
            else add_or_update_torrent
        )

        synced_count = 0
        failed_count = 0

        # ----------------------------------------------------
        # Web-observed hash sync: exact match on this Node.
        # ----------------------------------------------------
        if web_data_list:
            if not isinstance(web_data_list, (list, tuple)):
                print(
                    f"🛑 [{self.name}] Invalid web_data_list"
                )
                return False

            requests = []

            # Validate all requested entries before any writes.
            for web_item in web_data_list:
                if not isinstance(web_item, dict):
                    print(
                        f"🛑 [{self.name}] Invalid web item"
                    )
                    return False

                target_hash = str(
                    web_item.get("hash") or ""
                ).strip().lower()

                if (
                    len(target_hash) not in (40, 64)
                    or any(
                        ch not in "0123456789abcdef"
                        for ch in target_hash
                    )
                ):
                    print(
                        f"🛑 [{self.name}] Invalid requested infohash"
                    )
                    return False

                qbt_torrent = qbt_map.get(target_hash)

                if qbt_torrent is None:
                    print(
                        f"⚠️ [{self.name}] Requested hash "
                        f"{target_hash[:12]} is not present "
                        "in this Node's live inventory"
                    )
                    return False

                requests.append((web_item, qbt_torrent))

            for web_item, qbt_torrent in requests:
                client_data = {
                    "client_type": "qbittorrent",
                    "hash": qbt_torrent["hash"],
                    "name": qbt_torrent.get("name"),
                    "size_bytes": qbt_torrent.get("size_bytes", 0),
                    "state": qbt_torrent.get("state", "unknown"),
                    "ratio": qbt_torrent.get("ratio", 0.0)
                }

                node_data = {
                    "seedbox_host": self.name,
                    "save_path": qbt_torrent.get("save_path", ""),
                    "verification_method": "live_qbittorrent_inventory"
                }

                try:
                    result = await save_func(
                        client_data=client_data,
                        web_data=web_item,
                        node_data=node_data
                    )
                except Exception as exc:
                    result = False
                    print(
                        f"⚠️ [{self.name}] Mapper exception: "
                        f"{type(exc).__name__}: {exc}"
                    )

                if result is True:
                    synced_count += 1
                else:
                    failed_count += 1

            print(
                f"🔄 [{self.name}] Target Mapping sync: "
                f"success={synced_count}, failed={failed_count}"
            )

            return (
                synced_count == len(requests)
                and failed_count == 0
                and synced_count > 0
            )

        # ----------------------------------------------------
        # Normal complete Node inventory sync.
        # ----------------------------------------------------

        if not all_qbt_torrents:
            print(
                f"ℹ️ [{self.name}] Complete inventory is empty; "
                "no Mapping changes required"
            )
            return True

        for qbt_torrent in all_qbt_torrents:
            client_data = {
                "client_type": "qbittorrent",
                "hash": qbt_torrent["hash"],
                "name": qbt_torrent.get("name"),
                "size_bytes": qbt_torrent.get("size_bytes", 0),
                "state": qbt_torrent.get("state", "unknown"),
                "ratio": qbt_torrent.get("ratio", 0.0)
            }

            node_data = {
                "seedbox_host": self.name,
                "save_path": qbt_torrent.get("save_path", ""),
                "verification_method": "live_qbittorrent_inventory"
            }

            try:
                result = await save_func(
                    client_data=client_data,
                    web_data={},
                    node_data=node_data
                )
            except Exception as exc:
                result = False
                print(
                    f"⚠️ [{self.name}] Mapper exception: "
                    f"{type(exc).__name__}: {exc}"
                )

            if result is True:
                synced_count += 1
            else:
                failed_count += 1

        print(
            f"🔄 [{self.name}] Complete Mapping sync: "
            f"success={synced_count}, failed={failed_count}"
        )

        return failed_count == 0


    def get_stats_by_site(self):
        """ดึงสถิติแยกตาม Category ของแต่ละเว็บ"""
        try:
            # ใช้ _execute_request เพื่อรักษา Connection Pool และ Header ที่เรากำหนดไว้
            r = self._execute_request(
                'GET',
                f"{self.url}/api/v2/torrents/info",
                timeout=15
            )
            
            # ตรวจสอบว่าได้ข้อมูลและสถานะ 200
            if r is None or r.status_code != 200:
                if r is not None and r.status_code in [401, 403]:
                    self.is_connected = False
                return {}

            torrents = r.json()
            site_stats = {}
            
            for t in torrents:
                # แยกกลุ่มด้วย Category
                site = t.get('category') or "Uncategorized"
                
                up_speed = t.get('upspeed', 0)
                total_up = t.get('uploaded', 0)
                downloaded = t.get('downloaded', 0)

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
        except Exception:
            return {}


def _bencode_value_end(data, pos):
    """คืนตำแหน่ง byte ถัดจากค่า bencode หนึ่งค่า"""
    if pos >= len(data):
        raise ValueError("Unexpected end of bencode")

    token = data[pos]

    if token == ord("i"):
        end = data.find(b"e", pos + 1)
        if end < 0:
            raise ValueError("Unterminated bencode integer")
        return end + 1

    if token in (ord("l"), ord("d")):
        is_dict = token == ord("d")
        pos += 1

        while True:
            if pos >= len(data):
                raise ValueError("Unterminated bencode container")
            if data[pos] == ord("e"):
                return pos + 1

            pos = _bencode_value_end(data, pos)
            if is_dict:
                pos = _bencode_value_end(data, pos)

    if ord("0") <= token <= ord("9"):
        colon = data.find(b":", pos)
        if colon < 0:
            raise ValueError("Invalid bencode byte string")
        length_text = data[pos:colon]
        if not length_text or not length_text.isdigit():
            raise ValueError("Invalid byte string length")
        end = colon + 1 + int(length_text)
        if end > len(data):
            raise ValueError("Truncated bencode byte string")
        return end

    raise ValueError(f"Invalid bencode token at offset {pos}")



def _decode_bencode_value(data, offset=0):
    """Decode one bencode value, returning (value, next_offset)."""
    if offset >= len(data):
        raise ValueError("Unexpected end of bencode")

    token = data[offset]

    if token == ord("i"):
        end = data.find(b"e", offset + 1)
        if end < 0:
            raise ValueError("Unterminated integer")
        try:
            return int(data[offset + 1:end]), end + 1
        except ValueError as exc:
            raise ValueError("Invalid bencode integer") from exc

    if token == ord("l"):
        items = []
        pos = offset + 1
        while True:
            if pos >= len(data):
                raise ValueError("Unterminated list")
            if data[pos] == ord("e"):
                return items, pos + 1
            value, pos = _decode_bencode_value(data, pos)
            items.append(value)

    if token == ord("d"):
        result = {}
        pos = offset + 1
        while True:
            if pos >= len(data):
                raise ValueError("Unterminated dictionary")
            if data[pos] == ord("e"):
                return result, pos + 1

            key, pos = _decode_bencode_value(data, pos)
            if not isinstance(key, bytes):
                raise ValueError("Bencode dictionary key must be bytes")
            if key in result:
                raise ValueError("Duplicate Bencode dictionary key")

            value, pos = _decode_bencode_value(data, pos)
            result[key] = value

    if ord("0") <= token <= ord("9"):
        colon = data.find(b":", offset)
        if colon < 0:
            raise ValueError("Invalid byte-string length")
        raw_length = data[offset:colon]
        if not raw_length.isdigit():
            raise ValueError("Invalid byte-string length")

        end = colon + 1 + int(raw_length)
        if end > len(data):
            raise ValueError("Truncated byte string")
        return data[colon + 1:end], end

    raise ValueError(f"Invalid bencode token at offset {offset}")


def _torrent_text(value):
    if not isinstance(value, bytes):
        raise ValueError("Expected a bencode byte string")
    return value.decode("utf-8", errors="surrogateescape")


def _extract_torrent_manifest(content):
    """Extract v1 info hash, root name and per-file path/size."""
    data = bytes(content)
    decoded, end = _decode_bencode_value(data, 0)

    if end != len(data):
        raise ValueError("Unexpected trailing data after torrent metainfo")
    if not isinstance(decoded, dict):
        raise ValueError("Torrent metainfo is not a dictionary")

    info = decoded.get(b"info")
    if not isinstance(info, dict):
        raise ValueError("Missing info dictionary")

    name_raw = info.get(b"name.utf-8", info.get(b"name"))
    root_name = _torrent_text(name_raw)

    if (
        not root_name
        or root_name in {".", ".."}
        or "/" in root_name
        or "\\" in root_name
    ):
        raise ValueError("Invalid torrent root name")

    files = []

    if b"files" in info:
        entries = info.get(b"files")
        if not isinstance(entries, list) or not entries:
            raise ValueError("Invalid multi-file manifest")

        seen_paths = set()

        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("Invalid file entry")

            path_parts = entry.get(
                b"path.utf-8",
                entry.get(b"path"),
            )
            size = entry.get(b"length")

            if not isinstance(path_parts, list) or not path_parts:
                raise ValueError("Missing file path")
            if not isinstance(size, int) or size < 0:
                raise ValueError("Invalid file size")

            parts = [_torrent_text(part) for part in path_parts]

            if any(
                not part
                or part in {".", ".."}
                or "/" in part
                or "\\" in part
                for part in parts
            ):
                raise ValueError("Unsafe file path component")

            relative_path = "/".join(parts)
            if relative_path in seen_paths:
                raise ValueError("Duplicate file path in torrent")
            seen_paths.add(relative_path)

            files.append({
                "path": relative_path,
                "size_bytes": size,
            })

        multi_file = True

    else:
        size = info.get(b"length")
        if not isinstance(size, int) or size < 0:
            raise ValueError("Invalid single-file length")

        files.append({
            "path": root_name,
            "size_bytes": size,
        })
        multi_file = False

    tracker_urls = set()

    announce = decoded.get(b"announce")
    if isinstance(announce, bytes):
        tracker_urls.add(
            announce.decode("utf-8", errors="surrogateescape").strip()
        )

    announce_list = decoded.get(b"announce-list", [])
    if isinstance(announce_list, list):
        for group in announce_list:
            entries = group if isinstance(group, list) else [group]
            for entry in entries:
                if isinstance(entry, bytes):
                    tracker_urls.add(
                        entry.decode(
                            "utf-8", errors="surrogateescape"
                        ).strip()
                    )

    tracker_urls = sorted(
        url for url in tracker_urls
        if url.startswith(("http://", "https://", "udp://"))
    )

    return {
        "info_hash": _extract_torrent_info_hash(data),
        "root_name": root_name,
        "multi_file": multi_file,
        "files": files,
        "total_size_bytes": sum(f["size_bytes"] for f in files),
        "announce_urls": tracker_urls,
    }

def _extract_torrent_info_hash(content):
    """SHA-1 of the exact raw bencoded info dictionary."""
    data = bytes(content)
    decoded, total_end = _decode_bencode_value(data, 0)

    if total_end != len(data):
        raise ValueError("Unexpected trailing bytes in torrent metainfo")
    if not isinstance(decoded, dict):
        raise ValueError("Torrent metainfo is not a dictionary")
    if not isinstance(decoded.get(b"info"), dict):
        raise ValueError("Torrent has no valid info dictionary")

    pos = 1
    while pos < len(data):
        if data[pos] == ord("e"):
            break

        key, value_start = _decode_bencode_value(data, pos)
        if not isinstance(key, bytes):
            raise ValueError("Top-level Bencode key is not bytes")

        _, value_end = _decode_bencode_value(data, value_start)

        if key == b"info":
            return hashlib.sha1(data[value_start:value_end]).hexdigest().lower()

        pos = value_end

    raise ValueError("Torrent has no raw info dictionary")


def update_trackers(node):
    """Submit one guarded re-announce request per node."""

    try:
        if getattr(node, "is_connected", True) is False:
            print(
                f"⏭️ [{node.name}] Re-announce skipped: "
                "node is disconnected"
            )
            return False

        reannounce = getattr(
            node,
            "reannounce_all",
            None
        )

        if not callable(reannounce):
            print(
                f"⚠️ [{node.name}] "
                "No re-announce implementation"
            )
            return False

        success = bool(reannounce())

        if success:
            print(
                f"✅ [{node.name}] "
                "Re-announce request accepted"
            )
        else:
            print(
                f"⚠️ [{node.name}] "
                "Re-announce request failed"
            )

        return success

    except Exception as e:
        print(
            f"⚠️ [{getattr(node, 'name', 'Unknown')}] "
            f"Update trackers failed: {type(e).__name__}: {e}"
        )
        return False

def safe_add_torrent(node, content, site):
    if globals().get(
        "PHASE_G_ALLOW_NORMAL_TORRENT_ADD", False
    ) is not True:
        print(
            f"🔒 [{getattr(node, 'name', 'Unknown')}] "
            "Normal Torrent Add blocked (Phase G)"
        )
        return False

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
    _PROTECTED_CACHE_STORAGE = None

    def __init__(self, node_obj, node_clean_cfg, global_clean_cfg):
        self.node = node_obj
        raw_cfg = (
            node_clean_cfg
            if isinstance(node_clean_cfg, dict)
            else {}
        )
        self.node_cfg = raw_cfg

        nested = raw_cfg.get("clean_settings")
        if isinstance(nested, dict):
            self.clean_settings = nested
            self.node_enable = raw_cfg.get(
                "enable", raw_cfg.get("ENABLE")
            )
        else:
            # Some call sites pass clean_settings directly.
            self.clean_settings = raw_cfg
            self.node_enable = None

        self.global_cfg = global_clean_cfg or {}
        self.site_key = getattr(self.node, "site_key", "UNKNOWN")
        self._db_cache = None

    @classmethod
    def clear_cache(cls):
        """ล้าง Cache ทั้งหมดเพื่อให้โหลดใหม่จากไฟล์ DB"""
        cls._PROTECTED_CACHE_STORAGE = None
        print("🧹 [NodeCleaner] Cache ได้รับการเคลียร์เรียบร้อยแล้ว")

    @classmethod
    def get_global_protected_set(cls):
        """โหลดและแคชข้อมูล Hash ในระดับ Class จาก Unified DB ของทุกเว็บไซต์"""
        if cls._PROTECTED_CACHE_STORAGE is None:
            all_dbs = load_all_db()  
            cls._PROTECTED_CACHE_STORAGE = set()
            for db_name, db_data in all_dbs.items():
                for data in db_data.values():
                    # ตรวจสอบสถานะที่ต้องป้องกันจากโครงสร้าง Unified DB
                    if data.get("status") == "PROTECTED":
                        h = data.get("hash")
                        if h and h != "UNKNOWN":
                            cls._PROTECTED_CACHE_STORAGE.add(h.lower())
        return cls._PROTECTED_CACHE_STORAGE
    
    @classmethod
    def update_protected_cache_item(cls, t_hash, status):
        """
        อัปเดตเฉพาะรายการที่มีการเปลี่ยนแปลง (Incremental Update)
        ไม่ต้องโหลด DB ใหม่ทั้งหมด
        """
        if cls._PROTECTED_CACHE_STORAGE is None:
            # ถ้ายังไม่มี Cache เลย ให้สั่งโหลดใหม่แบบปกติ
            cls.get_global_protected_set()
            return

        h = t_hash.lower()
        if status == "PROTECTED":
            cls._PROTECTED_CACHE_STORAGE.add(h)
        elif h in cls._PROTECTED_CACHE_STORAGE:
            cls._PROTECTED_CACHE_STORAGE.remove(h)

    def _update_db_status(self, t_hash, new_status):
        target = str(t_hash or "").strip().lower()
        if not target:
            print("⚠️ [DB Sync] ปฏิเสธการอัปเดต: hash ว่าง")
            return

        matches = []
        for db_name, db in load_all_db().items():
            if not isinstance(db, dict):
                continue

            for key, record in db.items():
                if not isinstance(record, dict):
                    continue

                record_hash = str(record.get("hash") or "").strip().lower()
                key_hash = str(key or "").strip().lower()

                if target == record_hash or target == key_hash:
                    matches.append((db_name, db, record))

        # Fail closed: ไม่อัปเดตหากไม่มี record หรือพบหลาย record
        if len(matches) != 1:
            print(
                f"⚠️ [DB Sync] ไม่ได้อัปเดตสถานะ {target}: "
                f"พบ matching records={len(matches)}; ต้องเป็น 1"
            )
            return

        db_name, db, record = matches[0]
        record["status"] = new_status
        record["deleted_at"] = get_now().strftime("%Y-%m-%d %H:%M")

        save_db(db_name, db)
        NodeCleaner.update_protected_cache_item(target, new_status)

        print(
            f"🚀 [DB Sync] อัปเดต {target} "
            f"ใน DB={db_name} เป็น {new_status}"
        )

    def check_torrent_permission(self, t_hash):
        target = str(t_hash or "").strip().lower()
        if not target:
            return "UNKNOWN"

        matches = []

        # ใช้ DB ปัจจุบันโดยตรง ไม่พึ่ง Protected Cache ที่อาจล้าสมัย
        for db_name, db in load_all_db().items():
            if not isinstance(db, dict):
                continue

            for key, record in db.items():
                if not isinstance(record, dict):
                    continue

                record_hash = str(record.get("hash") or "").strip().lower()
                key_hash = str(key or "").strip().lower()

                if target == record_hash or target == key_hash:
                    status = str(record.get("status") or "").strip().upper()
                    matches.append((db_name, str(key), status or "UNKNOWN"))

        if not matches:
            return "NOT_FOUND"

        # หากพบสถานะ PROTECTED ที่ใดก็ตาม ต้องปฏิเสธการลบ
        if any(status == "PROTECTED" for _, _, status in matches):
            return "PROTECTED"

        # Hash เดียวกันปรากฏหลาย records: ไม่เดาว่า record ใดเป็นเจ้าของสถานะ
        if len(matches) != 1:
            print(
                f"🔒 [GUARD] Ambiguous DB records for {target}: "
                f"{len(matches)} matches; refusing deletion"
            )
            return "AMBIGUOUS"

        db_name, record_key, status = matches[0]
        print(
            f"🔎 [Permission] hash={target} "
            f"DB={db_name} key={record_key} status={status}"
        )
        return status

    def _get_node_free_gb(self):
        try:
            val = getattr(self.node, 'free_gb', 100.0)
            return float(val) if val is not None else 100.0
        except Exception:
            return 100.0

    def _purge_qbit_get_json(self, endpoint, params):
        """Read-only qBittorrent API request with one auth retry."""

        node = self.node
        requester = getattr(node, "_execute_request", None)
        base_url = str(getattr(node, "url", "") or "").rstrip("/")

        if not callable(requester) or not base_url:
            return None, "QBIT_API_UNAVAILABLE"

        def fetch():
            return requester(
                "GET",
                f"{base_url}{endpoint}",
                params=params,
                timeout=12,
            )

        try:
            response = fetch()

            if response is not None and response.status_code in (401, 403):
                login = getattr(node, "login", None)

                if callable(login) and login():
                    response = fetch()

            if response is None:
                return None, "NO_RESPONSE"

            if response.status_code != 200:
                return None, f"HTTP_{response.status_code}"

            try:
                payload = response.json()
            except Exception:
                return None, "INVALID_JSON"

            return payload, "OK"

        except Exception as exc:
            return None, f"API_EXCEPTION:{type(exc).__name__}"

    def _check_purge_authorization(self, t_hash):
        """
        Phase A authorization.

        - Requires one exact Mapping record.
        - Requires a fresh complete qBittorrent inventory result.
        - Requires recognized live tracker associations.
        - BEARBIT remains blocked until Phase B freshness verification.
        - Does not mutate Mapping, DB, trackers or torrents.
        """

        target = str(t_hash or "").strip().lower()

        if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", target):
            return False, "INVALID_HASH", None, []

        # 1. Verify live qBittorrent inventory.
        inventory, error = self._purge_qbit_get_json(
            "/api/v2/torrents/info",
            {"hashes": target},
        )

        if not isinstance(inventory, list):
            return False, f"INVENTORY_{error}", None, []

        exact = [
            item for item in inventory
            if (
                isinstance(item, dict)
                and str(item.get("hash") or "").strip().lower() == target
            )
        ]

        if len(exact) != 1:
            return False, f"INVENTORY_MATCHES_{len(exact)}", None, []

        live = exact[0]

        try:
            progress = float(live.get("progress", 0))
            amount_left = float(live.get("amount_left", -1))
        except (TypeError, ValueError, OverflowError):
            return False, "INVALID_COMPLETION_METADATA", None, []

        if not (0.999999 <= progress <= 1.000001):
            return False, "DOWNLOAD_NOT_COMPLETE", live, []

        if amount_left != 0:
            return False, "AMOUNT_LEFT_NOT_ZERO", live, []

        # 2. Read live Tracker associations.
        tracker_rows, error = self._purge_qbit_get_json(
            "/api/v2/torrents/trackers",
            {"hash": target},
        )

        if not isinstance(tracker_rows, list) or not tracker_rows:
            return False, f"TRACKER_API_{error}", live, []

        aliases = globals().get("PURGE_TRACKER_HOST_ALIASES", {})
        tracker_sites = set()
        unknown_hosts = set()

        for row in tracker_rows:
            if not isinstance(row, dict):
                return False, "INVALID_TRACKER_ROW", live, []

            raw_url = str(row.get("url") or "").strip()

            pseudo = re.fullmatch(
                r"\*\*\s*\[(DHT|PEX|LSD)\]\s*\*\*",
                raw_url,
                flags=re.IGNORECASE,
            )

            if pseudo:
                continue

            try:
                parsed = urlsplit(raw_url)
                hostname = (parsed.hostname or "").lower().rstrip(".")
                scheme = parsed.scheme.lower()
            except Exception:
                return False, "INVALID_TRACKER_URL", live, []

            if not hostname or scheme not in {"http", "https", "udp"}:
                return False, "UNCLASSIFIED_TRACKER_ROW", live, []

            matched_site = None

            for domain, site_name in aliases.items():
                domain = str(domain).strip().lower().rstrip(".")

                if (
                    hostname == domain
                    or hostname.endswith("." + domain)
                ):
                    matched_site = str(site_name).strip().upper()
                    break

            if not matched_site:
                unknown_hosts.add(hostname)
            else:
                tracker_sites.add(matched_site)

        if unknown_hosts:
            return (
                False,
                "UNKNOWN_TRACKER_HOST:" + ",".join(sorted(unknown_hosts)),
                live,
                sorted(tracker_sites),
            )

        if not tracker_sites:
            return False, "NO_RECOGNIZED_SITE_TRACKER", live, []

        # 3. Read the exact Mapping record.
        mapping_path = globals().get("MAPPING_FILE")

        if not mapping_path or not os.path.isfile(mapping_path):
            return False, "MAPPING_FILE_UNAVAILABLE", live, []

        try:
            with open(mapping_path, "r", encoding="utf-8") as handle:
                mapping = json.load(handle)
        except Exception:
            return False, "MAPPING_READ_ERROR", live, []

        if not isinstance(mapping, dict):
            return False, "MAPPING_INVALID_ROOT", live, []

        records = mapping.get("torrents")

        if not isinstance(records, list):
            return False, "MAPPING_INVALID_RECORDS", live, []

        mapping_matches = []

        for record in records:
            if not isinstance(record, dict):
                continue

            client_info = record.get("client_info")

            if not isinstance(client_info, dict):
                continue

            record_hash = str(
                client_info.get("hash") or ""
            ).strip().lower()

            if record_hash == target:
                mapping_matches.append(record)

        if len(mapping_matches) != 1:
            return (
                False,
                f"MAPPING_MATCHES_{len(mapping_matches)}",
                live,
                sorted(tracker_sites),
            )

        record = mapping_matches[0]

        # 4. Validate verified source and known_sites.
        source_verified = record.get("source_site_verified") is True
        source_site = ""
        source_bearbit_hint = False

        raw_source = str(record.get("source_site") or "").strip()

        if source_verified:
            source_site = _canonical_mapping_site(raw_source)

            if not source_site:
                return False, "VERIFIED_SOURCE_UNRECOGNIZED", live, sorted(tracker_sites)

        elif raw_source.casefold() == "bearbit":
            # An unverified BEARBIT label is a protection signal,
            # never proof of source provenance.
            source_bearbit_hint = True

        raw_known = record.get("known_sites", [])

        if raw_known is None:
            raw_known = []

        if not isinstance(raw_known, (list, tuple, set)):
            return False, "INVALID_KNOWN_SITES", live, sorted(tracker_sites)

        known_sites = set()

        for value in raw_known:
            canonical = _canonical_mapping_site(value)

            if not canonical:
                return False, "UNKNOWN_KNOWN_SITE", live, sorted(tracker_sites)

            known_sites.add(canonical)

        # 5. Strictly inspect the BEARBIT DB.
        # A missing/corrupt DB cannot be interpreted as no obligation.
        db_dir = globals().get("DB_DIR")

        if not db_dir or not os.path.isdir(db_dir):
            return False, "BEARBIT_DB_DIRECTORY_UNAVAILABLE", live, sorted(tracker_sites)

        try:
            db_files = [
                os.path.join(db_dir, filename)
                for filename in os.listdir(db_dir)
                if filename.casefold() == "bearbit_link_db.json"
            ]
        except Exception:
            return False, "BEARBIT_DB_DIRECTORY_READ_ERROR", live, sorted(tracker_sites)

        if len(db_files) != 1:
            return False, "BEARBIT_DB_FILE_MISSING_OR_AMBIGUOUS", live, sorted(tracker_sites)

        try:
            with open(db_files[0], "r", encoding="utf-8") as handle:
                bearbit_db = json.load(handle)
        except Exception:
            return False, "BEARBIT_DB_READ_ERROR", live, sorted(tracker_sites)

        if not isinstance(bearbit_db, dict):
            return False, "BEARBIT_DB_INVALID", live, sorted(tracker_sites)

        bearbit_matches = []

        for key, item in bearbit_db.items():
            if not isinstance(item, dict):
                continue

            key_hash = str(key or "").strip().lower()
            item_hash = str(item.get("hash") or "").strip().lower()

            if target == key_hash or target == item_hash:
                bearbit_matches.append(item)

        if len(bearbit_matches) > 1:
            return False, "BEARBIT_DB_AMBIGUOUS", live, sorted(tracker_sites)

        bearbit_related = (
            "BEARBIT" in tracker_sites
            or source_site == "BEARBIT"
            or source_bearbit_hint
            or "BEARBIT" in known_sites
            or bool(bearbit_matches)
        )

        if bearbit_related:
            if not bearbit_matches:
                return False, "BEARBIT_RECORD_MISSING", live, sorted(tracker_sites)

            # Phase A deliberately keeps all BEARBIT-associated torrents
            # blocked until Phase B can prove a fresh successful H&R/Quest sync.
            return (
                False,
                "BEARBIT_REQUIRES_PHASE_B_SYNC",
                live,
                sorted(tracker_sites),
            )

        # 6. General authorization for recognized non-BEARBIT sites.
        general_sites = tracker_sites - {"BEARBIT"}

        if not general_sites:
            return False, "NO_GENERAL_TRACKER_ASSOCIATION", live, []

        site_label = "+".join(sorted(general_sites))

        return (
            True,
            f"GENERAL_ASSOCIATED:{site_label}",
            live,
            sorted(tracker_sites),
        )

    def _hard_purge_sequence(self, t_hash):
        """Final fail-closed authorization gate. Phase A is Dry Run only."""

        try:
            allowed, reason, live, sites = (
                self._check_purge_authorization(t_hash)
            )
        except Exception as exc:
            print(
                f"🔒 [GUARD] Authorization exception: "
                f"{type(exc).__name__}; deletion refused."
            )
            return False

        if not allowed:
            print(
                f"🔒 [GUARD] ปฏิเสธการลบ: {reason} - {t_hash}"
            )
            return False

        site_label = ",".join(sites)

        print(
            f"🔎 [PURGE AUTH] ALLOWED "
            f"| class={reason} "
            f"| trackers={site_label} "
            f"| progress={live.get('progress')} "
            f"| amount_left={live.get('amount_left')}"
        )

        if globals().get("PURGE_AUTH_DRY_RUN", True) is True:
            print(
                f"🧪 [PURGE DRY-RUN] WOULD_ALLOW "
                f"| {reason} | {t_hash} "
                f"| No torrent mutation performed."
            )
            return False

        try:
            print(f"🔄 [PURGE] Starting deletion sequence: {t_hash}")

            self.node.reannounce_torrent(t_hash)
            self.node.stop_torrent(t_hash)

            success = self.node.delete_torrent(t_hash)

            if success:
                self._update_db_status(t_hash, "DELETED")
                print(f"✅ [PURGE] ลบสำเร็จ: {t_hash}")
                return True

            print(f"❌ [PURGE] Delete API failed: {t_hash}")
            return False

        except Exception as exc:
            print(
                f"⚠️ [PURGE] Sequence failed: "
                f"{type(exc).__name__}"
            )
            return False

    def process(self, force_emergency=False):
        # 1. เช็คสถานะการเปิดใช้งาน
        node_enable = self.node_enable
        global_enable = self.global_cfg.get('enable', self.global_cfg.get('ENABLE', False))
        # Global is the master switch.
        # An explicit per-node clean_settings.enable=False must disable Cleaner.
        is_enabled = bool(global_enable) and (
            node_enable is None or bool(node_enable)
        )

        print(f"⚙️ [Cleaner Engine] Node: {self.node.name} | Status: {'ACTIVE' if is_enabled else 'DISABLED'}")

        if not is_enabled:
            print(f"💤 [Cleaner Bypass] Skipped [{self.node.name}]")
            return

        print(f"🔄 [{self.node.name}] เริ่มต้นรอบการทำงาน (Cycle Start)...")
        
        # 2. จัดการโหมด Emergency หรือ Normal
        current_free = self._get_node_free_gb()
        if force_emergency or (current_free < 10.0):
            print(f"🚨 [{self.node.name}] สถานะวิกฤต: พื้นที่เหลือ {current_free:.2f} GB")
            # ใช้ self.smart_reclaim_process หากคุณย้ายมันเข้ามาเป็น Method แล้ว
            reclaim_ready = self.smart_reclaim_process(
                required_gb=10.0,
                is_emergency=True
            )

            if reclaim_ready:
                print(
                    f"✅ [{self.node.name}] "
                    "ยืนยันพื้นที่ว่างผ่านเป้าหมายแล้ว"
                )
            else:
                print(
                    f"⚠️ [{self.node.name}] "
                    "ยังไม่สามารถยืนยันพื้นที่ตามเป้าหมายได้ "
                    "(Dry Run / Guard / API / Insufficient Space)"
                )

            return

        # 3. โหมด Idle Cleanup (ยุบรวมเหลือเมธอดเดียว)
        print(f"🔍 [{self.node.name}] เริ่มสแกน Idle Cleanup...")
        grouped_logs = self._clean_normal()
        
        # 4. ส่ง Notification
        if grouped_logs:
            self._notify_results(grouped_logs)
            print(f"📝 [{self.node.name}] ส่งสรุปผลการลบเรียบร้อย")
        else:
            print(f"ℹ️ [{self.node.name}] ไม่พบทอร์เรนต์ที่เข้าเงื่อนไข")
            
        print(f"🏁 [{self.node.name}] จบรอบการทำงาน.")

    def _notify_results(self, active_logs):
        """แยก Logic การแจ้งเตือนออกมาเพื่อให้ Clean ขึ้น"""
        active_logs = {r: d for r, d in active_logs.items() if d.get("torrents")}
        if not active_logs: return

        emoji_map = {"Max Time Exceeded": "🚨", "Idle Dead": "💤", "Target Reached": "💰"}
        notify_lines = []
        for reason, log_data in active_logs.items():
            emoji = emoji_map.get(reason, "🧹")
            notify_lines.append(f"{emoji} <b>[{reason}]</b> {log_data['header']}")
            notify_lines.extend([f"  {emoji} {line}" for line in log_data["torrents"]])

        msg = f"<b>🧹 Cleanup Summary</b> [{self.node.name}]:\n" + "\n".join(notify_lines)
        send_func = globals().get('send_notify')
        if callable(send_func):
            asyncio.create_task(send_func(msg))
        else:
            print(f"📢 Notification:\n{msg}")

    def _should_remove(self, ratio, age_hours, up_speed, leechers):
        def get_cfg_value(key, default):
            clean_sets = self.clean_settings
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

    def _clean_normal(self):
        res_grouped = {}
        try:
            raw_torrents = self.node.get_all_torrents_info()
            if not raw_torrents:
                return {}

            now = time.time()
            for t in raw_torrents:
                t_hash = t.get('hash')
                if not t_hash: continue

                # ใช้ Key ที่คุณสร้างไว้ใน get_all_torrents_info คือ 'ts_finished'
                is_completed = t.get('is_rt_complete', False)
                ts_finished = t.get('ts_finished', 0)
                
                # ถ้ายังไม่เสร็จ หรือไม่มีเวลาจบงาน ให้ข้าม
                if not is_completed or ts_finished <= 0: 
                    continue

                try:
                    age_hours = (now - float(ts_finished)) / 3600
                    ratio = float(t.get('ratio', 0))
                    # หมายเหตุ: ถ้าต้องการ up_speed/num_leechers ต้องดึงเพิ่มใน multicall2
                    up_speed = float(t.get('up_speed', 0)) 
                    leechers = float(t.get('leechers', 0))
                except (ValueError, TypeError):
                    continue

                remove_check = self._should_remove(ratio, age_hours, up_speed, leechers)
                if remove_check:
                    reason_key, header_msg = remove_check
                    if self._hard_purge_sequence(t_hash):
                        raw_name = t.get('name', 'Unknown')
                        name_safeguard = raw_name[:27] + "..." if len(raw_name) > 27 else raw_name
                        line = f"{name_safeguard} (R:{ratio:.2f}, {age_hours:.1f}h)"
                        
                        if reason_key not in res_grouped:
                            res_grouped[reason_key] = {"header": header_msg, "torrents": []}
                        res_grouped[reason_key]["torrents"].append(line)
        except Exception as e:
            print(f"⚠️ [{self.node.name}] Cleanup Fetch Error: {e}")
        return res_grouped

    def smart_reclaim_process(self, required_gb, is_emergency=False):
        """
        ปรับปรุงเป็น Instance Method ของ NodeCleaner
        เพิ่มเงื่อนไข: โหลดเสร็จแล้วอย่างน้อย 1 ชม. และเรโชอย่างน้อย 1.0
        """
        if getattr(self.node, "allow_space_reclaim", True) is not True:
            print(
                f"🛡️ [{self.node.name}] Space reclaim disabled by node config; "
                "no reclaim scan or torrent action performed"
            )
            return False

        try:
            self.node.refresh_status()
            buffer_gb = 5.0 if is_emergency else 2.5
            target_free = required_gb + buffer_gb
            current_free = float(getattr(self.node, 'free_gb', 0.0))
            
            if current_free >= target_free and not is_emergency:
                return True

            raw_torrents = self.node.get_all_torrents_info()

            # None = inventory API failure; fail closed.
            if raw_torrents is None:
                print(
                    f"⚠️ [{self.node.name}] Reclaim aborted: "
                    "inventory API verification failed"
                )
                return False

            # Empty inventory is a valid API response.
            if not raw_torrents:
                if not self.node.refresh_status():
                    print(
                        f"⚠️ [{self.node.name}] "
                        "Cannot verify final free space"
                    )
                    return False

                final_free = self._get_node_free_gb()
                space_ready = final_free >= target_free

                print(
                    f"📊 [RECLAIM RETURN AUDIT] "
                    f"free={final_free:.2f}GB "
                    f"target={target_free:.2f}GB "
                    f"ready={space_ready}"
                )
                return space_ready

            # คัดกรองงานที่ลบได้
            scannable_torrents = []
            current_ts = time.time()

            for t in raw_torrents:
                t_hash = t.get('hash')
                if self.check_torrent_permission(t_hash) == "PROTECTED":
                    continue
    
                # 1. กรองเวลาเริ่มต้นเพิ่มเข้าคิว (Grace Period พื้นฐาน 45 นาที)
                ts_init = t.get('added_on', t.get('ts_init', 0))
                if (current_ts - ts_init) < 2700:
                    continue

                # 2. ดึงค่าสถานะไฟล์
                t_size_gb = t.get('size_bytes', 0) / (1024**3)
                t_ratio = t.get('ratio', 0.0)
                try:
                    progress_value = float(t.get('progress', 0))
                    amount_left_value = float(t.get('amount_left', -1))
                except (TypeError, ValueError, OverflowError):
                    progress_value = 0.0
                    amount_left_value = -1.0

                is_completed = (
                    progress_value >= 0.999999
                    and amount_left_value == 0
                )
    
                # ดึงค่าความนิ่ง
                upspeed = float(t.get('up_speed', 0))
                leechers = float(t.get('leechers', 0))
                is_stagnant = (upspeed == 0 and leechers == 0)

                # --- เช็คเวลาที่โหลดเสร็จแล้ว (Completion Time) ---
                # qBittorrent มักใช้ 'completion_on', ถ้าไม่มีให้ fallback ไปใช้เวลาปัจจุบันหรือเวลาแอด
                ts_completed = t.get('completion_on', t.get('completed_on', 0))
                if ts_completed == 0 and is_completed:
                    # ถ้า API ไม่ส่งเวลาเสร็จมา แต่สถานะบอกว่าเสร็จแล้ว ให้ประเมินว่าเพิ่งเสร็จ หรือใช้เวลาแอดแทน
                    ts_completed = ts_init 
                
                # เช็คว่าโหลดเสร็จมาแล้วอย่างน้อย 1 ชั่วโมง (3600 วินาที) หรือไม่
                has_matured_after_complete = is_completed and ((current_ts - ts_completed) >= 3600)

                t['_calculated_size_gb'] = t_size_gb
                t['_calculated_ratio'] = t_ratio

                if is_completed:
                    # เงื่อนไขการลบใหม่: 
                    # - โหมดฉุกเฉิน (ขนาดถึงเกณฑ์) OR
                    # - (โหลดเสร็จมาแล้ว >= 1 ชม. AND เรโช >= 1.0 AND ขนาด >= 1.0 GB) OR
                    # - (ไฟล์นิ่งสนิท AND ขนาด >= 1.0 GB)
                    can_remove_completed = (is_emergency and t_size_gb >= 1.0) or \
                                           (has_matured_after_complete and t_ratio >= 1.0 and t_size_gb >= 1.0) or \
                                           (is_stagnant and t_size_gb >= 1.0)
        
                    if can_remove_completed:
                        scannable_torrents.append(t)
            


            # 3. ให้คะแนนความคุ้มค่า (Scoring) เพื่อจัดลำดับการลบ
            def get_priority_score(t):
                score = t['_calculated_size_gb']
                if (t.get('up_speed', 0) == 0 and t.get('leechers', 0) == 0):
                    score *= 10.0
                return score

            scannable_torrents.sort(key=get_priority_score, reverse=True)
            
            # เลือกเป้าหมาย
            targets = scannable_torrents[:15 if is_emergency else 6]
            
            # 4. Authorization / Dry-Run / Purge
            for t in targets:
                print(
                    f"🔎 [PURGE CANDIDATE] "
                    f"{t.get('name', 'Unknown')} "
                    f"| Size: {t['_calculated_size_gb']:.2f}GB "
                    f"| Ratio: {t['_calculated_ratio']:.2f}"
                )
                self._hard_purge_sequence(t['hash'])

            # RECLAIM_RETURN_AUDIT_V1
            # Never report success based on attempted candidates.
            # Refresh node status and verify actual available space.
            if not self.node.refresh_status():
                print(
                    f"⚠️ [{self.node.name}] "
                    "Reclaim result unverifiable: status refresh failed"
                )
                return False

            final_free = self._get_node_free_gb()
            space_ready = final_free >= target_free

            print(
                f"📊 [RECLAIM RETURN AUDIT] "
                f"free={final_free:.2f}GB "
                f"target={target_free:.2f}GB "
                f"ready={space_ready} "
                f"dry_run={globals().get('PURGE_AUTH_DRY_RUN', True)}"
            )

            if not space_ready:
                print(
                    f"⚠️ [{self.node.name}] "
                    "Reclaim did not reach required free-space target"
                )

            return space_ready
            
        except Exception as e:
            print(f"❌ [SMART RECLAIM] Error: {str(e)}")
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
    if page is None:
        print("🚨 ตรวจพบ page เป็น None, ส่งสัญญาณรีเซ็ต...")
        return False # ลูปหลักจะได้รับค่านี้แล้วไปสร้าง instance ใหม่
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
        await asyncio.sleep(2) 

        js_code = """
        (() => {
            // 1. ตรวจสอบรูปแบบ A: ปุ่ม a[onclick] (แบบเดิม)
            const tdSayThanks = document.querySelector('td#saythanks');
            if (tdSayThanks) {
                const btnA = tdSayThanks.querySelector('a[onclick*="sndReq"]');
                if (btnA) { btnA.click(); return "clicked_a"; }
            }

            // 2. ตรวจสอบรูปแบบ B: ปุ่ม input[type="submit"] ในฟอร์ม (แบบใหม่)
            // ค้นหา div#ajax ที่มีฟอร์มขอบคุณ
            const divAjax = document.querySelector('div#ajax');
            if (divAjax) {
                const inputBtn = divAjax.querySelector('input[type="submit"][name="submit"]');
                if (inputBtn) {
                    inputBtn.click();
                    return "clicked_input";
                }
            }

            return "no_button";
        })()
        """
        
        status = await page.evaluate(js_code)

        if "clicked" in status:
            print(f"✅ กดขอบคุณสำเร็จ (รูปแบบ: {status})")
            return True
        else:
            print("⏭️ ไม่พบปุ่มขอบคุณในหน้าเว็บ (อาจจะขอบคุณไปแล้วหรือไม่มีระบบนี้)")
            return False
            
    except Exception as e:
        print(f"❌ Error ในการกดขอบคุณ: {e}")
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
    - [Anomaly Guard] ป้องกันสถิติลดลงหรือกระโดดผิดปกติจากเว็บต้นทางรวน (เช่น ค่าส่งมาเป็น 0 หรือดรอปฮวบ)
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
        def clean_float(val):
            if isinstance(val, (int, float)):
                return float(val)
            try:
                return float(str(val).replace(',', '').strip())
            except (ValueError, TypeError):
                return 0.0

        raw_up = clean_float(current_data.get('up', 0))
        raw_dl = clean_float(current_data.get('dl', 0))
        raw_bonus = clean_float(current_data.get('bonus', 0))
        raw_ratio = clean_float(current_data.get('ratio', 0))

        # 3. 🛡️ Anomaly Guard: ป้องกันสถิติลดลง/กระโดดผิดปกติจากเว็บรวนหรือรีเซ็ต
        existing_keys = sorted(all_history[site_name].keys())
        if existing_keys:
            latest_prev_key = existing_keys[-1]
            prev_data = all_history[site_name][latest_prev_key]
            
            prev_up = prev_data.get('up', 0)
            prev_dl = prev_data.get('dl', 0)
            
            # ถ้า Upload ปัจจุบันน้อยกว่าค่าเดิมอย่างมีนัยสำคัญ (เช่น เว็บพังส่งค่า 0 หรือหลุด) ให้ใช้ค่าเดิมกันพัง
            if raw_up < prev_up * 0.95:  # ยอมให้ลดลงได้ไม่เกิน 5% (หรือปรับเงื่อนไขตามความเหมาะสม)
                print(f"⚠️ [{site_name}] Anomaly Guard: ตรวจพบยอด Upload ลดลงผิดปกติจาก {prev_up} เหลือ {raw_up} -> ใช้ค่าเดิมแทน")
                raw_up = prev_up
            
            # ถ้า Download ลดลงผิดปกติ ให้ล็อกค่าเดิมไว้เช่นกัน
            if raw_dl < prev_dl * 0.95:
                print(f"⚠️ [{site_name}] Anomaly Guard: ตรวจพบยอด Download ลดลงผิดปกติจาก {prev_dl} เหลือ {raw_dl} -> ใช้ค่าเดิมแทน")
                raw_dl = prev_dl

        # บันทึกข้อมูล Snapshot ประจำชั่วโมง
        all_history[site_name][timestamp_key] = {
            'username': current_data.get('username', 'N/A'),
            'ratio': raw_ratio,
            'up': raw_up,
            'dl': raw_dl,
            'bonus': raw_bonus,
            'raw_time': now.strftime("%Y-%m-%d %H:%M:%S")
        }

        # 4. ⏱️ ปรับปรุงระบบ Retention (จำกัดความยาวประวัติ 31 วัน / 744 ชั่วโมง)
        site_keys = sorted(all_history[site_name].keys())
        while len(site_keys) > 744:
            oldest_key = site_keys.pop(0)  # ดึงคีย์ที่เก่าที่สุดออกทีละตัว
            del all_history[site_name][oldest_key]

        # 5. Save แบบ Atomic ป้องกันไฟล์พังเมื่อบอทโดนตัดการทำงาน
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
    พร้อมระบบ Anomaly Guard ป้องกัน Cache พังเมื่อเว็บรวนส่งค่า 0 หรือค่าดรอป
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

    # แปลงค่าปัจจุบันเตรียมไว้เช็ค
    curr_up = parse_size(current_data.get('up', '0'))
    curr_dl = parse_size(current_data.get('dl', '0'))
    
    def clean_float(val):
        try: return float(str(val).replace(',', ''))
        except: return 0.0

    curr_bonus = clean_float(current_data.get('bonus', 0))

    if old_data:
        try:
            old_up = parse_size(old_data.get('up', '0'))
            old_dl = parse_size(old_data.get('dl', '0'))
            old_bonus = clean_float(old_data.get('bonus', 0))

            # 🛡️ Anomaly Guard: ถ้าค่าปัจจุบันดรอปลงผิดปกติ (เช่นเว็บส่ง 0 หรือรวน) ห้ามเอาไปทับ Cache เก่า
            if curr_up == 0.0 and old_up > 0:
                print(f"⚠️ [{site_name}] Cache Guard: ตรวจพบยอด Upload เป็น 0 -> ใช้ค่าเก่า ({old_up}) คำนวณและดึง Cache เดิมไว้")
                curr_up = old_up
                current_data['up'] = old_data.get('up')  # ใช้ค่าเดิมกัน Cache พัง
            elif curr_up < old_up * 0.95:
                print(f"⚠️ [{site_name}] Cache Guard: ยอด Upload ลดลงผิดปกติ -> ใช้ค่าเดิมแทน")
                curr_up = old_up
                current_data['up'] = old_data.get('up')

            if curr_dl == 0.0 and old_dl > 0:
                curr_dl = old_dl
                current_data['dl'] = old_data.get('dl')
            elif curr_dl < old_dl * 0.95:
                curr_dl = old_dl
                current_data['dl'] = old_data.get('dl')

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

    # 3. อัปเดต Cache เฉพาะส่วนของ Site นี้ และบันทึกกลับแบบ Atomic (ใช้ข้อมูลที่ผ่าน Guard แล้ว)
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

class BotContext:
    def __init__(self, active_nodes, dl_session, seen_hashes, seen_ids, global_clean):
        self.active_nodes = active_nodes
        self.dl_session = dl_session
        self.seen_hashes = seen_hashes
        self.seen_ids = seen_ids
        self.global_clean = global_clean
        
async def get_site_stats(page: uc.Tab, site_cfg: dict, ctx: BotContext) -> str:
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
        # 🎯 0. [เพิ่มใหม่] จัดการเคลียร์สถานะคอนฟิกหากไม่ใช่เว็บ BEARBIT
        # -------------------------------------------------------------------------
        if 'bearbit' not in site.lower():
            if 'reset_bot_config_to_default' in globals():
                await asyncio.to_thread(reset_bot_config_to_default)

        # -------------------------------------------------------------------------
        # 🎯 1. ทำภารกิจกวาดล้าง (Clear Notifications & Auto-Vote)
        # -------------------------------------------------------------------------
        if 'bearbit' in site.lower():
            try:
                # 🛡️ ระบบเคลียร์แจ้งเตือน
                if 'clear_bearbit_notifications' in globals():
                    await clear_bearbit_notifications(page, base_url, site_name=site)
                
                # 🛡️ [เพิ่มใหม่] Sync H&R สดๆ จากหน้าเว็บเข้า DB ทุกครั้งที่เช็คสถิติ
                # หมายเหตุ: ใน nodriver 'page' คือ tab ซึ่งเราสามารถส่งเป็น context หรือใช้ session 
                # แต่เนื่องจากเราใช้ nodriver อยู่แล้ว เราสามารถดึง source ผ่าน page ได้
                hr_html = await page.get_content()
                await sync_hr_with_web(site_key=site, page=page, base_url=base_url, ctx=ctx)
                await sync_seed_quest_with_web(site_key=site, page=page, base_url=base_url, ctx=ctx)

            except Exception as sub_err:
                print(f"⚠️ [{site}] ระบบย่อยขัดข้อง: {sub_err}")

            try:
                # 🛡️ ระบบ Auto-Vote
                if 'auto_vote_snatched' in globals():
                    await auto_vote_snatched(page, base_url, site_name=site)
            except Exception as vote_sub_err:
                print(f"⚠️ [{site}] ระบบ Auto-Vote ขัดข้อง: {vote_sub_err}")
                
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
        curr_up    = extract(r"(?:Uploaded|Upload):?\s*([\d\.,]+\s*[KMGTPE]B)", text, None)
        curr_dl    = extract(r"(?:Downloaded|Download):?\s*([\d\.,]+\s*[KMGTPE]B)", text, None)
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

# แยก Regex ออกมาให้ชัดเจน
ID_REGEX = re.compile(r"id=(\d+)", re.I)
DL_REGEX = re.compile(r"download(new)?\.php", re.I)
BAD_LINK_PATTERN = re.compile(r"ndonatedn|vip|donate|/nDonatedN\.php", re.I)
SIZE_PATTERN = re.compile(r"(\d+(?:\.\d+)?\s*(?:MB|GB|TB))", re.I)

async def extract_torrent_data(row, base_url, dl_session=None, headers=None, checked_cache=None):
    if row is None: 
        return None
    
    url_lower = base_url.lower()
    
    # ใช้ try-except ครอบการทำงาน เพื่อป้องกันบอท Crash เมื่อเจอโครงสร้างเว็บผิดปกติ
    try:
        if "torrentdd" in url_lower:
            return await _extract_torrentdd_logic(row, base_url, dl_session, headers, checked_cache)
        elif "unlimitz" in url_lower:
            return await _extract_unlimitz_logic(row, base_url, dl_session, headers, checked_cache)
        elif "bearbit" in url_lower:
            return await _extract_bearbit_logic(row, base_url, dl_session, headers, checked_cache)
        elif "dedbit" in url_lower:
            return await _extract_dedbit_logic(row, base_url, dl_session, headers, checked_cache)
        elif "bitsuse" in url_lower:
            return await _extract_bitsuse_logic(row, base_url, dl_session, headers, checked_cache)
        else:
            print(f"⚠️ ไม่พบ Logic สำหรับเว็บไซต์: {base_url}")
            return None
            
    except Exception as e:
        print(f"❌ เกิดข้อผิดพลาดในการดึงข้อมูลจาก {base_url}: {e}")
        return None

async def _extract_bitsuse_logic(row, base_url, dl_session, headers, checked_cache):
    """
    ฟังก์ชันแกะข้อมูลทอร์เรนต์สำหรับเว็บไซต์ BITSUSE ตามโครงสร้าง HTML จริง
    """
    t_id, title, details_url = None, "Unknown File", None
    
    # 1. ดึง Title และ ID (รองรับ bs_details.php)
    title_tag = row.find("a", href=re.compile(r"(?:bs_)?details\.php\?id=\d+"))
    if title_tag:
        title = title_tag.get_text(strip=True)
        id_match = re.search(r"id=(\d+)", title_tag['href'])
        if id_match:
            t_id = id_match.group(1)
            # รักษาชื่อสคริปต์ไฟล์ตามเว็บ (bs_details.php หรือ details.php)
            script_name = title_tag['href'].split('?')[0]
            details_url = f"{base_url.rstrip('/')}/{script_name}?id={t_id}"

    # 2. ดึง Download URL (รองรับ bs_download.php)
    download_url = None
    dl_tag = row.find("a", href=re.compile(r"(?:bs_)?download\.php\?id=\d+"))
    if dl_tag:
        url = dl_tag['href']
        download_url = url if url.startswith('http') else f"{base_url.rstrip('/')}/{url.lstrip('/')}"

    # 3. ดึงขนาดไฟล์ (Size) จากคอลัมน์ที่มีหน่วย [KMGTP]B (ในตัวอย่างคือ 3.84 GB)
    size_str = "0 B"
    for cell in row.find_all(["td", "span", "div"]):
        txt = cell.get_text(strip=True)
        if re.search(r"[\d.]+\s*[KMGTP]B", txt, re.I) and not "Ratio" in txt:
            match = re.search(r"([\d.]+\s*[KMGTP]B)", txt, re.I)
            if match:
                size_str = match.group(1)
                break

    # 4. ดึงสถิติ Seeders, Leechers, Completed 
    # ตาม HTML ของ Bitsuse ตัวเลขจะอยู่ใน tag <b> ท้ายตาราง (เช่น Seeders: 32, Leechers: 4, Snatched/Completed: 57)
    seeders, leechers, completed = 0, 0, 0
    
    # รวบรวมตัวเลขจากทุกแท็ก <b> หรือ td ท้ายแถว
    all_b = row.find_all("b")
    numeric_stats = []
    for b in all_b:
        val = b.get_text(strip=True)
        if val.isdigit():
            numeric_stats.append(int(val))

    # แกะสถิติตามลำดับโครงสร้างแถวของ Bitsuse
    # ตัวอย่าง: [Completed (57 times), Seeders (32), Leechers (4)] หรือใกล้เคียง
    # ค้นหาคอลัมน์ Snatched / Completed (มักจะมีคำว่า times หรืออยู่ก่อนหน้า seeders)
    for cell in row.find_all(["td"]):
        txt = cell.get_text(strip=True)
        if "times" in txt:
            comp_match = re.search(r"([\d,]+)\s*times", txt)
            if comp_match:
                try: completed = int(comp_match.group(1).replace(',', ''))
                except: pass

    # Seeders และ Leechers มักอยู่ช่องท้ายๆ ที่เป็นตัวเลขโดดๆ
    if len(numeric_stats) >= 2:
        # สมมติฐาน: ตัวเลขท้ายๆ แถวคือ Seeders และ Leechers
        seeders = numeric_stats[-2] if len(numeric_stats) >= 2 else 0
        leechers = numeric_stats[-1] if len(numeric_stats) >= 1 else 0

    # 5. ดึงวันที่และเวลาอัปโหลด (ในตัวอย่างคือ 2026-09-19 17:22:58)
    raw_date = None
    for cell in row.find_all(["td", "nobr"]):
        txt = cell.get_text(separator=" ", strip=True)
        if re.search(r"\d{4}-\d{2}-\d{2}", txt):
            raw_date = txt.replace("\n", " ")
            break

    return { 
        "id": t_id, 
        "title": title, 
        "seeders": seeders, 
        "leechers": leechers, 
        "completed": completed, 
        "size_str": size_str, 
        "raw_date": raw_date,
        "download_url": download_url, 
        "details_url": details_url 
    }

async def _extract_dedbit_logic(row, base_url, dl_session, headers, checked_cache):
    """
    ฟังก์ชันแกะข้อมูลทอร์เรนต์สำหรับ DEDBIT / BITSUSE ตามโครงสร้าง HTML จริง
    """
    t_id, title, details_url = None, "Unknown File", None
    
    # 1. ดึง Title และ ID จากแท็ก a ที่ลิงก์ไป details.php?id=...
    title_tag = row.find("a", href=re.compile(r"details\.php\?id=\d+"))
    if title_tag:
        title = title_tag.get_text(strip=True)
        id_match = re.search(r"id=(\d+)", title_tag['href'])
        if id_match:
            t_id = id_match.group(1)
            details_url = f"{base_url.rstrip('/')}/details.php?id={t_id}"

    # 2. ดึง Download URL จากแท็ก img alt="download" หรือลิงก์ download.php
    download_url = None
    dl_tag = row.find("a", href=re.compile(r"download\.php\?id=\d+"))
    if dl_tag:
        url = dl_tag['href']
        download_url = url if url.startswith('http') else f"{base_url.rstrip('/')}/{url.lstrip('/')}"

    # 3. ดึงขนาดไฟล์ (Size) จากคอลัมน์ที่มีหน่วย [KMGTP]B (ในตัวอย่างคือ 10.58 MB)
    size_str = "0 B"
    for cell in row.find_all(["td", "span", "div"]):
        txt = cell.get_text(strip=True)
        if re.search(r"[\d.]+\s*[KMGTP]B", txt, re.I) and not "Ratio" in txt:
            match = re.search(r"([\d.]+\s*[KMGTP]B)", txt, re.I)
            if match:
                size_str = match.group(1)
                break

    # 4. ดึงสถิติ Seeders / Leechers / Completed จากคอลัมน์ที่เป็นตัวเลขท้ายตาราง
    # ตาม HTML: คอลัมน์ Seeders (91) และ Leechers (4) อยู่ในลิงก์ ajaxpeers.php
    seeders, leechers, completed = 0, 0, 0
    
    seed_tag = row.find("a", href=re.compile(r"ajaxpeers\.php.*type=seed"))
    if seed_tag:
        try: seeders = int(seed_tag.get_text(strip=True).replace(',', ''))
        except: pass

    leech_tag = row.find("a", href=re.compile(r"ajaxpeers\.php(?!.*type=seed)"))
    if leech_tag:
        try: leechers = int(leech_tag.get_text(strip=True).replace(',', ''))
        except: pass

    # ค้นหาจำนวนการดาวน์โหลดสำเร็จ (Completed / Snatched) จากลิงก์ filelist.php (ในตัวอย่างคือ 13)
    completed_tag = row.find("a", href=re.compile(r"filelist\.php"))
    if completed_tag:
        try: completed = int(completed_tag.get_text(strip=True).replace(',', ''))
        except: pass

    # 5. ดึงวันที่และเวลาอัปโหลด (ในตัวอย่างคือ 2012-06-28 10:36:28)
    raw_date = None
    for cell in row.find_all(["td", "nobr"]):
        txt = cell.get_text(separator=" ", strip=True)
        if re.search(r"\d{4}-\d{2}-\d{2}", txt):
            raw_date = txt.replace("\n", " ")
            break

    return { 
        "id": t_id, 
        "title": title, 
        "seeders": seeders, 
        "leechers": leechers, 
        "completed": completed, 
        "size_str": size_str, 
        "raw_date": raw_date,
        "download_url": download_url, 
        "details_url": details_url 
    }    

async def _extract_unlimitz_logic(row, base_url, dl_session, headers, checked_cache):
    tds = row.find_all("td")
    if len(tds) < 12: return None

    # 1. สกัด ID & Title
    title_tag = tds[1].find("a", href=re.compile(r"details\.php"))
    t_id = re.search(r"id=(\d+)", title_tag['href']).group(1) if title_tag else None
    title = title_tag.get_text(strip=True) if title_tag else "Unknown"
    details_url = f"{base_url.rstrip('/')}/details.php?id={t_id}"

    # 2. สกัดเวลา (อยู่ใน tds[7] มี tag <nobr>)
    # ข้อมูลดิบ: 2026-07-04<br />10:15:22
    raw_date = tds[7].get_text(separator=' ', strip=True) 

    # 3. สกัด Stats (Seed/Leech)
    # tds[10] = Seeders, tds[11] = Leechers
    seeders = extract_digit(tds[10])
    leechers = extract_digit(tds[11])
    
    # 4. สกัด Size (tds[8])
    size_str = tds[8].get_text(separator=' ', strip=True).replace(" ", "")

    # 5. สกัด Download URL (tds[2])
    dl_tag = tds[2].find("a", href=re.compile(r"/d\.php"))
    download_url = f"{base_url.rstrip('/')}/{dl_tag['href'].lstrip('/')}" if dl_tag else None

    return {
        "id": t_id,
        "title": title,
        "seeders": seeders,
        "leechers": leechers,
        "completed": 0, # Unlimitz ไม่มีคอลัมน์นี้ชัดเจน
        "size_str": size_str,
        "raw_date": raw_date,
        "download_url": download_url,
        "details_url": details_url
    }

async def _extract_torrentdd_logic(row, base_url, dl_session, headers, checked_cache):
    # --- Logic เดิม ---
    t_id, title, details_url = None, "Unknown File", None
    
    title_tag = row.find("a", href=re.compile(r"details\.php\?id=\d+"))
    if title_tag:
        title = title_tag.get_text(strip=True)
        t_id = re.search(r"id=(\d+)", title_tag['href']).group(1)
        details_url = f"{base_url.rstrip('/')}/details.php?id={t_id}"

    # --- เพิ่ม: การดึงเวลาที่ถูกต้องจาก span class="text-muted" ---
    date_tag = row.find("span", class_="text-muted")
    raw_date = date_tag.get_text(strip=True) if date_tag else None

    # ดึง Stats จาก div.dp-show-2
    stats = row.find("div", class_="dp-show-2")
    spans = stats.find_all("span") if stats else []
    completed = extract_digit(spans[2]) if len(spans) > 2 else 0
    seeders = extract_digit(spans[0]) if len(spans) > 0 else 0
    leechers = extract_digit(spans[1]) if len(spans) > 1 else 0

    # ดึง Size จาก div.dp-show-1
    size_span = row.find("span", class_="text-info")
    size_str = size_span.get_text(strip=True).replace("ขนาด:", "").strip() if size_span else "0 B"

    # ดึง Download URL จากปุ่ม button (onclick)
    btn = row.find("button", onclick=True)
    download_url = None
    if btn:
        match = re.search(r"'(.*?)'", btn['onclick'])
        if match:
            url = match.group(1)
            download_url = url if url.startswith('http') else f"{base_url.rstrip('/')}/{url.lstrip('/')}"

    return { 
        "id": t_id, 
        "title": title, 
        "seeders": seeders, 
        "leechers": leechers, 
        "completed": completed, 
        "size_str": size_str, 
        "raw_date": raw_date,
        "download_url": download_url, 
        "details_url": details_url 
    }

async def _extract_bearbit_logic(row, base_url, dl_session, headers, checked_cache):    
    t_id, title, details_url, download_url = None, "Unknown File", None, None
    completed, seeders, leechers = 0, 0, 0
    size_str = "0 B"

    # 1. สกัด ID & Title โดยใช้ Regex ค้นหาใน tag <a> ที่อยู่ใน td.bb-titlecell
    title_cell = row.find("td", class_="bb-titlecell")
    title_tag = title_cell.find("a", href=re.compile(r"details\.php")) if title_cell else None
    
    t_id, title, details_url = None, "Unknown File", None
    
    if title_tag:
        title = title_tag.get_text(strip=True)
        match = ID_REGEX.search(title_tag.get('href', ''))
        if match:
            t_id = match.group(1)
            details_url = f"{base_url.rstrip('/')}/details.php?id={t_id}"

    is_locked = False
    
    # วิธีที่ 1: ตรวจสอบจาก Class หรือ Text ภายใน title_cell
    if title_cell:
        cell_text = title_cell.get_text().lower()
        # เพิ่ม Keyword ที่บ่งบอกว่าไฟล์ล็อค
        lock_keywords = ["locked"]
        if any(keyword in cell_text for keyword in lock_keywords):
            is_locked = True
        
        # วิธีที่ 2: ตรวจสอบรูปภาพสถานะ (เช่น icon กุญแจ)
        if title_cell.find("img", src=re.compile(r"(lock|restricted)", re.I)):
            is_locked = True

    # 2. ปรับการดึงข้อมูลตัวเลข
    tds = row.find_all("td")
    
    # ลองเปลี่ยนฟังก์ชัน extract_digit ให้เคลียร์ค่าให้ชัวร์
    def extract_digit_debug(text):
        # แทนที่จะดึงเลขอย่างเดียว ให้เอาทุกอย่างที่เป็นเลขมารวมกัน
        match = re.search(r'\d+', text)
        return int(match.group()) if match else 0

    completed = extract_digit_debug(tds[-4].get_text(strip=True))
    seeders = extract_digit_debug(tds[-3].get_text(strip=True))
    leechers = extract_digit_debug(tds[-2].get_text(strip=True))
    
    # 3. ดึง Size ด้วยระบบ Fallback (ตาราง TD -> ปุ่มดาวน์โหลดใน action_div)
    # [แผนที่ 1] ดึงจาก <td> คอลัมน์ที่ 8 (สังเกตจาก HTML คือคอลัมน์ที่มีเนื้อหา 449.60 GB)
    # เราจะหา td ที่มี nowrap และมีข้อความรูปแบบ Size
    all_tds = row.find_all("td", nowrap=True)
    for td in all_tds:
        text = td.get_text(strip=True)
        if SIZE_PATTERN.search(text):
            size_str = text
            break
            
    # [แผนที่ 2] หากไม่เจอใน <td> ให้ลองดึงจาก attribute ในปุ่มดาวน์โหลด (กรณีตารางซ่อนค่า)
    if size_str == "0 B":
        # ค้นหา <a> ภายใน bb-file-actions ที่มี onclick ซึ่งมีชื่อฟังก์ชัน bbBrowserDownloadClick
        dl_link = row.find("a", class_=lambda x: x and "download" in x)
        if dl_link and dl_link.has_attr('onclick'):
            # ใช้ Regex ดึงค่าจาก arguments ใน onclick
            onclick_text = dl_link['onclick']
            match = re.search(r"['\"](\d+(?:\.\d+)?\s*(?:MB|GB|TB))['\"]", onclick_text, re.I)
            if match:
                size_str = match.group(0).replace("'", "")

    
    # 4. ตรวจสอบ Action ลิงก์ (คงเดิม)
    download_url = None
    action_div = row.find("div", class_="bb-file-actions")
    if action_div:
        btn_dl = action_div.find("a", href=DL_REGEX)
        if btn_dl:
            href = btn_dl.get('href', '')
            if not BAD_LINK_PATTERN.search(href):
                download_url = href if href.startswith('http') else f"{base_url.rstrip('/')}/{href.lstrip('/')}"

    # 4. Deep Scan แบบประหยัดพลังงาน
    if not download_url and details_url and dl_session:
        # เช็ค Cache
        if checked_cache is not None and details_url in checked_cache:
            pass 
        else:
            try:
                local_headers = headers.copy() if headers else {}
                #print(f"DEBUG: ส่ง Headers ไปดังนี้: {local_headers}")
                resp = await dl_session.get(details_url, timeout=10, headers=local_headers)
                
                # --- [ส่วนการตรวจสอบความสมบูรณ์] ---
                # ปรับเปลี่ยนจากการใช้ return เป็นการพิมพ์ Log แล้วให้ทำงานต่อด้วยค่าเริ่มต้น
                is_valid = True
                if not resp or resp.status_code != 200:
                    print(f"❌ [DeepScan] โหลดหน้าไม่ผ่าน (Status: {resp.status_code if resp else 'No Resp'})")
                    is_valid = False
                
                content = resp.text if resp else ""
                if is_valid and (not content or len(content) < 500):
                    print(f"❌ [DeepScan] หน้าเว็บว่างเปล่าหรือโดน Block")
                    is_valid = False
                
                # ถ้าเช็คผ่านทั้งหมด ค่อยทำ BeautifulSoup
                if is_valid:
                    # [สำคัญ] ถ้า dl_session คือ BrowserSessionWrapper ให้ดึง content ล่าสุดจาก tab
                    # เพราะอาจมี JS ทำงานต่อหลังจากโหลด HTML แรกมา
                    content = await dl_session.browser.main_tab.get_content()
                    soup = BeautifulSoup(content, 'lxml')
                    # เพิ่มหลังประกาศ soup = BeautifulSoup(content, 'lxml')
                    #print(f"DEBUG: จำนวนลิงก์ <a> ทั้งหมดที่พบ: {len(soup.find_all('a'))}")
                    # ลองพิมพ์ HTML เฉพาะจุดที่น่าจะมีปุ่มออกมาดู
                    # 1. ค้นหาจาก Text "ดาวน์โหลด" หรือคำที่มีความหมายในทุกๆ <a> (แม่นยำที่สุด)
                    dl_tag = soup.find("a", string=re.compile(r"ดาวน์โหลด", re.I))

                    # 2. ถ้าไม่เจอ ให้ค้นหาจาก href ที่มี pattern ของการดาวน์โหลด (ครอบคลุมทั้ง .torrent และ downloadnew.php)
                    if not dl_tag:
                        dl_tag = soup.find("a", href=re.compile(r"(downloadnew\.php|\.torrent|/download/)", re.I))

                    # 3. ถ้ายังไม่เจอ ให้หว่านแห (Catch-All) โดยหา <a> ที่มีคำว่า 'download' ใน href
                    # นี่คือวิธีที่บอทของคุณใช้จนเจอลิงก์ล่าสุด
                    if not dl_tag:
                        dl_tag = soup.find("a", href=lambda href: href and "download" in href.lower())

                    # 4. หากยังไม่เจอจริงๆ ให้ลองหาปุ่มที่อาจจะเป็นการส่ง Form หรือ JS (ตัวเลือกสุดท้าย)
                    if not dl_tag:
                        dl_tag = soup.find("button", string=re.compile(r"ดาวน์โหลด", re.I))

                    # 5. ตรวจสอบและดึงข้อมูล (คงเดิม)
                    if dl_tag and dl_tag.has_attr('href'):
                        href = dl_tag['href']
                        if not BAD_LINK_PATTERN.search(href):
                            download_url = href if href.startswith('http') else f"{base_url.rstrip('/')}/{href.lstrip('/')}"
                            print(f"✅ [DeepScan] พบลิงก์สำเร็จสำหรับ ID {t_id}")
                        else:
                            print(f"⚠️ [DeepScan] ลิงก์ที่พบติด BAD_LINK_PATTERN: {href}")
                    else:
                        # ถ้าไม่เจอให้ Log เช็คหน้า Login อีกครั้ง
                        content_text = soup.get_text()
                        if "เข้าสู่ระบบ" in content_text or "Login" in content_text:
                            print(f"❌ [CRITICAL] บอทถูกส่งไปหน้า Login! Cookie ไม่ทำงาน")
                        else:
                            print(f"❌ [DeepScan] ไม่พบปุ่มดาวน์โหลดในหน้า {details_url}")
                            # เก็บลง Cache เพื่อไม่ให้วนลูป
                            if checked_cache is not None:
                                if len(checked_cache) > 5000: # ถ้าเกิน 5,000 รายการ ให้ล้างทิ้งเพื่อประหยัด RAM
                                    checked_cache.clear()
                                checked_cache.add(details_url)
            except Exception as e:
                print(f"❌ [DeepScan Error] ID {t_id}: {str(e)}")
    
    return {
        "id": t_id, 
        "title": title, 
        "seeders": seeders, 
        "leechers": leechers,
        "completed": completed, 
        "size_str": size_str, 
        "raw_date": tds[6].get_text(strip=True) if len(tds) > 6 else "N/A",
        "download_url": download_url, 
        "details_url": details_url,
        "is_locked": is_locked
    }

class ResponseWrapper:
    def __init__(self, status, content, url):
        self.status_code = status # ปรับชื่อให้ตรงกับที่คุณใช้
        self.content = content
        self.url = url
        self.headers = {} # ถ้าต้องใช้ headers ให้ดึงมาใส่ด้วย

async def download_torrent_smart(tab, details_url, download_url):
    # 1. เข้าลิงค์ Download
    await tab.get(download_url)
    await asyncio.sleep(3)
    
    # 2. ตรวจสอบหน้าเว็บ
    unlimitz_link = await tab.evaluate("document.querySelector('a[href*=\"/dI.php/\"]') ? document.querySelector('a[href*=\"/dI.php/\"]').href : null")
    is_adgate = await tab.evaluate("!!document.getElementById('bbDlBtn')")
    
    final_dl_url = download_url # ตั้งค่าเริ่มต้น

    if unlimitz_link:
        print("🛡️ ตรวจพบหน้ายืนยัน Unlimitz... กำลังเข้าสู่ไฟล์")
        await tab.get(unlimitz_link)
        await asyncio.sleep(3)
        # อัปเดตลิงก์ให้เป็นลิงก์สุดท้ายที่ browser ไปถึง
        final_dl_url = await tab.evaluate("""
            (() => {
                // ค้นหา <a> ที่มี href ขึ้นต้นด้วย /dI.php/ 
                // โดยดูจากโครงสร้าง HTML ที่คุณให้มา
                const link = document.querySelector('a[href*="/dI.php/"]');
                return link ? link.href : null;
            })()
        """)
        
    elif is_adgate:
        print("🛡️ ตรวจพบระบบ AdGate...")
        for i in range(10):
            is_locked = await tab.evaluate("document.getElementById('bbDlBtn').classList.contains('bb-disabled')")
            if not is_locked: break
            await asyncio.sleep(1)
        final_dl_url = await tab.evaluate("document.getElementById('bbDlBtn').href")

    print(f"🔗 กำลังดักจับการดาวน์โหลด: {final_dl_url}")
    
    # 3. สั่งให้ Browser คลิกที่ปุ่ม (หรือเข้าลิงก์)
    # การคลิกจริงผ่าน Browser จะทำให้ตัวแปรสภาพแวดล้อมสมบูรณ์ที่สุด
    await tab.evaluate(f"window.location.href = '{final_dl_url}'")
    
    # 4. ใช้การดึง Response Body โดยตรงผ่าน Network.responseReceived
    # วิธีนี้คือการขอข้อมูลจาก Browser หลังจากมันโหลดเสร็จแล้ว
    # เราจะใช้คำสั่ง tab.send(uc.cdp.network.get_response_body(request_id=...)) 
    # แต่เนื่องจากเราไม่ทราบ request_id ให้เราใช้การวนลูปเช็คจาก 'Network.responseReceived'
    
    # คำแนะนำ: ถ้าการดึงผ่าน Network ตรงๆ ยังยาก ให้ใช้เทคนิค "รอไฟล์ใน Disk"
    # แต่เพื่อไม่ให้คุณต้องเก็บไฟล์ ผมแนะนำให้ดึงผ่าน Fetch แบบใส่ Header ตัวเต็ม:
    
    import aiohttp
    cookies = {c.name: c.value for c in await tab.send(uc.cdp.network.get_cookies())}
    
    async with aiohttp.ClientSession(cookies=cookies) as session:
        headers = {
            'User-Agent': await tab.evaluate("navigator.userAgent"),
            'Referer': final_dl_url,
            'Accept': '*/*'
        }
        async with session.get(final_dl_url, headers=headers) as resp:
            if resp.status == 200:
                content = await resp.read()
                if content.startswith(b'd8:'):
                    print("✅ ดาวน์โหลดสำเร็จผ่าน Session ที่ดึงจาก Browser!")
                    return content
                else:
                    print("❌ สิ่งที่ได้ไม่ใช่ไฟล์ Torrent")
                    return None
            else:
                print(f"❌ ดาวน์โหลดล้มเหลว Status: {resp.status}")
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
    current_time = get_now().strftime("%H:%M:%S")
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

async def sync_hr_with_web(site_key, page, base_url, ctx):
    print(f"🔄 [{site_key}] เริ่มต้นกระบวนการ Sync H&R (Unified DB)...")
    await page.get(f"{base_url.rstrip('/')}/myhr.php")
    await asyncio.sleep(2)
    
    content = await page.get_content()
    
    # 🛑 ตรวจจับหน้ากำลังอัพเดทข้อมูล
    if "ระบบกำลัง อัพเดทข้อมูล" in content or "กรุณารอสักครู่" in content:
        print(f"⚠️ [{site_key}] เว็บกำลังอยู่ในระหว่างอัพเดทข้อมูล ข้ามการ Sync รอบนี้ไปก่อน")
        await send_notify(f"⚠️ <b>Sync H&R: {site_key}</b>\nเว็บกำลังอยู่ในระหว่างอัพเดทข้อมูล กรุณารอสักครู่...")
        return
        
    soup = BeautifulSoup(content, 'lxml')
    db = await async_load_db(site_key)

    is_empty = soup.find('td', class_='empty') is not None
    is_vip = soup.find('div', class_='vipbox') is not None
    
    stats = {"total": 0, "scanned": 0, "warning": 0, "completed": 0}
    current_ids_on_web = []

    if not is_empty and not is_vip:
        rows = soup.select('table.t tr')
        for row in rows:
            if row.find('th') or row.find('td', class_='empty'): continue
            
            links = row.find_all('a', href=re.compile(r'details\.php\?id='))
            valid_link = next((l for l in links if 'userdetails.php' not in l['href']), None)
            if not valid_link: continue
            
            torrent_id = re.search(r'id=(\d+)', valid_link['href']).group(1)
            current_ids_on_web.append(torrent_id)
            stats["total"] += 1
            
            hr_status = extract_hr_status(row)
            if hr_status in ['warning', 'danger']: stats["warning"] += 1

            if torrent_id not in db:
                db[torrent_id] = {
                    "status": "PROTECTED", 
                    "hash": "UNKNOWN", 
                    "seed_quest": False,
                    "hr": True,
                    "added_at": get_now().strftime("%Y-%m-%d %H:%M"), 
                    "retry_count": 0
                }
            
            tid_info = db[torrent_id]
            tid_info["hr"] = True
            tid_info["hr_status"] = hr_status
            meta = {}

            if tid_info.get("status") == "COMPLETED": continue
    
            is_unknown_hash = (tid_info.get("hash") == "UNKNOWN")
            needs_deep_scan = is_unknown_hash or (hr_status in ['warning', 'danger'])
            if tid_info.get("status") == "PROTECTED" and not needs_deep_scan: continue
            
            stats["scanned"] += 1
            
            # กู้คืน Hash
            if is_unknown_hash:
                new_tab = await page.browser.get("about:blank", new_tab=True)
                try:
                    details_url = f"{base_url.rstrip('/')}/details.php?id={torrent_id}"
                    meta = await get_torrent_details_full(new_tab, base_url, details_url, torrent_id)
                    if meta.get('hash') and meta['hash'] != "UNKNOWN":
                        tid_info["hash"] = meta['hash'].lower()
                    else:
                        tid_info["status"] = "ERROR"
                        continue
                except Exception as e:
                    print(f"❌ [{site_key}] Error ดึง Hash ID {torrent_id}: {e}")
                finally:
                    if new_tab: await new_tab.close()
    
            # ตรวจสอบความซ้ำซ้อนแบบ tri-state
            hash_val = tid_info.get("hash")
            hash_exists = False
            hash_check_failed = not bool(ctx.active_nodes)

            for node_obj, _ in ctx.active_nodes:
                exists_method = getattr(
                    node_obj, "is_torrent_exists", None
                )

                if not callable(exists_method):
                    hash_check_failed = True
                    continue

                try:
                    exists = exists_method(hash_val)
                except Exception as check_error:
                    exists = None
                    print(
                        f"⚠️ [HR Protection] "
                        f"[{getattr(node_obj, 'name', 'Unknown')}] "
                        f"{check_error}"
                    )

                if exists is None or type(exists) is not bool:
                    hash_check_failed = True
                    print(
                        f"⚠️ [HR Protection] "
                        f"[{getattr(node_obj, 'name', 'Unknown')}] "
                        "ตรวจสอบ Hash ไม่สำเร็จ"
                    )
                    continue

                if exists is True:
                    hash_exists = True
                    break

            if hash_check_failed and not hash_exists:
                print(
                    f"🛑 [HR Protection] ID={torrent_id} "
                    "ยังยืนยัน Hash ไม่ได้ "
                    "คงสถานะเดิมและข้ามรอบนี้"
                )
                await async_save_db(site_key, db)
                continue

            if hash_exists:
                tid_info.update({"status": "PROTECTED"})
                await async_save_db(site_key, db)
                continue

            # ดาวน์โหลด
            if not meta.get('download_url'):
                details_url = f"{base_url.rstrip('/')}/details.php?id={torrent_id}"
                meta = await get_torrent_details_full(page, base_url, details_url, torrent_id)
            
            if not meta.get('download_url'):
                continue

            success = await trigger_download_if_needed(
                torrent_id, meta.get('name'), meta.get('size_gb'), 
                details_url, meta.get('download_url'), site_key, 
                ctx.dl_session, page, ctx.active_nodes, ctx.seen_hashes, 
                ctx.seen_ids, ctx.global_clean, force_download=(hr_status in ['danger', 'warning'])
            )
    
            if success is True:
                tid_info.update({
                    "status": "PROTECTED",
                    "retry_count": 0
                })

            elif success is None:
                # Preserve previous status and retry counter.
                print(
                    f"⚠️ [HR Protection] ID={torrent_id} "
                    "ตรวจสอบ Node ไม่ครบ "
                    "ยังไม่เปลี่ยนเป็น ERROR"
                )
                await async_save_db(site_key, db)
                continue

            else:
                tid_info.update({
                    "status": "ERROR",
                    "retry_count": tid_info.get("retry_count", 0) + 1
                })

            await async_save_db(site_key, db)
    else:
        print(f"✨ [{site_key}] ข้าม H&R เนื่องจากสถานะปกติหรือ VIP")

    # เรียกใช้ฟังก์ชันแยก cleanup ของ H&R ที่เราคุยกัน
    completed_count = await perform_hr_cleanup(site_key, db, current_ids_on_web)
    stats["completed"] = completed_count

    # ส่ง Notify
    summary_msg = f"🏁 <b>SYNC SUMMARY: {site_key}</b>\n📋 Total: `{stats['total']}`\n🔍 Scanned: `{stats['scanned']}`\n✅ Completed: `{stats['completed']}`" 
    await send_notify(summary_msg)
    print(f"📧 [{site_key}] สรุปผลเรียบร้อย: {stats}")

async def sync_seed_quest_with_web(site_key, page, base_url, ctx):
    print(f"🔄 [{site_key}] เริ่มต้นกระบวนการ Sync Seed Quest (Strict Parser)...")
    await page.get(f"{base_url.rstrip('/')}/mybonus.php")
    await asyncio.sleep(2)
    
    # ดึง Content ล่าสุดหลังจากกาง details แล้ว
    content = await page.get_content()
    
    # 🛑 ตรวจจับหน้ากำลังอัพเดทข้อมูล
    if "ระบบกำลัง อัพเดทข้อมูล" in content or "กรุณารอสักครู่" in content:
        print(f"⚠️ [{site_key}] เว็บกำลังอยู่ในระหว่างอัพเดทข้อมูล ข้ามการ Sync รอบนี้ไปก่อน")
        await send_notify(f"⚠️ <b>SEED QUEST SYNC: {site_key}</b>\nเว็บกำลังอยู่ในระหว่างอัพเดทข้อมูล กรุณารอสักครู่...")
        return

    soup = BeautifulSoup(content, 'lxml')
    db = await async_load_db(site_key)

    stats = {"total": 0, "scanned": 0, "active": 0, "inactive": 0}
    current_ids_on_web = []

    # ค้นหา <ul class='bbx-qlist'> ทุกตัวในหน้าเว็บ
    quest_lists = soup.find_all('ul', class_='bbx-qlist')
    print(f"📌 [{site_key}] พบกล่องรายการ Quest ทั้งหมด: {len(quest_lists)} บล็อก")

    torrent_links = []
    for q_list in quest_lists:
        for li in q_list.find_all('li'):
            link = li.find('a', href=re.compile(r'details\.php\?id=\d+'))
            if link and link not in torrent_links:
                # เก็บทั้ง element ของ link และ text สถานะจาก bbx-chip ใน li เดียวกัน
                chip = li.find('span', class_='bbx-chip')
                chip_text = chip.get_text(strip=True) if chip else ""
                torrent_links.append((link, chip_text))
        
    print(f"📌 [{site_key}] ตรวจพบลิงก์ Quest รวมทุกบล็อก: {len(torrent_links)} รายการ")
    
    for link, chip_text in torrent_links:
        href = link['href']
        match_id = re.search(r'id=(\d+)', href)
        if not match_id:
            continue
            
        torrent_id = match_id.group(1)
        if torrent_id in current_ids_on_web:
            continue
            
        current_ids_on_web.append(torrent_id)
        stats["total"] += 1
        
        if torrent_id not in db:
            db[torrent_id] = {
                "status": "PROTECTED", 
                "hash": "UNKNOWN",
                "seed_quest": True,
                "hr": False,
                "seed_state": "inactive",
                "seed_quest_status": "",
                "seed_quest_added_at": get_now().strftime("%Y-%m-%d %H:%M")
            }
        
        tid_info = db[torrent_id]
        
        # วิเคราะห์สถานะจาก chip_text ที่ดึงมา
        tid_info["seed_quest_status"] = chip_text
        if "ครบแล้ว" in chip_text:
            tid_info["seed_state"] = "completed"
            tid_info["seed_quest"] = False
            # ถ้าเควสเสร็จแล้ว ไม่ล็อก PROTECTED (ปล่อยให้เป็น NORMAL หรือสถานะเดิม)
            if tid_info.get("status") == "PROTECTED":
                tid_info["status"] = "NORMAL" if not tid_info.get("hr") else "PROTECTED"
            print(f"✅ [{site_key}] Seed Quest Completed: ID {torrent_id} [{tid_info['seed_state'].upper()}] ({chip_text}) - {link.get_text().strip()}")
        else:
            tid_info["seed_quest"] = True
            tid_info["status"] = "PROTECTED"
            
            if "กำลังนับ" in chip_text:
                tid_info["seed_state"] = "active"
            elif "พัก" in chip_text:
                tid_info["seed_state"] = "pause"
            else:
                tid_info["seed_state"] = "inactive"

            print(f"🔒 [{site_key}] Seed Quest Locked: ID {torrent_id} [{tid_info['seed_state'].upper()}] ({chip_text}) - {link.get_text().strip()}")

        if tid_info.get("hash") == "UNKNOWN":
            stats["scanned"] += 1
            new_tab = await page.browser.get("about:blank", new_tab=True)
            try:
                details_url = f"{base_url.rstrip('/')}/details.php?id={torrent_id}"
                meta = await get_torrent_details_full(new_tab, base_url, details_url, torrent_id)
                if meta.get('hash') and meta['hash'] != "UNKNOWN":
                    tid_info["hash"] = meta['hash'].lower()
            except Exception as e:
                print(f"❌ [{site_key}] Error ดึง Hash ID {torrent_id}: {e}")
            finally:
                if new_tab: 
                    await new_tab.close()

        hash_val = tid_info.get("hash")
        is_box_active = False

        box_check_failed = (
            not hash_val
            or hash_val == "UNKNOWN"
            or not bool(ctx.active_nodes)
        )

        if not box_check_failed:
            for node_obj, _ in ctx.active_nodes:
                exists_method = getattr(
                    node_obj, "is_torrent_exists", None
                )

                if not callable(exists_method):
                    box_check_failed = True
                    continue

                try:
                    exists = exists_method(hash_val)
                except Exception as check_error:
                    exists = None
                    print(
                        f"⚠️ [Seed Quest] "
                        f"[{getattr(node_obj, 'name', 'Unknown')}] "
                        f"{check_error}"
                    )

                if exists is None or type(exists) is not bool:
                    box_check_failed = True
                    print(
                        f"⚠️ [Seed Quest] "
                        f"[{getattr(node_obj, 'name', 'Unknown')}] "
                        "ตรวจสอบ Hash ไม่สำเร็จ"
                    )
                    continue

                if exists is True:
                    is_box_active = True
                    break

        if box_check_failed and not is_box_active:
            stats.setdefault("unverified", 0)
            stats["unverified"] += 1

            print(
                f"⚠️ [Seed Quest] ID={torrent_id} "
                "ไม่สามารถยืนยันสถานะ Node ได้ "
                "ไม่นับเป็น Inactive"
            )

            await async_save_db(site_key, db)
            continue

        if is_box_active:
            stats["active"] += 1
        else:
            stats["inactive"] += 1

        await async_save_db(site_key, db)
    else:
        print(f"✨ [{site_key}] ไม่พบการแสดงผล Seed Quest ในหน้า mybonus.php")

    completed_count = await perform_quest_cleanup(site_key, db, current_ids_on_web)
    
    summary_msg = f"🎯 <b>SEED QUEST SYNC: {site_key}</b>\n📋 Total Active: `{stats['total']}`\n🟢 Active Box: `{stats['active']}`\n🔴 Inactive Box: `{stats['inactive']}`\n⚪ Unverified Box: `{stats.get('unverified', 0)}`\n🔍 Scanned Hash: `{stats['scanned']}`\n✅ Completed Cleaned: `{completed_count}`"
    await send_notify(summary_msg)
    print(f"📧 [{site_key}] สรุปผล Seed Quest เรียบร้อย: {stats}")

def extract_hr_status(row):
    """
    ตรวจสอบสถานะ H&R (Hit & Run) และป้ายกำกับจากแถวข้อมูล HTML
    คืนค่าเป็น: 'danger', 'warning', หรือ 'normal'
    """
    row_html = str(row).lower()
    row_text = row.get_text().lower()
    
    # 1. เช็คสถานะ Danger (ติดแดง / ผิดเงื่อนไข H&R / โค้ดสีแดง #c1352b)
    if ('color:#c1352b' in row_html or 
        'color: #c1352b' in row_html or 
        'danger' in row_html or 
        'class=\'bd hit\'' in row_html): # ครอบคลุมคำว่า 'เตือน' หรือ 'ผิดเงื่อนไข H&R' จากตัวอย่างล่าสุด
        return 'danger'
    
    # 2. เช็คสถานะ Warning (เตือน / สีเหลือง / คลาส warn หรือ pause)
    if ('warning' in row_html or 
        'yellow' in row_html or 
        'pause' in row_html or
        'class=\'bd pause\'' in row_html or
        'class=\'bd warn\'' in row_html):
        return 'warning'
        
    # 3. หากเป็นสถานะปกติหรือปลอดภัย (เช่น seed เรียบร้อย, สีเขียว) ให้คืนค่า 'normal'
    return 'normal'

async def perform_hr_cleanup(site_key, db, current_hr_ids):
    """เคลียร์เฉพาะรายการที่หลุดจากหน้า H&R บนเว็บ"""
    updated = False
    stats_completed = 0
    
    for tid, info in db.items():
        # พิจารณาเฉพาะรายการที่เป็น H&R และยังไม่ COMPLETED
        if info.get("hr") and info.get("status") != "COMPLETED":
            if tid not in current_hr_ids:
                info["hr"] = False
                # ถ้าไม่ได้ติด Seed Quest อยู่ด้วย ถึงจะเปลี่ยนเป็น COMPLETED
                if not info.get("seed_quest"):
                    info["status"] = "COMPLETED"
                    info["seed_quest_completed_at"] = get_now().strftime("%Y-%m-%d %H:%M")
                
                info["hr_status"] = "none"
                info["hr_completed_at"] = get_now().strftime("%Y-%m-%d %H:%M")
                updated = True
                stats_completed += 1
                print(f"✅ [{site_key}] ID {tid}: หลุดจาก H&R แล้ว บันทึกสถานะเรียบร้อย")

    if updated:
        await async_save_db(site_key, db)
        NodeCleaner.clear_cache()
    return stats_completed

async def perform_quest_cleanup(site_key, db, current_quest_ids):
    """เคลียร์เฉพาะรายการที่หลุดจากหน้า Seed Quest บนเว็บ"""
    updated = False
    stats_completed = 0
    
    for tid, info in db.items():
        # พิจารณาเฉพาะรายการที่เป็น Seed Quest และยังไม่ COMPLETED
        if info.get("seed_quest") and info.get("status") != "COMPLETED":
            if tid not in current_quest_ids:
                info["seed_quest"] = False
                # ถ้าไม่ได้ติด H&R อยู่ด้วย ถึงจะเปลี่ยนเป็น COMPLETED
                if not info.get("hr"):
                    info["status"] = "COMPLETED"
                    info["hr_completed_at"] = get_now().strftime("%Y-%m-%d %H:%M")
                
                info["seed_quest_completed_at"] = get_now().strftime("%Y-%m-%d %H:%M")
                updated = True
                stats_completed += 1
                print(f"✅ [{site_key}] ID {tid}: จบเควสต์ Seed Quest แล้ว บันทึกสถานะเรียบร้อย")

    if updated:
        await async_save_db(site_key, db)
        NodeCleaner.clear_cache()
    return stats_completed

async def get_torrent_details_full(page, base_url, details_url, torrent_id):
    """ฟังก์ชันดึงข้อมูลครบจบในที่เดียว (ความปลอดภัยสูง)"""
    await page.get(details_url)
    await asyncio.sleep(1.5)
    content = await page.get_content()
    
    # 1. ดึง Metadata
    t_name, t_size_gb = extract_torrent_metadata(content)
    
    # 2. ดึง Download URL
    soup = BeautifulSoup(content, 'html.parser')
    dl_tag = soup.find("a", href=re.compile(r"download(new)?\.php\?id=" + str(torrent_id), re.I))
    download_url = urljoin(base_url, dl_tag['href']) if dl_tag else None
    
    # 3. ดึงและสกัด Hash
    real_hash = "UNKNOWN"
    if download_url:
        raw_data = await download_torrent_smart(page, details_url, download_url)
        
        # ป้องกันกรณีโหลดได้ HTML หรือ data ว่างเปล่า
        if raw_data:
            if raw_data.startswith(b'd'):
                extracted = extract_info_hash(raw_data)
                # ✅ เช็กความปลอดภัยก่อนใช้ .lower()
                if extracted:
                    real_hash = extracted.lower()
                else:
                    print(f"⚠️ [BEARBIT] ID {torrent_id}: โครงสร้าง Bencode สมบูรณ์แต่ไม่พบ b'info'")
            else:
                # กรณีดาวน์โหลดมาได้เป็นหน้า HTML Error/Login
                preview = raw_data[:150].decode('utf-8', errors='ignore').replace('\n', ' ')
                print(f"⚠️ [BEARBIT] ID {torrent_id}: ข้อมูลที่ได้ไม่ใช่ไฟล์ .torrent (Preview: {preview})")

    return {
        "hash": real_hash, 
        "name": t_name, 
        "size_gb": t_size_gb, 
        "download_url": download_url
    }

def extract_torrent_metadata(html_content):
    soup = BeautifulSoup(html_content, 'lxml')
    
    t_name = "Unknown Title"
    
    # 1. ค้นหา h1 ทั้งหมด แล้วกรองหาตัวที่มีเนื้อหา (ไม่เอาตัวว่าง)
    # เราใช้ list comprehension ดึงข้อความของทุก h1 มาดู
    all_h1 = [h1.get_text(strip=True) for h1 in soup.find_all('h1') if h1.get_text(strip=True)]
    
    # ถ้ามี h1 ที่มีข้อความ ให้เลือกตัวแรกที่เจอ (ซึ่งมักจะเป็นชื่อเรื่องหลัก)
    if all_h1:
        t_name = all_h1[0]
    
    # 2. ถ้ายังไม่ได้ ให้ลองหาจาก <title> tag
    if t_name == "Unknown Title" and soup.title:
        # สมมติชื่อเว็บคือ ":: Bearbit" เราตัดทิ้งเพื่อให้เหลือแค่ชื่อหนัง
        t_name = soup.title.get_text(strip=True).split('::')[0].strip()

    # 3. ดึงขนาดไฟล์ (ใช้ string match ที่แม่นยำขึ้น)
    t_size_gb = 0.0
    # ใช้ select_one หา td ที่มีคำว่า Size 
    size_td = soup.find('td', string=lambda text: text and 'Size' in text)
    
    if size_td:
        # ขยับไปช่องข้างๆ
        sibling = size_td.find_next_sibling('td')
        if sibling:
            # ใช้ regex หาเลขหน้าคำว่า GB
            match = re.search(r'([\d\.]+)\s*GB', sibling.get_text())
            if match:
                t_size_gb = float(match.group(1))
            
    return t_name, t_size_gb

async def trigger_download_if_needed(t_id, t_name, t_size_gb, details_url, download_url, site, dl_session, browser_instance, active_nodes, seen_hashes, seen_ids, global_clean, force_download=False):
    try:
        print(f"🚀 เริ่มดาวน์โหลดไฟล์: {t_id}")
        raw_data_bytes = None
        download_ready = False

        # 1. พยายามดาวน์โหลดผ่าน Session ปกติ (Wrapper หรือ aiohttp)
        if isinstance(dl_session, BrowserSessionWrapper):
            try:
                resp = await dl_session.get(download_url)
                if resp and resp.status_code == 200:
                    raw_data_bytes = resp.content
            except Exception as e:
                print(f"⚠️ ดาวน์โหลดผ่าน Wrapper ล้มเหลว: {e}")
        
        elif hasattr(dl_session, 'get'):
            try:
                async with dl_session.get(download_url) as resp:
                    if resp.status == 200:
                        raw_data_bytes = await resp.read()
            except Exception as e:
                print(f"⚠️ ดาวน์โหลดผ่าน Session ล้มเหลว: {e}")

        # 2. ตรวจสอบเบื้องต้น (ถ้าโหลดได้แล้วให้ข้ามไปตรวจสอบ Hash เลย)
        if raw_data_bytes and raw_data_bytes.startswith(b'd8:'):
            download_ready = True
        else:
            # 3. โหมดกู้คืน (Recovery) - ทำงานเมื่อโหลดปกติไม่สำเร็จ
            print(f"🔄 เข้าสู่โหมดกู้คืนผ่าน Browser สำหรับ ID: {t_id}")
            raw_content = await download_torrent_smart(browser_instance, details_url, download_url)
            
            if raw_content and raw_content.startswith(b'd8:'):
                raw_data_bytes = raw_content
                download_ready = True
                print(f"✅ กู้คืนสำเร็จ!")
            else:
                print(f"❌ ไม่สามารถดาวน์โหลดไฟล์ {t_id} ได้ แม้จะลองกู้คืนแล้ว")
                seen_ids.add(t_id) # Blacklist ไว้ไม่ให้วนลูปซ้ำ
                return False

        # 4. ตรวจสอบ Hash และจัดการ Node
        t_hash = extract_info_hash(raw_data_bytes)
        if not t_hash:
            return False

        if download_ready:
            is_already_in_node = False
            target_node_name = ""
            inventory_check_failed = not bool(active_nodes)

            if not active_nodes:
                print(
                    "⚠️ [Duplicate Check] ไม่มี Active Node "
                    "จึงไม่สามารถยืนยัน Hash ได้"
                )

            for node_obj, _ in active_nodes:
                exists_method = getattr(
                    node_obj, "is_torrent_exists", None
                )

                if not callable(exists_method):
                    inventory_check_failed = True
                    print(
                        f"⚠️ [{getattr(node_obj, 'name', 'Unknown')}] "
                        "ไม่มี Hash verification method"
                    )
                    continue

                try:
                    exists = exists_method(t_hash)
                except Exception as check_error:
                    exists = None
                    print(
                        f"⚠️ [{getattr(node_obj, 'name', 'Unknown')}] "
                        f"Hash check exception: {check_error}"
                    )

                if exists is None or type(exists) is not bool:
                    inventory_check_failed = True
                    print(
                        f"⚠️ [{getattr(node_obj, 'name', 'Unknown')}] "
                        "Hash verification failed"
                    )
                    continue

                if exists is True:
                    is_already_in_node = True
                    target_node_name = getattr(
                        node_obj, "name", "Unknown"
                    )
                    break

            if is_already_in_node:
                print(
                    f"❌ ข้าม: ตรวจพบ Hash [...{t_hash[-5:]}] "
                    f"วิ่งอยู่ใน {target_node_name}"
                )
                seen_hashes.add(t_hash)
                seen_ids.add(t_id)
                return True

            if inventory_check_failed:
                print(
                    f"🛑 [Duplicate Check] Hash={t_hash[:12]} "
                    "ตรวจสอบไม่ครบทุก Node จึงยกเลิก Add"
                )
                return None

        # จัดการส่งเข้า Node
        if download_ready:
            print(f"✅ [{site}] พร้อมส่งไฟล์เข้า Client (Hash: {t_hash})")
            
            # เรียงลำดับ Node ตาม Free GB (ดีแล้ว)
            sorted_nodes = sorted(active_nodes, key=lambda x: x[0].free_gb, reverse=True)
            
            task_weight = calculate_task_weight(t_size_gb)
            success_node = None
        
            for node_obj, n_cfg in sorted_nodes:
                # ตรวจสอบสถานะโหลดและพื้นที่
                d_type = n_cfg.get('disk_type', 'HDD')
                dynamic_max_cap, _ = get_node_dynamic_cap(node_obj, d_type)
                current_load = round(get_node_current_weight(node_obj), 1)
                
                print(f"📡 Check [{node_obj.name}]: Load {current_load:.1f}/{dynamic_max_cap}")
                if (current_load + task_weight) > dynamic_max_cap:
                    print(f" ⏳ [Queue Full] {node_obj.name} ลอง Node ถัดไป")
                    continue # Node นี้โหลดเต็มแล้ว

                # เช็คพื้นที่และพยายาม Reclaim ถ้าจำเป็น
                cleaner = NodeCleaner(node_obj, n_cfg, global_clean)
                needed_gb = t_size_gb + 15.0
                effective_free = node_obj.free_gb - node_obj.get_downloading_size()
                
                if effective_free < needed_gb:
                    cleaner.smart_reclaim_process(required_gb=(t_size_gb + 15.0), is_emergency=False)
                    node_obj.refresh_status()
                    effective_free = node_obj.free_gb - node_obj.get_downloading_size()
                    if effective_free < (t_size_gb + 5.0): # ยอมลด Buffer ลงนิดหน่อยในกรณีจำเป็น
                        continue

                # Verify immediately before Add.
                # dict  = found
                # False = confirmed not found
                # None  = verification failure; abort Add

                try:
                    torrent_info = node_obj.get_torrent_by_hash(t_hash)
                except Exception as lookup_error:
                    print(
                        f"🛑 [{node_obj.name}] Hash lookup exception: "
                        f"{lookup_error} | ยกเลิก Add"
                    )
                    return None

                if torrent_info is None:
                    print(
                        f"🛑 [{node_obj.name}] Hash inventory "
                        "ตรวจสอบไม่ได้ | ยกเลิก Add"
                    )
                    return None

                if isinstance(torrent_info, dict):
                    torrent_state = str(
                        torrent_info.get('state') or ''
                    ).strip().lower()

                    if torrent_state in {
                        'paused',
                        'stopped',
                        'error',
                        'pauseddl',
                        'pausedup',
                        'stoppeddl',
                        'stoppedup',
                    }:
                        if n_cfg.get('allow_resume_existing_torrents', False) is True:
                            node_obj.resume_torrent(t_hash)
                        else:
                            print(
                                f"🔒 [{node_obj.name}] Existing paused torrent "
                                f"{t_hash[:12]} kept paused (Auto-Resume disabled)"
                            )

                    print(
                        f"ℹ️ ไฟล์ {t_hash[:8]} "
                        f"มีอยู่แล้วใน {node_obj.name}"
                    )
                    seen_hashes.add(t_hash)
                    seen_ids.add(t_id)
                    return True

                if torrent_info is not False:
                    print(
                        f"🛑 [{node_obj.name}] Hash lookup "
                        "คืนค่าที่ไม่ถูกต้อง | ยกเลิก Add"
                    )
                    return None

                # Add permitted only after confirmed Not Found.

                result = safe_add_torrent(node_obj, raw_data_bytes, site)
                if result:
                    print(f"✅ [Success] {node_obj.name} | {t_name[:30]}")
                    asyncio.create_task(handle_thanks_click(browser_instance, details_url))
                    seen_hashes.add(t_hash)
                    seen_ids.add(t_id)
                    return True

            if not success_node:
                print(f"❌ [Full/Error] ทุก Node ไม่พร้อมรับไฟล์ {t_id}")
                return False

    except Exception as e:
        print(f"❌ [Download Trigger Error] {e}")
        return False

async def handle_thanks_click(browser_instance, details_url):
    """แยกออกมาเป็น Task เพื่อไม่ให้กระทบการดาวน์โหลดหลัก"""
    thanks_tab = await browser_instance.get(details_url, new_tab=True)
    try:
        await auto_click_thanks(thanks_tab, details_url)
    finally:
        await thanks_tab.close()

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

def get_bearbit_item_status(soup):
    try:
        active_item = "NONE"
        display_exp = "N/A"
        
        # 1. ฟังก์ชันช่วยดึงวันที่จากตาราง
        def get_date_from_table(label_pattern):
            # ค้นหา label ด้วย regex
            label_element = soup.find(string=re.compile(label_pattern, re.I))
            if label_element:
                # ขยับไปหา parent tr และดึง td ตัวที่สอง
                parent = label_element.find_parent("tr")
                if parent:
                    tds = parent.find_all("td")
                    if len(tds) > 1:
                        date_text = tds[1].get_text(separator=" ", strip=True)
                        # รองรับรูปแบบวันที่หลากหลาย
                        match = re.search(r"(\d{4}[-/]\d{2}[-/]\d{2})\s+(\d{2}:\d{2}:\d{2})?", date_text)
                        if match:
                            return f"{match.group(1)} {match.group(2) if match.group(2) else '23:59:59'}"
            return "N/A"

        # 2. ตรวจสอบสถานะ Pause (Priority 1)
        is_pause = soup.find("img", src=re.compile(r"Pause_big\.png", re.I))
        
        # 3. กำหนด active_item
        if is_pause:
            active_item = "PAUSE_DOWNLOAD"
            display_exp = get_date_from_table(r"หมดอายุ\s*Pause\s*Download")
        else:
            # ตรวจสอบไอเทมฟรี (Priority 2)
            item_img = soup.find("img", src=re.compile(r"pic/item/item\d+\.gif", re.I))
            if item_img:
                src = item_img.get('src', '')
                mapping = {"item1.gif": "FREELOAD_100", "item3.gif": "FREELOAD_50", 
                           "item5.gif": "FREELOAD_10", "item6.gif": "FREELOAD_15"}
                active_item = next((v for k, v in mapping.items() if k in src), "NONE")
            
            # กรณีไม่มีรูป ใช้การอ่าน text ทั้งหน้า (Fallback)
            if active_item == "NONE":
                page_text = soup.get_text().lower()
                item_map = {"FREELOAD_100": ["ซานตาคลอส"], "FREELOAD_50": ["ตุ๊กตาซานต้า"]} # เพิ่มคีย์เวิร์ดที่คุณใช้
                for key, keywords in item_map.items():
                    if any(k in page_text for k in keywords):
                        active_item = key
                        break
            
            if active_item.startswith("FREELOAD"):
                display_exp = get_date_from_table(r"Item\s*Status|สถานะ\s*ไอเทม")

        # 4. ส่งค่าอัปเดต (ลบเงื่อนไข != "NONE" ออก เพื่อให้มันรีเซ็ตค่ากลับเป็นปกติได้เมื่อไอเทมหมด/ปิด)
        if 'update_bot_config' in globals():
            update_bot_config(active_item, site_name="BEARBIT")

        return f"{active_item} ({display_exp})"

    except Exception as e:
        print(f"❌ [Parser Error]: {e}")
        return "NONE"

def update_bot_config(active_item, site_name="BEARBIT"):
    global CFG
    if not CFG or 'SETTING' not in CFG: return
    
    # จำกัดให้ทำงานเฉพาะเว็บ BEARBIT เท่านั้น
    if site_name.upper() != "BEARBIT":
        return

    # กรณีไม่มีไอเทม หรือสั่งปิด (NONE) ให้รีเซ็ตกลับค่าเริ่มต้นจากไฟล์ config.json ทันที
    if active_item == "NONE":
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                new_cfg = json.load(f)
                CFG['SETTING'].update(new_cfg.get('SETTING', {}))
            CFG['SETTING']['CURRENT_DISCOUNT'] = 0
            print("🛡️ [BEARBIT - NORMAL MODE]: รีเซ็ตค่ากลับสู่โหมดปกติเรียบร้อย")
        except Exception as e:
            CFG['SETTING']['CURRENT_DISCOUNT'] = 0
            CFG['SETTING']['FREELOAD_ENABLE'] = False
            print(f"❌ Error reloading config: {e}")
        return

    # --- ลอจิกส่วนลดและ Pause เดิม ---
    if active_item == "PAUSE_DOWNLOAD":
        CFG['SETTING']['CURRENT_DISCOUNT'] = 100
        CFG['SETTING']['FREELOAD_ENABLE'] = True
        CFG['SETTING']['MIN_FREE_PERCENT'] = 0
        CFG['SETTING']['EXCLUDE_WEB_FREE'] = False 
        print("⏸️ [BEARBIT - PAUSE DOWNLOAD MODE]: ฟรีโควต้าดาวน์โหลด 100%")
        return

    discounts = {
        "FREELOAD_100": 100, "FREELOAD_50": 50,
        "FREELOAD_30": 30, "FREELOAD_15": 15, "FREELOAD_10": 10
    }

    current_discount = discounts.get(active_item, 0)
    CFG['SETTING']['CURRENT_DISCOUNT'] = current_discount

    if current_discount == 100:
        CFG['SETTING']['FREELOAD_ENABLE'] = True
        CFG['SETTING']['MIN_FREE_PERCENT'] = 0
        CFG['SETTING']['EXCLUDE_WEB_FREE'] = False 
        print("🚀 [BEARBIT - FREE 100% MODE]")
    elif current_discount > 0:
        CFG['SETTING']['FREELOAD_ENABLE'] = True
        CFG['SETTING']['MIN_FREE_PERCENT'] = 0
        CFG['SETTING']['EXCLUDE_WEB_FREE'] = True
        print(f"⚠️ [BEARBIT - DISCOUNT {current_discount}% MODE]")
    else:
        # เผื่อกรณีอื่นๆ ให้รีเซ็ตเช่นกัน
        CFG['SETTING']['CURRENT_DISCOUNT'] = 0
        CFG['SETTING']['FREELOAD_ENABLE'] = False

def reset_bot_config_to_default():
    """รีเซ็ตค่าคอนฟิก global กลับเป็นค่าตั้งต้นจากไฟล์ config.json ทันที (ใช้เมื่อย้ายไปเว็บอื่น)"""
    global CFG
    if not CFG or 'SETTING' not in CFG: return
    
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            base_cfg = json.load(f)
            CFG['SETTING'].update(base_cfg.get('SETTING', {}))
        
        # กำหนดค่าความปลอดภัยพื้นฐานสำหรับเว็บทั่วไป
        CFG['SETTING']['CURRENT_DISCOUNT'] = 0
        # สมมติว่าค่าเริ่มต้นปกติของเว็บทั่วไปตั้งค่า Freeload เป็น False ไว้
        # หรือปล่อยให้ config.json จัดการโดยตรง
        print("🛡️ [SYSTEM]: รีเซ็ตค่าคอนฟิกกลางกลับสู่ค่าตั้งต้นสำหรับเว็บทั่วไปเรียบร้อย")
    except Exception as e:
        CFG['SETTING']['CURRENT_DISCOUNT'] = 0
        CFG['SETTING']['FREELOAD_ENABLE'] = False
        CFG['SETTING']['EXCLUDE_WEB_FREE'] = True
        print(f"❌ Error resetting config to default: {e}")

async def auto_vote_snatched(page, base_url: str, site_name: str = "BEARBIT") -> bool:
    try:
        max_p = 5
        total_voted = 0
        clean_base = base_url.rstrip('/')
        snatch_url = f"{clean_base}/snatchdown.php"
        
        print(f"🗳️ [{site_name}] เริ่มระบบ Auto-Vote...")
        
        # 1. บังคับเปลี่ยน URL และรอจนโหลดเสร็จจริงๆ
        await page.get(snatch_url)
        await asyncio.sleep(2.5)
        
        vote_img_selector = 'img[src*="v5.1.1.png"], img[title="ยอดเยี่ยม"]'
        
        for p_idx in range(1, max_p + 1):
            # 2. ตรวจสอบและดึง Tab กลับหากหลุด URL หลัก
            try:
                current_url = await page.evaluate("window.location.href")
            except Exception:
                current_url = ""

            if "snatchdown.php" not in current_url:
                print(f"⚠️ [{site_name}] ตรวจพบการแทรกแซง! กำลังดึง Tab กลับมาที่ Snatchdown...")
                await page.get(f"{snatch_url}?page={p_idx}")
                await asyncio.sleep(2.5)

            # Query ปุ่มโหวตในหน้านั้นๆ
            try:
                all_vote_btns = await page.select_all(vote_img_selector)
            except Exception:
                all_vote_btns = []
            
            if not all_vote_btns:
                print(f"✅ [{site_name}] หน้า {p_idx} ไม่มีรายการค้าง - ตรวจสอบเสร็จสิ้น")
                break
                
            print(f"🔍 [{site_name}] พบ {len(all_vote_btns)} รายการ (หน้า {p_idx})")
            
            # 3. กดคลิกโหวตทีละรายการแบบใช้ JS / Safe Click เพื่อป้องกัน Node -32000
            for i in range(len(all_vote_btns)):
                try:
                    # ค้นหา Element ณ วินาทีนั้นๆ ใหม่เพื่อป้องกัน Node ID หมดอายุ
                    fresh_btns = await page.select_all(vote_img_selector)
                    if not fresh_btns or i >= len(fresh_btns):
                        break
                    
                    target_btn = fresh_btns[i]
                    await target_btn.click()
                    total_voted += 1
                    await asyncio.sleep(random.uniform(0.8, 1.2))
                except Exception as click_err:
                    # หาก Node หลุด ให้ลองใช้ JS Click สำรอง
                    try:
                        await page.evaluate(f'''
                            let btns = document.querySelectorAll('{vote_img_selector}');
                            if (btns[{i}]) {{ btns[{i}].click(); }}
                        ''')
                        total_voted += 1
                        await asyncio.sleep(1.0)
                    except Exception:
                        continue
            
            # 4. เช็คและเปลี่ยนไปหน้าถัดไป
            if p_idx >= max_p:
                break

            try:
                next_btn = await page.select('img[src*="nextpage.gif"]')
                if not next_btn:
                    break
                
                print(f"➡️ ไปหน้า {p_idx + 1}...")
                await next_btn.click()
                await asyncio.sleep(3.0)
            except Exception as nav_err:
                # กรณีคลิกปุ่ม Next แล้วเจอ Error Node -32000 ให้ใช้วิธี Navigate ตรงผ่าน URL
                print(f"🔄 [{site_name}] สลับใช้วิธีเปิด URL หน้า {p_idx + 1} โดยตรง...")
                await page.get(f"{snatch_url}?page={p_idx + 1}")
                await asyncio.sleep(3.0)
        
        # 5. ระบบแจ้งเตือน (Notify)
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
        'HYBRID': 15,
        'HDD': 10
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
    quota_basis_gb = getattr(
        node, "quota_total_gb", None
    )

    if (
        isinstance(quota_basis_gb, bool)
        or not isinstance(quota_basis_gb, (int, float))
        or quota_basis_gb <= 0
    ):
        quota_basis_gb = node.quota_gb or 1000

    free_percent = (
        node.free_gb / quota_basis_gb
    ) * 100

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

    calculated_cap = int(latency_cap * space_factor)

    # Provider nodes must stop when quota space reaches the safety stop.
    # Preserve the historical minimum-cap behavior for other nodes.
    if (
        getattr(node, "provider_sso", False)
        or getattr(node, "storage_api", "") == "swizzin"
    ) and space_factor <= 0:
        final_cap = 0
    else:
        final_cap = max(3, calculated_cap)

    print(
        f"[CAP AUDIT] [{node.name}] "
        f"disk={disk_type} "
        f"latency_cap={latency_cap} "
        f"space_factor={space_factor} "
        f"free={node.free_gb:.1f}GB "
        f"quota={quota_basis_gb:.1f}GB "
        f"free_pct={free_percent:.1f}% "
        f"pending={pending_gb:.1f}GB "
        f"proxy_wait={proxy_wait:.2f} "
        f"final_cap={final_cap}"
    )
    return final_cap, proxy_wait


def get_node_current_weight(node):
    active_torrents = node.get_active_downloads()
    total_weight = 0.0
    now = time.time()

    for t in active_torrents:
        state = str(t.get("state") or "").lower()
        dl_speed = max(0, int(t.get("dlspeed") or 0))
        progress = float(t.get("progress") or 0)
        size_bytes = max(0, int(t.get("size_bytes") or 0))
        size_gb = size_bytes / (1024 ** 3)

        weight = calculate_task_weight(size_gb)

        last_activity = t.get("last_activity")
        idle_time = None

        # 0 หรือ None ถือว่าไม่มีข้อมูลเวลา ไม่ใช้คำนวณ idle
        if last_activity is not None:
            try:
                last_activity = float(last_activity)
                if last_activity > 0:
                    idle_time = max(0, now - last_activity)
            except (TypeError, ValueError):
                idle_time = None

        # ลดน้ำหนักเฉพาะสถานะที่ยืนยันว่าหยุด/รออยู่
        is_stalled = "stalled" in state
        is_queued = "queued" in state

        if is_stalled:
            weight *= 0.1

        elif is_queued and dl_speed == 0:
            # Queued ไม่ได้ใช้ disk I/O ดาวน์โหลดอยู่ในขณะนั้น
            weight *= 0.1

        elif (
            dl_speed == 0
            and idle_time is not None
            and idle_time > 300
        ):
            weight *= 0.1

        elif dl_speed < (500 * 1024) and progress > 0.05:
            weight *= 0.2
            print(
                f"🕵️ [Anti-Gak] {t.get('name', 'Unknown')[:40]} "
                f"| speed={dl_speed / 1024:.1f} KiB/s "
                f"| Weight={weight:.2f}"
            )

        elif dl_speed < (2 * 1024 * 1024) and progress > 0.01:
            weight *= 0.4

        total_weight += weight

    print(
        f"[WEIGHT SUMMARY] [{getattr(node, 'name', 'Unknown')}] "
        f"active_count={len(active_torrents)} "
        f"total_weight={total_weight:.1f}"
    )

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
        
        # 1. เตรียมข้อมูลพื้นฐาน
        now = get_now()
        time_str = data.get('raw_date') or now.strftime('%Y-%m-%d %H:%M:%S')
        
        # 2. แปลงเวลา (Clean & Parse)
        try:
            formatted = re.sub(r'(\d{4})(\d{2}:\d{2}:\d{2})', r'\1 \2', time_str.replace('/', '-').replace('.', '-'))
            fmt = '%Y-%m-%d %H:%M:%S' if re.match(r'^\d{4}-\d{2}-\d{2}', formatted) else '%d-%m-%Y %H:%M:%S'
            upload_time = tz.localize(datetime.strptime(formatted, fmt))
        except:
            upload_time = now

        # 3. คำนวณสถานะไฟล์
        age_delta = now - upload_time
        total_minutes = age_delta.total_seconds() / 60
        s = int(data.get('seeders', 0) or 0)
        l = int(data.get('leechers', 0) or 0)
        efficiency_ratio = (l + 1) / (s + 1)
        
        # 4. ตรวจสอบเงื่อนไขคัดออก (Fail-fast strategy)
        if age_delta.total_seconds() < -300: return False # เวลาเพี้ยน
        if (age_delta.total_seconds() / 3600) > max_age_hours: 
            print(f" ⏭️ ข้าม: [เก่าเกิน {max_age_hours} ชม.]")
            return False 
        if data.get('is_locked'): 
            print(f" ⏭️ ข้าม: ไฟล์โดนล็อค")
            return False
        
        # 5. เงื่อนไขข้อยกเว้นสำหรับไฟล์ใหม่มาก (Grace Period)
        if total_minutes < 10: return True
        
        # 6. เงื่อนไขคัดกรองไฟล์ปกติ
        if l < 1: 
            print(f" ⏭️ ข้าม: [ไม่มีคนโหลด]")
            return False # ไม่มีคนโหลด
        if s < 1 and total_minutes > 30: return False # ไฟล์ตาย
        if efficiency_ratio < 0.5: # ไม่นิยม
            print(f" ⏭️ ข้าม: ไม่ได้รับความนิยม Ratio:{efficiency_ratio:.2f}")
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
        tab = self.browser.main_tab

        async def add_headers(event):
            if headers:
                event.request.headers.update(headers)

        try:
            tab.add_handler(
                uc.cdp.network.RequestWillBeSent,
                add_headers
            )
            try:
                await tab.get(url)
                await tab.wait(timeout)
                content = await tab.get_content()
            finally:
                tab.remove_handler(
                    uc.cdp.network.RequestWillBeSent,
                    add_headers
                )

            if not content:
                return None

            return self.MockResponse(
                content.encode('utf-8', errors='ignore')
            )

        except Exception as e:
            print(f"❌ Error: {e}")
            return None

async def safe_timeout(coro, timeout_sec):
    if sys.version_info >= (3, 11):
        # ถ้าเป็น Python 3.11+ ใช้ syntax ใหม่ที่ดูสวยกว่า
        async with asyncio.timeout(timeout_sec):
            return await coro
    else:
        # ถ้าเป็นเวอร์ชันเก่า ใช้ wait_for
        return await asyncio.wait_for(coro, timeout=timeout_sec)


def build_search_url(
    site_name,
    search_keyword,
    selected_cats=None,
    subcat2=None,
    incldead=1,
    freeload=False,
    bonus_upload=False,
    searchin=0,
    sortby=0,
    quality_options=None,
    target_path=None,
):
    """Build verified GET search URLs for BearBit, BITSUSE and DEDBIT."""
    from urllib.parse import urlencode, urlsplit
    import re as _re

    site_key = str(site_name or "").strip().lower()

    is_bearbit = any(name in site_key for name in ("bearbit", "siambit"))
    is_bitsuse = "bitsuse" in site_key
    is_dedbit = "dedbit" in site_key

    if not (is_bearbit or is_bitsuse or is_dedbit):
        print(
            f"⚠️ [{site_name}] ยังไม่มี Search URL builder "
            "สำหรับเว็บไซต์นี้"
        )
        return None

    keyword = str(search_keyword or "").strip()
    keyword = _re.sub(
        r"\.(?:mp4|mkv|avi|mov|wmv|m4v|mpg|mpeg|ts|m2ts|webm|flv|iso|zip|rar|7z)$",
        "",
        keyword,
        flags=_re.IGNORECASE,
    )
    keyword = _re.sub(r"\s+", " ", keyword).strip()

    if not keyword:
        return None

    if is_bearbit:
        default_path = "viewno18sbx.php"
        allowed_paths = {"viewno18sbx.php", "viewbrsb.php"}
        allowed_categories = (
            {
                901, 902, 903, 904, 905, 906,
                907, 908, 910, 911, 912,
            }
            if "viewbrsb.php" in str(target_path or "")
            else {
                1, 75, 10, 9, 110, 7, 40, 16,
                3, 70, 100, 85, 105, 8, 56, 19,
                20, 55, 21, 2, 130, 60, 90, 91,
                92, 89, 93, 4, 50, 18, 17, 11,
                5, 54, 51, 52, 57, 53,
            }
        )
    elif is_bitsuse:
        default_path = "bs_browse.php"
        allowed_paths = {"bs_browse.php"}
        # Confirmed by the live BITSUSE browse form.
        allowed_categories = {180, 181, 182, 183, 184}
    else:
        default_path = "browse.php"
        allowed_paths = {"browse.php"}
        # Confirmed by the live DEDBIT browse form.
        allowed_categories = {
            4, 7, 12, 13, 16, 18, 21, 22,
            35, 41, 53, 61, 62, 72,
        }

    requested_path = str(target_path or default_path).strip()
    path_name = urlsplit(requested_path).path.rsplit("/", 1)[-1]

    if path_name not in allowed_paths:
        print(
            f"⚠️ [{site_name}] Search path ไม่รองรับ: "
            f"{requested_path}"
        )
        return None

    if selected_cats is None:
        selected_cats = []
    elif isinstance(selected_cats, (str, int)):
        selected_cats = [selected_cats]

    params = [("search", keyword)]

    if is_bearbit:
        try:
            searchin_value = int(searchin)
        except (TypeError, ValueError):
            searchin_value = 0
        if searchin_value not in (0, 1, 2):
            searchin_value = 0

        try:
            sortby_value = int(sortby)
        except (TypeError, ValueError):
            sortby_value = 0
        if sortby_value not in range(14):
            sortby_value = 0

        params.extend([
            ("searchin", str(searchin_value)),
            ("sortby", str(sortby_value)),
        ])

        if path_name == "viewno18sbx.php":
            try:
                incldead_value = int(incldead)
            except (TypeError, ValueError):
                incldead_value = 1
            if incldead_value not in (0, 1, 2):
                incldead_value = 1
            params.append(("incldead", str(incldead_value)))

    else:
        # Both live browse forms expose GET controls named search,
        # incldead and freeload.
        try:
            incldead_value = int(incldead)
        except (TypeError, ValueError):
            incldead_value = 1
        if incldead_value not in (0, 1, 2):
            incldead_value = 1

        params.append(("incldead", str(incldead_value)))

        # HTML checkboxes should be omitted when not checked.
        if freeload:
            params.append(("freeload", "1"))

    seen_categories = set()
    for category in selected_cats:
        try:
            category_id = int(str(category).strip().lstrip("c"))
        except (TypeError, ValueError):
            continue

        if (
            category_id in allowed_categories
            and category_id not in seen_categories
        ):
            params.append((f"c{category_id}", "1"))
            seen_categories.add(category_id)

    return f"{path_name}?{urlencode(params)}"


def build_cross_seed_targets(
    mapping_data,
    max_targets=10,
    active_hashes=None,
    source_inventory=None,
):
    """
    Build targets exclusively from completed live inventory.

    source_site:
        Trusted only when explicitly verified, or when a legacy
        site_name is corroborated by a matching configured details URL.

    known_sites:
        Historical observations from mapping and per-site hash history.
        Never used as source provenance.
    """
    if (
        not isinstance(source_inventory, dict)
        or not source_inventory
    ):
        print(
            "⚠️ [Cross-Seed Targets] No completed source inventory; "
            "failing closed."
        )
        return []

    if not isinstance(mapping_data, dict):
        return []

    torrents = mapping_data.get("torrents", [])
    if not isinstance(torrents, list):
        return []

    # --------------------------------------------------------
    # Load per-site seen hash files once for this build.
    # This is observation history, not source verification.
    # --------------------------------------------------------

    site_history = {}
    configured_sites = []

    cfg = globals().get("CFG", {})
    site_configs = (
        cfg.get("SITE", [])
        if isinstance(cfg, dict)
        else []
    )

    for site_cfg in site_configs:
        if not isinstance(site_cfg, dict):
            continue

        canonical = _canonical_mapping_site(
            site_cfg.get("name")
        )

        if canonical and canonical not in configured_sites:
            configured_sites.append(canonical)

    history_dir = Path(BASE_DIR) / "history"

    for site_name in configured_sites:
        history_path = (
            history_dir / f"hash_seen_{site_name.upper()}.txt"
        )

        history = set()

        try:
            if history_path.is_file():
                with history_path.open(
                    "r",
                    encoding="utf-8"
                ) as f:
                    history = {
                        line.strip().lower()
                        for line in f
                        if line.strip()
                    }
        except OSError as history_error:
            print(
                f"⚠️ [Cross-Seed History] "
                f"{site_name}: {history_error}"
            )

        site_history[site_name] = history

    # --------------------------------------------------------
    # Configured details URL -> canonical site resolver.
    # --------------------------------------------------------

    def site_from_details_url(value):
        try:
            host = urllib.parse.urlsplit(
                str(value or "")
            ).hostname

            host = str(host or "").casefold().rstrip(".")

            if not host:
                return ""

            for site_cfg in site_configs:
                if not isinstance(site_cfg, dict):
                    continue

                name = _canonical_mapping_site(
                    site_cfg.get("name")
                )

                base_host = urllib.parse.urlsplit(
                    str(site_cfg.get("base_url") or "")
                ).hostname

                base_host = str(
                    base_host or ""
                ).casefold().rstrip(".")

                if (
                    name
                    and base_host
                    and (
                        host == base_host
                        or host.endswith("." + base_host)
                    )
                ):
                    return name

        except Exception:
            return ""

        return ""

    active_hashes = (
        {
            str(h).strip().upper()
            for h in active_hashes
            if h
        }
        if active_hashes is not None
        else None
    )

    targets = []
    seen = set()

    for record in torrents:
        if not isinstance(record, dict):
            continue

        client_info = record.get("client_info") or {}
        if not isinstance(client_info, dict):
            continue

        source_hash = str(
            client_info.get("hash")
            or record.get("hash")
            or ""
        ).strip().upper()

        if not source_hash or source_hash in seen:
            continue

        if (
            active_hashes is not None
            and source_hash not in active_hashes
        ):
            continue

        if not re.fullmatch(r"[0-9A-F]{40}", source_hash):
            continue

        raw_candidates = source_inventory.get(source_hash)
        if isinstance(raw_candidates, dict):
            # Backward compatibility with the pre-Phase-D inventory shape.
            raw_candidates = [raw_candidates]
        if not isinstance(raw_candidates, (list, tuple)):
            continue

        try:
            mapped_size = int(
                client_info.get("size_bytes") or 0
            )
        except (TypeError, ValueError, OverflowError):
            continue
        if mapped_size <= 0:
            continue

        valid_candidates = []
        seen_candidate_nodes = set()

        for candidate in raw_candidates:
            if not isinstance(candidate, dict):
                continue

            candidate_node = candidate.get("node")
            candidate_cfg = candidate.get("n_cfg") or {}
            candidate_torrent = candidate.get("torrent") or {}

            if (
                candidate_node is None
                or not isinstance(candidate_cfg, dict)
                or not isinstance(candidate_torrent, dict)
            ):
                continue

            # A single Node must contribute at most one source record per hash.
            candidate_node_key = id(candidate_node)
            if candidate_node_key in seen_candidate_nodes:
                continue

            try:
                candidate_size = int(
                    candidate_torrent.get("size_bytes") or 0
                )
                candidate_amount_left = int(
                    candidate_torrent.get("amount_left", -1)
                )
                candidate_progress = float(
                    candidate_torrent.get("progress", 0.0)
                )
            except (TypeError, ValueError, OverflowError):
                continue

            candidate_save_path = str(
                candidate_torrent.get("save_path") or ""
            ).strip()

            if (
                candidate_size <= 0
                or candidate_size != mapped_size
                or candidate_amount_left != 0
                or candidate_progress < 0.999999
                or not candidate_save_path
            ):
                continue

            candidate_node_name = str(
                candidate.get("node_name")
                or getattr(candidate_node, "name", "Unknown Node")
            )

            valid_candidates.append({
                "node": candidate_node,
                "n_cfg": candidate_cfg,
                "torrent": candidate_torrent,
                "node_name": candidate_node_name,
            })
            seen_candidate_nodes.add(candidate_node_key)

        if not valid_candidates:
            continue

        # Preserve legacy single-source fields while carrying all live copies.
        inventory = valid_candidates[0]
        source_node = inventory.get("node")
        source_cfg = inventory.get("n_cfg") or {}
        source_torrent = inventory.get("torrent") or {}
        actual_size = int(source_torrent.get("size_bytes") or 0)
        save_path = str(
            source_torrent.get("save_path") or ""
        ).strip()

        keyword = str(
            record.get("release_name")
            or client_info.get("name")
            or source_torrent.get("name")
            or ""
        ).strip()

        if not keyword:
            continue

        # ----------------------------------------------------
        # Verified source provenance.
        # ----------------------------------------------------

        source_site_verified = (
            record.get("source_site_verified") is True
        )

        source_site = (
            _canonical_mapping_site(
                record.get("source_site")
            )
            if source_site_verified
            else ""
        )

        # Conservative legacy recovery:
        # site_name is trusted only if details_url's hostname
        # independently confirms that exact configured site.
        if not source_site:
            legacy_site = _canonical_mapping_site(
                record.get("site_name")
            )

            details_site = site_from_details_url(
                record.get("details_url")
            )

            if legacy_site and details_site == legacy_site:
                source_site = legacy_site
                source_site_verified = True

        if not source_site:
            source_site = "Unknown"
            source_site_verified = False

        # ----------------------------------------------------
        # Known sites = stored observations + history matches.
        # Hash history never establishes source provenance.
        # ----------------------------------------------------

        known_sites = []
        raw_known = record.get("known_sites", [])

        if isinstance(raw_known, (list, tuple, set)):
            for value in raw_known:
                canonical = _canonical_mapping_site(value)

                if canonical and canonical not in known_sites:
                    known_sites.append(canonical)

        source_hash_lower = source_hash.lower()

        for site_name, history in site_history.items():
            if (
                source_hash_lower in history
                and site_name not in known_sites
            ):
                known_sites.append(site_name)

        if (
            source_site_verified
            and source_site != "Unknown"
            and source_site not in known_sites
        ):
            known_sites.append(source_site)

        source_node_name = str(
            getattr(source_node, "name", "Unknown Node")
        )

        source_zone_evidence = None
        source_zone_id = "unknown"
        source_zone_verified = False
        if record.get("source_zone_verified") is True:
            _raw_source_zone_evidence = record.get("source_zone_evidence")
            source_zone_evidence = _validate_mapping_source_zone_evidence(
                _raw_source_zone_evidence,
                source_site,
                record.get("torrent_id") or (
                    _raw_source_zone_evidence.get("torrent_id")
                    if isinstance(_raw_source_zone_evidence, dict) else None
                ),
                record.get("details_url") or (
                    _raw_source_zone_evidence.get("details_url")
                    if isinstance(_raw_source_zone_evidence, dict) else None
                ),
            )
            if (
                source_zone_evidence
                and str(record.get("source_zone_id") or "").casefold()
                    == source_zone_evidence["zone_id"]
            ):
                source_zone_id = source_zone_evidence["zone_id"]
                source_zone_verified = True
            else:
                source_zone_evidence = None

        verified_site_listings = []
        raw_site_listings = record.get("site_listings")

        from urllib.parse import parse_qs, urlsplit

        allowed_listing_domains = {
            "BEARBIT": "bearbit.org",
            "BITSUSE": "bitsuse.com",
            "DEDBIT": "dedbit.com",
            "TORRENTDD": "torrentdd.com",
            "UNLIMITZ": "unlimitz.biz",
        }

        required_listing_proofs = {
            "exact_source_candidate_file_manifest",
            "live_qbittorrent_complete_inventory",
            "candidate_tracker_api_all_present",
        }

        if isinstance(raw_site_listings, list):
            for listing in raw_site_listings:
                if (
                    not isinstance(listing, dict)
                    or listing.get("verified") is not True
                    or listing.get("verification_status")
                    != "live_inventory_exact_manifest_tracker_verified"
                ):
                    continue

                listing_meta = _validate_mapping_target_zone_meta(listing)

                listing_hash = str(
                    listing.get("infohash") or ""
                ).strip().upper()

                listing_torrent_id = str(
                    listing.get("torrent_id") or ""
                ).strip()

                listing_details_url = str(
                    listing.get("details_url") or ""
                ).strip()

                verified_by = listing.get("verified_by")
                verified_at = str(
                    listing.get("verified_at") or ""
                ).strip()

                if (
                    not listing_meta
                    or not re.fullmatch(r"[0-9A-F]{40}", listing_hash)
                    or not re.fullmatch(r"[0-9]{1,20}", listing_torrent_id)
                    or not isinstance(verified_by, (list, tuple, set))
                    or not required_listing_proofs.issubset(
                        {str(value) for value in verified_by}
                    )
                    or not verified_at
                ):
                    continue

                try:
                    parsed_details = urlsplit(listing_details_url)
                    details_host = (
                        parsed_details.hostname or ""
                    ).casefold().rstrip(".")

                    detail_query = parse_qs(parsed_details.query)

                    detail_ids = {
                        str(value).strip()
                        for field in ("id", "tid", "torrentid")
                        for value in detail_query.get(field, [])
                    }

                except (TypeError, ValueError):
                    continue

                expected_domain = allowed_listing_domains.get(
                    listing_meta["site_key"]
                )

                domain_matches = bool(
                    expected_domain
                    and (
                        details_host == expected_domain
                        or details_host.endswith("." + expected_domain)
                    )
                )

                if (
                    parsed_details.scheme.casefold() not in {"http", "https"}
                    or not domain_matches
                    or listing_torrent_id not in detail_ids
                ):
                    continue

                verified_site_listings.append({
                    **listing_meta,
                    "torrent_id": listing_torrent_id,
                    "details_url": listing_details_url,
                    "infohash": listing_hash,
                    "verified": True,
                    "verification_status":
                        "live_inventory_exact_manifest_tracker_verified",
                    "verified_by": [
                        "exact_source_candidate_file_manifest",
                        "live_qbittorrent_complete_inventory",
                        "candidate_tracker_api_all_present",
                    ],
                    "verified_at": verified_at,
                })

        targets.append({
            "keyword": keyword,
            "target_size_bytes": actual_size,
            "source_hash": source_hash,
            "source_site": source_site,
            "source_site_verified": source_site_verified,
            "source_zone_id": source_zone_id,
            "source_zone_verified": source_zone_verified,
            "source_zone_evidence": source_zone_evidence,
            "site_listings": verified_site_listings,
            "known_sites": known_sites,
            "source_details_url": record.get("details_url", ""),
            "source_torrent_name": str(
                source_torrent.get("name") or keyword
            ),
            "source_size_bytes": actual_size,
            "source_save_path": save_path,
            "source_node": source_node,
            "source_node_config": source_cfg,
            "source_node_name": source_node_name,
            "source_candidates": valid_candidates,
        })

        seen.add(source_hash)

        if len(targets) >= max(0, int(max_targets)):
            break

    print(
        f"🎯 [Cross-Seed Targets] "
        f"{len(targets)} completed source(s) verified from live inventory"
    )
    return targets


async def _read_cross_seed_filelist(browser_tab, site, torrent_id, details_url):
    """Read a tracker's visible File List using its already-authenticated tab.

    This is a metadata cross-check only. It does not change torrent state.
    Expected list sizes are treated as rounded display values (decimal/binary
    units are both considered when the site labels units ambiguously).
    """
    from urllib.parse import urlsplit, urlunsplit, urlencode

    torrent_id = str(torrent_id or "").strip()
    if not re.fullmatch(r"\d+", torrent_id):
        raise ValueError("Invalid numeric tracker torrent ID for file list")
    if browser_tab is None:
        raise ValueError("Authenticated browser tab unavailable for file list")

    parsed = urlsplit(str(details_url or ""))
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Invalid candidate details URL")

    site_key = re.sub(r"[^a-z0-9]+", "", str(site or "").casefold())
    if "bearbit" in site_key or "bearbit" in parsed.netloc.casefold():
        path = "/details1.php"
        params = {"id": torrent_id, "numfile": "1"}
    elif "bitsuse" in site_key or "bitsuse" in parsed.netloc.casefold():
        path = "/filelist.php"
        params = {"id": torrent_id, "div": "1", "width": "800", "height": "450"}
    elif "dedbit" in site_key or "dedbit" in parsed.netloc.casefold():
        path = "/filelist.php"
        params = {"id": torrent_id, "div": "1", "width": "800", "height": "450"}
    else:
        raise ValueError(f"No File List URL rule for tracker {site!r}")

    filelist_url = urlunsplit((
        parsed.scheme, parsed.netloc, path, urlencode(params), ""
    ))
    await browser_tab.get(filelist_url)
    await asyncio.sleep(0.5)
    html = await browser_tab.get_content()
    if not isinstance(html, str) or not html.strip():
        raise ValueError("File List page returned empty HTML")

    soup = BeautifulSoup(html, "html.parser")
    page_title = soup.title.get_text(" ", strip=True) if soup.title else ""

    size_pattern = re.compile(
        r"^\s*(?P<number>[\d,]+(?:\.\d+)?)\s*"
        r"(?P<unit>bytes?|[kmgt]i?b)\s*(?P<name>.+?)\s*$",
        re.IGNORECASE,
    )

    def parse_list_entry(text_value):
        text_value = re.sub(r"\s+", " ", str(text_value or "")).strip()
        match = size_pattern.match(text_value)
        if not match:
            return None
        name = match.group("name").strip(" \t\r\n•·")
        if not name:
            return None

        number_text = match.group("number").replace(",", "")
        number = float(number_text)
        unit = match.group("unit").casefold()
        decimal_places = len(number_text.partition(".")[2])
        if unit.startswith("byte") or unit == "b":
            powers = [1]
        else:
            prefix = unit[0]
            exponent = {"k": 1, "m": 2, "g": 3, "t": 4}.get(prefix)
            if exponent is None:
                return None
            # kB/MB/GB labels are ambiguous across trackers; retain both
            # plausible interpretations and verify with the .torrent bytes.
            powers = [1024 ** exponent, 1000 ** exponent]
            if "i" in unit:
                powers = [1024 ** exponent]

        estimates = []
        for factor in dict.fromkeys(powers):
            estimate = number * factor
            half_unit = 0.5 * (10 ** (-decimal_places)) * factor
            estimates.append({
                "bytes": int(round(estimate)),
                "tolerance_bytes": max(1, int(half_unit + 0.999999)),
            })
        return {
            "name": name,
            "display_size": f"{match.group('number')} {match.group('unit')}",
            "size_estimates": estimates,
        }

    # First handle explicit Path / Size table rows (BearBit).
    # Size conversion continues to use the existing parse_list_entry().
    entries = []
    table_size_pattern = re.compile(
        r"^\s*[\d,]+(?:\.\d+)?\s*(?:bytes?|[kmgt]i?b)\s*$",
        re.IGNORECASE,
    )
    header_words = {"path", "file", "files", "filename", "name", "size"}

    for row in soup.find_all("tr"):
        cells = row.find_all(["td", "th"], recursive=False)
        if len(cells) < 2:
            continue

        values = [
            re.sub(r"\s+", " ", cell.get_text(" ", strip=True)).strip()
            for cell in cells
        ]
        size_cells = [
            (idx, value)
            for idx, value in enumerate(values)
            if table_size_pattern.fullmatch(value)
        ]

        # Do not guess when a row has zero or multiple size cells.
        if len(size_cells) != 1:
            continue

        size_idx, size_text = size_cells[0]
        names = [
            value
            for idx, value in enumerate(values)
            if idx != size_idx
            and value
            and value.casefold() not in header_words
        ]

        # Only accept an unambiguous filename/size row.
        if len(names) != 1:
            continue

        parsed_entry = parse_list_entry(f"{size_text} {names[0]}")
        if parsed_entry is not None:
            entries.append(parsed_entry)

    # Fall back to bullet-list parsing.
    if not entries:
        list_texts = [
            node.get_text(" ", strip=True)
            for node in soup.find_all("li")
        ]
        entries = [
            parsed_entry
            for text_value in list_texts
            if (
                parsed_entry := parse_list_entry(text_value)
            ) is not None
        ]

    # Last visible-data fallback: parse plain-text lines.
    if not entries:
        lines = soup.get_text("\n", strip=True).splitlines()
        entries = [
            parsed_entry
            for text_value in lines
            if (
                parsed_entry := parse_list_entry(text_value)
            ) is not None
        ]

    if not entries:
        lower_title = page_title.casefold()
        if "login" in lower_title or "เข้าสู่ระบบ" in lower_title:
            raise ValueError(
                f"File List redirected to login page: {page_title!r}"
            )

        page_body = " ".join(
            soup.get_text(" ", strip=True).split()
        )

        # Only this explicit marker enables the hidden-list fallback.
        # An arbitrary parsing error must still fail closed.
        if page_body == "ไม่เปิดเผย":
            print(
                f"⚠️ [Cross-Seed FileList Privacy] site={site!r} "
                f"id={torrent_id!r}: explicit hidden marker; "
                "visible File List unavailable; strict source/candidate "
                "manifest comparison is required."
            )
            return {
                "url": filelist_url,
                "title": page_title,
                "entries": [],
                "visibility": "hidden",
                "hidden_reason": "explicit_not_disclosed_marker",
            }

        _diag_counts = {
            name: len(soup.find_all(name))
            for name in ("table", "tr", "td", "li", "pre", "iframe")
        }
        _diag_rows = [
            " ".join(tr.stripped_strings)[:220]
            for tr in soup.find_all("tr")[:8]
        ]
        _diag_text = page_body[:400]
        print(
            f"⚠️ [Cross-Seed FileList Debug] site={site!r} "
            f"id={torrent_id!r} title={page_title!r} "
            f"html_len={len(html)} counts={_diag_counts!r} "
            f"rows={_diag_rows!r} body={_diag_text!r}"
        )
        raise ValueError(
            f"No parseable file rows on File List page "
            f"(title={page_title!r})"
        )

    return {
        "url": filelist_url,
        "title": page_title,
        "entries": entries,
        "visibility": "visible",
    }


def _cross_seed_name_key(value):
    """Conservative name normalizer; deliberately does not transliterate text."""
    import unicodedata
    value = str(value or "").replace("\\", "/").rsplit("/", 1)[-1]
    value = unicodedata.normalize("NFKC", value).casefold()
    return "".join(ch for ch in value if ch not in "\u200b\u200c\u200d\ufeff").strip()


def _cross_seed_size_estimate_matches(filelist_entry, exact_size):
    try:
        exact_size = int(exact_size)
    except (TypeError, ValueError, OverflowError):
        return False
    for estimate in filelist_entry.get("size_estimates", []):
        try:
            expected = int(estimate["bytes"])
            tolerance = int(estimate["tolerance_bytes"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if abs(exact_size - expected) <= tolerance + 2:
            return True
    return False


def _verify_cross_seed_filelist(filelist_data, candidate_manifest):
    """One-to-one comparison of web File List rows with .torrent manifest."""
    entries = filelist_data.get("entries", []) if isinstance(filelist_data, dict) else []
    files = candidate_manifest.get("files", []) if isinstance(candidate_manifest, dict) else []
    if not entries or not files:
        return {"ok": False, "error": "Empty web File List or torrent manifest"}
    if len(entries) != len(files):
        return {
            "ok": False,
            "error": f"File List count {len(entries)} != torrent manifest count {len(files)}",
        }

    unused = set(range(len(files)))
    matches = []
    for entry in entries:
        web_name = _cross_seed_name_key(entry.get("name"))
        size_match = lambda idx: _cross_seed_size_estimate_matches(
            entry, files[idx].get("size_bytes")
        )
        same_name = [idx for idx in unused
                     if _cross_seed_name_key(files[idx].get("path")) == web_name]
        if same_name:
            valid = [idx for idx in same_name if size_match(idx)]
            if len(valid) != 1:
                return {
                    "ok": False,
                    "error": f"Web File List name matched but size was different/ambiguous: {entry.get('name')!r}",
                }
            chosen = valid[0]
            method = "name_and_rounded_size"
        else:
            # A filename can be mangled by legacy encoding tools. Fall back
            # only to a unique exact extension + displayed-size candidate.
            web_ext = _cross_seed_name_key(entry.get("name")).rsplit(".", 1)[-1] if "." in web_name else ""
            alternatives = []
            for idx in unused:
                candidate_path = str(files[idx].get("path") or "")
                candidate_name = _cross_seed_name_key(candidate_path)
                candidate_ext = candidate_name.rsplit(".", 1)[-1] if "." in candidate_name else ""
                if web_ext == candidate_ext and size_match(idx):
                    alternatives.append(idx)
            if len(alternatives) != 1:
                return {
                    "ok": False,
                    "error": f"Could not uniquely pair File List row by filename/rounded size: {entry.get('name')!r}",
                }
            chosen = alternatives[0]
            method = "unique_extension_and_rounded_size"

        unused.remove(chosen)
        matches.append({
            "web_name": entry.get("name"),
            "torrent_path": files[chosen].get("path"),
            "web_display_size": entry.get("display_size"),
            "torrent_size_bytes": int(files[chosen].get("size_bytes") or 0),
            "match_method": method,
        })

    return {
        "ok": not unused,
        "error": None if not unused else f"Unmatched torrent files: {len(unused)}",
        "matched_count": len(matches),
        "file_count": len(files),
        "matches": matches,
    }


# PHASE_D_PREFLIGHT_SHARED_ARTIFACT_CACHE_V1
async def _preflight_cross_seed_candidate_single_source(
    browser_tab,
    cross_seed_target,
    site,
    torrent_id,
    details_url,
    download_url,
    _shared_preflight_cache=None,
):
    """
    Download candidate metadata and compare it with the live source manifest.
    Never calls add, resume, recheck, delete or tracker mutation APIs.
    """
    tab = browser_tab
    phase = "validate source metadata"

    # This cache is private to one candidate torrent and is shared only across
    # source-node fallback attempts for that same tracker result.
    cache_key = tuple(
        str(value or "")
        for value in (site, torrent_id, details_url, download_url)
    )
    if isinstance(_shared_preflight_cache, dict):
        shared_cache = _shared_preflight_cache
        if shared_cache.get("_cache_key") != cache_key:
            shared_cache.clear()
            shared_cache["_cache_key"] = cache_key
    else:
        shared_cache = {}

    try:
        source_node = cross_seed_target.get("source_node")
        source_hash = str(
            cross_seed_target.get("source_hash") or ""
        ).strip().upper()
        source_name = str(
            cross_seed_target.get("source_torrent_name") or ""
        )
        source_size = int(
            cross_seed_target.get("source_size_bytes") or 0
        )
        source_save_path = str(
            cross_seed_target.get("source_save_path") or ""
        ).strip()

        if source_node is None:
            raise ValueError("Missing source node")
        if not re.fullmatch(r"[0-9A-F]{40}", source_hash):
            raise ValueError("Unsupported or invalid source info hash")
        if source_size <= 0 or not source_save_path:
            raise ValueError("Missing source size or save path")
        if not source_name:
            raise ValueError("Missing source torrent name")
        if not callable(
            getattr(source_node, "get_torrent_file_manifest", None)
        ):
            raise ValueError("Source client has no manifest reader")
        phase = "acquire existing site tab"
        if tab is None:
            raise ValueError("Existing authenticated site tab is unavailable")

        # Reuse the authenticated search tab: do not create another CDP session.
        # Candidate torrent bytes, parsed metainfo and tracker File List are
        # common to every source copy of this one target; acquire them once.
        if shared_cache.get("ready") is True:
            phase = "validate cached tracker metadata"
            content = shared_cache.get("content")
            candidate = shared_cache.get("candidate")
            candidate_hash = str(
                shared_cache.get("candidate_hash") or ""
            ).strip().upper()
            site_filelist = shared_cache.get("site_filelist")
            filelist_hidden = shared_cache.get("filelist_hidden") is True
            filelist_check = shared_cache.get("filelist_check")

            if (
                not isinstance(content, bytes)
                or not isinstance(candidate, dict)
                or not re.fullmatch(r"[0-9A-F]{40}", candidate_hash)
                or not candidate.get("announce_urls")
                or not isinstance(site_filelist, dict)
                or not isinstance(filelist_check, dict)
            ):
                raise ValueError("Cached candidate/tracker metadata is invalid")

            print(
                f"♻️ [Cross-Seed Preflight] [{site}] ID={torrent_id}: "
                "reusing downloaded metainfo and tracker File List for source fallback"
            )
        else:
            phase = "download candidate torrent"
            content = await download_torrent_smart(
                tab, details_url, download_url
            )

            phase = "parse candidate torrent metainfo"

            if not isinstance(content, (bytes, bytearray)) or len(content) < 10:
                raise ValueError("Torrent download returned no usable bytes")

            content = bytes(content)
            candidate = _extract_torrent_manifest(content)

            phase = "validate candidate hash and trackers"
            candidate_hash = str(
                candidate.get("info_hash") or ""
            ).strip().upper()

            if not re.fullmatch(r"[0-9A-F]{40}", candidate_hash):
                raise ValueError("Candidate does not have a valid v1 info hash")
            if not candidate.get("announce_urls"):
                raise ValueError("Candidate contains no usable tracker URL")

            # Verify the tracker-visible File List against the downloaded metainfo.
            # A different torrent root name is not by itself a rejection reason.
            phase = "read tracker file list"
            site_filelist = await _read_cross_seed_filelist(
                browser_tab=tab,
                site=site,
                torrent_id=torrent_id,
                details_url=details_url,
            )

            filelist_hidden = (
                site_filelist.get("visibility") == "hidden"
                and not site_filelist.get("entries")
            )

            if filelist_hidden:
                # This does NOT mean the web File List was verified.
                # Strict source-vs-candidate manifest checks must succeed below.
                filelist_check = {
                    "ok": None,
                    "verification": "skipped_explicit_hidden_marker",
                    "matched_count": 0,
                    "file_count": len(candidate.get("files") or []),
                    "matches": [],
                }
                print(
                    f"⚠️ [Cross-Seed File List] [{site}] ID={torrent_id}: "
                    "web list explicitly hidden; source/candidate manifest "
                    "equivalence required."
                )
            else:
                phase = "compare tracker file list with candidate metainfo"
                filelist_check = _verify_cross_seed_filelist(
                    site_filelist, candidate
                )
                if not filelist_check.get("ok"):
                    raise ValueError(
                        "Tracker File List does not match candidate metainfo: "
                        + str(
                            filelist_check.get("error") or "unknown mismatch"
                        )
                    )
                print(
                    f"🧾 [Cross-Seed File List] [{site}] ID={torrent_id} "
                    f"verified={filelist_check.get('matched_count', 0)}/"
                    f"{filelist_check.get('file_count', 0)} files"
                )

            # Populate only after all tracker-side checks have completed.
            # A source-manifest failure on Node-A can then fall back to Node-B
            # without a second torrent download or a second File List request.
            shared_cache.update({
                "_cache_key": cache_key,
                "ready": True,
                "content": content,
                "candidate": candidate,
                "candidate_hash": candidate_hash,
                "site_filelist": site_filelist,
                "filelist_hidden": filelist_hidden,
                "filelist_check": filelist_check,
            })

        phase = "read source file manifest"
        source_files = source_node.get_torrent_file_manifest(source_hash)
        if not isinstance(source_files, list) or not source_files:
            raise ValueError("Unable to read source file manifest")

        def normalize_file_path(value):
            value = str(value or "").replace("\\", "/")

            if (
                not value
                or value.startswith("/")
                or re.match(r"^[A-Za-z]:/", value)
            ):
                raise ValueError("Absolute or empty file path")

            parts = [
                part for part in value.split("/")
                if part not in ("", ".")
            ]

            if not parts or any(part == ".." for part in parts):
                raise ValueError("Unsafe relative path")

            return "/".join(parts)

        phase = "compare source files against candidate manifest"
        source_pairs = []
        for item in source_files:
            source_pairs.append((
                normalize_file_path(item.get("path")),
                int(item.get("size_bytes")),
            ))

        candidate_pairs = []
        for item in candidate.get("files", []):
            candidate_pairs.append((
                normalize_file_path(item.get("path")),
                int(item.get("size_bytes")),
            ))

        if not source_pairs or not candidate_pairs:
            raise ValueError("Empty source or candidate file manifest")

        source_paths = [path for path, _ in source_pairs]
        candidate_paths = [path for path, _ in candidate_pairs]
        if len(set(source_paths)) != len(source_paths):
            raise ValueError("Duplicate source paths")
        if len(set(candidate_paths)) != len(candidate_paths):
            raise ValueError("Duplicate candidate paths")

        source_total = sum(size for _, size in source_pairs)
        candidate_total = sum(size for _, size in candidate_pairs)
        if source_total != source_size:
            raise ValueError(
                "Source manifest total differs from live torrent size"
            )
        if candidate_total <= 0:
            raise ValueError("Candidate manifest total is not positive")

        # Candidate torrents can contain additional cover/screenshot files.
        # Map every source file to one unique candidate file by exact bytes,
        # preferring normalized basename matches. Size-only mapping is allowed
        # only when extension and size identify a single candidate uniquely.
        unused = set(range(len(candidate_pairs)))
        source_file_mapping = []
        for source_path, source_file_size in source_pairs:
            source_base = _cross_seed_name_key(source_path)
            source_ext = source_base.rsplit(".", 1)[-1] if "." in source_base else ""
            same_name = [
                idx for idx in unused
                if _cross_seed_name_key(candidate_pairs[idx][0]) == source_base
            ]
            exact = [
                idx for idx in same_name
                if candidate_pairs[idx][1] == source_file_size
            ]

            if same_name and not exact:
                raise ValueError(
                    f"Same-name file has different size: {source_path!r}"
                )
            if len(exact) == 1:
                chosen = exact[0]
                mapping_method = "basename_and_exact_size"
            elif len(exact) > 1:
                raise ValueError(
                    f"Ambiguous duplicate candidate basename: {source_path!r}"
                )
            else:
                alternatives = []
                for idx in unused:
                    candidate_path, candidate_size = candidate_pairs[idx]
                    candidate_base = _cross_seed_name_key(candidate_path)
                    candidate_ext = candidate_base.rsplit(".", 1)[-1] if "." in candidate_base else ""
                    if candidate_size == source_file_size and candidate_ext == source_ext:
                        alternatives.append(idx)
                if len(alternatives) != 1:
                    raise ValueError(
                        "Source file could not be mapped uniquely to candidate "
                        f"by exact size and extension: {source_path!r} "
                        f"({source_file_size} bytes; choices={len(alternatives)})"
                    )
                chosen = alternatives[0]
                mapping_method = "unique_extension_and_exact_size"

            unused.remove(chosen)
            source_file_mapping.append({
                "source_path": source_path,
                "source_size_bytes": source_file_size,
                "candidate_path": candidate_pairs[chosen][0],
                "candidate_size_bytes": candidate_pairs[chosen][1],
                "match_method": mapping_method,
            })

        candidate_extra_files = [
            {"path": candidate_pairs[idx][0], "size_bytes": candidate_pairs[idx][1]}
            for idx in sorted(unused)
        ]

        if filelist_hidden and candidate_extra_files:
            raise ValueError(
                "Tracker File List is explicitly hidden; strict fallback "
                "rejects candidate with "
                f"{len(candidate_extra_files)} additional file(s)"
            )

        if candidate_hash == source_hash and candidate_extra_files:
            raise ValueError(
                "Same info hash unexpectedly produced an additional-file difference"
            )

        print(
            f"🧩 [Cross-Seed File Mapping] [{site}] "
            f"source_matched={len(source_file_mapping)}/{len(source_pairs)} | "
            f"candidate_files={len(candidate_pairs)} | "
            f"additional_candidate_files={len(candidate_extra_files)}"
        )

        action = (
            "same_info_hash_requires_tracker_check"
            if candidate_hash == source_hash
            else "different_info_hash_requires_paused_add_and_recheck"
        )

        verification_label = (
            "source_manifest_verified_filelist_hidden"
            if filelist_hidden
            else "filelist_manifest_verified"
        )
        print(
            f"✅ [Cross-Seed Preflight] [{site}] ID={torrent_id} "
            f"{verification_label} | files={len(candidate_pairs)} | "
            f"hash={candidate_hash[:12]}... | action={action}"
        )

        return {
            "ok": True,
            "status": (
                "source_manifest_verified_filelist_hidden_recheck_required"
                if filelist_hidden
                else "filelist_manifest_verified_recheck_required"
            ),
            "action": action,
            "candidate_info_hash": candidate_hash,
            "candidate_trackers": candidate["announce_urls"],
            "candidate_manifest": candidate,
            "filelist": {
                "url": site_filelist.get("url"),
                "title": site_filelist.get("title"),
                "visibility": "hidden" if filelist_hidden else "visible",
                "verification": (
                    "strict_source_candidate_manifest_fallback"
                    if filelist_hidden
                    else "web_filelist_matches_candidate_manifest"
                ),
                "web_file_count": len(site_filelist.get("entries", [])),
                "manifest_file_count": len(candidate_pairs),
                "matched_count": filelist_check.get("matched_count", 0),
                "matches": filelist_check.get("matches", []),
            },
            "source_file_mapping": source_file_mapping,
            "candidate_extra_files": candidate_extra_files,
            "candidate_total_size_bytes": candidate_total,
            "source_total_size_bytes": source_total,
            "content_verification": "NOT_VERIFIED_RECHECK_REQUIRED",
            "torrent_content": content,
        }

    except Exception as exc:
        return {
            "ok": False,
            "error": f"{phase}: {type(exc).__name__}: {exc}",
        }

    finally:
        # The caller owns this tab. It remains available for the next search zone.
        pass




# MAPPING_MULTINODE_PHASE_D_V1
async def _preflight_cross_seed_candidate(
    browser_tab,
    cross_seed_target,
    site,
    torrent_id,
    details_url,
    download_url,
):
    """Try live source copies until one passes the existing strict preflight.

    Candidate torrent download and tracker File List checks are retried only
    when the failure is source-specific. No qBittorrent mutation API is used.
    """
    if not isinstance(cross_seed_target, dict):
        return {"ok": False, "error": "Invalid Cross-Seed target"}

    raw_candidates = cross_seed_target.get("source_candidates")
    if isinstance(raw_candidates, (list, tuple)) and raw_candidates:
        candidates = list(raw_candidates)
    else:
        # Backward compatibility for old callers/targets without a candidate list.
        candidates = [{
            "node": cross_seed_target.get("source_node"),
            "n_cfg": cross_seed_target.get("source_node_config") or {},
            "node_name": cross_seed_target.get("source_node_name") or "",
            "torrent": {
                "name": cross_seed_target.get("source_torrent_name") or "",
                "size_bytes": cross_seed_target.get("source_size_bytes") or 0,
                "save_path": cross_seed_target.get("source_save_path") or "",
                "amount_left": 0,
                "progress": 1.0,
            },
        }]

    retryable_prefixes = (
        "validate source metadata:",
        "read source file manifest:",
        "compare source files against candidate manifest:",
    )
    candidate_errors = []
    valid_candidate_count = 0
    shared_preflight_cache = {}

    for candidate_index, candidate in enumerate(candidates, start=1):
        if not isinstance(candidate, dict):
            candidate_errors.append(
                f"candidate#{candidate_index}: invalid candidate record"
            )
            continue

        source_node = candidate.get("node")
        source_cfg = candidate.get("n_cfg") or {}
        source_torrent = candidate.get("torrent") or {}
        if not isinstance(source_cfg, dict):
            source_cfg = {}
        if not isinstance(source_torrent, dict):
            source_torrent = {}

        node_name = str(
            candidate.get("node_name")
            or getattr(source_node, "name", "Unknown Node")
        )

        attempt_target = dict(cross_seed_target)
        attempt_target.update({
            "source_node": source_node,
            "source_node_config": source_cfg,
            "source_node_name": node_name,
            # Do not borrow another Node's path/name/size when this
            # candidate is incomplete. The legacy fallback candidate above
            # already carries the original top-level values explicitly.
            "source_torrent_name": str(source_torrent.get("name") or ""),
            "source_size_bytes": source_torrent.get("size_bytes") or 0,
            "source_save_path": str(
                source_torrent.get("save_path") or ""
            ).strip(),
        })

        valid_candidate_count += 1
        result = await _preflight_cross_seed_candidate_single_source(
            browser_tab=browser_tab,
            cross_seed_target=attempt_target,
            site=site,
            torrent_id=torrent_id,
            details_url=details_url,
            download_url=download_url,
            _shared_preflight_cache=shared_preflight_cache,
        )

        if result.get("ok") is True:
            result.update({
                "source_hash": str(
                    attempt_target.get("source_hash") or ""
                ).strip().upper(),
                "source_node": source_node,
                "source_node_config": source_cfg,
                "source_node_name": node_name,
                "source_save_path": attempt_target.get("source_save_path", ""),
                "source_torrent_name": attempt_target.get("source_torrent_name", ""),
                "source_size_bytes": int(
                    attempt_target.get("source_size_bytes") or 0
                ),
                "source_candidate_index": candidate_index,
                "source_candidate_count": len(candidates),
            })
            print(
                f"✅ [Cross-Seed Source Selected] [{site}] "
                f"{node_name}: manifest passed "
                f"(candidate {candidate_index}/{len(candidates)})"
            )
            return result

        error = str(result.get("error") or "Unknown preflight error")
        candidate_errors.append(f"{node_name}: {error}")

        # Candidate download / tracker File List / metainfo failures are not
        # caused by the source Node. Do not repeat those web operations.
        if not error.startswith(retryable_prefixes):
            return result

    if valid_candidate_count == 0:
        return {
            "ok": False,
            "error": "No structurally valid live source candidate was available",
        }

    return {
        "ok": False,
        "error": (
            "All live source candidates failed manifest preflight: "
            + " | ".join(candidate_errors)
        ),
    }

async def scrape_site_search_nodriver(CFG, SET, active_nodes, stop_event, stealth_args, global_clean=None, cross_seed_target=None):
    """
    ฟังก์ชันสแกนและค้นหาทอร์เรนต์โดยใช้ nodriver (Browser Automation)
    รวมถึงการจัดการ Smart Download และคัดเลือก Node ตาม Capacity / Space
    """
    browser_instance = None
    site_page = None
    dl_session = None
    cross_seed_matches = []  # สำหรับโหมด Cross-Seed

    target_sites_cfg = [s for s in CFG.get('SITE', []) if s.get('enable', True)]
    print(f"📡 Detected Sites: {[s['name'] for s in target_sites_cfg]}")

    for site_cfg in target_sites_cfg:
        if stop_event.is_set():
            break

        site = site_cfg['name']

        current_site_seen_file = get_seen_file(site)
        seen_ids = load_data(current_site_seen_file)
        current_site_hash_file = get_hash_file(site)
        seen_hashes = load_data(current_site_hash_file)
        data_saved = False

        try:
            # -----------------------------------------------------------------
            # 1. BROWSER HEALTH CHECK & INITIALIZATION
            # -----------------------------------------------------------------
            is_browser_healthy = False
            if browser_instance is not None:
                try:
                    await browser_instance.update_targets()
                    is_browser_healthy = True
                except Exception as e:
                    print(
                        f"⚠️ [{site}] Browser connection lost: "
                        f"{type(e).__name__}: {e}. Resetting..."
                    )
                    browser_instance = None
                    site_page = None
                    dl_session = None

            if not is_browser_healthy:
                print(f"🌐 [{site}] Starting new Browser Instance...")
                browser_instance = await launch_any_browser(site, stealth_args)
                site_page = await browser_instance.get("about:blank", new_tab=True)
                dl_session = BrowserSessionWrapper(browser_instance)
            elif site_page is None:
                site_page = await browser_instance.get("about:blank", new_tab=True)
                dl_session = BrowserSessionWrapper(browser_instance)

            # -----------------------------------------------------------------
            # 2. SITE LOGIN & COOKIE SYNC
            # -----------------------------------------------------------------
            login_result = await safe_await(ensure_site_logged_in(site_page, site_cfg), "SiteLogin")
            
            if login_result is True:
                try:
                    if site_page is None:
                        site_page = await browser_instance.get("about:blank", new_tab=True)
                    
                    await asyncio.sleep(1)
                    
                    # Sync Cookies เข้า dl_session
                    if hasattr(browser_instance, 'cookies'):
                        all_cookies = await browser_instance.cookies.get_all()
                        raw_domain = site_cfg.get('base_url', '').split('//')[-1].split('/')[0].split(':')[0]
                        base_domain = raw_domain.lstrip('.')

                        synced_count = 0
                        for c in all_cookies:
                            is_dict = isinstance(c, dict)
                            c_name = c.get('name') if is_dict else getattr(c, 'name', None)
                            c_value = c.get('value') if is_dict else getattr(c, 'value', None)
                            c_domain = (c.get('domain') if is_dict else getattr(c, 'domain', '')) or ''
                            c_path = (c.get('path') if is_dict else getattr(c, 'path', '/')) or '/'
                            c_secure = c.get('secure', False) if is_dict else getattr(c, 'secure', False)

                            if not c_name or not c_value:
                                continue

                            clean_c_domain = c_domain.lstrip('.')
                            if base_domain in clean_c_domain or clean_c_domain in base_domain:
                                if hasattr(dl_session, 'cookies'):
                                    dl_session.cookies.set(c_name, c_value, domain=c_domain, path=c_path, secure=c_secure)
                                    dl_session.cookies.set(c_name, c_value, path=c_path)
                                    synced_count += 1

                        print(f"✅ [{site}] Synchronized {synced_count} cookies to HTTP session successfully.")

                except Exception as cookie_err:
                    print(f"⚠️ [{site}] Cookie sync issue: {cookie_err}")

                if cross_seed_target:
                    print(
                        f"🛡️ [{site}] Cross-Seed search mode: "
                        "skipping get_site_stats() and its maintenance actions."
                    )
                else:
                    ctx = BotContext(
                        active_nodes, dl_session, seen_hashes,
                        seen_ids, global_clean
                    )
                    stats_data = await get_site_stats(
                        site_page, site_cfg, ctx
                    )
                    if stats_data and isinstance(stats_data, str):
                        print(stats_data)
                        asyncio.create_task(send_notify(stats_data))

                # -------------------------------------------------------------
                # 3. TARGET SEARCH ZONES LOOP — PATCHED
                # -------------------------------------------------------------
                from urllib.parse import urljoin, urlsplit

                base_url = site_cfg.get("base_url", "").rstrip("/")

                if not base_url:
                    print(f"❌ [{site}] ไม่มี base_url ใน config")
                    continue

                #Normalize Cross-Seed input: รองรับ dict และ list[dict]
                if cross_seed_target:
                    if isinstance(cross_seed_target, dict):
                        cross_seed_targets = [cross_seed_target]
                    elif isinstance(cross_seed_target, (list, tuple)):
                        cross_seed_targets = [
                            item for item in cross_seed_target
                            if isinstance(item, dict)
                        ]
                    else:
                        cross_seed_targets = []

                    target_zones = []

                    site_key = str(site or "").strip().lower()
                    is_bearbit = any(
                        name in site_key for name in ("bearbit", "siambit")
                    )

                    if is_bearbit:
                        # ใช้โซนที่เปิดใช้งานใน config ปัจจุบัน
                        configured_zones = [
                            zone
                            for zone in (site_cfg.get("target_urls") or [])
                            if isinstance(zone, dict)
                            and zone.get("enable", True)
                            and str(zone.get("url") or "").strip()
                        ]

                        # Fallback หาก config ไม่มีรายการโซน
                        if not configured_zones:
                            configured_zones = [
                                {
                                    "name": "โซนปกติ (No 18+)",
                                    "url": "viewno18sbx.php",
                                    "enable": True,
                                },
                                {
                                    "name": "โซนพิเศษ",
                                    "url": "viewbrsb.php",
                                    "enable": True,
                                },
                            ]

                        # สร้าง Search target แยกตาม keyword และแต่ละโซน
                        for item in cross_seed_targets:
                            keyword = str(item.get("keyword") or "").strip()

                            if not keyword:
                                print(f"⚠️ [{site}] ข้าม Cross-Seed: keyword ว่าง")
                                continue

                            base_params = dict(item.get("search_params") or {})
                            base_params.setdefault("search_keyword", keyword)

                            for zone in configured_zones:
                                zone_params = dict(base_params)

                                # ใช้ URL ของโซนเป็นตัวกำหนด path สำหรับ Search
                                zone_params["target_path"] = zone["url"]

                                target_zones.append({
                                    "name": (
                                        f"Cross-Seed Search [{keyword}] "
                                        f"— {zone.get('name', 'Zone')}"
                                    ),
                                    "search_params": zone_params,
                                    "cross_seed_meta": item,
                                    "target_zone_meta": _build_mapping_target_zone_meta(site_cfg, zone),
                                })

                    elif any(
                        name in site_key for name in ("bitsuse", "dedbit")
                    ):
                        configured_zones = [
                            zone
                            for zone in (site_cfg.get("target_urls") or [])
                            if isinstance(zone, dict)
                            and zone.get("enable", True)
                            and str(zone.get("url") or "").strip()
                        ]

                        if not configured_zones:
                            fallback_path = (
                                "bs_browse.php"
                                if "bitsuse" in site_key
                                else "browse.php"
                            )
                            configured_zones = [{
                                "name": "Browse search",
                                "url": fallback_path,
                                "enable": True,
                            }]

                        for item in cross_seed_targets:
                            keyword = str(item.get("keyword") or "").strip()
                            if not keyword:
                                print(
                                    f"⚠️ [{site}] ข้าม Cross-Seed: keyword ว่าง"
                                )
                                continue

                            base_params = dict(item.get("search_params") or {})
                            base_params.setdefault("search_keyword", keyword)

                            for zone in configured_zones:
                                zone_params = dict(base_params)
                                zone_params["target_path"] = zone["url"]

                                target_zones.append({
                                    "name": (
                                        f"Cross-Seed Search [{keyword}] "
                                        f"— {zone.get('name', 'Browse')}"
                                    ),
                                    "search_params": zone_params,
                                    "cross_seed_meta": item,
                                    "target_zone_meta": _build_mapping_target_zone_meta(site_cfg, zone),
                                })

                    else:
                        # Tracker อื่นคง flow เดิมไว้
                        for item in cross_seed_targets:
                            keyword = str(item.get("keyword") or "").strip()

                            if not keyword:
                                print(f"⚠️ [{site}] ข้าม Cross-Seed: keyword ว่าง")
                                continue

                            params = dict(item.get("search_params") or {})
                            params.setdefault("search_keyword", keyword)

                            target_zones.append({
                                "name": f"Cross-Seed Search [{keyword}]",
                                "search_params": params,
                                "cross_seed_meta": item,
                            })

                else:
                    # Normal scan ไม่เปลี่ยนพฤติกรรมเดิม
                    target_zones = site_cfg.get("target_urls", [])

                for target_item in target_zones:
                    if stop_event.is_set():
                        break

                    added_in_zone = []
                    full_nodes_in_zone = []
                    count_skip = 0
                    cross_seed_matches_before_zone = (
                        len(cross_seed_matches) if cross_seed_target else 0
                    )
                    target_zone_meta = (
                        target_item.get("target_zone_meta")
                        if isinstance(target_item, dict)
                        else None
                    )
                    source_zone_meta = None
                    if not cross_seed_target and isinstance(target_item, dict):
                        source_zone_meta = _validate_mapping_target_zone_meta(
                            _build_mapping_target_zone_meta(site_cfg, target_item)
                        )
                        if source_zone_meta is None:
                            print(
                                f"⚠️ [{site}] Source Zone is not registered/enabled: "
                                f"{target_item.get('name', 'Unknown Zone')!r}"
                            )

                    # Cross-seed skipping is zone-scoped. known_sites and hash
                    # history are observations, not proof of a listing in this zone.
                    if cross_seed_target and isinstance(target_item, dict):
                        _zone_for_skip = _validate_mapping_target_zone_meta(
                            target_zone_meta
                        )
                        _source_for_skip = target_item.get("cross_seed_meta")
                        if _zone_for_skip and isinstance(_source_for_skip, dict):
                            _source_site_for_skip = (
                                _canonical_mapping_site(_source_for_skip.get("source_site"))
                                if _source_for_skip.get("source_site_verified") is True
                                else ""
                            )
                            _same_verified_source_zone = (
                                _source_site_for_skip == _zone_for_skip["site_key"]
                                and _source_for_skip.get("source_zone_verified") is True
                                and str(_source_for_skip.get("source_zone_id") or "").casefold()
                                    == _zone_for_skip["zone_id"]
                            )
                            _zone_listing_exists = any(
                                isinstance(_listing, dict)
                                and _listing.get("verified") is True
                                and _listing.get("verification_status")
                                    == "live_inventory_exact_manifest_tracker_verified"
                                and str(_listing.get("site_key") or "").upper()
                                    == _zone_for_skip["site_key"]
                                and str(_listing.get("zone_id") or "").casefold()
                                    == _zone_for_skip["zone_id"]
                                and re.fullmatch(
                                    r"[0-9A-F]{40}",
                                    str(_listing.get("infohash") or "").upper()
                                )
                                for _listing in (
                                    _source_for_skip.get("site_listings") or []
                                    if isinstance(_source_for_skip.get("site_listings"), (list, tuple))
                                    else []
                                )
                            )
                            if _same_verified_source_zone or _zone_listing_exists:
                                _reason = (
                                    "verified source listing in this zone"
                                    if _same_verified_source_zone
                                    else "verified Site Listing already exists in this zone"
                                )
                                print(
                                    f"↩️ [Cross-Seed] [{site}/{_zone_for_skip['zone_id']}] "
                                    f"skipped: {_reason}."
                                )
                                continue

                    # ---------------------------------------------------------
                    # A. Resolve target URL
                    # ---------------------------------------------------------
                    try:
                        site_page = await ensure_active_page(
                            browser_instance, site_page, site_cfg
                        )

                        if not site_page:
                            site_page = await browser_instance.get(
                                "about:blank", new_tab=True
                            )

                        if isinstance(target_item, dict):
                            if not target_item.get("enable", True):
                                continue

                            display_zone = target_item.get("name", "Zone")
                            search_params = target_item.get("search_params") or {}

                            # URL ที่กำหนดโดยตรงใน config มีลำดับความสำคัญก่อน
                            raw_target_path = target_item.get("url")

                            if not raw_target_path and search_params:
                                builder = globals().get("build_search_url")

                                if not callable(builder):
                                    print(
                                        f"❌ [{site}] ไม่มี build_search_url() "
                                        "ไม่สามารถสร้าง Search URL ได้"
                                    )
                                    continue

                                raw_target_path = builder(
                                    site_name=site,
                                    search_keyword=search_params.get("search_keyword", ""),
                                    selected_cats=search_params.get("selected_cats", []),
                                    subcat2=search_params.get("subcat2"),
                                    incldead=search_params.get("incldead", 1),
                                    freeload=search_params.get("freeload", False),
                                    bonus_upload=search_params.get("bonus_upload", False),
                                    searchin=search_params.get("searchin", 0),
                                    sortby=search_params.get("sortby", 0),
                                    quality_options=search_params.get("quality_options", {}),
                                    target_path=search_params.get("target_path"),
                                )

                            if not raw_target_path:
                                print(
                                    f"⚠️ [{site}] ข้าม [{display_zone}]: "
                                    "ไม่ได้ URL หรือสร้าง Search URL ไม่สำเร็จ"
                                )
                                continue
                        else:
                            display_zone = "Zone"
                            raw_target_path = str(target_item).strip()

                        raw_target_path = str(raw_target_path).strip()

                        if not raw_target_path:
                            print(f"⚠️ [{site}] ข้าม [{display_zone}]: URL ว่าง")
                            continue

                        target_url = urljoin(
                            base_url + "/",
                            raw_target_path
                        )

                        # ป้องกัน URL ผิด host จากข้อมูล config หรือ builder
                        base_parts = urlsplit(base_url)
                        target_parts = urlsplit(target_url)

                        if (
                            target_parts.scheme not in ("http", "https")
                            or target_parts.netloc.lower() != base_parts.netloc.lower()
                        ):
                            print(
                                f"❌ [{site}] URL ไม่ผ่านการตรวจสอบ: "
                                f"{target_url}"
                            )
                            continue

                        # One-shot diagnostic: compare BEARBIT keyword variants.
                        import os as _kw_os
                        from urllib.parse import (
                            parse_qsl as _kw_parse_qsl,
                            urlencode as _kw_urlencode,
                            urlunsplit as _kw_urlunsplit,
                        )

                        _kw_marker = _kw_os.path.join(
                            _kw_os.path.dirname(_kw_os.path.abspath(__file__)),
                            ".cross_seed_keyword_probe_done",
                        )

                        if (
                            cross_seed_target
                            and any(
                                token in str(site or "").lower()
                                for token in ("bearbit", "siambit")
                            )
                            and urlsplit(target_url).path.rsplit("/", 1)[-1]
                                == "viewno18sbx.php"
                            and not _kw_os.path.exists(_kw_marker)
                        ):
                            _kw_parts = urlsplit(target_url)
                            _kw_pairs = _kw_parse_qsl(
                                _kw_parts.query,
                                keep_blank_values=True,
                            )
                            _kw_full = next(
                                (
                                    value for key, value in _kw_pairs
                                    if key == "search"
                                ),
                                "",
                            )
                            _kw_no_ext = re.sub(
                                r"\.(?:mp4|mkv|avi|mov|wmv|iso|zip|rar)$",
                                "",
                                _kw_full,
                                flags=re.I,
                            ).strip()
                            _kw_code = _kw_no_ext.split()[0] if _kw_no_ext else ""

                            _kw_queries = []
                            for _kw_value in (
                                _kw_full,
                                _kw_no_ext,
                                _kw_code,
                            ):
                                if _kw_value and _kw_value not in _kw_queries:
                                    _kw_queries.append(_kw_value)

                            print(
                                f"[Cross-Seed Keyword Probe] "
                                f"target={display_zone!r} "
                                f"variants={_kw_queries!r}"
                            )

                            try:
                                for _kw_value in _kw_queries:
                                    _kw_new_pairs = [
                                        (key, value)
                                        for key, value in _kw_pairs
                                        if key != "search"
                                    ]
                                    _kw_new_pairs.append(("search", _kw_value))

                                    _kw_test_url = _kw_urlunsplit((
                                        _kw_parts.scheme,
                                        _kw_parts.netloc,
                                        _kw_parts.path,
                                        _kw_urlencode(_kw_new_pairs),
                                        _kw_parts.fragment,
                                    ))

                                    try:
                                        await site_page.get(_kw_test_url)
                                        await asyncio.sleep(2.5)

                                        _kw_html = await site_page.get_content() or ""
                                        _kw_soup = BeautifulSoup(
                                            _kw_html, "html.parser"
                                        )
                                        _kw_body = re.sub(
                                            r"\s+",
                                            " ",
                                            _kw_soup.get_text(" ", strip=True),
                                        )

                                        _kw_detail_rx = re.compile(
                                            r"(?:^|/)details(?:new)?\.php\?id=\d+(?:[&#]|$)",
                                            re.I,
                                        )
                                        _kw_details = _kw_soup.find_all(
                                            "a", href=_kw_detail_rx
                                        )

                                        _kw_non_profile_links = []
                                        for _kw_a in _kw_soup.find_all(
                                            "a", href=True
                                        ):
                                            _kw_href = str(
                                                _kw_a.get("href", "")
                                            )
                                            if not re.search(
                                                r"details|detail|download|torrent|topic|file",
                                                _kw_href,
                                                re.I,
                                            ):
                                                continue
                                            if re.search(
                                                r"userdetails|bookmarks|bonuslog",
                                                _kw_href,
                                                re.I,
                                            ):
                                                continue

                                            _kw_non_profile_links.append((
                                                _kw_href.split("?", 1)[0][:120],
                                                _kw_a.get_text(" ", strip=True)[:60],
                                            ))
                                            if len(_kw_non_profile_links) >= 8:
                                                break

                                        _kw_no_result = []
                                        for _kw_phrase in (
                                            "ไม่พบ",
                                            "ไม่มีรายการ",
                                            "ไม่มีข้อมูล",
                                            "No torrents found",
                                        ):
                                            _kw_pos = _kw_body.casefold().find(
                                                _kw_phrase.casefold()
                                            )
                                            if _kw_pos >= 0:
                                                _kw_no_result.append(
                                                    _kw_body[
                                                        max(0, _kw_pos - 100):
                                                        _kw_pos + 180
                                                    ]
                                                )

                                        _kw_title = (
                                            _kw_soup.title.get_text(" ", strip=True)
                                            if _kw_soup.title else "(no title)"
                                        )

                                        print(
                                            f"[Cross-Seed Keyword Probe] "
                                            f"query={_kw_value!r} "
                                            f"title={_kw_title!r} "
                                            f"html_chars={len(_kw_html)} "
                                            f"details_links={len(_kw_details)} "
                                            f"tr_count={len(_kw_soup.find_all('tr'))} "
                                            f"no_result_context={_kw_no_result!r} "
                                            f"other_links={_kw_non_profile_links!r}"
                                        )

                                    except Exception as _kw_exc:
                                        print(
                                            f"[Cross-Seed Keyword Probe] "
                                            f"query={_kw_value!r} "
                                            f"ERROR={type(_kw_exc).__name__}: "
                                            f"{_kw_exc}"
                                        )

                            finally:
                                try:
                                    with open(
                                        _kw_marker, "w", encoding="utf-8"
                                    ) as _kw_file:
                                        _kw_file.write(
                                            f"Completed {datetime.now().isoformat()}\n"
                                        )
                                except OSError as _kw_marker_exc:
                                    print(
                                        "[Cross-Seed Keyword Probe] "
                                        f"Marker write failed: {_kw_marker_exc}"
                                    )

                            print(
                                "[Cross-Seed Keyword Probe] "
                                "จบการทดสอบ จะกลับไป URL ค้นหาปกติ"
                            )

                        print(
                            f"\n🌐 [{site}] Navigating Zone: "
                            f"[{display_zone}] -> {target_url}"
                        )

                    except Exception as e:
                        print(
                            f"❌ [{site}] สร้าง Target URL ไม่สำเร็จ: "
                            f"{type(e).__name__}: {e}"
                        )
                        continue

                    # ---------------------------------------------------------
                    # B. Navigate + retry หน้าแจ้ง external link / Cloudflare
                    # ---------------------------------------------------------
                    try:
                        page_source = ""

                        for attempt in range(2):
                            await site_page.get(target_url)
                            await asyncio.sleep(2.5)

                            page_source = await site_page.get_content() or ""
                            soup = BeautifulSoup(page_source, "html.parser")

                            if not page_source.strip():
                                print(
                                    f"⚠️ [{site}] HTML ว่าง "
                                    f"(attempt {attempt + 1}/2)"
                                )
                                continue

                            if is_cloudflare(soup):
                                print(
                                    f"🛡 [{site}] Cloudflare detected "
                                    f"(attempt {attempt + 1}/2)"
                                )

                                if attempt == 0:
                                    await asyncio.sleep(10)
                                    continue

                                print(
                                    f"⚠️ [{site}] ยังติด Cloudflare "
                                    "ข้ามโซนนี้"
                                )
                                break

                            if "ไม่สามารถเปิดลิงก์จากภายนอกได้" in soup.get_text(
                                " ", strip=True
                            ):
                                if attempt == 0:
                                    await site_page.get(
                                        urljoin(base_url + "/", "index.php")
                                    )
                                    await asyncio.sleep(1.5)
                                    continue

                                print(
                                    f"⚠️ [{site}] ยังพบหน้าแจ้ง "
                                    "ไม่สามารถเปิดลิงก์จากภายนอกได้"
                                )
                                break

                            # ได้หน้าเว็บที่ไม่ตรงกับ error ที่รู้จัก
                            # ให้ส่งต่อไปยังขั้นตอน parser เดิม
                            break

                        else:
                            soup = BeautifulSoup("", "html.parser")

                        if not page_source.strip():
                            continue

                        # ตรวจสถานะสุดท้ายหลัง retry ด้วย
                        final_text = soup.get_text(" ", strip=True)

                        if is_cloudflare(soup):
                            continue

                        if "ไม่สามารถเปิดลิงก์จากภายนอกได้" in final_text:
                            continue

                        # -----------------------------------------------------
                        # สำคัญ:
                        # ต่อจากตรงนี้ให้ใช้โค้ดเดิมในส่วน 4. PARSE RESULTS
                        # และการตรวจ torrent / cross_seed_matches
                        # โดยไม่สร้าง parser ชุดใหม่ซ้ำ
                        # -----------------------------------------------------

                    except Exception as e:
                        print(
                            f"❌ [{site}] Navigation Error "
                            f"[{display_zone}]: {type(e).__name__}: {e}"
                        )
                        continue

                    # ---------------------------------------------------------
                    # BEARBIT Cross-Seed fallback search (fail-closed)
                    # ใช้เมื่อคำค้นหาหลักไม่มี torrent detail links เท่านั้น
                    # ส่งผลที่พบเข้า parser/preflight เดิม ไม่มีการเพิ่ม torrent ที่นี่
                    # ---------------------------------------------------------
                    try:
                        from urllib.parse import (
                            parse_qsl as _fb_parse_qsl,
                            urlencode as _fb_urlencode,
                            urlsplit as _fb_urlsplit,
                            urlunsplit as _fb_urlunsplit,
                        )

                        _fb_site_key = str(site or "").strip().lower()
                        _fb_path = _fb_urlsplit(target_url).path.rsplit(
                            "/", 1
                        )[-1]

                        _fb_enabled = (
                            bool(cross_seed_target)
                            and any(
                                token in _fb_site_key
                                for token in ("bearbit", "siambit")
                            )
                            and _fb_path in {
                                "viewno18sbx.php",
                                "viewbrsb.php",
                            }
                        )

                        _fb_detail_rx = re.compile(
                            r"(?:^|/)(?:bs_)?details(?:new)?"
                            r"\.php\?id=\d+(?:[&#]|$)",
                            re.I,
                        )

                        _fb_existing_details = (
                            soup.find_all("a", href=_fb_detail_rx)
                            if soup else []
                        )

                        if _fb_enabled and not _fb_existing_details:
                            _fb_parts = _fb_urlsplit(target_url)
                            _fb_pairs = _fb_parse_qsl(
                                _fb_parts.query,
                                keep_blank_values=True,
                            )
                            _fb_original_search = next(
                                (
                                    value for key, value in _fb_pairs
                                    if key == "search"
                                ),
                                "",
                            ).strip()

                            # รองรับรหัสนำหน้า เช่น CAD-031, MIDA-846,
                            # GVG-954 โดยตัดส่วน suffix ที่ต่อด้วย _ หรือ [
                            _fb_code_match = re.match(
                                r"^\s*([A-Za-z]{2,12}-\d{2,6})"
                                r"(?=$|[^A-Za-z0-9])",
                                _fb_original_search,
                                re.I,
                            )
                            _fb_fallbacks = []

                            if _fb_code_match:
                                _fb_code = _fb_code_match.group(1).strip()
                                if (
                                    _fb_code
                                    and _fb_code.casefold()
                                    != _fb_original_search.casefold()
                                ):
                                    _fb_fallbacks.append(_fb_code)

                            if _fb_fallbacks:
                                print(
                                    "[Cross-Seed Fallback] "
                                    f"[{site}] primary search has no detail "
                                    f"links; trying {_fb_fallbacks!r} "
                                    f"on {_fb_path}"
                                )

                            for _fb_keyword in _fb_fallbacks:
                                _fb_new_pairs = [
                                    (key, value)
                                    for key, value in _fb_pairs
                                    if key != "search"
                                ]
                                _fb_new_pairs.append(("search", _fb_keyword))

                                _fb_test_url = _fb_urlunsplit((
                                    _fb_parts.scheme,
                                    _fb_parts.netloc,
                                    _fb_parts.path,
                                    _fb_urlencode(_fb_new_pairs),
                                    _fb_parts.fragment,
                                ))

                                try:
                                    await site_page.get(_fb_test_url)
                                    await asyncio.sleep(2.5)

                                    _fb_html = (
                                        await site_page.get_content() or ""
                                    )
                                    if not _fb_html.strip():
                                        print(
                                            "[Cross-Seed Fallback] "
                                            f"[{site}] keyword={_fb_keyword!r} "
                                            "returned empty HTML"
                                        )
                                        continue

                                    _fb_soup = BeautifulSoup(
                                        _fb_html, "html.parser"
                                    )

                                    if is_cloudflare(_fb_soup):
                                        print(
                                            "[Cross-Seed Fallback] "
                                            f"[{site}] Cloudflare detected; "
                                            "rejecting fallback page"
                                        )
                                        continue

                                    _fb_text = _fb_soup.get_text(
                                        " ", strip=True
                                    )
                                    if (
                                        "ไม่สามารถเปิดลิงก์จากภายนอกได้"
                                        in _fb_text
                                    ):
                                        print(
                                            "[Cross-Seed Fallback] "
                                            f"[{site}] external-link error; "
                                            "rejecting fallback page"
                                        )
                                        continue

                                    _fb_found = _fb_soup.find_all(
                                        "a", href=_fb_detail_rx
                                    )
                                    print(
                                        "[Cross-Seed Fallback] "
                                        f"[{site}] keyword={_fb_keyword!r} "
                                        f"details_links={len(_fb_found)}"
                                    )

                                    # เปลี่ยนไปใช้ผล fallback เฉพาะเมื่อพบ
                                    # detail links จริง แล้วใช้ parser เดิมต่อ
                                    if _fb_found:
                                        soup = _fb_soup
                                        page_source = _fb_html
                                        target_url = _fb_test_url
                                        print(
                                            "[Cross-Seed Fallback] "
                                            f"[{site}] accepted fallback "
                                            f"keyword={_fb_keyword!r}; "
                                            "continuing with existing parser"
                                        )
                                        break

                                except Exception as _fb_exc:
                                    print(
                                        "[Cross-Seed Fallback] "
                                        f"[{site}] keyword={_fb_keyword!r} "
                                        f"failed: {type(_fb_exc).__name__}: "
                                        f"{_fb_exc}"
                                    )
                        elif _fb_enabled:
                            print(
                                "[Cross-Seed Fallback] "
                                f"[{site}] primary search already has "
                                f"{len(_fb_existing_details)} detail links; "
                                "no fallback needed"
                            )

                    except Exception as _fb_outer_exc:
                        # ความผิดพลาดใน fallback ต้องไม่ทำให้ parser
                        # เดิมล้ม หรือยกเลิก fail-closed
                        print(
                            "[Cross-Seed Fallback] "
                            f"[{site}] safely skipped: "
                            f"{type(_fb_outer_exc).__name__}: "
                            f"{_fb_outer_exc}"
                        )

                    # ---------------------------------------------------------
                    # 4. ROW EXTRACT & PARSING
                    # ---------------------------------------------------------
                    all_details = soup.find_all("a", href=re.compile(r"(?:^|/)(?:bs_)?details(?:new)?\.php\?id=\d+(?:[&#]|$)", re.I))
                    if not all_details:
                        _cs_debug_count = 0
                        for _cs_a in soup.find_all('a', href=True):
                            _cs_href = str(_cs_a.get('href', ''))
                            if not re.search(r'detail|torrent|download|view|browse|topic|file|show|id=', _cs_href, re.I):
                                continue
                            _cs_path = _cs_href.split('?', 1)[0].split('#', 1)[0][:160]
                            _cs_query_match = re.search(r'[?&](id|tid|torrentid)=(\d+)(?:&|$)', _cs_href, re.I)
                            if _cs_query_match:
                                _cs_safe_href = _cs_path + '?' + _cs_query_match.group(1) + '=' + _cs_query_match.group(2)
                            elif '?' in _cs_href:
                                _cs_safe_href = _cs_path + '?[query-redacted]'
                            else:
                                _cs_safe_href = _cs_path
                            _cs_text = _cs_a.get_text(' ', strip=True)[:70]
                            _cs_debug_count += 1
                            if _cs_debug_count <= 18:
                                print(f"[Cross-Seed Link Inventory] href={_cs_safe_href!r} text={_cs_text!r}")
                        print(f"[Cross-Seed Link Inventory] relevant_links={_cs_debug_count}")
                        print(f"[Cross-Seed Form Inventory] forms={len(soup.find_all(chr(102)+chr(111)+chr(114)+chr(109)))}")
                        for _cs_fi, _cs_form in enumerate(soup.find_all("form")[:10], 1):
                            _cs_action = str(_cs_form.get("action", ""))[:160]
                            _cs_method = str(_cs_form.get("method", "get")).lower()
                            print(f"[Cross-Seed Form Inventory] form={_cs_fi} action={_cs_action!r} method={_cs_method!r}")
                            _cs_shown = 0
                            for _cs_el in _cs_form.find_all(["input", "select", "button", "textarea"]):
                                _cs_name = str(_cs_el.get("name", ""))
                                _cs_type = str(_cs_el.get("type", _cs_el.name)).lower()
                                if re.search(r"pass|token|csrf|cookie|session|auth", _cs_name, re.I):
                                    continue
                                _cs_relevant = bool(re.search(r"search|keyword|sort|cat|subcat|incldead|freeload|bonus|quality|ajax", _cs_name, re.I)) or _cs_type in ("text", "search", "submit") or _cs_el.name in ("button", "textarea")
                                if not _cs_relevant:
                                    continue
                                if _cs_shown >= 20:
                                    break
                                _cs_value = str(_cs_el.get("value", ""))[:100] if re.search(r"search|keyword|sort|cat|subcat|incldead|freeload|bonus|quality|ajax", _cs_name, re.I) else "(omitted)"
                                _cs_checked = bool(_cs_el.has_attr("checked"))
                                _cs_selected = [
                                    str(o.get("value", ""))
                                    for o in _cs_el.find_all("option")
                                    if o.has_attr("selected")
                                ] if _cs_el.name == "select" else []
                                print(
                                    f"[Cross-Seed Form Inventory] "
                                    f"control tag={_cs_el.name} "
                                    f"name={_cs_name!r} type={_cs_type!r} "
                                    f"value={_cs_value!r} "
                                    f"checked={_cs_checked} "
                                    f"selected={_cs_selected!r}"
                                )
                                if _cs_el.name == "select":
                                    _cs_opts = [(str(o.get("value", "")), o.get_text(" ", strip=True)[:40], bool(o.has_attr("selected"))) for o in _cs_el.find_all("option")[:15]]
                                    print(f"[Cross-Seed Form Inventory] options name={_cs_name!r} values={_cs_opts!r}")
                                _cs_shown += 1
                        _cs_all_rows = soup.find_all('tr')
                        for _cs_i, _cs_tr in enumerate(_cs_all_rows[-8:], start=max(1, len(_cs_all_rows) - 7)):
                            _cs_row_text = _cs_tr.get_text(' ', strip=True)[:220]
                            if _cs_row_text:
                                print(f"[Cross-Seed Row Inventory] row={_cs_i} text={_cs_row_text!r}")
                    rows = []
                    if cross_seed_target:
                        page_title = soup.title.get_text(" ", strip=True) if soup.title else "(no title)"
                        preview = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))[:180]
                        tr_count = len(soup.find_all("tr"))
                        print(
                            f"🔎 [Cross-Seed Parse Debug] [{site}] html_chars={len(page_source)} "
                            f"title={page_title!r} details_links={len(all_details)} "
                            f"tr_count={tr_count} preview={preview!r}"
                        )
                    if cross_seed_target:
                        for link_i, link in enumerate(all_details[:8], 1):
                            ancestor_tags = []
                            parent = link.parent
                            for _ in range(5):
                                if parent is None:
                                    break
                                classes = parent.get('class', [])
                                if isinstance(classes, str):
                                    classes = [classes]
                                suffix = '.' + '.'.join(classes) if classes else ''
                                ancestor_tags.append(f'{parent.name}{suffix}')
                                parent = parent.parent
                            link_href = link.get('href')
                            link_text = link.get_text(' ', strip=True)[:100]
                            print(
                                f'🔎 [Cross-Seed Link Debug] [{site}] #{link_i} '
                                f'href={link_href!r} text={link_text!r} '
                                f'ancestors={ancestor_tags}'
                            )
                    for a in all_details:
                        if stop_event.is_set(): 
                            break
                        if len(a.get_text(strip=True)) <= 5: 
                            continue
                        parent_tr = a.find_parent("tr")
                        if parent_tr and parent_tr not in rows:
                            row_raw_text = parent_tr.get_text().lower()
                            user_stat_keywords = ['ratio:', 'bonus:', 'upload:', 'download:', 'อัพโหลด:', 'ดาวน์โหลด:']
                        
                            if any(key in row_raw_text for key in user_stat_keywords):
                                continue
                            rows.append(parent_tr)

                    if cross_seed_target:
                        print(f"🔎 [Cross-Seed Parse Debug] [{site}] candidate_rows={len(rows)}")
                    for row in rows:
                        if stop_event.is_set(): 
                            break
                        
                        t_id = None
                        try:
                            extract_headers = {
                                'User-Agent': stealth_args.get("user_agent", ""),
                                'Referer': target_url
                            }
                            data = await extract_torrent_data(row, base_url, dl_session, extract_headers)
                            if cross_seed_target:
                                is_dict = isinstance(data, dict)
                                extracted_id = bool(is_dict and data.get('id'))
                                has_download = bool(is_dict and data.get('download_url'))
                                size_value = data.get('size_str') if is_dict else None
                                row_preview = row.get_text(" ", strip=True)[:140]
                                print(f"🔎 [Cross-Seed Extract Debug] [{site}] type={type(data).__name__} id_ok={extracted_id} download_ok={has_download} size={size_value!r} row={row_preview!r}")
                            if not data or not data.get('id'): 
                                continue

                            t_id = str(data['id'])
                            raw_title = data.get('title', 'Unknown')
                            download_url = data['download_url']
                            details_url = data['details_url']

                            if not download_url:
                                with open(f"debug_failed_{t_id}.html", "w", encoding="utf-8") as f:
                                    f.write(page_source)
                                print(f" ⚠️ [{t_id}] ข้าม: ไม่พบลิงก์ดาวน์โหลด")
                                continue
                                    
                            safe_title = clean_name(raw_title)
                            is_stat = any(word in safe_title.lower() for word in ['ratio', 'bonus', 'upload', 'download'])
                                        
                            if not is_stat and len(safe_title) >= 10:
                                t_name = safe_title
                            else:
                                t_name = f"Torrent_ID_{t_id}"
                            
                            size_str = str(data.get("size_str") or "")
                            t_size_gb = parse_size(size_str)

                            # ใช้ขนาดจริงจาก extractor ก่อน ถ้ามี
                            raw_size_bytes = data.get("size_bytes")

                            try:
                                exact_size_bytes = int(raw_size_bytes or 0)
                            except (TypeError, ValueError, OverflowError):
                                exact_size_bytes = 0

                            if exact_size_bytes > 0:
                                t_size_bytes = exact_size_bytes
                                tolerance_bytes = 1024 * 1024
                                size_source = "exact_bytes"

                            else:
                                # ไม่มีขนาดเป็นไบต์ จึงต้องคำนวณจากข้อความที่แสดงบนหน้าเว็บ
                                t_size_bytes = int(round(t_size_gb * (1024 ** 3)))
                                tolerance_bytes = 1024 * 1024
                                size_source = "display_size"

                                # ประมาณความคลาดเคลื่อนจากการปัดเศษตามความละเอียดที่แสดง
                                size_match = re.search(
                                    r"(\d+(?:[.,]\d+)?)\s*(TB|GB|MB|KB|B)\b",
                                    size_str,
                                    re.IGNORECASE,
                                )

                                if size_match:
                                    number_text = size_match.group(1)
                                    unit = size_match.group(2).upper()

                                    if "." in number_text:
                                        decimal_places = len(number_text.split(".", 1)[1])
                                    elif "," in number_text:
                                        decimal_places = len(number_text.split(",", 1)[1])
                                    else:
                                        decimal_places = 0

                                    # parse_size() ของระบบใช้หน่วย GB โดย GB = 1
                                    unit_factor_gb = {
                                        "TB": 1024,
                                        "GB": 1,
                                        "MB": 1 / 1024,
                                        "KB": 1 / (1024 ** 2),
                                        "B": 1 / (1024 ** 3),
                                    }

                                    factor = unit_factor_gb.get(unit)

                                    if factor is not None and decimal_places > 0:
                                        rounding_tolerance = int(
                                            0.5
                                            * (10 ** (-decimal_places))
                                            * factor
                                            * (1024 ** 3)
                                        )

                                        # อนุญาตเฉพาะกรณีที่ความคลาดเคลื่อนอธิบายได้
                                        # ด้วยการปัดเศษ และจำกัดไม่เกิน 8 MiB
                                        if rounding_tolerance <= 8 * 1024 * 1024:
                                            tolerance_bytes = max(
                                                tolerance_bytes,
                                                rounding_tolerance + 1,
                                            )

                            # CROSS-SEED MATCHING MODE
                            if cross_seed_target:
                                source_site_key = re.sub(
                                    r"[^a-z0-9]+",
                                    "",
                                    str(
                                        cross_seed_target.get("source_site") or ""
                                    ).casefold(),
                                )

                                candidate_site_key = re.sub(
                                    r"[^a-z0-9]+",
                                    "",
                                    str(site or "").casefold(),
                                )

                                try:
                                    target_bytes = int(
                                        cross_seed_target.get("target_size_bytes", 0) or 0
                                    )
                                except (TypeError, ValueError, OverflowError):
                                    target_bytes = 0

                                size_diff = abs(t_size_bytes - target_bytes)

                                print(
                                    f"🔎 [Cross-Seed Size Check] [{site}] ID={t_id} | "
                                    f"size_str={size_str!r} | source={size_source} | "
                                    f"web={t_size_bytes} | target={target_bytes} | "
                                    f"diff={size_diff} bytes | tolerance={tolerance_bytes} bytes"
                                )

                                if (
                                    target_bytes > 0
                                    and size_diff <= tolerance_bytes
                                ):
                                    print(
                                        f"🎯 [Cross-Seed Match!] [{site}] "
                                        f"ID: {t_id} | {raw_title[:60]} | "
                                        f"Size: {t_size_bytes} Bytes"
                                    )

                                    _cs_preflight = await _preflight_cross_seed_candidate(
                                        browser_tab=site_page,
                                        cross_seed_target=cross_seed_target,
                                        site=site,
                                        torrent_id=t_id,
                                        details_url=details_url,
                                        download_url=download_url,
                                    )
                                    if not _cs_preflight.get("ok"):
                                        print(
                                            f"⛔ [Cross-Seed Preflight Rejected] [{site}] "
                                            f"ID={t_id}: {_cs_preflight.get('error', 'Unknown error')}"
                                        )
                                        continue
                                    cross_seed_matches.append({
                                        "torrent_id": t_id,
                                        "site_name": site,
                                        "target_zone_meta": (
                                            dict(target_zone_meta)
                                            if isinstance(target_zone_meta, dict)
                                            else None
                                        ),
                                        "release_name": raw_title,
                                        "details_url": details_url,
                                        "download_url": download_url,
                                        "web_size_bytes": t_size_bytes,
                                        "size_str": size_str,

                                        # Keep exact source context for a later,
                                        # verified add stage.
                                        "source_hash": _cs_preflight.get("source_hash", cross_seed_target.get("source_hash", "")),
                                        "source_node": _cs_preflight.get("source_node"),
                                        "source_node_config": _cs_preflight.get("source_node_config"),
                                        "source_node_name": _cs_preflight.get("source_node_name", ""),
                                        "source_save_path": _cs_preflight.get("source_save_path", ""),
                                        "source_torrent_name": _cs_preflight.get("source_torrent_name", ""),
                                        "source_size_bytes": _cs_preflight.get("source_size_bytes", 0),
                                        "source_site": cross_seed_target.get("source_site", "Unknown"),
                                        "source_site_verified": (
                                            cross_seed_target.get("source_site_verified") is True
                                        ),
                                        "source_zone_id": cross_seed_target.get("source_zone_id", "unknown"),
                                        "source_zone_verified": (
                                            cross_seed_target.get("source_zone_verified") is True
                                        ),
                                        "source_zone_evidence": cross_seed_target.get("source_zone_evidence"),
                                        "known_sites": list(
                                            cross_seed_target.get("known_sites") or []
                                        ),
                                        "preflight_status": _cs_preflight.get("status"),
                                        "preflight_action": _cs_preflight.get("action"),
                                        "candidate_info_hash": _cs_preflight.get("candidate_info_hash"),
                                        "candidate_trackers": _cs_preflight.get("candidate_trackers", []),
                                        "candidate_manifest": _cs_preflight.get("candidate_manifest"),
                                        "torrent_content": _cs_preflight.get("torrent_content"),
                                        "candidate_filelist": _cs_preflight.get("filelist", {}),
                                        "source_file_mapping": _cs_preflight.get("source_file_mapping", []),
                                        "candidate_extra_files": _cs_preflight.get("candidate_extra_files", []),
                                    })
                                else:
                                    print(
                                        f"❌ [Cross-Seed No Match] [{site}] "
                                        f"ID={t_id} | ขนาดต่าง {size_diff} bytes"
                                    )

                                continue

                            # 🎯 NORMAL AUTO-GRAB MODE
                            print(f"🔍 [{site.upper()}] Checking: {t_name[:50]}... (ID: {t_id})")
                            if not is_fresh_and_racing(data):
                                count_skip += 1
                                continue 
                            if t_id in seen_ids:
                                print(f" ❌ ข้าม: เคยเพิ่มไปแล้ว (ใน {site})")
                                count_skip += 1
                                continue

                            if not (SET.get('MIN_SIZE_GB', 0) <= t_size_gb <= SET.get('MAX_SIZE_GB', 999)):
                                print(f" ❌ ข้าม: ขนาด {t_size_gb:.2f}GB ไม่ตรงเงื่อนไข")
                                seen_ids.add(t_id)
                                count_skip += 1
                                continue

                            is_free_to_go = False
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
                            elif "bearbit" in site_name:
                                free_p = check_freeload_status(row)
                                if item_discount > 0:
                                    if free_p > item_discount:
                                        print(f" ❌ ข้าม: หน้าเว็บฟรี {free_p}% ซึ่งดีกว่าไอเทม {item_discount}%")
                                        count_skip += 1
                                        continue
                                    else:
                                        is_free_to_go = True
                                        print(f" 🎫 [ITEM MODE] บังคับใช้ไอเทม {item_discount}% (หน้าเว็บฟรี {free_p}%)")
                                else:
                                    if free_p >= min_free_req:
                                        is_free_to_go = True
                                        print(f" ✅ [NORMAL MODE] หน้าเว็บฟรี {free_p}% ผ่านเกณฑ์ขั้นต่ำ ({min_free_req}%)")
                                    else:
                                        print(f" ❌ ข้าม: หน้าเว็บ ({free_p}%) ต่ำกว่าเกณฑ์ที่กำหนด ({min_free_req}%)")
                                        count_skip += 1
                                        continue
                            else:
                                free_p_others = check_freeload_status(row)
                                if free_p_others == 100:
                                    is_free_to_go = True
                                else:
                                    print(f" ❌ ข้าม: ไฟล์นี้ไม่ฟรี 100% ({free_p_others}%)")
                                    count_skip += 1
                                    continue

                            if not is_free_to_go:
                                continue

                            # -------------------------------------------------
                            # 5. SMART DOWNLOAD & NODE ALLOCATION LOGIC
                            # -------------------------------------------------
                            current_url = "unknown"
                            local_headers = {
                                'User-Agent': stealth_args.get("user_agent", ""),
                                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                                'Accept-Language': 'th-TH,th;q=0.9,en-US;q=0.8,en;q=0.7',
                                'Connection': 'keep-alive',
                                'Referer': details_url
                            }

                            print(f"🚀 เริ่มดาวน์โหลดไฟล์: {t_id}")
                            r_dl = None
                            try:
                                r_dl = await safe_timeout(dl_session.get(download_url, headers=local_headers), 30)
                            except asyncio.TimeoutError:
                                print("หมดเวลาดาวน์โหลด!")

                            if not r_dl:
                                raise Exception("ไม่สามารถดึงข้อมูลจาก BrowserSessionWrapper ได้")

                            current_url = getattr(getattr(dl_session, 'browser', None), 'main_tab', site_page).url if hasattr(getattr(dl_session, 'browser', None), 'main_tab') else "unknown"
                            raw_data_bytes = getattr(r_dl, 'content', b'')
                            raw_data_text = getattr(r_dl, 'text', '')
                            is_torrent = raw_data_bytes.startswith(b'd8:')

                            t_hash = None
                            download_ready = False

                            if is_torrent:
                                t_hash = extract_info_hash(raw_data_bytes)
                                if t_hash:
                                    download_ready = True
                                    print(f"✅ พบไฟล์ทอร์เรนต์ Hash: {t_hash}")
                            else:
                                print(f"🔄 พบปัญหาการดาวน์โหลด (URL: {current_url}), กำลังเข้าสู่โหมดกู้คืนการคลิกผ่าน Browser...")
                                tab_target = site_page if site_page else (dl_session.browser.main_tab if hasattr(dl_session, 'browser') else None)
                                raw_content = await download_torrent_smart(
                                    tab_target, 
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

                            # ---------------------------------------------------------
                            # ---------------------------------------------------------
                            # 6. CHECK DUPLICATE HASH & SYNC
                            # ---------------------------------------------------------
                            if download_ready:
                                # Site history prevents repeated processing,
                                # but never proves that a torrent exists on a Node.
                                seen_hash_duplicate = t_hash in seen_hashes

                                matched_nodes = []
                                inventory_check_failed = not bool(active_nodes)
                                checked_node_names = set()

                                # Check every Active Node. Do not stop at
                                # the first positive match.
                                for node_obj, _ in active_nodes:
                                    node_name = str(
                                        getattr(
                                            node_obj,
                                            "name",
                                            ""
                                        ) or ""
                                    ).strip()

                                    if not node_name:
                                        inventory_check_failed = True
                                        print(
                                            "⚠️ [Mapper] Active Node "
                                            "has no usable Node name"
                                        )
                                        continue

                                    node_key = node_name.casefold()

                                    if node_key in checked_node_names:
                                        inventory_check_failed = True
                                        print(
                                            f"⚠️ [Mapper] Duplicate Active Node "
                                            f"name: {node_name!r}"
                                        )
                                        continue

                                    checked_node_names.add(node_key)

                                    exists_method = getattr(
                                        node_obj,
                                        "is_torrent_exists",
                                        None
                                    )

                                    if not callable(exists_method):
                                        inventory_check_failed = True
                                        print(
                                            f"⚠️ [Mapper] [{node_name}] "
                                            "ไม่มี Hash verification method"
                                        )
                                        continue

                                    try:
                                        exists = exists_method(t_hash)
                                    except Exception as check_error:
                                        exists = None
                                        print(
                                            f"⚠️ [Mapper] [{node_name}] "
                                            f"{type(check_error).__name__}: "
                                            f"{check_error}"
                                        )

                                    if type(exists) is not bool:
                                        inventory_check_failed = True
                                        print(
                                            f"⚠️ [Mapper] [{node_name}] "
                                            "Hash verification inconclusive"
                                        )
                                        continue

                                    if exists is True:
                                        matched_nodes.append(
                                            (node_obj, node_name)
                                        )

                                duplicate_state = _classify_hash_presence_state(
                                    seen_hash_duplicate,
                                    len(matched_nodes),
                                    inventory_check_failed,
                                )

                                # ------------------------------------------------
                                # Live duplicate: persist every positively
                                # verified Node location independently.
                                # ------------------------------------------------
                                if duplicate_state == "live_duplicate":
                                    matched_names = [
                                        node_name
                                        for _, node_name in matched_nodes
                                    ]

                                    print(
                                        f"❌ Live duplicate [...{t_hash[-5:]}] "
                                        f"found on {len(matched_nodes)} Node(s): "
                                        f"{', '.join(matched_names)}"
                                    )

                                    seen_ids.add(t_id)
                                    seen_hashes.add(t_hash)

                                    sync_data = {
                                        "hash": t_hash,
                                        "observed_site": site,
                                        "observed_torrent_id": t_id,
                                        "observed_details_url": details_url,
                                        "observed_release_name": raw_title,
                                        "observed_web_size": data.get(
                                            "size_str", ""
                                        ),
                                    }

                                    sync_success_nodes = []
                                    sync_failed_nodes = []

                                    for node_obj, node_name in matched_nodes:
                                        sync_method = getattr(
                                            node_obj,
                                            "sync_to_mapping",
                                            None
                                        )

                                        if not callable(sync_method):
                                            sync_failed_nodes.append(node_name)
                                            print(
                                                f"⚠️ [Mapper] [{node_name}] "
                                                "ไม่มี sync_to_mapping()"
                                            )
                                            continue

                                        try:
                                            sync_result = await sync_method(
                                                web_data_list=[sync_data]
                                            )
                                        except Exception as sync_error:
                                            sync_result = False
                                            print(
                                                f"⚠️ [Mapper] [{node_name}] "
                                                f"Sync exception: "
                                                f"{type(sync_error).__name__}: "
                                                f"{sync_error}"
                                            )

                                        if sync_result is True:
                                            sync_success_nodes.append(node_name)
                                        else:
                                            sync_failed_nodes.append(node_name)

                                    print(
                                        f"📍 [Mapper] Location sync result: "
                                        f"success={sync_success_nodes!r}, "
                                        f"failed={sync_failed_nodes!r}"
                                    )

                                    # A live positive match always prevents
                                    # another Add, even if another Node failed.
                                    await handle_thanks_click(
                                        browser_instance,
                                        details_url
                                    )
                                    count_skip += 1
                                    download_ready = False

                                # ------------------------------------------------
                                # No positive match + incomplete checks:
                                # fail closed; do not Add or assign a fallback.
                                # ------------------------------------------------
                                elif duplicate_state == "inventory_incomplete":
                                    print(
                                        f"🛑 [Mapper] Hash={t_hash[:12]} "
                                        "ตรวจสอบ Active Node ไม่ครบ "
                                        "และไม่มี Node ใดยืนยันว่าพบ Hash "
                                        "ยกเลิกการ Add รอบนี้"
                                    )
                                    download_ready = False

                                # ------------------------------------------------
                                # Historical site observation only:
                                # preserve old skip behaviour, but never assign
                                # a Node or create a Node location from history.
                                # ------------------------------------------------
                                elif duplicate_state == "history_duplicate":
                                    print(
                                        f"⏭️ [Mapper] Hash={t_hash[:12]} "
                                        "อยู่ใน Site History แต่ไม่พบใน Live "
                                        "Inventory ของ Node ที่ตรวจครบ "
                                        "ข้ามการประมวลผลซ้ำโดยไม่บันทึก Node"
                                    )

                                    seen_ids.add(t_id)
                                    seen_hashes.add(t_hash)

                                    await handle_thanks_click(
                                        browser_instance,
                                        details_url
                                    )
                                    count_skip += 1
                                    download_ready = False

                                # eligible_for_add:
                                # all Nodes returned False and this site's
                                # history does not contain the hash.
                                # Continue to the existing Add workflow.


                            # 7. PROCESS & SEND TO CLIENT NODE
                            # ---------------------------------------------------------
                            if download_ready:
                                print(f"✅ [{site}] พร้อมส่งไฟล์เข้า Client (Hash: {t_hash})")

                                # เรียงลำดับ Node ที่มีพื้นที่ว่างมากที่สุดขึ้นก่อน
                                active_nodes.sort(key=lambda x: getattr(x[0], 'free_gb', 0), reverse=True)
                                success_node = None
                                task_weight = calculate_task_weight(t_size_gb) if 'calculate_task_weight' in globals() else 1.0

                                for node_obj, n_cfg in active_nodes:
                                    if stop_event.is_set():
                                        break

                                    node_name = getattr(node_obj, 'name', 'Unknown Node')


                                    # 1. ตรวจสอบ Load (Weight) พร้อมวิเคราะห์สาเหตุ
                                    d_type = n_cfg.get('disk_type', 'HDD') if isinstance(n_cfg, dict) else 'HDD'

                                    dynamic_max_cap = 100.0
                                    proxy_wait = None

                                    if 'get_node_dynamic_cap' in globals():
                                        dynamic_max_cap, proxy_wait = get_node_dynamic_cap(node_obj, d_type)

                                    current_load = 0.0
                                    if 'get_node_current_weight' in globals():
                                        current_load = round(get_node_current_weight(node_obj), 1)

                                    projected_load = current_load + task_weight

                                    print(
                                        f"[LOAD AUDIT] [{node_name}] "
                                        f"disk={d_type} "
                                        f"load={current_load:.1f} "
                                        f"task={task_weight:.1f} "
                                        f"cap={dynamic_max_cap:.1f} "
                                        f"projected={projected_load:.1f} "
                                        f"proxy_wait={proxy_wait}"
                                    )

                                    if projected_load > dynamic_max_cap:
                                        print(
                                            f"⚠️ [{node_name}] โหลดเกินกำหนด "
                                            f"(Load: {current_load:.1f} + Task: {task_weight:.1f} "
                                            f"> Cap: {dynamic_max_cap:.1f}) -> ข้าม"
                                        )
                                        continue


                                    # 2. คำนวณพื้นที่ (Buffer)
                                    downloading_size = 0
                                    if hasattr(node_obj, 'get_downloading_size'):
                                        downloading_size = max(0, node_obj.get_downloading_size())
                                    
                                    node_free_gb = getattr(node_obj, 'free_gb', 0)
                                    effective_free_gb = max(0, node_free_gb - downloading_size)
                                    
                                    required_space = t_size_gb + 15.0
                                    cleaner = None
                                    if 'NodeCleaner' in globals():
                                        cleaner = NodeCleaner(node_obj, n_cfg, global_clean if 'global_clean' in globals() else None)

                                    # 3. Smart Reclaim ล้างดิสก์อัตโนมัติหากพื้นที่ไม่พอ
                                    if effective_free_gb < required_space and cleaner:
                                        cleaner.smart_reclaim_process(required_gb=required_space, is_emergency=False)
                                        if hasattr(node_obj, 'refresh_status'):
                                            node_obj.refresh_status()
                                        
                                        downloading_size = max(0, node_obj.get_downloading_size()) if hasattr(node_obj, 'get_downloading_size') else 0
                                        effective_free_gb = max(0, getattr(node_obj, 'free_gb', 0) - downloading_size)

                                    # 4. Final Check & Emergency Flush
                                    if effective_free_gb < (t_size_gb + 5.0):
                                        if cleaner:
                                            print(f"🔄 [{node_name}] Reclaim รอบแรกพื้นที่ไม่พอ Try Emergency Flush...")
                                            cleaner.smart_reclaim_process(required_gb=required_space, is_emergency=True)
                                            if hasattr(node_obj, 'refresh_status'):
                                                node_obj.refresh_status()
                                            
                                            dl_size = max(0, node_obj.get_downloading_size()) if hasattr(node_obj, 'get_downloading_size') else 0
                                            effective_free_gb = max(0, getattr(node_obj, 'free_gb', 0) - dl_size)
                                        
                                        if effective_free_gb < (t_size_gb + 5.0):
                                            print(f"⚠️ [{node_name}] พื้นที่ไม่พอจริงๆ (เหลือ {effective_free_gb:.1f}GB) -> ข้าม")
                                            continue

                                    # =========================================================================
                                    # 5. ส่งไฟล์เข้า Client Node & ประมวลผล Payload
                                    # =========================================================================
                                    try:
                                        raw_bytes = raw_data_bytes if 'raw_data_bytes' in locals() else None
                                        if not raw_bytes:
                                            print(f"❌ [Error] ข้อมูลไฟล์ทอร์เรนต์ (raw_data_bytes) ว่างเปล่า ไม่สามารถส่งเข้า {node_name}")
                                            continue

                                        result = False
                                        if 'safe_add_torrent' in globals():
                                            result = safe_add_torrent(node_obj, raw_bytes, site)
                                        elif hasattr(node_obj, 'add_torrent'):
                                            if globals().get(
                                                "PHASE_G_ALLOW_NORMAL_TORRENT_ADD",
                                                False
                                            ) is True:
                                                result = await node_obj.add_torrent(
                                                    torrent_bytes=raw_bytes,
                                                    category=site
                                                )
                                            else:
                                                print(
                                                    f"🔒 [{node_name}] "
                                                    "Normal Torrent Add blocked (Phase G)"
                                                )
                                                result = False

                                        if result:
                                            size_gb_val = t_size_gb if t_size_gb is not None else 0.0
                                            success_msg = f"📥 [Success] {node_name} | {size_gb_val:.1f}GB | {raw_title[:40]}"
                                            print(f"🎯 เลือก Node: [{node_name}] -> {success_msg}")
                                            
                                            current_free = getattr(node_obj, 'free_gb', 0.0)
                                            node_obj.free_gb = max(0.0, current_free - (size_gb_val + 0.1))
                                            
                                            added_in_zone.append(success_msg)
                                            seen_ids.add(t_id)
                                            seen_hashes.add(t_hash)
                                            data_saved = True
                                            success_node = node_obj

                                            web_item_payload = {
                                                "torrent_id": t_id,
                                                "site_name": site,
                                                "release_name": raw_title,
                                                "web_size": data.get('size_str', ''),
                                                "download_url": download_url,
                                                "details_url": details_url,
                                                "source_zone_meta": (
                                                    dict(source_zone_meta)
                                                    if isinstance(source_zone_meta, dict)
                                                    else None
                                                ),
                                            }

                                            client_response_payload = {
                                                "client_type": getattr(node_obj, "client_type", "rtorrent"),
                                                "hash": t_hash,
                                                "name": t_name if 't_name' in locals() else raw_title,
                                                "size_bytes": int(size_gb_val * 1024 * 1024 * 1024),
                                                "state": "downloading",
                                                "save_path": getattr(node_obj, "save_path", "")
                                            }

                                            if 'handle_new_torrent_grabbed' in globals():
                                                await handle_new_torrent_grabbed(
                                                    web_item=web_item_payload, 
                                                    client_response=client_response_payload, 
                                                    host_name=node_name
                                                )

                                            await handle_thanks_click(browser_instance, details_url)
                                            break  # ส่งเข้า Node สำเร็จ ออกจาก loop node

                                    except Exception as send_err:
                                        print(f"❌ เกิดข้อผิดพลาดขณะส่งไฟล์เข้า Node [{node_name}]: {send_err}")

                                if not success_node:
                                    print(f"⛔ [{site}] ไม่มี Node ใดสามารถรับไฟล์ขนาด {t_size_gb:.2f} GB ได้")
                                    full_nodes_in_zone.append(f"❌ [Full] {raw_title[:40]}")

                        except Exception as row_err:
                            print(f"⚠️ [{site}] เกิดข้อผิดพลาดขณะประมวลผลรายการ Torrent (ID: {t_id if 't_id' in locals() else 'N/A'}): {row_err}")
                            if 'traceback' in globals():
                                traceback.print_exc()

                    # =========================================================================
                    # 📊 สรุปและแจ้งเตือนประจำ Zone (ทำงานหลังจบการวนลูป Torrent ใน Zone นั้นๆ)
                    # =========================================================================
                    try:
                        summary_msg = []

                        min_gb = SET.get('MIN_SIZE_GB', 0) if 'SET' in globals() else 0
                        max_gb = SET.get('MAX_SIZE_GB', 999) if 'SET' in globals() else 999

                        if 'generate_main_status' in globals() and 'CFG' in globals():
                            condition_text = generate_main_status(CFG, site_name=site)
                        else:
                            condition_text = f"⚙️ เงื่อนไข: ขนาด {min_gb:.1f}-{max_gb:.1f}GB"

                        summary_msg.append(condition_text)
                        summary_msg.append(f"🌐 Scanning: [{display_zone}] {target_url if 'target_url' in locals() else ''}\n")

                        if cross_seed_target:
                            zone_match_count = (
                                len(cross_seed_matches) - cross_seed_matches_before_zone
                            )

                            if zone_match_count:
                                summary_msg.append(
                                    f"🎯 พบ Cross-Seed Match: {zone_match_count} รายการ"
                                )
                            else:
                                summary_msg.append(
                                    "❌ ไม่พบ Cross-Seed ที่ตรงกันในโซนนี้"
                                )

                        elif not added_in_zone and not full_nodes_in_zone:
                            summary_msg.append("❌ ไม่มีไฟล์เข้าเงื่อนไข")

                        else:
                            if added_in_zone:
                                summary_msg.append("✅ Added:")
                                summary_msg.extend(added_in_zone)

                            if full_nodes_in_zone:
                                if added_in_zone:
                                    summary_msg.append("")
                                summary_msg.append("⚠️ Queue Full:")
                                summary_msg.extend(full_nodes_in_zone)

                        summary_msg.append(f"\n📊 สรุป {display_zone}: เพิ่ม {len(added_in_zone)} | เต็ม {len(full_nodes_in_zone)} | ข้าม {count_skip}")

                        full_report = "\n".join(summary_msg)
                        print("\n" + full_report + "\n")

                        if 'send_notify' in globals():
                            await send_notify(full_report)

                    except Exception as zone_err:
                        print(f"❌ [{site}] เกิดข้อผิดพลาดขณะสรุปผล Zone [{display_zone}]: {zone_err}")

        except Exception as site_err:
            print(f"❌ [{site}] เกิดข้อผิดพลาดระดับเว็บ: {site_err}")
            if 'traceback' in globals():
                traceback.print_exc()

        finally:
            # บันทึกข้อมูลประวัติ Seen IDs & Hashes ประจำเว็บ
            if 'data_saved' in locals() and data_saved:
                try:
                    save_data(current_site_seen_file, seen_ids)
                    save_data(current_site_hash_file, seen_hashes)
                    print(f"💾 [{site}] บันทึกประวัติ Seen IDs ({len(seen_ids)}) และ Hashes ({len(seen_hashes)}) เรียบร้อย")
                except Exception as save_err:
                    print(f"⚠️ [{site}] ไม่สามารถบันทึก Seen Data ได้: {save_err}")

    # =========================================================================
    # 🏁 สรุปผลการสแกนทั้งหมด (ทำงานเมื่อวนครบทุก Site แล้ว)
    # =========================================================================
    if 'cross_seed_target' in locals() and cross_seed_target:
        print(f"\n🎯 [Cross-Seed Summary] พบรายการที่ตรงกันทั้งหมด {len(cross_seed_matches)} รายการ")
        return cross_seed_matches

    print("\n🏁 สแกนและประมวลผลทุกเว็บไซต์เสร็จสิ้น")
    return True



def _cross_seed_tracker_url_key(value):
    """Canonical comparison key; never use this to print sensitive URLs."""
    from urllib.parse import urlsplit, parse_qsl

    raw = str(value or "").strip()
    if (
        not raw
        or "\r" in raw
        or "\n" in raw
        or any(ord(ch) < 32 for ch in raw)
    ):
        return None

    try:
        parsed = urlsplit(raw)
        scheme = parsed.scheme.casefold()
        host = (parsed.hostname or "").casefold()
        port = parsed.port
    except (ValueError, TypeError):
        return None

    if scheme not in {"http", "https", "udp"} or not host:
        return None

    query = tuple(sorted(
        parse_qsl(parsed.query, keep_blank_values=True)
    ))
    return (
        scheme, host, port, parsed.path or "/", query,
        parsed.username or "", parsed.password or "",
    )


def run_opt_in_cross_seed_tracker_additions(matches, active_nodes):
    """
    Opt-in, same-info-hash tracker addition only.
    Rechecks presence after POST. Never adds torrent data or resumes a torrent.
    """
    from urllib.parse import urlsplit

    exec_cfg = CFG.get("CROSS_SEED_EXECUTION", {})
    if (
        not isinstance(exec_cfg, dict)
        or exec_cfg.get("enable_tracker_add") is not True
    ):
        print(
            "🔒 [Cross-Seed Execute] Tracker addition disabled; "
            "read-only audit only."
        )
        return

    allowed_preflight = {
        "filelist_manifest_verified_recheck_required",
        "source_manifest_verified_filelist_hidden_recheck_required",
    }

    active_node_ids = set()
    for entry in active_nodes or []:
        if isinstance(entry, (tuple, list)) and entry:
            active_node_ids.add(id(entry[0]))

    seen = set()

    for item in matches or []:
        if not isinstance(item, dict):
            continue

        candidate_hash = str(
            item.get("candidate_info_hash") or ""
        ).strip().upper()
        source_hash = str(
            item.get("source_hash") or ""
        ).strip().upper()
        site = str(item.get("site_name") or "Unknown")

        if (
            len(candidate_hash) != 40
            or any(c not in "0123456789ABCDEF" for c in candidate_hash)
        ):
            print(f"⛔ [Cross-Seed Execute] Invalid candidate hash; site={site!r}")
            continue

        # Different hash requires a separate paused-add/recheck workflow.
        if candidate_hash != source_hash:
            continue

        if item.get("preflight_status") not in allowed_preflight:
            print(
                f"⛔ [Cross-Seed Execute] Hash={candidate_hash[:12]} "
                "preflight status not eligible; skipped."
            )
            continue

        if item.get("candidate_extra_files"):
            print(
                f"⛔ [Cross-Seed Execute] Hash={candidate_hash[:12]} "
                "has extra candidate files; skipped."
            )
            continue

        node = item.get("source_node")
        if (
            node is None
            or id(node) not in active_node_ids
            or not callable(getattr(node, "_execute_request", None))
            or not getattr(node, "url", None)
        ):
            print(
                f"⛔ [Cross-Seed Execute] Hash={candidate_hash[:12]} "
                "source node is not active; skipped."
            )
            continue

        unique_key = (id(node), candidate_hash, site.casefold())
        if unique_key in seen:
            continue
        seen.add(unique_key)

        try:
            source_size = int(item.get("source_size_bytes") or 0)
            manifest = item.get("candidate_manifest") or {}
            manifest_size = int(manifest.get("total_size_bytes") or 0)
        except (TypeError, ValueError, OverflowError):
            print(
                f"⛔ [Cross-Seed Execute] Hash={candidate_hash[:12]} "
                "invalid size metadata; skipped."
            )
            continue

        if source_size <= 0 or manifest_size != source_size:
            print(
                f"⛔ [Cross-Seed Execute] Hash={candidate_hash[:12]} "
                "candidate/source total size mismatch; skipped."
            )
            continue

        # Accept only syntactically valid announce URLs; never log full URLs.
        announce_urls = []
        url_keys = set()
        for value in item.get("candidate_trackers") or []:
            url = str(value or "").strip()
            key = _cross_seed_tracker_url_key(url)
            if key is None or key in url_keys:
                continue
            announce_urls.append(url)
            url_keys.add(key)

        if not announce_urls:
            print(
                f"⛔ [Cross-Seed Execute] Hash={candidate_hash[:12]} "
                "no valid announce URLs; skipped."
            )
            continue

        def request_trackers():
            response = node._execute_request(
                "GET",
                f"{node.url}/api/v2/torrents/trackers",
                params={"hash": candidate_hash.lower()},
                auth=getattr(node, "auth", None),
                verify=False,
                timeout=12,
            )
            if response is None or response.status_code != 200:
                return None
            try:
                payload = response.json()
            except Exception:
                return None
            return payload if isinstance(payload, list) else None

        # Re-read live torrent metadata immediately before any write.
        try:
            response = node._execute_request(
                "GET",
                f"{node.url}/api/v2/torrents/info",
                params={"hashes": candidate_hash.lower()},
                auth=getattr(node, "auth", None),
                verify=False,
                timeout=12,
            )
            if response is None or response.status_code != 200:
                print(
                    f"⚠️ [Cross-Seed Execute] Hash={candidate_hash[:12]} "
                    "live torrent read failed; no mutation."
                )
                continue

            info_list = response.json()
            if not isinstance(info_list, list):
                print(
                    f"⚠️ [Cross-Seed Execute] Hash={candidate_hash[:12]} "
                    "invalid live torrent response; no mutation."
                )
                continue

            exact = [
                info for info in info_list
                if str(info.get("hash") or "").strip().upper()
                == candidate_hash
            ]
            if len(exact) != 1:
                print(
                    f"⚠️ [Cross-Seed Execute] Hash={candidate_hash[:12]} "
                    f"expected one live torrent, got {len(exact)}; no mutation."
                )
                continue

            info = exact[0]
            try:
                complete = (
                    float(info.get("progress", 0)) >= 0.999999
                    and int(info.get("amount_left", -1)) == 0
                    and int(info.get("total_size", -1)) == source_size
                )
            except (TypeError, ValueError, OverflowError):
                complete = False

            if not complete:
                print(
                    f"⛔ [Cross-Seed Execute] Hash={candidate_hash[:12]} "
                    "live torrent is incomplete or size changed; no mutation."
                )
                continue

            current_trackers = request_trackers()
            if current_trackers is None:
                print(
                    f"⚠️ [Cross-Seed Execute] Hash={candidate_hash[:12]} "
                    "tracker read failed; no mutation."
                )
                continue

            existing_keys = {
                key
                for row in current_trackers
                if isinstance(row, dict)
                for key in [_cross_seed_tracker_url_key(row.get("url"))]
                if key is not None
            }

            missing_urls = [
                url for url in announce_urls
                if _cross_seed_tracker_url_key(url) not in existing_keys
            ]

            if not missing_urls:
                print(
                    f"✅ [Cross-Seed Execute] site={site!r} "
                    f"hash={candidate_hash[:12]} node={node.name}: "
                    "ALREADY_PRESENT; no tracker write needed."
                )
                continue

            missing_hosts = sorted({
                (urlsplit(url).hostname or "unknown").casefold()
                for url in missing_urls
            })

            response = node._execute_request(
                "POST",
                f"{node.url}/api/v2/torrents/addTrackers",
                data={
                    "hash": candidate_hash.lower(),
                    "urls": "\n".join(missing_urls),
                },
                headers={"Referer": node.url},
                auth=getattr(node, "auth", None),
                verify=False,
                timeout=20,
            )

            if response is None or response.status_code != 200:
                print(
                    f"⚠️ [Cross-Seed Execute] site={site!r} "
                    f"hash={candidate_hash[:12]} node={node.name}: "
                    f"addTrackers HTTP "
                    f"{response.status_code if response is not None else 'no response'}."
                )
                continue

            # HTTP 200 alone is not treated as proof that all URLs were added.
            confirmed = request_trackers()
            if confirmed is None:
                print(
                    f"⚠️ [Cross-Seed Execute] hash={candidate_hash[:12]} "
                    "POST returned 200 but verification read failed; "
                    f"hosts={missing_hosts!r}."
                )
                continue

            confirmed_keys = {
                key
                for row in confirmed
                if isinstance(row, dict)
                for key in [_cross_seed_tracker_url_key(row.get("url"))]
                if key is not None
            }
            remaining = [
                url for url in missing_urls
                if _cross_seed_tracker_url_key(url) not in confirmed_keys
            ]

            if not remaining:
                print(
                    f"✅ [Cross-Seed Execute] site={site!r} "
                    f"hash={candidate_hash[:12]} node={node.name}: "
                    f"ADDED_AND_VERIFIED count={len(missing_urls)} "
                    f"hosts={missing_hosts!r}."
                )
            else:
                remaining_hosts = sorted({
                    (urlsplit(url).hostname or "unknown").casefold()
                    for url in remaining
                })
                print(
                    f"⚠️ [Cross-Seed Execute] hash={candidate_hash[:12]} "
                    "some trackers were not confirmed; "
                    f"hosts={remaining_hosts!r}."
                )

        except Exception as exc:
            # Avoid logging exception payloads which might contain tracker URLs.
            print(
                f"⚠️ [Cross-Seed Execute] hash={candidate_hash[:12]} "
                f"inspection failed: {type(exc).__name__}; no further action."
            )



def run_opt_in_new_hash_cross_seed(matches, active_nodes):
    """
    Strict new-info-hash flow:
    - disabled by default
    - exact multi-file root/path/size match only
    - add paused, request recheck, never resume automatically
    - never delete source or candidate torrents
    """
    import posixpath

    exec_cfg = CFG.get("CROSS_SEED_EXECUTION", {})
    if (
        not isinstance(exec_cfg, dict)
        or exec_cfg.get("enable_new_hash_add") is not True
    ):
        print(
            "🔒 [Cross-Seed New Hash] Disabled; "
            "no new torrents added."
        )
        return

    allowed_statuses = {
        "filelist_manifest_verified_recheck_required",
        "source_manifest_verified_filelist_hidden_recheck_required",
    }

    configured = [
        item for item in CFG.get("NODES", [])
        if isinstance(item, dict) and item.get("enable")
    ]
    if not configured:
        print("⛔ [Cross-Seed New Hash] No enabled-node config; abort.")
        return

    # Fail closed if any enabled node cannot be inspected by this qBittorrent
    # API handler. Do not infer global absence from a partial inventory.
    if any(str(item.get("type", "qbit")).casefold() == "rtorrent"
           for item in configured):
        print(
            "⛔ [Cross-Seed New Hash] An enabled rTorrent node exists; "
            "cross-node duplicate check is unsupported. No additions."
        )
        return

    active_by_name = {}
    active_nodes_clean = []
    for entry in active_nodes or []:
        if not isinstance(entry, (tuple, list)) or len(entry) < 1:
            continue
        node = entry[0]
        node_cfg = entry[1] if len(entry) > 1 and isinstance(entry[1], dict) else {}
        if node is None:
            continue
        name = str(node_cfg.get("name") or getattr(node, "name", "")).strip()
        if name:
            active_by_name[name] = node
        active_nodes_clean.append((node, node_cfg))

    missing_nodes = [
        str(item.get("name") or "")
        for item in configured
        if str(item.get("name") or "") not in active_by_name
    ]
    if missing_nodes:
        print(
            "⛔ [Cross-Seed New Hash] Cannot inspect all enabled nodes: "
            f"{sorted(missing_nodes)!r}; no additions."
        )
        return

    def normalize_abs(value):
        value = str(value or "").strip()
        if not value or not value.startswith("/"):
            raise ValueError("Expected an absolute Linux path")
        return posixpath.normpath(value)

    def normalize_rel(value):
        value = str(value or "").replace("\\", "/")
        if not value or value.startswith("/") or "\x00" in value:
            raise ValueError("Empty or absolute relative path")
        if len(value) >= 2 and value[1] == ":":
            raise ValueError("Drive-qualified relative path")
        parts = [part for part in value.split("/") if part not in ("", ".")]
        if not parts or any(part == ".." for part in parts):
            raise ValueError("Unsafe relative path")
        return "/".join(parts)

    def read_hash_info(node, torrent_hash):
        try:
            response = node._execute_request(
                "GET",
                f"{node.url}/api/v2/torrents/info",
                params={"hashes": torrent_hash.lower()},
                auth=getattr(node, "auth", None),
                verify=False,
                timeout=12,
            )
            if response is None or response.status_code != 200:
                return False, []
            payload = response.json()
            if not isinstance(payload, list):
                return False, []
            rows = [
                item for item in payload
                if isinstance(item, dict)
                and str(item.get("hash") or "").strip().upper() == torrent_hash
            ]
            return True, rows
        except Exception:
            return False, []

    seen_candidates = set()

    for item in matches or []:
        if not isinstance(item, dict):
            continue

        candidate_hash = str(
            item.get("candidate_info_hash") or ""
        ).strip().upper()
        source_hash = str(item.get("source_hash") or "").strip().upper()
        site = str(item.get("site_name") or "Unknown")
        hash_prefix = candidate_hash[:12] if candidate_hash else "INVALID"

        if (
            len(candidate_hash) != 40
            or any(c not in "0123456789ABCDEF" for c in candidate_hash)
            or len(source_hash) != 40
            or any(c not in "0123456789ABCDEF" for c in source_hash)
        ):
            print(f"⛔ [Cross-Seed New Hash] Invalid hash; site={site!r}")
            continue

        if candidate_hash == source_hash:
            continue

        if candidate_hash in seen_candidates:
            continue
        seen_candidates.add(candidate_hash)

        if item.get("preflight_status") not in allowed_statuses:
            print(
                f"⛔ [Cross-Seed New Hash] hash={hash_prefix}: "
                "preflight status not eligible; no addition."
            )
            continue

        if item.get("content_verification") != "NOT_VERIFIED_RECHECK_REQUIRED":
            print(
                f"⛔ [Cross-Seed New Hash] hash={hash_prefix}: "
                "unexpected verification state; no addition."
            )
            continue

        node = item.get("source_node")
        if (
            node is None
            or not any(active is node for active, _ in active_nodes_clean)
            or not callable(getattr(node, "add", None))
            or not callable(getattr(node, "get_torrent_file_manifest", None))
        ):
            print(
                f"⛔ [Cross-Seed New Hash] hash={hash_prefix}: "
                "source node is not active or lacks required methods."
            )
            continue

        manifest = item.get("candidate_manifest")
        if not isinstance(manifest, dict):
            print(f"⛔ [Cross-Seed New Hash] hash={hash_prefix}: missing manifest.")
            continue

        if manifest.get("multi_file") is not True:
            print(
                f"⛔ [Cross-Seed New Hash] hash={hash_prefix}: "
                "single-file layout is unsupported by the strict path gate."
            )
            continue

        root_name = str(manifest.get("root_name") or "")
        source_name = str(item.get("source_torrent_name") or "")
        source_save_path = str(item.get("source_save_path") or "").strip()

        try:
            save_path_norm = normalize_abs(source_save_path)
            expected_root = normalize_abs(
                posixpath.join(save_path_norm, root_name)
            )
            source_size = int(item.get("source_size_bytes") or 0)
            candidate_total = int(manifest.get("total_size_bytes") or 0)
            files = manifest.get("files")
            if not root_name or root_name in {".", ".."} or "/" in root_name or "\\" in root_name:
                raise ValueError("Invalid torrent root name")
            if root_name != source_name:
                raise ValueError("Candidate root name differs from source torrent name")
            if source_size <= 0 or candidate_total != source_size:
                raise ValueError("Source/candidate total size mismatch")
            if not isinstance(files, list) or not files:
                raise ValueError("Empty candidate file manifest")
            if item.get("candidate_extra_files"):
                raise ValueError("Candidate contains additional files")
            content = item.get("torrent_content")
            if not isinstance(content, (bytes, bytearray)) or len(content) < 1000:
                raise ValueError("Missing usable candidate torrent bytes")

            candidate_pairs = []
            for file_row in files:
                if not isinstance(file_row, dict):
                    raise ValueError("Invalid candidate file entry")
                candidate_pairs.append((
                    normalize_rel(file_row.get("path")),
                    int(file_row.get("size_bytes")),
                ))

            candidate_paths = [path for path, _ in candidate_pairs]
            if len(set(candidate_paths)) != len(candidate_paths):
                raise ValueError("Duplicate candidate file paths")
            if any(size < 0 for _, size in candidate_pairs):
                raise ValueError("Negative candidate file size")
            if sum(size for _, size in candidate_pairs) != source_size:
                raise ValueError("Candidate file total does not match source size")

        except (TypeError, ValueError, OverflowError) as exc:
            print(
                f"⛔ [Cross-Seed New Hash] hash={hash_prefix}: "
                f"strict candidate validation failed ({exc}); no addition."
            )
            continue

        # Read source live metadata immediately before any operation.
        ok, source_rows = read_hash_info(node, source_hash)
        if not ok or len(source_rows) != 1:
            print(
                f"⛔ [Cross-Seed New Hash] hash={hash_prefix}: "
                "source live metadata unavailable or ambiguous."
            )
            continue

        source_info = source_rows[0]
        try:
            source_live_complete = (
                float(source_info.get("progress", 0)) >= 0.999999
                and int(source_info.get("amount_left", -1)) == 0
                and int(source_info.get("total_size", -1)) == source_size
            )
            live_save_path = normalize_abs(source_info.get("save_path"))
            live_root_path = normalize_abs(source_info.get("root_path"))
            live_name = str(source_info.get("name") or "")
        except (TypeError, ValueError, OverflowError):
            source_live_complete = False
            live_save_path = ""
            live_root_path = ""
            live_name = ""

        if not source_live_complete:
            print(
                f"⛔ [Cross-Seed New Hash] hash={hash_prefix}: "
                "source is incomplete or live size changed."
            )
            continue
        if live_save_path != save_path_norm or live_root_path != expected_root:
            print(
                f"⛔ [Cross-Seed New Hash] hash={hash_prefix}: "
                "live source save/root path does not match expected layout."
            )
            continue
        if live_name != source_name:
            print(
                f"⛔ [Cross-Seed New Hash] hash={hash_prefix}: "
                "live source name changed."
            )
            continue

        # Compare candidate paths/sizes to the live source manifest exactly.
        try:
            source_files = node.get_torrent_file_manifest(source_hash)
            if not isinstance(source_files, list) or not source_files:
                raise ValueError("Source file manifest unavailable")

            source_pairs = []
            for file_row in source_files:
                if not isinstance(file_row, dict):
                    raise ValueError("Invalid source file entry")
                source_pairs.append((
                    normalize_rel(file_row.get("path")),
                    int(file_row.get("size_bytes")),
                ))

            if len(source_pairs) != len(candidate_pairs):
                raise ValueError("Source/candidate file count differs")
            if len({path for path, _ in source_pairs}) != len(source_pairs):
                raise ValueError("Duplicate source paths")
            if set(source_pairs) != set(candidate_pairs):
                raise ValueError("Exact source/candidate path or size mismatch")
            if sum(size for _, size in source_pairs) != source_size:
                raise ValueError("Source manifest total differs from live size")

        except (TypeError, ValueError, OverflowError) as exc:
            print(
                f"⛔ [Cross-Seed New Hash] hash={hash_prefix}: "
                f"source/candidate manifest check failed ({exc}); no addition."
            )
            continue

        # Search every enabled qBittorrent node before adding. An API failure
        # is not evidence of absence.
        present_elsewhere = False
        inventory_incomplete = False
        for active_node, _ in active_nodes_clean:
            ok, rows = read_hash_info(active_node, candidate_hash)
            if not ok:
                inventory_incomplete = True
                print(
                    f"⚠️ [Cross-Seed New Hash] hash={hash_prefix}: "
                    f"cannot inspect {getattr(active_node, 'name', 'unknown')}; no addition."
                )
                break
            if rows:
                present_elsewhere = True
                print(
                    f"⏭️ [Cross-Seed New Hash] hash={hash_prefix}: "
                    f"already present on {getattr(active_node, 'name', 'unknown')}; no duplicate."
                )
                break

        if inventory_incomplete or present_elsewhere:
            continue

        # The only mutation in this handler: add paused, with normal checking enabled.
        try:
            node_cfg = item.get("source_node_config")
            if not isinstance(node_cfg, dict):
                node_cfg = next(
                    (
                        cfg for active, cfg in active_nodes_clean
                        if active is node
                    ),
                    {},
                )

            added = node.add(
                content=bytes(content),
                site_name=site,
                size=source_size,
                n_cfg=node_cfg,
                save_path=save_path_norm,
                skip_checking=False,
                tags="AutoPilot,Cross-Seed",
                paused=True,
                cross_seed=True,
            )

            if not added:
                print(
                    f"⚠️ [Cross-Seed New Hash] hash={hash_prefix}: "
                    "add API did not confirm success; no resume/delete attempted."
                )
                continue

            ok, candidate_rows = read_hash_info(node, candidate_hash)
            if not ok or len(candidate_rows) != 1:
                print(
                    f"⚠️ [Cross-Seed New Hash] hash={hash_prefix}: "
                    "add returned success but live candidate could not be verified; "
                    "left untouched and not resumed."
                )
                continue

            candidate_info = candidate_rows[0]
            try:
                candidate_save = normalize_abs(candidate_info.get("save_path"))
                candidate_root = normalize_abs(candidate_info.get("root_path"))
                candidate_size_live = int(candidate_info.get("total_size", -1))
            except (TypeError, ValueError, OverflowError):
                candidate_save = ""
                candidate_root = ""
                candidate_size_live = -1

            if (
                candidate_save != save_path_norm
                or candidate_root != expected_root
                or candidate_size_live != source_size
            ):
                try:
                    node.stop_torrent(candidate_hash)
                except Exception:
                    pass
                print(
                    f"⛔ [Cross-Seed New Hash] hash={hash_prefix}: "
                    "post-add path/size verification failed; pause requested, "
                    "candidate not resumed or deleted."
                )
                continue

            state = str(candidate_info.get("state") or "").casefold()
            checking = state.startswith("checking")
            paused_states = {
                "pausedup", "pauseddl", "paused",
                "stoppedup", "stoppeddl", "stopped",
            }

            if not checking and state not in paused_states:
                try:
                    node.stop_torrent(candidate_hash)
                except Exception:
                    pass

                ok, refreshed_rows = read_hash_info(node, candidate_hash)
                if not ok or len(refreshed_rows) != 1:
                    print(
                        f"⚠️ [Cross-Seed New Hash] hash={hash_prefix}: "
                        "could not confirm pause after add; no resume."
                    )
                    continue

                candidate_info = refreshed_rows[0]
                state = str(candidate_info.get("state") or "").casefold()
                checking = state.startswith("checking")
                if not checking and state not in paused_states:
                    print(
                        f"⛔ [Cross-Seed New Hash] hash={hash_prefix}: "
                        "torrent did not reach a paused/checking state; "
                        "no recheck/resume issued."
                    )
                    continue

            if checking:
                print(
                    f"🟡 [Cross-Seed New Hash] hash={hash_prefix}: "
                    "qBittorrent is already checking; no resume issued."
                )
                continue

            response = node._execute_request(
                "POST",
                f"{node.url}/api/v2/torrents/recheck",
                data={"hashes": candidate_hash.lower()},
                headers={"Referer": node.url},
                auth=getattr(node, "auth", None),
                verify=False,
                timeout=20,
            )

            if response is None or response.status_code != 200:
                try:
                    node.stop_torrent(candidate_hash)
                except Exception:
                    pass
                print(
                    f"⚠️ [Cross-Seed New Hash] hash={hash_prefix}: "
                    "recheck request failed; pause requested; never resumed."
                )
                continue

            # Re-read state to catch any unexpected start. Never auto-resume.
            ok, refreshed_rows = read_hash_info(node, candidate_hash)
            if ok and len(refreshed_rows) == 1:
                state_after = str(refreshed_rows[0].get("state") or "").casefold()
                if (
                    not state_after.startswith("checking")
                    and state_after not in paused_states
                ):
                    try:
                        node.stop_torrent(candidate_hash)
                    except Exception:
                        pass

            print(
                f"🟡 [Cross-Seed New Hash] hash={hash_prefix}: "
                "PAUSED_ADD_RECHECK_REQUESTED; content verification still pending; "
                "no resume or deletion performed."
            )

        except Exception as exc:
            print(
                f"⚠️ [Cross-Seed New Hash] hash={hash_prefix}: "
                f"execution failed ({type(exc).__name__}); no resume/delete attempted."
            )


async def audit_cross_seed_execution_plan(matches, active_nodes):
    """Read-only audit; never adds trackers/torrents or changes torrent state."""
    from urllib.parse import urlsplit, parse_qsl

    def url_key(value):
        try:
            parsed = urlsplit(str(value or "").strip())
            scheme = parsed.scheme.casefold()
            host = (parsed.hostname or "").casefold()
            if scheme not in {"http", "https", "udp"} or not host:
                return None
            try:
                port = parsed.port
            except ValueError:
                return None
            query = tuple(sorted(parse_qsl(
                parsed.query, keep_blank_values=True
            )))
            return (scheme, host, port, parsed.path or "/", query)
        except Exception:
            return None

    def safe_host(value):
        try:
            return (urlsplit(str(value or "")).hostname or "unknown").casefold()
        except Exception:
            return "unknown"

    def completed(info):
        try:
            return (
                float(info.get("progress", 0)) >= 0.999999
                and int(info.get("amount_left", -1)) == 0
            )
        except (TypeError, ValueError, OverflowError):
            return False

    def exact_known_site_manifest(item):
        """Require exact source/candidate file paths and byte sizes."""
        try:
            manifest = item.get("candidate_manifest") or {}
            files = manifest.get("files") or []
            mapping = item.get("source_file_mapping") or []
            extras = item.get("candidate_extra_files") or []

            if extras or not files or not mapping:
                return False

            if len(files) != len(mapping):
                return False

            source_pairs = []
            candidate_pairs = []
            mapped_pairs = []

            for row in mapping:
                source_path = str(row.get("source_path") or "")
                candidate_path = str(row.get("candidate_path") or "")
                source_size = int(row.get("source_size_bytes"))
                candidate_size = int(row.get("candidate_size_bytes"))

                if (
                    not source_path
                    or source_path != candidate_path
                    or source_size <= 0
                    or source_size != candidate_size
                ):
                    return False

                source_pairs.append((source_path, source_size))
                mapped_pairs.append((candidate_path, candidate_size))

            for file in files:
                path = str(file.get("path") or "")
                size = int(file.get("size_bytes"))

                if not path or size <= 0:
                    return False

                candidate_pairs.append((path, size))

            # Reject duplicate paths and non-bijective mappings.
            if len({path for path, _ in source_pairs}) != len(source_pairs):
                return False

            if len({path for path, _ in candidate_pairs}) != len(candidate_pairs):
                return False

            if set(source_pairs) != set(candidate_pairs):
                return False

            if set(mapped_pairs) != set(candidate_pairs):
                return False

            # source_size_bytes is explicitly propagated into
            # cross_seed_matches. Candidate total is derived from
            # its downloaded and verified metainfo manifest.
            source_total = int(item.get("source_size_bytes") or 0)
            mapped_source_total = sum(
                size for _, size in source_pairs
            )
            candidate_total = sum(
                size for _, size in candidate_pairs
            )

            if (
                source_total <= 0
                or mapped_source_total != source_total
                or candidate_total != source_total
            ):
                return False

            return True

        except (AttributeError, TypeError, ValueError, OverflowError):
            return False

    # Keep only qBittorrent nodes that are active in this cycle.
    nodes = []
    for entry in active_nodes or []:
        node = entry[0] if isinstance(entry, (tuple, list)) and entry else None
        if node is not None and hasattr(node, "_execute_request") and hasattr(node, "url"):
            nodes.append(node)

    # Preserve every source association before deduplicating
    # candidate inspection by (site, candidate_hash).
    allowed_known_site_statuses = {
        "filelist_manifest_verified_recheck_required",
        "source_manifest_verified_filelist_hidden_recheck_required",
    }

    eligible_source_hashes_by_candidate = {}
    eligible_zone_meta_by_candidate = {}
    known_site_updates = {}
    zone_listing_updates = {}
    unique = {}

    for item in matches or []:
        if not isinstance(item, dict):
            continue

        candidate_hash = str(
            item.get("candidate_info_hash") or ""
        ).strip().upper()

        source_hash = str(
            item.get("source_hash") or ""
        ).strip().upper()

        site = str(item.get("site_name") or "Unknown").strip()

        if (
            len(candidate_hash) != 40
            or any(c not in "0123456789ABCDEF" for c in candidate_hash)
        ):
            print(
                f"🛑 [Cross-Seed Audit] Invalid candidate hash; "
                f"skipped site={site!r}"
            )
            continue

        target_zone_meta = _validate_mapping_target_zone_meta(
            item.get("target_zone_meta")
        )
        canonical_site = _canonical_mapping_site(site)
        if target_zone_meta and canonical_site != target_zone_meta.get("site_key"):
            target_zone_meta = None
        zone_bucket = str(
            target_zone_meta.get("zone_id") if target_zone_meta else "unknown"
        ).casefold()
        key = (site.casefold(), zone_bucket, candidate_hash)

        if (
            canonical_site
            and re.fullmatch(r"[0-9A-F]{40}", source_hash)
            and item.get("preflight_status") in allowed_known_site_statuses
        ):
            if exact_known_site_manifest(item):
                eligible_source_hashes_by_candidate.setdefault(
                    key, set()
                ).add(source_hash)
                if target_zone_meta:
                    eligible_zone_meta_by_candidate[key] = target_zone_meta
            else:
                print(
                    f"⛔ [Cross-Seed Known Site] site={site!r} "
                    f"candidate={candidate_hash[:12]} "
                    "not recorded: source/candidate manifest is not exact"
                )

        old = unique.get(key)

        if old is None or (
            source_hash == candidate_hash
            and str(old.get("source_hash") or "").strip().upper()
                != candidate_hash
        ):
            unique[key] = item

    if not unique:
        print("ℹ️ [Cross-Seed Audit] No preflight candidates to inspect")
        return

    print(
        f"\n🔍 [Cross-Seed Audit] DRY-RUN ONLY "
        "(qBittorrent/tracker read-only; Known Site metadata only) | "
        f"unique site/hash candidates={len(unique)}"
    )

    for (site_key, zone_bucket, candidate_hash), item in unique.items():
        site = str(item.get("site_name") or "Unknown")
        source_hash = str(item.get("source_hash") or "").strip().upper()
        tracker_urls = []
        tracker_keys = set()

        for value in item.get("candidate_trackers") or []:
            key = url_key(value)
            if key is not None and key not in tracker_keys:
                tracker_keys.add(key)
                tracker_urls.append(str(value).strip())

        if not tracker_urls:
            print(
                f"⛔ [Cross-Seed Audit] site={site!r} "
                f"hash={candidate_hash[:12]} no usable candidate trackers"
            )
            continue

        present = []
        inventory_errors = []

        # Query each active node by exact hash. API errors are not treated
        # as proof that the torrent is absent.
        for node in nodes:
            try:
                response = node._execute_request(
                    "GET",
                    f"{node.url}/api/v2/torrents/info",
                    params={"hashes": candidate_hash.lower()},
                    auth=getattr(node, "auth", None),
                    verify=False,
                    timeout=10,
                )
                if response is None or response.status_code != 200:
                    inventory_errors.append(getattr(node, "name", "unknown"))
                    continue
                payload = response.json()
                if not isinstance(payload, list):
                    inventory_errors.append(getattr(node, "name", "unknown"))
                    continue
                for info in payload:
                    if str(info.get("hash") or "").strip().upper() == candidate_hash:
                        present.append((node, info))
            except Exception:
                inventory_errors.append(getattr(node, "name", "unknown"))

        if present:
            complete_copies = [
                (node, info) for node, info in present if completed(info)
            ]
            if not complete_copies:
                states = ",".join(
                    f"{getattr(node, 'name', 'unknown')}:{info.get('state', 'unknown')}"
                    for node, info in present
                )
                print(
                    f"⏭️ [Cross-Seed Audit] site={site!r} "
                    f"hash={candidate_hash[:12]} exists but is not complete "
                    f"({states}); no mutation"
                )
                continue

            for node, info in complete_copies:
                try:
                    response = node._execute_request(
                        "GET",
                        f"{node.url}/api/v2/torrents/trackers",
                        params={"hash": candidate_hash.lower()},
                        auth=getattr(node, "auth", None),
                        verify=False,
                        timeout=12,
                    )
                    if response is None or response.status_code != 200:
                        print(
                            f"⚠️ [Cross-Seed Audit] site={site!r} "
                            f"hash={candidate_hash[:12]} node={node.name}: "
                            "tracker read failed; no mutation"
                        )
                        continue
                    existing = response.json()
                    if not isinstance(existing, list):
                        print(
                            f"⚠️ [Cross-Seed Audit] site={site!r} "
                            f"hash={candidate_hash[:12]} node={node.name}: "
                            "unexpected tracker response; no mutation"
                        )
                        continue

                    existing_keys = {
                        key for key in
                        (url_key(t.get("url")) for t in existing if isinstance(t, dict))
                        if key is not None
                    }
                    missing = [
                        url for url in tracker_urls
                        if url_key(url) not in existing_keys
                    ]

                    if missing:
                        hosts = sorted({safe_host(url) for url in missing})
                        print(
                            f"🟡 [Cross-Seed Audit] site={site!r} "
                            f"hash={candidate_hash[:12]} node={node.name}: "
                            f"WOULD_ADD_TRACKERS count={len(missing)} "
                            f"hosts={hosts!r}; read-only"
                        )
                    else:
                        print(
                            f"✅ [Cross-Seed Audit] site={site!r} "
                            f"hash={candidate_hash[:12]} node={node.name}: "
                            "ALREADY_PRESENT; no action needed"
                        )

                        # Candidate is complete, tracker API succeeded,
                        # and every candidate tracker is already present.
                        canonical_site = _canonical_mapping_site(site)

                        candidate_audit_key = (
                            site.casefold(), zone_bucket, candidate_hash
                        )
                        eligible_sources = (
                            eligible_source_hashes_by_candidate.get(
                                candidate_audit_key,
                                set()
                            )
                        )

                        if canonical_site and eligible_sources:
                            for verified_source_hash in eligible_sources:
                                known_site_updates.setdefault(
                                    verified_source_hash, set()
                                ).add(canonical_site)

                            verified_zone_meta = eligible_zone_meta_by_candidate.get(
                                candidate_audit_key
                            )
                            if verified_zone_meta:
                                for verified_source_hash in eligible_sources:
                                    zone_listing_updates.setdefault(
                                        verified_source_hash, []
                                    ).append({
                                        "target_zone_meta": verified_zone_meta,
                                        "torrent_id": item.get("torrent_id"),
                                        "details_url": item.get("details_url"),
                                        "infohash": candidate_hash,
                                    })

                            print(
                                f"🧭 [Cross-Seed Known Site] "
                                f"site={canonical_site!r} "
                                f"source associations={len(eligible_sources)}"
                            )
                except Exception as exc:
                    print(
                        f"⚠️ [Cross-Seed Audit] site={site!r} "
                        f"hash={candidate_hash[:12]} node={node.name}: "
                        f"tracker inspection failed ({type(exc).__name__}); no mutation"
                    )
            continue

        if inventory_errors:
            print(
                f"🛑 [Cross-Seed Audit] site={site!r} "
                f"hash={candidate_hash[:12]}: inventory incomplete on "
                f"{sorted(set(inventory_errors))!r}; cannot safely infer absence"
            )
            continue

        if candidate_hash == source_hash:
            print(
                f"🛑 [Cross-Seed Audit] site={site!r} "
                f"hash={candidate_hash[:12]} matches source hash but was not found "
                "in active node inventory; no action"
            )
            continue

        # New-hash additions are proposed only for an exact root/path/size
        # layout. This is intentionally stricter than preflight's fallback
        # filename/extension mapping.
        manifest = item.get("candidate_manifest") or {}
        root_name = str(manifest.get("root_name") or "")
        source_name = str(item.get("source_torrent_name") or "")
        save_path = str(item.get("source_save_path") or "").strip()
        files = manifest.get("files") or []
        mapping = item.get("source_file_mapping") or []
        extras = item.get("candidate_extra_files") or []

        reasons = []
        if not save_path:
            reasons.append("missing_source_save_path")
        if not root_name or root_name != source_name:
            reasons.append("root_name_mismatch")
        if extras:
            reasons.append("candidate_has_extra_files")
        if not files or len(mapping) != len(files):
            reasons.append("file_count_mismatch")

        for row in mapping:
            try:
                source_path = str(row.get("source_path") or "")
                candidate_path = str(row.get("candidate_path") or "")
                source_size = int(row.get("source_size_bytes"))
                candidate_size = int(row.get("candidate_size_bytes"))
                if not source_path or source_path != candidate_path:
                    reasons.append("relative_path_mismatch")
                    break
                if source_size != candidate_size:
                    reasons.append("file_size_mismatch")
                    break
            except (AttributeError, TypeError, ValueError, OverflowError):
                reasons.append("invalid_file_mapping")
                break

        try:
            if int(item.get("source_total_size_bytes") or 0) != int(
                item.get("candidate_total_size_bytes") or 0
            ):
                reasons.append("total_size_mismatch")
        except (TypeError, ValueError, OverflowError):
            reasons.append("invalid_total_size")

        candidate_pairs = []
        try:
            for file in files:
                candidate_pairs.append((
                    str(file.get("path") or ""),
                    int(file.get("size_bytes")),
                ))
            if len({path for path, _ in candidate_pairs}) != len(candidate_pairs):
                reasons.append("duplicate_candidate_paths")
            mapped_pairs = [
                (str(row.get("candidate_path") or ""), int(row.get("candidate_size_bytes")))
                for row in mapping
            ]
            if set(candidate_pairs) != set(mapped_pairs):
                reasons.append("manifest_mapping_not_exact")
        except (AttributeError, TypeError, ValueError, OverflowError):
            reasons.append("invalid_candidate_manifest")

        if reasons:
            print(
                f"⛔ [Cross-Seed Audit] site={site!r} "
                f"hash={candidate_hash[:12]}: REJECT_NEW_HASH "
                f"reasons={sorted(set(reasons))!r}"
            )
        else:
            print(
                f"🟡 [Cross-Seed Audit] site={site!r} "
                f"hash={candidate_hash[:12]}: WOULD_ADD_PAUSED_AND_RECHECK "
                f"target_node={item.get('source_node_name')!r} "
                f"root={root_name!r}; strict layout passed; read-only"
            )

    print(
        "🔒 [Cross-Seed Audit] Finished; "
        "no qBittorrent/tracker mutation performed."
    )

    if known_site_updates:
        await record_cross_seed_known_sites(known_site_updates)
    else:
        print(
            "ℹ️ [Cross-Seed Known Sites] "
            "No candidate met all persistence conditions"
        )



    if zone_listing_updates:
        await record_cross_seed_site_listings(zone_listing_updates)
    else:
        print(
            "ℹ️ [Cross-Seed Site Listings] "
            "No candidate met verified Zone listing conditions"
        )

@_mapping_transactional
async def _persist_cross_seed_linking():
    if globals().get("PHASE_G_ALLOW_MAPPING_WRITES", False) is not True:
        print("🔒 [Cross-Seed Linking] Persistence blocked by Phase G.")
        return False

    mapping_data = await load_mapping()
    updated_mapping = process_cross_seed_linking(mapping_data)
    return await save_mapping(updated_mapping)


async def main():
    global browser_instance
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, handle_exit, sig)
        
    startup_msg = "🚀 Universal Auto-Pilot (nodriver Edition) : Started"
    print(startup_msg)
    
    asyncio.create_task(send_notify(startup_msg))
    browser_instance = None
    site_page = None
    dl_session = None
    resp = None
    consecutive_errors = 0
    # Alternate Cross-Seed and Normal Search per active cycle.
    cycle_mode = "cross_seed"

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
                        if n_cfg.get('allow_sweeper_auto_resume', False) is True:
                            node._sweeper_force_start()
                        else:
                            print(
                                f"🔒 [{node.name}] Sweeper Auto-Resume disabled "
                                "(set allow_sweeper_auto_resume=true to opt in)"
                            )

                    node.refresh_status()
                    
                    await node.sync_to_mapping()
                    
                    await _persist_cross_seed_linking()

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
                if globals().get(
                    "PHASE_G_ALLOW_STARTUP_REANNOUNCE", False
                ) is True:
                    update_trackers(node)
                else:
                    print(
                        f"🔒 [{node.name}] "
                        "Startup Re-announce disabled (Phase G)"
                    )

            if (
                active_nodes
                and globals().get(
                    "PHASE_G_ALLOW_STARTUP_REANNOUNCE", False
                ) is True
            ):
                print("⏳ Waiting 5s for trackers to sync with All Trackers")
                await asyncio.sleep(5)

            if node_status_buffer:
                msg = "🔌 <b>Node Status Report</b>\n" + "\n".join(node_status_buffer)
                asyncio.create_task(send_notify(msg))

            if not active_nodes:
                print("⚠️ [Warning] ไม่มี Node ไหนพร้อมใช้งานในรอบนี้ ข้ามไปรอรอบถัดไป")
                await asyncio.sleep(60)
                continue


            # =================================================================
            # 2. BROWSER SECTION (nodriver Implementation)
            # =================================================================
            print("\n🌐 STARTING SITE SEARCH & SCRAPING...")

            cycle_mode_completed = False

            try:
                stealth_args = SET.get("stealth_args", {})

                # โหลด mapping
                mapping_data = await load_mapping()

                # สร้าง inventory จาก torrent ที่อยู่บน node ปัจจุบัน
                live_hashes = set()
                source_inventory = {}
                inventory_nodes_ok = 0
                inventory_nodes_failed = 0
                inventory_nodes_expected = len(active_nodes)

                for node_entry in active_nodes:
                    if isinstance(node_entry, (tuple, list)):
                        node_obj = node_entry[0] if node_entry else None
                        node_cfg = (
                            node_entry[1]
                            if len(node_entry) > 1
                            and isinstance(node_entry[1], dict)
                            else {}
                        )
                    else:
                        node_obj = node_entry
                        node_cfg = {}

                    node_name = getattr(
                        node_obj, "name", "Unknown"
                    )

                    if node_obj is None:
                        inventory_nodes_failed += 1
                        print(
                            "⚠️ [Cross-Seed Inventory] "
                            "พบ Node entry ที่ไม่มี node object"
                        )
                        continue

                    get_torrents = getattr(
                        node_obj, "get_all_torrents_info", None
                    )

                    if not callable(get_torrents):
                        inventory_nodes_failed += 1
                        print(
                            f"⚠️ [Cross-Seed Inventory] [{node_name}] "
                            "ไม่มี get_all_torrents_info()"
                        )
                        continue

                    try:
                        client_torrents = get_torrents()

                        if not isinstance(client_torrents, (list, tuple)):
                            inventory_nodes_failed += 1
                            print(
                                f"⚠️ [Cross-Seed Inventory] [{node_name}] "
                                "อ่าน inventory ไม่สำเร็จ "
                                "(API failure / invalid response)"
                            )
                            continue

                        # --------------------------------------------
                        # Temporary per-node inventory.
                        # Do not modify global inventory until the
                        # entire node has been processed successfully.
                        # --------------------------------------------
                        node_live_hashes = set()
                        node_source_inventory = {}
                        node_hash_count = 0

                        for client_torrent in client_torrents:
                            if not isinstance(client_torrent, dict):
                                raise TypeError(
                                    "Invalid torrent entry type"
                                )

                            torrent_hash = str(
                                client_torrent.get("hash") or ""
                            ).strip().upper()

                            if not re.fullmatch(
                                r"(?:[0-9A-F]{40}|[0-9A-F]{64})",
                                torrent_hash,
                            ):
                                raise ValueError(
                                    "Torrent entry has invalid v1/v2 hash"
                                )

                            if torrent_hash in node_live_hashes:
                                raise ValueError(
                                    f"Duplicate hash in node inventory: {torrent_hash}"
                                )

                            node_live_hashes.add(torrent_hash)
                            node_hash_count += 1

                            # Mapping currently stores v1 SHA1 hashes only.
                            # Keep v2 hashes in the live-presence set, but do
                            # not treat them as v1 Cross-Seed source records.
                            if len(torrent_hash) != 40:
                                continue

                            # Only fully completed torrents are valid
                            # Cross-Seed sources.
                            try:
                                source_size = int(
                                    client_torrent.get("size_bytes") or 0
                                )
                                amount_left = int(
                                    client_torrent.get("amount_left", -1)
                                )
                                progress = float(
                                    client_torrent.get("progress", 0.0)
                                )
                            except (
                                TypeError,
                                ValueError,
                                OverflowError
                            ):
                                continue

                            if (
                                source_size > 0
                                and amount_left == 0
                                and progress >= 0.999999
                                and str(
                                    client_torrent.get("save_path") or ""
                                ).strip()
                            ):
                                node_source_inventory.setdefault(
                                    torrent_hash,
                                    [],
                                ).append({
                                    "node": node_obj,
                                    "n_cfg": node_cfg,
                                    "torrent": client_torrent,
                                    "node_name": str(node_name),
                                })

                        # --------------------------------------------
                        # Atomic commit: reached only after all
                        # torrent entries were processed successfully.
                        # --------------------------------------------
                        live_hashes.update(node_live_hashes)

                        for torrent_hash, source_records in (
                            node_source_inventory.items()
                        ):
                            committed_records = source_inventory.setdefault(
                                torrent_hash,
                                [],
                            )
                            if not isinstance(committed_records, list):
                                raise TypeError(
                                    "Global source inventory candidate list is invalid"
                                )
                            committed_records.extend(source_records)

                        inventory_nodes_ok += 1

                        print(
                            f"🔎 [Cross-Seed Inventory] [{node_name}] "
                            f"อ่านได้ {len(client_torrents)} torrents | "
                            f"มี hash {node_hash_count} รายการ"
                        )

                    except Exception as inventory_error:
                        inventory_nodes_failed += 1

                        print(
                            f"⚠️ [Cross-Seed Inventory] [{node_name}] "
                            f"{type(inventory_error).__name__}: "
                            f"{inventory_error} "
                            "| discard temporary inventory"
                        )

                if (
                    inventory_nodes_expected == 0
                    or inventory_nodes_ok != inventory_nodes_expected
                    or inventory_nodes_failed != 0
                ):
                    # Fail closed: ไม่ใช้ mapping เก่าเมื่ออ่าน inventory ไม่ได้เลย
                    print(
                        "❌ [Cross-Seed] อ่าน Inventory ไม่ครบทุก active node "
                        "จึงยกเลิก Cross-Seed รอบนี้"
                    )
                    cross_seed_targets = []

                else:
                    print(
                        f"🔎 [Cross-Seed Inventory] "
                        f"รวม {len(live_hashes)} unique hashes "
                        f"จาก {inventory_nodes_ok} node(s)"
                    )

                    # Build Cross-Seed targets only on Cross-Seed cycles.
                    if cycle_mode == "cross_seed":
                        cross_seed_targets = build_cross_seed_targets(
                            mapping_data,
                            max_targets=10,
                            active_hashes=live_hashes,
                            source_inventory=source_inventory,
                        )
                    else:
                        cross_seed_targets = []
                        print(
                            "🔎 [Cycle Mode] Normal Search: "
                            "skip Cross-Seed target construction."
                        )

                if cycle_mode == "normal":

                    print(
                        "\n🔎 [Cycle Mode] NORMAL SEARCH "
                        "— configured sites and target_urls"
                    )

                    normal_result = await scrape_site_search_nodriver(
                        CFG=CFG,
                        SET=SET,
                        active_nodes=active_nodes,
                        stop_event=stop_event,
                        stealth_args=stealth_args,
                        global_clean=global_clean,
                        cross_seed_target=None,
                    )

                    if normal_result is True:
                        print(
                            "✅ [Normal Search] "
                            "Search routine finished."
                        )
                    else:
                        print(
                            "⚠️ [Normal Search] Unexpected result: "
                            f"{type(normal_result).__name__}"
                        )

                else:

                    print("\n🎯 [Cycle Mode] CROSS-SEED")

                    all_cross_seed_matches = []

                    if cross_seed_targets:
                        print(
                            f"\n🎯 เริ่ม Cross-seed จำนวน "
                            f"{len(cross_seed_targets)} เป้าหมาย"
                        )

                        for index, target in enumerate(cross_seed_targets, start=1):
                            if stop_event.is_set():
                                print("🛑 หยุด Cross-seed ตาม stop_event")
                                break

                            print(
                                f"\n[{index}/{len(cross_seed_targets)}] "
                                f"[TARGET] {target['source_site']} / "
                                f"{target.get('source_zone_id', 'unknown') if target.get('source_zone_verified') is True else 'zone-unknown'} | "
                                f"{target['keyword']} | "
                                f"{target['target_size_bytes']} bytes"
                            )

                            result = await scrape_site_search_nodriver(
                                CFG=CFG,
                                SET=SET,
                                active_nodes=active_nodes,
                                stop_event=stop_event,
                                stealth_args=stealth_args,
                                global_clean=global_clean,
                                cross_seed_target=target
                            )

                            if isinstance(result, list):
                                all_cross_seed_matches.extend(result)

                        print(
                            "\n🎯 [Cross-Seed Summary] "
                            f"ค้นหา {len(cross_seed_targets)} เป้าหมาย | "
                            f"พบผลลัพธ์รวม {len(all_cross_seed_matches)} รายการ"
                        )
                        await audit_cross_seed_execution_plan(
                            all_cross_seed_matches, active_nodes
                        )
                        _cs_exec_cfg = CFG.get("CROSS_SEED_EXECUTION", {})
                        if (
                            isinstance(_cs_exec_cfg, dict)
                            and _cs_exec_cfg.get("enable_tracker_add") is True
                        ):
                            run_opt_in_cross_seed_tracker_additions(
                                all_cross_seed_matches, active_nodes
                            )
                        else:
                            print(
                                "🔒 [Cross-Seed Execute] Tracker addition disabled; "
                                "read-only audit only."
                            )

                        run_opt_in_new_hash_cross_seed(
                            all_cross_seed_matches, active_nodes
                        )

                    else:
                        print(
                            "⚠️ ไม่มี Cross-seed target "
                            "ให้ตรวจสอบ build_cross_seed_targets() และ mapping"
                        )

                cycle_mode_completed = True
                consecutive_errors = 0

            except Exception as e:
                consecutive_errors += 1
                print(
                    f"❌ [Error] การ Scrape ขัดข้อง "
                    f"({consecutive_errors} ครั้งติดต่อกัน): {e}"
                )

                if consecutive_errors >= 3:
                    err_msg = (
                        f"⚠️ <b>System Alert</b>\n"
                        f"พบข้อผิดพลาดใน Browser Loop ติดต่อกัน "
                        f"{consecutive_errors} ครั้ง\n"
                        f"<code>{e}</code>"
                    )
                    asyncio.create_task(send_notify(err_msg))

            # Switch only after a search branch actually started.
            if cycle_mode_completed:
                previous_cycle_mode = cycle_mode

                cycle_mode = (
                    "normal"
                    if cycle_mode == "cross_seed"
                    else "cross_seed"
                )

                print(
                    f"🔁 [Cycle Scheduler] "
                    f"previous={previous_cycle_mode.upper()} | "
                    f"next={cycle_mode.upper()}"
                )
            else:
                print(
                    f"↩️ [Cycle Scheduler] Search branch did not complete; "
                    f"keep={cycle_mode.upper()}"
                )

            #รันรายงานสถิติ (ยิง api ตรง)
            stats_report = format_site_stats_report([n[0] for n in active_nodes])
            if stats_report:
                print(stats_report)
                await send_notify(stats_report) # แนะนำให้ใช้ await ถ้าเป็นไปได้
            
            #Cycle complete (เข้าสู่ช่วงพัก)
            wait_sec = random.randint(SET.get('MIN_WAIT_MINUTES', 2)*60, SET.get('MAX_WAIT_MINUTES', 10)*60)
            wait_msg = f"\n💤 Cycle finished. Waiting {wait_sec//60} minutes for next scan..."
            print(wait_msg)
            await send_notify(wait_msg)
        
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
            

    # GRACEFUL_SHUTDOWN_CLEANUP_V1
    print("\n🧹 [Shutdown] Main Loop หยุดแล้ว; เริ่ม Cleanup...")

    # รอ Browser background task ที่ทราบว่ามีการสร้างไว้
    current_task = asyncio.current_task()
    browser_tasks = []

    for task in asyncio.all_tasks():
        if task is current_task or task.done():
            continue

        try:
            coro_name = getattr(
                task.get_coro(),
                "__qualname__",
                ""
            )
        except Exception:
            coro_name = ""

        if "handle_thanks_click" in coro_name:
            browser_tasks.append(task)

    browser_tasks_pending = False

    if browser_tasks:
        print(
            f"⏳ [Shutdown] รอ Browser background tasks "
            f"{len(browser_tasks)} งาน..."
        )

        _, pending_browser_tasks = await asyncio.wait(
            browser_tasks,
            timeout=10
        )

        browser_tasks_pending = bool(pending_browser_tasks)

    browser_cleanup_ok = False
    profile_cleanup_ok = False

    if browser_tasks_pending:
        print(
            "🔒 [Shutdown] ยังมี Browser task ทำงานอยู่ "
            "งดปิด Browser/Profile/Xvfb เพื่อป้องกันการชนกัน"
        )

    else:
        # ใช้ Reference ที่ระบบติดตามอยู่
        browser_to_stop = (
            browser_instance
            if browser_instance is not None
            else globals().get("_active_browser_instance")
        )

        if browser_to_stop is not None:
            print("⏳ [Shutdown] กำลัง Stop Browser...")

            try:
                stop_fn = getattr(browser_to_stop, "stop", None)

                if callable(stop_fn):
                    stop_fn()
                    await asyncio.sleep(0.25)

            except Exception as exc:
                print(
                    f"⚠️ [Shutdown] Browser stop แจ้งข้อผิดพลาด: "
                    f"{type(exc).__name__}: {exc}"
                )

        # ฟังก์ชันนี้ตรวจสอบ PID และ Profile ตามระบบเดิม
        try:
            browser_cleanup_ok = bool(kill_specific_browser())
        except Exception as exc:
            print(
                f"⚠️ [Shutdown] Browser verification error: {exc}"
            )

        # ฟังก์ชันนี้จะไม่ลบ Profile หากยังมี Chrome ใช้อยู่
        try:
            profile_cleanup_ok = bool(await cleanup_profile())
        except Exception as exc:
            print(
                f"⚠️ [Shutdown] Profile cleanup error: {exc}"
            )

        if browser_cleanup_ok and profile_cleanup_ok:
            try:
                kill_xvfb()
                browser_instance = None

                print(
                    "✅ [Shutdown] Browser/Profile/Xvfb "
                    "Cleanup เสร็จสมบูรณ์"
                )

            except Exception as exc:
                print(
                    f"⚠️ [Shutdown] Xvfb cleanup error: {exc}"
                )

        else:
            print(
                "🔒 [Shutdown] ไม่สามารถยืนยันการปิดทรัพยากรได้ครบ "
                "จึงคงทรัพยากรที่เหลือไว้เพื่อความปลอดภัย"
            )

    # แจ้งเตือนหลังจัดการ Cleanup แล้ว
    try:
        await send_notify(
            "🛑 Universal Auto-Pilot : Graceful Shutdown"
        )
    except Exception as exc:
        print(f"⚠️ [Shutdown] Notification error: {exc}")

    print("👋 [Shutdown] Main coroutine จบการทำงาน")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # หากกด Ctrl+C แล้วติดตรงนี้ ไม่ต้องทำอะไร ปล่อยให้มันจบเอง
        pass
