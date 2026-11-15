"""Unlimitz login through the browser's existing HTTPS session.

CAPTCHA images are fetched explicitly because the normal browser launcher disables
image rendering. Credentials are submitted once, only after server validation.
"""
import asyncio
import base64
import json
import re
import secrets
from functools import lru_cache
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup


ORIGIN = "https://www.unlimitz.biz"
_HOSTS = {"www.unlimitz.biz", "unlimitz.biz"}
_LOGIN_PATHS = {"/login.php", "/takelogin.php"}


def is_unlimitz(site_cfg):
    return str(site_cfg.get("name", "")).upper() == "UNLIMITZ"


def _trusted_url(url):
    try:
        parts = urlsplit(url)
        return (parts.scheme == "https" and parts.hostname in _HOSTS
                and parts.port in (None, 443) and not parts.username
                and not parts.password)
    except (TypeError, ValueError):
        return False


def check_url(site_cfg):
    """Prefer the enabled normal zone; never probe a disabled special zone."""
    candidates = []
    for item in site_cfg.get("target_urls", []):
        if isinstance(item, dict):
            if item.get("enable", True) is not True:
                continue
            value = item.get("url", item.get("link", ""))
        else:
            value = item
        if not isinstance(value, str) or not value:
            continue
        url = urljoin(ORIGIN + "/", value)
        if _trusted_url(url) and urlsplit(url).path in {"/browse.php", "/clip18x.php"}:
            candidates.append(url)
    return next((url for url in candidates if urlsplit(url).path == "/browse.php"),
                candidates[0] if candidates else ORIGIN + "/index.php")


def session_is_authenticated(html, current_url, username):
    """Require a protected page, logout link and the configured member identity."""
    if not username or not _trusted_url(current_url):
        return False
    path = urlsplit(current_url).path
    if path in _LOGIN_PATHS or path.startswith("/captcha/"):
        return False
    soup = BeautifulSoup(html, "html.parser")
    if soup.select_one('input[type="password"], input[name="password"], input[name="captcha"]'):
        return False
    logout = member = False
    origin = urlsplit(current_url)
    for anchor in soup.find_all("a", href=True):
        target = urljoin(current_url, anchor["href"])
        if not _trusted_url(target):
            continue
        parts = urlsplit(target)
        if (parts.hostname, parts.port or 443) != (origin.hostname, origin.port or 443):
            continue
        if parts.path == "/logout.php":
            logout = True
        elif parts.path == "/userdetails.php" and re.search(r"(?:^|&)id=\d+(?:&|$)", parts.query):
            # The real header adds a separate alias after the account name.
            member = member or any(text.strip().casefold() == str(username).strip().casefold()
                                   for text in anchor.find_all(string=True))
    return logout and member


async def _evaluate_json(page, script):
    # JSON strings also avoid nodriver's RemoteObject result for falsy JS values.
    value = await page.evaluate(script, await_promise=True, return_by_value=True)
    return json.loads(value)


async def _read_state(page):
    return await _evaluate_json(page, """JSON.stringify({
        url: location.href, ready: document.readyState,
        navigation_token: globalThis.__uapUnlimitzNavigation || null,
        html: document.documentElement ? document.documentElement.outerHTML : ''
    })""")


async def _read_ready_state(page, *, previous_token=None):
    for _ in range(40):
        try:
            state = await _read_state(page)
            if (state.get("ready") in {"interactive", "complete"} and state.get("html")
                    and (previous_token is None or state.get("navigation_token") != previous_token)):
                return state
        except Exception:
            # Chrome replaces the execution context during navigation.
            pass
        await asyncio.sleep(0.25)
    return {}


async def _navigate(page, url):
    # Tab.get() returns before the new document is ready, even on a same-URL reload.
    token = secrets.token_hex(16)
    await _evaluate_json(page, "JSON.stringify(globalThis.__uapUnlimitzNavigation = " + json.dumps(token) + ")")
    await page.get(url)
    return await _read_ready_state(page, previous_token=token)


async def _wait_after_submit(page):
    for _ in range(40):
        try:
            state = await _read_state(page)
            if urlsplit(state.get("url", "")).path not in _LOGIN_PATHS:
                return
        except Exception:
            pass
        await asyncio.sleep(0.25)


async def _captcha_bytes(page):
    result = await _evaluate_json(page, """(async () => {
        const fail = () => JSON.stringify({ok: false});
        if (location.origin !== 'https://www.unlimitz.biz' || location.pathname !== '/login.php') return fail();
        const img = document.querySelector('img.cimage');
        if (!img) return fail();
        const url = new URL(img.getAttribute('src'), location.href);
        if (url.origin !== location.origin || url.pathname !== '/captcha/GD_Security_image.php') return fail();
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 10000);
        try {
            const response = await fetch(url.href, {credentials: 'same-origin', cache: 'no-store',
                redirect: 'error', signal: controller.signal});
            if (!response.ok || !(response.headers.get('content-type') || '').startsWith('image/')) return fail();
            const bytes = new Uint8Array(await response.arrayBuffer());
            if (!bytes.length || bytes.length > 262144) return fail();
            let binary = '';
            for (const byte of bytes) binary += String.fromCharCode(byte);
            return JSON.stringify({ok: true, image: btoa(binary)});
        } catch (_) { return fail(); }
        finally { clearTimeout(timer); }
    })()""")
    if not isinstance(result, dict) or result.get("ok") is not True:
        return b""
    return base64.b64decode(result["image"], validate=True)


@lru_cache(maxsize=1)
def _beta_ocr():
    import ddddocr
    return ddddocr.DdddOcr(show_ad=False, beta=True)


