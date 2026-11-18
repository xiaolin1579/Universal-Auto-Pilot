"""Live-form category checks and conservative repairs for unsubmitted jobs."""
import asyncio
from copy import deepcopy
from pathlib import Path

from auto_upload import UploadBlocked, write_artifact, upload_filename
from torrent_store import stamp
from torrent_metainfo import _extract_torrent_info_hash, payload_signature, _extract_torrent_manifest
from upload_classifier import classify_job, extract_identity, input_fingerprint, origin_requires_review, clean_text
from upload_sites import form_readiness
from upload_languages import language_value

BLOCKING_FLAGS = {'origin_requires_review', 'destination_schema_unavailable', 'multiple_release_codes',
                  'catalog_identity_ambiguous', 'conflicting_evidence', 'censorship_claim_conflict'}


def correction_plan(job, report, auto_fix):
    """Fill supported enums and declared track languages from high evidence."""
    fields = dict(job.get('rule', {}).get('fields', {}))
    overrides = (job.get('field_overrides') or {}).get('fields', {})
    fields.update(overrides)
    managed = job.get('category_auto_fields') or {}
    schema = job['form_schema']['fields']
    issues = set(report['review_flags']) & BLOCKING_FLAGS
    changes = {}
    for suggestion in report['suggestions']:
        field = suggestion['field']; value = suggestion['value']
        spec = schema.get(field, {}); options = spec.get('options')
        language = (field in ('c3dub', 'c3sub') and options is None and language_value(value)
                    and suggestion['reasons'] == ['mediainfo_track_languages'])
        # Only the two language controls accept generated text; other text stays manual.
        if (not language and (not isinstance(options, list) or value not in options)) or suggestion['confidence'] != 'high':
            continue
        before = fields.get(field)
        valid = isinstance(before, str) and bool(before.strip()) and (options is None or before in options)
        if valid:
            if before != value: issues.add('existing_value_conflicts_with_evidence:' + field)
            continue
        explicit = field in overrides and bool(str(overrides[field] or '').strip())
        if explicit and managed.get(field) != overrides[field]:
            issues.add('explicit_invalid_value:' + field)
            continue
        if auto_fix:
            changes[field] = value
    if issues: changes = {}
    return changes, sorted(issues)


def block_origin(ledger, expected):
    """Record a review hold before reading source metainfo or a destination form."""
    def apply(data):
        current = data['upload_jobs'][expected['id']]
        if current['state'] not in ('draft', 'prepared') or not ledger.same_configuration(current, expected):
            raise UploadBlocked('Job changed during category review; inspect it again')
        checked = stamp()
        report = classify_job(current)
        current['classification'] = {**report, 'saved':True, 'checked_at':checked}
        current['category_review'] = {'version':1, 'checked_at':checked, 'status':'review_required',
            'job_fingerprint':input_fingerprint(current), 'issues':['origin_requires_review'],
            'applied_fields':{}, 'auto_fix':False, 'missing_fields':[], 'invalid_fields':[]}
        current.setdefault('readiness', {}).update(ready=False, category_issues=['origin_requires_review'])
        current.update(state='draft', updated_at=checked)
        return deepcopy(current)
    return ledger.store.update(apply)


