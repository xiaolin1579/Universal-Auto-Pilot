"""Single-line upload progress on stdout, captured by the normal application log.

Only allowlisted scalar fields and integer counters can be emitted. Never dump a
job, config, HTML, exception body, filesystem path or account announce wholesale.
"""
from datetime import datetime, timedelta, timezone
import json
import re

EVENTS = {
    'cycle_wait': 'รอรอบค้นหาก่อนเข้า Auto Upload',
    'cycle_disabled': 'ปิด Auto Upload', 'cycle_start': 'เริ่มรอบ',
    'queue': 'สถานะคิว', 'cycle_limit': 'ครบจำนวนงานต่อรอบ',
    'cycle_summary': 'จบรอบ', 'cycle_error': 'รอบทำงานขัดข้อง',
    'job_start': 'เริ่มทำงาน', 'job_skip': 'ข้ามงาน', 'job_result': 'ผลการทำงาน',
    'job_error': 'งานหยุด', 'stage': 'ขั้นตอน', 'prepare_stage': 'เตรียมงาน',
    'browser_start': 'เปิดเบราว์เซอร์', 'site_login': 'ตรวจ session เว็บไซต์',
    'site_ready': 'เว็บไซต์พร้อม', 'browser_close': 'ปิดเบราว์เซอร์',
    'inventory': 'ตรวจรายการต้นทาง', 'source_error': 'ต้นทางยังไม่พร้อม',
    'prepare_result': 'ผลเตรียมงาน', 'duplicate_result': 'ผลค้นหาซ้ำ',
    'state_change': 'สถานะงาน', 'recheck_wait': 'รอผลตรวจไฟล์',
    'repack_status': 'สถานะ OnlyOne maker', 'repack_start': 'สั่งสร้าง torrent ใหม่',
    'repack_ready': 'ตรวจ torrent ใหม่ผ่าน',
}
FIELDS = frozenset({
    'job', 'node', 'site', 'state', 'source_hash', 'target_hash', 'stage', 'reason',
    'error_type', 'mode', 'status', 'publish', 'seed', 'resume', 'nodes', 'queued',
    'max_jobs', 'processed', 'skipped', 'remaining', 'discovered', 'counts',
    'skip_reasons', 'elapsed_seconds', 'complete', 'pages', 'candidates_checked',
    'metainfo_bytes', 'max_file_size', 'files', 'completed', 'total', 'done',
    'progress', 'amount_left', 'listing_id',
})
THAI_TIME = timezone(timedelta(hours=7))


def _text(value):
    value = re.sub(r'https?://\S+', '[URL]', str(value), flags=re.I)
    value = re.sub(r'(?i)\b(cookie|authorization)\s*[:=].*', r'\1=[REDACTED]', value)
    value = re.sub(r'''(?ix)\b(passkey|token|password|secret|csrf|sid)\s*[:=]\s*(?:"[^"]*"|'[^']*'|\S+)''',
                   r'\1=[REDACTED]', value)
    return re.sub(r'[\x00-\x1f\x7f]+', ' ', value)[:300]


def log_event(event, job=None, **fields):
    if event not in EVENTS:
        return
    context = {}
    if isinstance(job, dict):
        context.update(job=job.get('id'), node=job.get('source_node'), site=job.get('target_site'),
                       state=job.get('state'), source_hash=job.get('source_hash'))
    context.update(fields)
    output = {'event': event}
    for key, value in context.items():
        if key not in FIELDS or value is None:
            continue
        if key in ('counts', 'skip_reasons'):
            if isinstance(value, dict):
                output[key] = {k: v for k, v in value.items()
                               if isinstance(k, str) and re.fullmatch(r'[a-z_]{1,40}', k)
                               and type(v) is int and v >= 0}
        elif type(value) in (bool, int, float):
            output[key] = value
        elif isinstance(value, str):
            output[key] = _text(value)
            if key in ('job', 'source_hash', 'target_hash'):
                output[key] = output[key][:12]
    line = '[Auto Upload] ' + datetime.now(THAI_TIME).isoformat(timespec='seconds') + ' ' + EVENTS[event]
    try:
        print(line + ' ' + json.dumps(output, ensure_ascii=False), flush=True)
    except OSError:
        # Logging must not turn a successful POST into an uncertain submission.
        pass