def _captcha_candidates(image_bytes, primary_ocr):
    candidates = []
    # The beta model retained all six characters on the verified live sample.
    try:
        beta_model = _beta_ocr()
    except Exception:
        beta_model = None
    for model in (beta_model, primary_ocr):
        if model is None:
            continue
        try:
            code = re.sub(r"[^a-zA-Z0-9]", "", model.classification(image_bytes)).upper()
        except Exception:
            continue
        if re.fullmatch(r"[A-Z0-9]{6}", code) and code not in candidates:
            candidates.append(code)
    return candidates


async def _validate_captcha(page, code):
    if not re.fullmatch(r"[A-Z0-9]{6}", code):
        return False
    result = await _evaluate_json(page, """(async () => {
        const fail = () => JSON.stringify({valid: false});
        if (location.origin !== 'https://www.unlimitz.biz' || location.pathname !== '/login.php') return fail();
        const url = new URL('/captcha/process.php', location.origin);
        url.searchParams.set('captcha', CODE);
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 10000);
        try {
            const response = await fetch(url.href, {credentials: 'same-origin', cache: 'no-store',
                redirect: 'error', signal: controller.signal});
            return JSON.stringify({valid: response.ok && (await response.text()).trim() === '1'});
        } catch (_) { return fail(); }
        finally { clearTimeout(timer); }
    })()""".replace("CODE", json.dumps(code)))
    return isinstance(result, dict) and result.get("valid") is True


async def _submit(page, site_cfg, code):
    values = {key: site_cfg[key] for key in ("username", "password")}
    values["captcha"] = code
    result = await _evaluate_json(page, """(() => {
        const fail = () => JSON.stringify({submitted: false});
        if (location.origin !== 'https://www.unlimitz.biz' || location.pathname !== '/login.php') return fail();
        const form = document.querySelector('form[action*="takelogin"]');
        if (!form || form.method.toLowerCase() !== 'post') return fail();
        const action = new URL(form.getAttribute('action'), location.href);
        if (action.origin !== location.origin || action.pathname !== '/takelogin.php') return fail();
        const values = VALUES;
        for (const name of ['username', 'password', 'captcha']) {
            const input = form.querySelector('input[name="' + name + '"]');
            if (!input || input.disabled) return fail();
        }
        for (const [name, value] of Object.entries(values)) {
            const input = form.querySelector('input[name="' + name + '"]');
            input.value = value;
            input.dispatchEvent(new Event('input', {bubbles: true}));
            input.dispatchEvent(new Event('change', {bubbles: true}));
        }
        HTMLFormElement.prototype.submit.call(form);
        return JSON.stringify({submitted: true});
    })()""".replace("VALUES", json.dumps(values, ensure_ascii=True)))
    return isinstance(result, dict) and result.get("submitted") is True


async def login(page, site_cfg, *, primary_ocr=None, max_captcha_attempts=5):
    """Validate CAPTCHA first and verify the protected member page after one POST."""
    try:
        if (not _trusted_url(site_cfg.get("base_url", ORIGIN))
                or not site_cfg.get("username") or not site_cfg.get("password")):
            return False
        for attempt in range(min(max_captcha_attempts, 5)):
            state = await _navigate(page, ORIGIN + "/login.php")
            if session_is_authenticated(state.get("html", ""), state.get("url", ""), site_cfg["username"]):
                print("✅ [UNLIMITZ] ยืนยัน session สมาชิกแล้ว")
                return True
            image_bytes = await _captcha_bytes(page)
            if not image_bytes:
                print("⚠️ [UNLIMITZ] อ่านภาพ CAPTCHA ไม่สำเร็จ")
                return False
            candidates = await asyncio.to_thread(_captcha_candidates, image_bytes, primary_ocr)
            for code in candidates:
                if not await _validate_captcha(page, code):
                    continue
                if not await _submit(page, site_cfg, code):
                    print("⚠️ [UNLIMITZ] โครงสร้างฟอร์มล็อกอินไม่ตรงกับที่รองรับ")
                    return False
                await _wait_after_submit(page)
                state = await _navigate(page, check_url(site_cfg))
                success = session_is_authenticated(state.get("html", ""), state.get("url", ""), site_cfg["username"])
                print("✅ [UNLIMITZ] ล็อกอินและยืนยันหน้าสมาชิกสำเร็จ" if success
                      else "⚠️ [UNLIMITZ] ส่งฟอร์มแล้วแต่ยังยืนยัน session สมาชิกไม่ได้")
                return success
            print(f"🔄 [UNLIMITZ] CAPTCHA ไม่ผ่าน รอบที่ {attempt + 1}; ขอภาพใหม่")
        print("⚠️ [UNLIMITZ] CAPTCHA ไม่ผ่านภายในจำนวนครั้งที่กำหนด")
    except Exception:
        # Browser/transport errors may contain credentials or private response URLs.
        print("⚠️ [UNLIMITZ] ล็อกอินไม่สำเร็จจากข้อผิดพลาดของเบราว์เซอร์หรือการเชื่อมต่อ")
    return False


async def ensure_logged_in(page, site_cfg, *, primary_ocr=None):
    try:
        state = await _navigate(page, check_url(site_cfg))
        if session_is_authenticated(state.get("html", ""), state.get("url", ""), site_cfg.get("username")):
            return True
        return await login(page, site_cfg, primary_ocr=primary_ocr)
    except Exception:
        print("⚠️ [UNLIMITZ] ตรวจสอบ session ไม่สำเร็จ")
        return False
