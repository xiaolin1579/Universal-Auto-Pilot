"""Durable BearBit upload workflow, independent of browser/client SDKs."""
from copy import deepcopy
import hashlib
import os
import re
from pathlib import Path
import tempfile
from torrent_store import infohash, stamp, UPLOAD_STATES as STATES, UPLOAD_HOLD_STATES as HOLD_STATES
from torrent_metainfo import (_extract_torrent_info_hash, payload_signature,
                              create_variant, verify_completed, UPLOAD_TARGETS,
                              upload_route_supported, valid_target_announce)

class UploadBlocked(ValueError):
    """A safe, operator-readable reason that contains no server response or secret."""


class UploadRejected(UploadBlocked):
    def __init__(self, code, title_limit=None):
        self.code, self.title_limit = code, title_limit
        super().__init__('Upload explicitly rejected by the website: ' + code)


def upload_title(job):
    from upload_titles import format_title, TitleError
    try:
        return format_title(job['metadata'], job['target_site'],
                            job.get('field_overrides', {}).get('title'))
    except TitleError as error:
        raise UploadBlocked(str(error)) from None


def upload_filename(job):
    from upload_titles import torrent_filename, TitleError
    try:
        override = job.get('field_overrides', {}).get('title')
        metadata = {'title': override} if override is not None else job['metadata']
        return torrent_filename(metadata, job['target_site'])
    except TitleError as error:
        raise UploadBlocked(str(error)) from None


ROUTES = UPLOAD_TARGETS
PUBLISH_CONFIRMATIONS = {'DEDBIT': {'ack2', 'ack4'}, 'BITSUSE': {'ack2'},
                         'TORRENTDD': {'cf1', 'cf2'}}


def duplicate_block_reason(evidence, site):
    """Log only controlled result codes and a numeric listing ID, never raw URLs."""
    listing = evidence.get('listing') or {}
    torrent_id = str(listing.get('torrent_id', '')) if isinstance(listing, dict) else ''
    destination = site if site in PUBLISH_CONFIRMATIONS else 'destination'
    match = ' (' + destination + ' #' + torrent_id + ')' if re.fullmatch(r'[0-9]{1,20}', torrent_id) else ''
    if evidence.get('status') == 'duplicate':
        return 'Duplicate payload already exists' + match
    if evidence.get('status') == 'possible_duplicate':
        return 'Possible duplicate payload requires review' + match
    reasons = {
        'page_budget_exceeded': 'page budget exceeded',
        'candidate_budget_exceeded': 'candidate budget exceeded',
        'search_page_unverified': 'search page could not be verified',
        'unrecognized_result_row': 'result row could not be parsed',
        'empty_result_section_unverified': 'empty result section could not be verified',
        'pagination_query_unverified': 'pagination filters could not be verified',
    }
    reason = reasons.get(evidence.get('reason'), 'results could not be fully verified')
    return 'Duplicate search incomplete: ' + reason


def publication_failure(adapter, error):
    """Persist controlled diagnostics; never store exception text or full URLs."""
    context = getattr(adapter, '_publication_context', None)
    context = context if isinstance(context, dict) else {}
    stages = {'prepare_submit', 'submit', 'await_result', 'resolve_listing'}
    paths = {'/upload.php', '/bs_upload.php', '/takeupload.php', '/browse.php', '/bs_browse.php',
             '/details.php', '/bs_details.php', '/login.php', '/bs_login.php'}
    return {'stage': context.get('stage') if context.get('stage') in stages else 'unknown',
            'page_path': context.get('page_path') if context.get('page_path') in paths else 'unknown',
            'error_type': type(error).__name__, 'checked_at': stamp()}