async def review_categories(ledger, key, spec, source, settings, cache_dir):
    expected = ledger.get(key)
    if expected['state'] not in ('draft', 'prepared'):
        raise UploadBlocked('Category repair only accepts unsubmitted jobs')
    if (_extract_torrent_info_hash(source) != expected['source_hash']
            or payload_signature(source) != expected['content_group_id']):
        raise UploadBlocked('Category review source differs from the queued job')
    schema = {k:deepcopy(spec[k]) for k in ('fields','acknowledgements','max_file_size') if k in spec}
    if not isinstance(schema.get('fields'), dict) or not isinstance(schema.get('acknowledgements'), list):
        raise UploadBlocked('Category review needs a verified live destination form')
    snapshot = {**deepcopy(expected), 'form_schema':schema}
    manifest = _extract_torrent_manifest(source)
    files = [{'path':f['path'],'size_bytes':f['size_bytes']} for f in manifest['files'] if not f.get('is_padding')]
    records = []; diagnostics = []; providers = []
    from metadata_providers import lookup_metadata, load_catalog_records
    if not origin_requires_review(snapshot):
        if settings.get('catalog_file'):
            try:
                records = await asyncio.to_thread(load_catalog_records, settings['catalog_file'])
            except (OSError, ValueError):
                diagnostics.append('catalog_file_unavailable')
        if settings.get('musicbrainz') is True:
            lookup = await asyncio.to_thread(lookup_metadata, extract_identity(snapshot), musicbrainz=True,
                                             cache_dir=cache_dir)
            records += lookup['records']; diagnostics += lookup['diagnostics']; providers = lookup['providers']
    report = classify_job(snapshot, files=files, records=records, diagnostics=diagnostics)
    changes, issues = correction_plan(snapshot, report, settings.get('auto_fix') is True)

    def apply(data):
        current = data['upload_jobs'][key]
        if (current['state'] != expected['state'] or current.get('updated_at') != expected.get('updated_at')
                or not ledger.same_configuration(current, expected)
                or current.get('form_schema') != expected.get('form_schema')):
            raise UploadBlocked('Job changed during category review; inspect it again')
        checked = stamp()
        current['form_schema'] = schema
        if changes:
            current['field_overrides'] = current.get('field_overrides') or {}
            current['field_overrides'].setdefault('fields', {}).update(changes)
            current.setdefault('category_auto_fields', {}).update(changes)
            history = current.setdefault('category_corrections', [])
            for field, value in changes.items():
                suggestion = next(s for s in report['suggestions'] if s['field'] == field)
                history.append({'field':field, 'before':clean_text(suggestion['current']), 'after':value,
                    'confidence':'high','reasons':suggestion['reasons'],'evidence':suggestion['evidence'],
                    'checked_at':checked})
        from auto_upload import effective_rule
        readiness = form_readiness(schema, effective_rule(current))
        if issues:
            readiness.update(ready=False, category_issues=issues)
        current.update(readiness=readiness, state='prepared' if readiness['ready'] else 'draft', updated_at=checked)
        # A verified source export can replace an inaccessible pointer from another checkout.
        current['source_artifact'] = write_artifact(ledger.artifacts, key, 'source', source, upload_filename(current))
        updated = classify_job(current, files=files, records=records, diagnostics=diagnostics)
        current['classification'] = {**updated,'saved':True,'checked_at':checked,'providers':providers}
        current['category_review'] = {'version':1,'checked_at':checked,'job_fingerprint':input_fingerprint(current),
            'status':'ready' if readiness['ready'] else 'review_required','issues':issues,
            'applied_fields':deepcopy(changes),'auto_fix':settings.get('auto_fix') is True,
            'missing_fields':readiness['missing_fields'],'invalid_fields':readiness['invalid_fields']}
        return deepcopy(current)
    result = await asyncio.to_thread(ledger.store.update, apply)
    from upload_logging import log_event
    log_event('category_result', result, status=result['category_review']['status'],
              counts={'applied_fields':len(changes),'issues':len(issues)},
              reason='category_review_required' if issues else 'live_form_verified')
    return result


def category_settings(config, base_dir):
    settings = deepcopy(config.get('category_review') or {})
    if not isinstance(settings, dict) or any(type(settings[k]) is not bool
            for k in ('enable','auto_fix','musicbrainz') if k in settings):
        raise UploadBlocked('AUTO_UPLOAD.category_review must contain Boolean enable/auto_fix/musicbrainz flags')
    if 'catalog_file' in settings and not isinstance(settings['catalog_file'], str):
        raise UploadBlocked('AUTO_UPLOAD.category_review.catalog_file must be a path string')
    if settings.get('catalog_file'):
        path = Path(settings['catalog_file'])
        if not path.is_absolute(): path = Path(base_dir) / path
        settings['catalog_file'] = str(path)
    settings['cache_dir'] = str(Path(base_dir) / '.metadata_cache')
    return settings
