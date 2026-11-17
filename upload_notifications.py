"""Durable, coalesced upload notices sent through the application's notifier."""
import hashlib
from html import escape
import re

from auto_upload import UploadBlocked
from torrent_store import stamp


def safe_error(error, job=None):
    if isinstance(error, UploadBlocked):
        return str(error)
    if job and job.get('state') == 'publication_uncertain':
        failure = job.get('last_publication_failure', {})
        stage = failure.get('stage')
        if stage in ('prepare_submit', 'submit', 'await_result', 'resolve_listing', 'unknown'):
            return 'Publication unconfirmed at ' + stage + ' (' + type(error).__name__ + '); inspect destination before retry'
    return type(error).__name__


def _text(value, limit=300):
    text = re.sub(r'https?://\S+', '[URL]', str(value or ''), flags=re.I)
    text = re.sub(r'(?i)\b(passkey|token|password|cookie|authorization)\s*[:=]\s*\S+', r'\1=[REDACTED]', text)
    text = re.sub(r'[\x00-\x1f\x7f]+', ' ', text)
    result = ''
    for char in text:
        encoded = escape(char, quote=True)
        if len(result) + len(encoded) > limit:
            break
        result += encoded
    return result


def _listing(site, listing):
    from upload_sites import listing_identity
    try:
        # Rebuild the canonical URL; never forward extra query parameters.
        url = listing_identity(site, listing['details_url'])['details_url']
        return escape(url, quote=True) if len(url) < 200 else ''
    except (KeyError, TypeError, ValueError):
        return ''


def message_for(job, error=None):
    labels = {
        'draft': '📝 Auto Upload: รอข้อมูลเพิ่มเติม',
        'prepared': '📦 Auto Upload: พร้อมอัปโหลด',
        'published': '📤 Auto Upload: เว็บรับรายการแล้ว รอตรวจไฟล์ torrent',
        'verified': '✅ Auto Upload: อัปโหลดสำเร็จและตรวจไฟล์ torrent แล้ว',
        'rechecking': '🔎 Auto Upload: อัปโหลดสำเร็จ อยู่ระหว่างตรวจไฟล์บน seedbox',
        'seeding': '🌱 Auto Upload: เริ่ม seed แล้ว',
        'verification_failed': '⚠️ Auto Upload: เว็บรับรายการแล้ว แต่ตรวจไฟล์ torrent ไม่ผ่าน',
        'submitting': '🚨 Auto Upload: ผลการส่งยังไม่ยืนยัน ต้องตรวจสอบก่อนส่งซ้ำ',
        'publication_uncertain': '🚨 Auto Upload: ผลการส่งยังไม่ยืนยัน ต้องตรวจสอบก่อนส่งซ้ำ',
        'adding': '🚨 Auto Upload: ผลการเพิ่มงานบน seedbox ยังไม่ยืนยัน',
        'seed_uncertain': '🚨 Auto Upload: ผลการเพิ่มงานบน seedbox ยังไม่ยืนยัน',
        'cancelled': '🛑 Auto Upload: ยกเลิกงานแล้ว',
    }
    label = labels.get(job.get('state'))
    if job.get('state') == 'seeding' and job.get('duplicate_resolution', {}).get('method') == 'existing_destination_torrent':
        label = '♻️ Auto Upload: ผูกรายการเดิมที่กำลัง seed แล้ว'
    if not label:
        return None
    needs_input = job['state'] == 'draft' and error and error.startswith('Destination form is incomplete:')
    if error and not needs_input and job['state'] not in ('publication_uncertain', 'submitting', 'adding', 'seed_uncertain', 'verification_failed'):
        label = '⚠️ Auto Upload: งานหยุดรอตรวจสอบ'
    manual_retry = job.get('publication_retry', {}).get('manual_publish_required') is True
    if manual_retry and job['state'] == 'prepared' and not error:
        label = '🔁 Auto Upload: อนุญาตส่งใหม่ รอคำสั่ง publish'
    lines = ['<b>' + label + '</b>', _text(job.get('metadata', {}).get('title')),
             'BEARBIT (' + _text(job.get('source_zone')) + ') → ' + _text(job.get('target_site'))
             + ' (' + _text(job.get('target_zone')) + ')',
             'โหนด: ' + _text(job.get('source_node')), 'สถานะ: ' + _text(job['state']),
             'Job: ' + _text(job['id'])]
    if manual_retry and job['state'] in ('draft', 'prepared'):
        lines.append('ส่งใหม่ด้วยคำสั่ง publish เท่านั้น คิวอัตโนมัติจะยังไม่ส่งงานนี้')
    if error:
        lines.append('เหตุผล: ' + ('ข้อมูลแบบฟอร์มยังไม่ครบ จึงยังไม่ได้ส่งอัปโหลด' if needs_input else _text(error, 400)))
    if job['state'] in ('publication_uncertain', 'submitting') and job.get('last_publication_failure'):
        failure = job['last_publication_failure']
        lines.append('ขั้นตอนล่าสุด: ' + _text(failure.get('stage'), 40)
                     + ' | หน้า: ' + _text(failure.get('page_path'), 60))
    if job['state'] == 'draft':
        readiness = job.get('readiness', {})
        missing = [str(v) for key in ('missing_fields', 'invalid_fields', 'unknown_fields', 'missing_confirmations')
                   for v in readiness.get(key, [])]
        if missing:
            field_labels = {'c100type': 'ประเภทเนื้อหา', 'c100type2': 'ประเภทย่อย', 'c100cat': 'ประเทศ/หมวด'} if job.get('target_site') == 'BITSUSE' else {}
            names = [field_labels[name] + ' (' + name + ')' if name in field_labels else name for name in missing]
            lines.append('ข้อมูลที่ต้องตรวจ: ' + _text(', '.join(names)))
    url = _listing(job['target_site'], job.get('listing') or {})
    if url:
        lines.append('รายการปลายทาง: ' + url)
    elif error:
        duplicate = _listing(job['target_site'], job.get('duplicate_check', {}).get('listing') or {})
        if duplicate:
            lines.append('รายการที่พบระหว่างตรวจซ้ำ: ' + duplicate)
    return '\n'.join(lines)