def select_rules(rules, metadata):
    """Select one explicit category rule per destination for a verified source."""
    zone = metadata.get('zone')
    if zone not in ROUTES or metadata.get('zone_verified') is not True or metadata.get('site') != 'BEARBIT':
        raise UploadBlocked('BearBit zone is not verified')
    matches = [r for r in rules if isinstance(r, dict) and r.get('enable') is True
               and r.get('source_zone') == zone
               and str(r.get('source_category')) == str(metadata.get('category'))]
    if not matches:
        raise UploadBlocked('An explicit category rule is required for this BearBit category')
    destinations = set()
    for rule in matches:
        site = rule.get('target_site')
        if (site in destinations or not upload_route_supported(site, zone, rule.get('target_category'))):
            raise UploadBlocked('Exactly one supported category rule per destination is required')
        destinations.add(site)
        if not isinstance(rule.get('fields', {}), dict) or not isinstance(rule.get('confirmations', {}), dict):
            raise UploadBlocked('Category fields and confirmations must be dictionaries')
        allowed = {'cf1', 'cf2'} if site == 'TORRENTDD' else {'ack2', 'ack4'}
        if (set(rule.get('confirmations', {})) - allowed
                or any(type(value) is not bool for value in rule.get('confirmations', {}).values())):
            raise UploadBlocked('Only factual website confirmations can be configured')
        if set(rule) - {'enable', 'source_zone', 'source_category', 'target_site', 'target_category',
                        'fields', 'confirmations', 'short_description'}:
            raise UploadBlocked('Unsupported category rule fields')
    return deepcopy(matches)


def select_rule(rules, metadata, target_site=None):
    matches = select_rules(rules, metadata)
    if target_site is not None:
        matches = [rule for rule in matches if rule['target_site'] == str(target_site).upper()]
    if len(matches) != 1:
        raise UploadBlocked('Select an explicit destination when multiple upload rules match')
    return matches[0]


def effective_rule(job):
    rule = deepcopy(job['rule'])
    overrides = job.get('field_overrides', {})
    for key in ('fields', 'confirmations'):
        rule.setdefault(key, {}).update(overrides.get(key, {}))
    if job.get('target_site') == 'TORRENTDD':
        rule['fields'].setdefault('poster', job.get('metadata', {}).get('picture', ''))
    return rule


def write_artifact(directory, job_id, kind, content, filename=None):
    directory = Path(directory).resolve(); directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if filename is not None:
        if (not re.fullmatch(r'[0-9a-f]{64}', job_id) or kind not in {'source', 'prepared', 'submission', 'canonical'}
                or filename in {'.', '..'} or not filename.endswith('.torrent')
                or re.search(r'[/\\\x00-\x1f]', filename)):
            raise UploadBlocked('Invalid upload artifact path')
        root = directory
        directory = root / job_id / kind
        if ((root / job_id).is_symlink() or directory.is_symlink()
                or not directory.resolve().is_relative_to(root)):
            raise UploadBlocked('Artifact directory escapes its upload root')
        (root / job_id).mkdir(exist_ok=True, mode=0o700)
        directory.mkdir(exist_ok=True, mode=0o700)
        path = directory / filename
    else:
        path = directory / (job_id + '.' + kind + '.torrent')
    fd, tmp = tempfile.mkstemp(dir=directory)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(content); handle.flush(); os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
    return str(path)


class UploadLedger:
    def __init__(self, store, artifacts):
        self.store, self.artifacts = store, artifacts

    def prepare(self, source, inventory, files, metadata, rule, announce, readiness=None, form_schema=None):
        manifest = verify_completed(source, inventory, files)
        if metadata.get('infohash') != manifest['info_hash'] or metadata.get('site') != 'BEARBIT':
            raise UploadBlocked('BearBit metadata does not match this source hash')
        select_rule([rule], metadata)
        if not metadata.get('title') or not metadata.get('description') or not metadata.get('details_url'):
            raise UploadBlocked('BearBit primary metadata incomplete')
        signature = payload_signature(source)
        key_text = ':'.join((manifest['info_hash'], rule['target_site'], metadata['zone'], str(rule['target_category'])))
        key = hashlib.sha256(key_text.encode()).hexdigest()
        variant = create_variant(source, rule['target_site'], metadata['zone'], rule['target_category'], announce)
        variant_hash = _extract_torrent_info_hash(variant)
        if readiness is not None and type(readiness.get('ready')) is not bool:
            raise UploadBlocked('Invalid draft readiness')
        job = {'id': key, 'state': 'draft' if readiness and not readiness['ready'] else 'prepared', 'source_hash': manifest['info_hash'],
               'source_site': 'BEARBIT', 'source_zone': metadata['zone'], 'source_node': inventory['node'],
               'save_path': inventory['save_path'], 'target_site': rule['target_site'],
               'target_zone': metadata['zone'], 'target_category': str(rule['target_category']),
               'content_group_id': signature, 'prepared_hash': variant_hash,
               'metadata': deepcopy(metadata), 'rule': deepcopy(rule), 'created_at': stamp(), 'updated_at': stamp()}
        if readiness is not None:
            job['readiness'] = deepcopy(readiness)
        if form_schema is not None:
            job['form_schema'] = {k: deepcopy(form_schema[k]) for k in ('fields', 'acknowledgements')}
        def apply(data):
            old = data['upload_jobs'].get(key)
            if old:
                if old['state'] not in ('draft', 'prepared', 'cancelled'):
                    return deepcopy(old)
                if (old['state'] != 'cancelled' and old['rule'] == job['rule']
                        and old.get('readiness') == job.get('readiness')
                        and old['save_path'] == job['save_path'] and old['source_node'] == job['source_node']
                        and old.get('metadata') == job.get('metadata')):
                    return deepcopy(old)
                if old['rule'] == job['rule'] and old.get('field_overrides') and job.get('form_schema'):
                    from upload_sites import form_readiness
                    job['field_overrides'] = deepcopy(old['field_overrides'])
                    job['readiness'] = form_readiness(job['form_schema'], effective_rule(job))
                    job['state'] = 'prepared' if job['readiness']['ready'] else 'draft'
                job['created_at'] = old['created_at']
                job['prepare_revision'] = old.get('prepare_revision', 1) + 1
            if any(j['id'] != key and j['source_hash'] == job['source_hash'] and j['target_site'] == job['target_site']
                   and j['state'] != 'cancelled' for j in data['upload_jobs'].values()):
                raise UploadBlocked('This source already has a job on this destination')
            # Write before the ledger pointer, under its lock; never overwrite a published artifact.
            filename = upload_filename(job)
            job['source_artifact'] = write_artifact(self.artifacts, key, 'source', source, filename)
            job['prepared_artifact'] = write_artifact(self.artifacts, key, 'prepared', variant, filename)
            data['upload_jobs'][key] = job
            group = data['content_groups'].setdefault(signature, {'id': signature, 'hashes': [], 'primary_site': 'BEARBIT'})
            if job['source_hash'] not in group['hashes']: group['hashes'].append(job['source_hash'])
            for row in data['torrents']:
                if infohash(row['client_info'].get('hash')) == job['source_hash']:
                    row['content_group_id'] = signature
            return deepcopy(job)
        return self.store.update(apply)

    def get(self, key):
        return deepcopy(self.store.read()['upload_jobs'][key])

    def review(self, key):
        job = self.get(key)
        public = {k: deepcopy(job[k]) for k in (
            'id', 'state', 'source_hash', 'source_site', 'source_zone', 'source_node',
            'target_site', 'target_zone', 'target_category', 'content_group_id', 'prepared_hash',
            'metadata', 'rule', 'readiness', 'form_schema', 'duplicate_check',
            'source_verified_at', 'field_overrides', 'created_at', 'updated_at', 'target_hash', 'listing',
            'notifications', 'submission_started_at', 'last_publication_failure') if k in job}
        public['target_title'] = upload_title(job)
        public['torrent_filename'] = upload_filename(job)
        public['effective_fields'] = effective_rule(job).get('fields', {})
        public['effective_confirmations'] = effective_rule(job).get('confirmations', {})
        # Artifact files contain announce credentials; expose only their content identity.
        return public

    def configure_job(self, key, changes):
        if not isinstance(changes, dict) or set(changes) - {'fields', 'confirmations', 'title'}:
            raise UploadBlocked('Job configuration accepts only fields, confirmations and display title')
        if any(not isinstance(v, dict) for k, v in changes.items() if k != 'title'):
            raise UploadBlocked('Job overrides must be dictionaries')
        def apply(data):
            from upload_sites import form_readiness
            job = data['upload_jobs'][key]
            if job['state'] not in ('draft', 'prepared') or not job.get('form_schema'):
                raise UploadBlocked('Only an unsubmitted job with a known form can be configured')
            fields = changes.get('fields', {})
            confirmations = changes.get('confirmations', {})
            allowed_ack = set(job['form_schema']['acknowledgements']) - {'ack3'}
            if set(fields) - set(job['form_schema']['fields']):
                raise UploadBlocked('Unknown destination field in job override')
            if set(confirmations) - allowed_ack or any(type(v) is not bool for v in confirmations.values()):
                raise UploadBlocked('Only the factual form confirmations can be configured')
            overrides = job.setdefault('field_overrides', {})
            for section, values in changes.items():
                if section == 'title':
                    if not isinstance(values, str) or not values.strip():
                        raise UploadBlocked('Display title must be nonempty text')
                    overrides['title'] = values
                else:
                    overrides.setdefault(section, {}).update(deepcopy(values))
            upload_title(job)
            readiness = form_readiness(job['form_schema'], effective_rule(job))
            if readiness['invalid_fields']:
                raise UploadBlocked('Invalid destination field value in job override')
            job.update(readiness=readiness, state='prepared' if readiness['ready'] else 'draft', updated_at=stamp())
        self.store.update(apply)
        return self.review(key)

    def confirm_for_publish(self, key):
        """Called only when the operator explicitly enables publication.

        ack3 is derived from a fresh duplicate check by the worker, never here.
        Keep the stored rule intact so disabling/changing it still blocks jobs.
        """
        job = self.get(key)
        if job['state'] not in ('draft', 'prepared') or not job.get('form_schema'):
            raise UploadBlocked('Only an unsubmitted job with a known form can be confirmed')
        factual = set(job['form_schema']['acknowledgements']) - {'ack3'}
        if not factual <= PUBLISH_CONFIRMATIONS.get(job['target_site'], set()):
            raise UploadBlocked('Destination confirmations changed; inspect the upload form')
        return self.configure_job(key, {'confirmations': {name: True for name in factual}})

    def transition(self, key, expected, state, **fields):
        if state not in STATES:
            raise UploadBlocked('Invalid upload state')
        def apply(data):
            job = data['upload_jobs'][key]
            if job['state'] not in expected:
                raise UploadBlocked('Upload state changed; refusing repeated action')
            job.update(fields, state=state, updated_at=stamp())
            return deepcopy(job)
        return self.store.update(apply)

    @staticmethod
    def same_configuration(current, expected):
        keys = ('source_hash', 'source_node', 'save_path', 'prepared_hash', 'metadata', 'rule', 'field_overrides')
        return all(current.get(k) == expected.get(k) for k in keys)

    def claim_submission(self, key, expected, evidence):
        def apply(data):
            current = data['upload_jobs'][key]
            if current['state'] != 'prepared' or not self.same_configuration(current, expected):
                raise UploadBlocked('Job configuration changed during preflight; check the job again')
            current.update(state='submitting', duplicate_check={**evidence, 'checked_at': stamp()},
                           submission_started_at=stamp(), updated_at=stamp())
        self.store.update(apply)

    def record_check(self, key, expected, evidence, readiness=None):
        def apply(data):
            current = data['upload_jobs'][key]
            if current['state'] not in ('draft', 'prepared') or not self.same_configuration(current, expected):
                raise UploadBlocked('Job configuration changed while it was being checked')
            current['duplicate_check'] = {**evidence, 'checked_at': stamp()}
            if readiness is not None:
                current.update(readiness=readiness, state='prepared' if readiness['ready'] else 'draft',
                               source_verified_at=stamp())
            current['updated_at'] = stamp()
        self.store.update(apply)

    def record_publication(self, key, listing):
        if not str(listing.get('torrent_id', '')).isdigit() or not listing.get('details_url'):
            raise UploadBlocked('Publication has no verified listing identity')
        return self.transition(key, {'submitting', 'publication_uncertain'}, 'published', listing=listing)

    def accept_canonical(self, key, content):
        job = self.get(key); target = _extract_torrent_info_hash(content)
        if payload_signature(content) != job['content_group_id'] or target == job['source_hash']:
            raise UploadBlocked('Published torrent payload differs, or hash is unchanged')
        # Only destination announce hosts; source credentials must not reach the client.
        from torrent_metainfo import _extract_torrent_manifest
        from torrent_metainfo import _decode_bencode_value
        decoded, _ = _decode_bencode_value(content)
        if decoded[b'info'].get(b'private') != 1 or any(k in decoded for k in (b'url-list', b'httpseeds', b'nodes')):
            raise UploadBlocked('Canonical torrent is not private or contains alternate data sources')
        urls = _extract_torrent_manifest(content)['announce_urls']
        if not urls or any(not valid_target_announce(job['target_site'], url) for url in urls):
            raise UploadBlocked('Canonical torrent has an unrecognized tracker')
        def apply(data):
            current = data['upload_jobs'][key]
            if current['state'] not in {'published', 'verification_failed'}:
                raise UploadBlocked('Canonical verification is not allowed in this state')
            group = data['content_groups'][job['content_group_id']]
            if (target in group['hashes']
                    or any(infohash(r['client_info'].get('hash')) == target for r in data['torrents'])):
                raise UploadBlocked('Canonical hash already belongs to another source or variant')
            current.update(state='verified', target_hash=target, updated_at=stamp(),
                canonical_artifact=write_artifact(self.artifacts, key, 'canonical', content, upload_filename(job)))
            group['hashes'].append(target)
            listing = current['listing']; site = job['target_site']; tid = str(listing['torrent_id'])
            site_key = site + ':' + tid
            previous = data['site_records'].get(site_key)
            if previous and previous.get('infohash') not in (None, target):
                raise UploadBlocked('Listing already has another infohash')
            if not previous:
                data['site_records'][site_key] = {'site': site, 'torrent_id': tid, 'infohash': target,
                    'legacy_state': {'hash': target, 'name': job['metadata']['title'],
                                     'url': listing['details_url']},
                    'obligations': {k: {'status': 'unknown'} for k in ('hr', 'quest_seed')}}
            if not any(infohash(r['client_info'].get('hash')) == target for r in data['torrents']):
                data['torrents'].append({'client_info': {'hash': target, 'name': job['metadata']['title']},
                    'content_group_id': job['content_group_id'], 'source_site': site, 'source_site_verified': True,
                    'source_zone_id': job['target_zone'], 'source_zone_verified': True,
                    'source_zone_evidence': {'method': 'authenticated_upload_canonical_torrent',
                        'details_url': listing['details_url'], 'verified_at': stamp()},
                    'variant_info': {'role': 'uploaded_variant', 'original_infohash': job['source_hash'],
                        'target_site': site, 'target_zone_id': job['target_zone'], 'verified': True},
                    'site_listings': [{'site_key': site, 'torrent_id': tid, 'details_url': listing['details_url'],
                                       'zone_id': job['target_zone'], 'verified': True}], 'node_locations': []})
            return deepcopy(current)
        return self.store.update(apply)