class UploadNotifications:
    def __init__(self, app, ledger):
        self.app, self.ledger = app, ledger

    def record(self, job, error=None):
        """Keep only the latest notice per job; a notice never changes job state."""
        try:
            message = message_for(job, error)
            if message is None:
                return
            fingerprint = hashlib.sha256(message.encode()).hexdigest()
            note = self.ledger.get(job['id']).get('notifications', {})
            if (error is None and job['state'] in ('publication_uncertain', 'submitting', 'adding', 'seed_uncertain')
                    and note.get('observed_state') == job['state']):
                return
            if (note.get('pending', {}).get('fingerprint') == fingerprint
                    or (note.get('last_sent') == fingerprint and not note.get('pending'))):
                return
            def update(data):
                current = data['upload_jobs'][job['id']]
                if current['state'] != job['state']:
                    return
                note = current.setdefault('notifications', {})
                note['observed_state'] = job['state']
                if note.get('last_sent') == fingerprint:
                    note.pop('pending', None)
                else:
                    note['pending'] = {'fingerprint': fingerprint, 'message': message, 'queued_at': stamp()}
            self.ledger.store.update(update)
        except Exception as exc:
            print('[Auto Upload Notify] Queue failed:', type(exc).__name__)

    async def flush(self):
        """Retry pending notices once per cycle, including terminal/uncertain jobs."""
        sender = getattr(self.app, 'send_notify', None)
        if not callable(sender):
            return
        try:
            jobs = self.ledger.store.read()['upload_jobs'].values()
            for job in jobs:
                pending = job.get('notifications', {}).get('pending')
                if not pending:
                    continue
                try:
                    if await sender(pending['message']) is not True:
                        continue
                    def delivered(data):
                        note = data['upload_jobs'][job['id']].setdefault('notifications', {})
                        note.update(last_sent=pending['fingerprint'], sent_at=stamp())
                        if note.get('pending', {}).get('fingerprint') == pending['fingerprint']:
                            note.pop('pending', None)
                    self.ledger.store.update(delivered)
                except Exception as exc:
                    print('[Auto Upload Notify] Delivery failed:', type(exc).__name__)
        except Exception as exc:
            print('[Auto Upload Notify] Pending notices unavailable:', type(exc).__name__)