class UploadWorker:
    """Adapters must supply live metadata/duplicate evidence, never optimistic fallbacks."""
    def __init__(self, ledger, *, enable_publish=False):
        self.ledger = ledger
        self.enable_publish = enable_publish is True

    async def check_job(self, key, adapter, client):
        """Inspect a draft/prepared job without any publication or client mutation."""
        job = self.ledger.get(key)
        if job['state'] not in ('draft', 'prepared'):
            raise UploadBlocked('Only an unsubmitted job can be checked')
        self.ledger.record_check(key, job, {'status': 'unknown', 'complete': False, 'reason': 'check_in_progress'})
        live = client.inventory(job['source_hash'])
        if client.name != job['source_node'] or live.get('save_path') != job['save_path']:
            raise UploadBlocked('Source client or save path changed')
        verify_completed(Path(job['source_artifact']).read_bytes(), live, client.files(job['source_hash']))
        spec = await adapter.load_form(job, require_ready=False)
        evidence = await adapter.check_duplicates(job)
        self.ledger.record_check(key, job, evidence, readiness=spec['readiness'])
        return self.ledger.review(key)

    async def publish(self, key, adapter, client):
        job = self.ledger.get(key)
        if self.enable_publish and job['state'] in ('draft', 'prepared'):
            self.ledger.confirm_for_publish(key)
            job = self.ledger.get(key)
        if job['state'] == 'draft':
            readiness = job.get('readiness', {})
            names = [str(name) for group in ('missing_fields', 'invalid_fields', 'unknown_fields', 'missing_confirmations')
                     for name in readiness.get(group, [])]
            raise UploadBlocked('Destination form is incomplete: ' + (', '.join(names) or 'inspect the job fields'))
        if job['state'] != 'prepared':
            if job['state'] in ('submitting', 'publication_uncertain'):
                raise UploadBlocked('Uncertain submission requires reconciliation before publication')
            raise UploadBlocked('Only prepared jobs can be published (current state: ' + job['state'] + ')')
        # Revalidate live source; Cleaner holds it while this job is active.
        source = Path(job['source_artifact']).read_bytes()
        live = client.inventory(job['source_hash'])
        if client.name != job['source_node'] or live.get('save_path') != job['save_path']:
            raise UploadBlocked('Source client or save path changed; prepare a new job')
        verify_completed(source, live, client.files(job['source_hash']))
        form = await adapter.load_form(job)
        variant = create_variant(source, job['target_site'], job['target_zone'], job['target_category'], form['announce'])
        if _extract_torrent_info_hash(variant) != job['prepared_hash']:
            raise UploadBlocked('Prepared variant no longer matches the rule')
        self.ledger.record_check(key, job, {'status': 'unknown', 'complete': False, 'reason': 'check_in_progress'})
        evidence = await adapter.check_duplicates(job)
        if evidence.get('status') != 'clear' or evidence.get('complete') is not True:
            self.ledger.record_check(key, job, evidence)
            raise UploadBlocked(duplicate_block_reason(evidence, job['target_site']))
        # Duplicate search navigates away: refresh nonce and validate fields once more.
        form = await adapter.load_form(job)
        variant = create_variant(source, job['target_site'], job['target_zone'], job['target_category'], form['announce'])
        path = write_artifact(self.ledger.artifacts, key, 'submission', variant, upload_filename(job))
        await adapter.fill(job, path)
        self.ledger.claim_submission(key, job, evidence)
        try:
            listing = await adapter.submit(job)
            self.ledger.record_publication(key, listing)
        except UploadRejected as exc:
            self.ledger.transition(key, {'submitting'}, 'prepared',
                last_submission_failure={'code': exc.code, 'title_limit': exc.title_limit, 'verified_at': stamp()})
            raise
        except BaseException as exc:
            self.ledger.transition(key, {'submitting'}, 'publication_uncertain',
                                   last_publication_failure=publication_failure(adapter, exc))
            raise
        return await self.verify_publication(key, adapter)

    async def verify_publication(self, key, adapter):
        job = self.ledger.get(key)
        try:
            canonical = await adapter.download_listing(job['listing']['details_url'])
            return self.ledger.accept_canonical(key, canonical)
        except Exception:
            self.ledger.transition(key, {'published', 'verification_failed'}, 'verification_failed')
            raise

    def seed(self, key, client, resume=False):
        job = self.ledger.get(key)
        if job['source_node'] != client.name:
            raise UploadBlocked('Cross-seed must use the verified source client and save path')
        if job['state'] == 'verified':
            live = client.inventory(job['source_hash'])
            if live.get('save_path') != job['save_path']:
                raise UploadBlocked('Source save path changed')
            verify_completed(Path(job['source_artifact']).read_bytes(), live, client.files(job['source_hash']))
            content = Path(job['canonical_artifact']).read_bytes()
            if (_extract_torrent_info_hash(content) != job['target_hash']
                    or payload_signature(content) != job['content_group_id']):
                raise UploadBlocked('Canonical artifact changed')
            # Persist before client mutation: never blindly retry an interrupted add.
            self.ledger.transition(key, {'verified'}, 'adding')
            try:
                if client.inventory(job['target_hash'], missing_ok=True) is not None:
                    raise UploadBlocked('Target hash already exists; reconcile it explicitly')
                if not client.add_paused(content, job):
                    raise RuntimeError('Paused add not confirmed')
                if not client.recheck(job['target_hash']):
                    raise RuntimeError('Recheck request not confirmed')
                self.ledger.transition(key, {'adding'}, 'rechecking', recheck_requested_at=stamp())
                # A later pass must observe checking and then a completed paused state.
            except BaseException:
                self.ledger.transition(key, {'adding'}, 'seed_uncertain')
                raise
            return self.ledger.get(key)
        if job['state'] != 'rechecking':
            return job
        row = client.inventory(job['target_hash'])
        if row.get('save_path') != job['save_path']:
            raise UploadBlocked('Target save path changed during recheck')
        state = str(row.get('state', '')).lower()
        if state.startswith('checking'):
            return self.ledger.transition(key, {'rechecking'}, 'rechecking', recheck_observed=True)
        if not job.get('recheck_observed') and not client.recheck_complete(job['target_hash']):
            return job
        verify_completed(Path(job['canonical_artifact']).read_bytes(), row, client.files(job['target_hash']))
        if not resume:
            return job
        if not client.resume(job['target_hash']):
            raise RuntimeError('Resume not confirmed')
        confirmed = client.inventory(job['target_hash'])
        if confirmed.get('save_path') != job['save_path']:
            raise UploadBlocked('Target save path changed after resume')
        verify_completed(Path(job['canonical_artifact']).read_bytes(), confirmed, client.files(job['target_hash']))
        if str(confirmed.get('state', '')).lower() not in ('uploading', 'stalledup', 'forcedup'):
            return self.ledger.get(key)
        def apply(data):
            current = data['upload_jobs'][key]
            if current['state'] != 'rechecking':
                raise UploadBlocked('Seed state changed during verification')
            for record in data['torrents']:
                if infohash(record['client_info'].get('hash')) == job['target_hash']:
                    locations = record.setdefault('node_locations', [])
                    locations[:] = [loc for loc in locations if loc.get('seedbox_host') != client.name]
                    locations.append({'seedbox_host': client.name, 'save_path': confirmed['save_path'],
                        'present': True, 'inventory_verified': True, 'verified_at': stamp(),
                        'verification_method': 'live_upload_recheck_complete'})
            current.update(state='seeding', verified_at=stamp(), updated_at=stamp())
            return deepcopy(current)
        return self.ledger.store.update(apply)

    def reconcile_seed(self, key, client):
        job = self.ledger.get(key)
        if job['state'] not in ('adding', 'seed_uncertain') or client.name != job['source_node']:
            raise UploadBlocked('Only an uncertain add on its source client can be reconciled')
        row = client.inventory(job['target_hash'])
        if row.get('save_path') != job['save_path']:
            raise UploadBlocked('Existing target save path differs from the verified source')
        from torrent_metainfo import _extract_torrent_manifest
        m = _extract_torrent_manifest(Path(job['canonical_artifact']).read_bytes())
        expected = [(m['root_name'] + '/' + f['path'] if m['multi_file'] else f['path'], f['size_bytes']) for f in m['files']]
        ordinary = [(m['root_name'] + '/' + f['path'] if m['multi_file'] else f['path'], f['size_bytes'])
                    for f in m['files'] if not f.get('is_padding')]
        if sorted((f['path'], f['size_bytes']) for f in client.files(job['target_hash'])) not in (sorted(expected), sorted(ordinary)):
            raise UploadBlocked('Existing target manifest differs from canonical torrent')
        if not client.recheck(job['target_hash']):
            raise RuntimeError('Recheck request not confirmed')
        return self.ledger.transition(key, {'adding', 'seed_uncertain'}, 'rechecking',
                                      recheck_requested_at=stamp(), recheck_observed=False)
