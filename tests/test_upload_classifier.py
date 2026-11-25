import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from upload_classifier import classify_job, extract_identity, spelling_match, input_fingerprint, public_source
from metadata_providers import MetadataCache, lookup_metadata, load_catalog_records


def music(title='Ambra - Child Of The Universe - 2003 (SACD ISO)'):
    return {'id':'a'*64,'source_hash':'b'*40,'source_node':'node','source_zone':'normal',
        'metadata':{'title':title,'root_name':title,'category':'เพลงคุณภาพสูง (Lossless)','description':''},
        'target_site':'DEDBIT','target_category':'41','rule':{'fields':{}},'field_overrides':None,
        'form_schema':{'fields':{
            'c4genre':{'options':['ไทยสากล','ไทยลูกทุ่ง','สากล','เอเชีย','เพลงบรรเลง(ตปท)']},
            'c41type':{'options':['รวม','OST','Full Album','Single','Concert']},
            'c41sub':{'options':['MP3','WAV','AAC','Lossless','วิดีโอ']}}}}


def adult(title='ABC-123 Sample'):
    return {**music(title),'source_zone':'special','target_site':'BITSUSE','target_category':'180',
        'metadata':{'title':title,'root_name':title,'category':'ญี่ปุ่น ไม่เซ็น','description':''},
        'form_schema':{'fields':{'c100type':{'options':['ภาพยนตร์','การ์ตูน','รูปภาพ']},
             'c100cat':{'options':['ไทย','ญี่ปุ่น','ฝรั่ง']},'c100type2':{'options':['ชาย-หญิง','หญิง-หญิง']}}}}


def album(**values):
    return {'kind':'music','provider':'musicbrainz','url':'https://musicbrainz.org/release/example',
        'title':'Child Of The Universe','artist':'Ambra','year':2003,'release_type':'Album',
        'secondary_types':[],'format':'Hybrid SACD',**values}


def values(report): return {s['field']:s['value'] for s in report['suggestions']}


class ClassifierTests(unittest.TestCase):
    def test_empty_stored_schema_is_flagged_but_verified_live_form_is_not(self):
        row=music();row['form_schema']['fields']={}
        self.assertIn('destination_schema_unavailable',classify_job(row)['review_flags'])
        self.assertNotIn('destination_schema_unavailable',
                         classify_job(row,live_schema_verified=True)['review_flags'])

    def test_ambiguous_year_and_volume_are_not_release_year(self):
        identity=extract_identity(music('[MP3]-Albums Singles Vol 1974'))
        self.assertIsNone(identity['year']);self.assertEqual(identity['release_codes'],[])

    def test_album_identity_is_cleaned_without_losing_artist(self):
        identity=extract_identity(music('[SACD-R][OF] Ambra – Child Of The Universe - 2003 (New Age)'))
        self.assertEqual((identity['artist'],identity['title'],identity['year']),('Ambra','Child Of The Universe',2003))

    def test_code_prefix_never_determines_country_or_year(self):
        row=adult();row['metadata']['category']='unknown'
        identity=extract_identity(row)
        self.assertEqual(identity['release_codes'],['ABC-123']);self.assertIsNone(identity['year'])
        self.assertNotIn('c100cat',values(classify_job(row)))

    def test_musicbrainz_matches_artist_title_year_and_medium(self):
        report=classify_job(music(),records=[album()],files=[{'path':'album.iso'}])
        self.assertEqual(values(report)['c41type'],'Full Album')
        self.assertEqual(values(report)['c41sub'],'Lossless')
        self.assertIn('c4genre',report['unresolved_fields'])
        self.assertFalse(report['applied'])

    def test_wrong_artist_year_or_edition_is_not_evidence(self):
        for record in (album(artist='Another'),album(year=2004),album(format='CD')):
            with self.subTest(record=record):
                self.assertNotIn('c41type',values(classify_job(music(),records=[record])))

    def test_compilation_and_soundtrack_override_plain_album_semantics(self):
        for secondary,expected in [(['Compilation'],'รวม'),(['Soundtrack'],'OST')]:
            report=classify_job(music(),records=[album(secondary_types=secondary)])
            self.assertEqual(values(report)['c41type'],expected)

    def test_two_matching_editions_require_review(self):
        report=classify_job(music(),records=[album(),album(url='https://musicbrainz.org/release/another')])
        self.assertIn('catalog_identity_ambiguous',report['review_flags'])
        self.assertNotIn('c41type',values(report))

    def test_sales_country_and_tracklist_language_do_not_infer_music_genre(self):
        report=classify_job(music(),records=[album(country='DE',language='eng')])
        self.assertNotIn('c4genre',values(report))

    def test_mixed_compilation_is_not_forced_into_one_music_region(self):
        row=music('Albums Singles Vol 1974');row['metadata']['description']='รวมเพลง ไทยสตริง ลูกทุ่ง สากล เอเชีย'
        report=classify_job(row,files=[{'path':'track.mp3'}])
        self.assertEqual(values(report)['c41type'],'รวม');self.assertEqual(values(report)['c41sub'],'MP3')
        self.assertNotIn('c4genre',values(report));self.assertIn('mixed_music_regions_or_styles',report['review_flags'])

    def test_mixed_or_unknown_audio_container_does_not_get_single_format(self):
        for paths in [('one.mp3','two.flac'),('one.mp3','two.m4a'),('one.mp3','two.iso'),('one.m4a',),
                      ('one.mp3','bonus.mp4'),('one.mp3','two.opus')]:
            with self.subTest(paths=paths):
                report=classify_job(music(),files=[{'path':p} for p in paths])
                self.assertNotIn('c41sub',values(report))

    def test_strict_spelling_match_is_not_substring_semantic_match(self):
        self.assertEqual(spelling_match('Full Alubm',['Full Album','Single'])['value'],'Full Album')
        self.assertIsNone(spelling_match('สากล',['ไทยสากล','เอเชีย']))
        self.assertIsNone(spelling_match('MP4',['MP3','WAV']))

    def test_unknown_form_options_never_get_emitted(self):
        row=music();row['form_schema']['fields']['c41type']['options']=['Single']
        self.assertNotIn('c41type',values(classify_job(row,records=[album()])))

    def test_conflicting_sources_are_not_silently_ranked_away(self):
        row=music('Ambra - Child Of The Universe - 2003 Full Album');row['metadata']['description']='compilation'
        report=classify_job(row)
        self.assertIn('c41type',report['conflict_fields']);self.assertNotIn('c41type',values(report))

    def test_leaked_label_goes_to_review_and_does_not_prepare_fields(self):
        row=adult('ABC-123 Uncensored Leaked');row['rule']['fields']={'c100type':'ภาพยนตร'}
        report=classify_job(row,files=[{'path':'sample.mp4'}])
        self.assertIn('origin_requires_review',report['review_flags']);self.assertEqual(report['suggestions'],[])

    def test_adult_catalog_needs_exact_single_code(self):
        record={'kind':'adult','provider':'authorized_catalog','url':'https://example.com/item/123',
                'release_code':'XYZ-123','media_type':'movie','production_country':'Japan'}
        row=adult();row['metadata']['category']='unknown'
        self.assertNotIn('c100cat',values(classify_job(row,records=[record])))
        record['release_code']='ABC-123'
        report=classify_job(row,records=[record])
        self.assertEqual(values(report)['c100cat'],'ญี่ปุ่น')
        self.assertNotIn('c100type2',values(report))

    def test_censorship_cannot_be_verified_by_filename_alone(self):
        row=adult('ABC-123 Uncensored')
        report=classify_job(row)
        self.assertIn('censorship_requires_edition_evidence',report['review_flags'])
        self.assertFalse(next(o for o in report['observations'] if o['kind']=='censorship_claim')['verified'])

    def test_censorship_disagreement_is_explicit(self):
        record={'kind':'adult','url':'https://example.com/catalog/123','release_code':'ABC-123','censorship':'censored'}
        report=classify_job(adult('ABC-123 Uncensored'),records=[record])
        self.assertIn('censorship_claim_conflict',report['review_flags'])
        self.assertFalse(next(o for o in report['observations'] if o['kind']=='censorship')['verified'])

    def test_suggestions_never_mutate_the_input_or_copy_credentials(self):
        row=music();row['password']='secret';row['metadata']['description']='token=hidden https://example.org/?passkey=hidden'
        before=copy.deepcopy(row)
        report=classify_job(row,records=[album()])
        self.assertEqual(row,before);self.assertNotIn('hidden',json.dumps(report));self.assertNotIn('secret',json.dumps(report))
        self.assertNotEqual(input_fingerprint(row),input_fingerprint({**row,'field_overrides':{'fields':{'c41sub':'MP3'}}}))

    def test_video_filename_parser_extracts_episode_properties(self):
        row=music('Example.Show.S01E02.1080p.WEB-DL.mkv');row['form_schema']={'fields':{}};row['metadata']['category']='series'
        identity=extract_identity(row)
        self.assertEqual(identity['type'],'episode');self.assertEqual(identity['episode'],2)
        self.assertEqual(identity['screen_size'],'1080p')


class ProviderTests(unittest.TestCase):
    def test_adult_queries_never_go_to_web_search_or_external_provider(self):
        with tempfile.TemporaryDirectory() as d,patch('metadata_providers.subprocess.run',side_effect=AssertionError('must not search')):
            r=lookup_metadata(extract_identity(adult()),musicbrainz=True,web=True,cache_dir=d)
        self.assertEqual(r['records'],[]);self.assertIn('external_lookup_unavailable_for_adult',r['diagnostics'])

    def test_timeout_returns_diagnostic_not_a_made_up_result(self):
        import subprocess
        with tempfile.TemporaryDirectory() as d,patch('metadata_providers.subprocess.run',side_effect=subprocess.TimeoutExpired('query',1)):
            r=lookup_metadata(extract_identity(music()),musicbrainz=True,cache_dir=d,timeout=1)
        self.assertEqual(r['records'],[]);self.assertIn('musicbrainz_unavailable',r['diagnostics'])

    def test_cache_prevents_repeated_network_queries(self):
        output={'records':[album()],'references':[],'status':'ok'}
        with tempfile.TemporaryDirectory() as d,patch('metadata_providers.subprocess.run',return_value=SimpleNamespace(stdout=json.dumps(output))) as request:
            a=lookup_metadata(extract_identity(music()),musicbrainz=True,cache_dir=d)
            b=lookup_metadata(extract_identity(music()),musicbrainz=True,cache_dir=d)
            request.assert_called_once()
            self.assertFalse(a['providers'][0]['cached']);self.assertTrue(b['providers'][0]['cached'])

    def test_web_search_results_are_references_not_category_evidence(self):
        output={'records':[],'references':[{'url':'https://example.com/music','title':'Full Album'}],'status':'references_only'}
        with tempfile.TemporaryDirectory() as d,patch('metadata_providers.subprocess.run',return_value=SimpleNamespace(stdout=json.dumps(output))):
            r=lookup_metadata(extract_identity(music()),web=True,cache_dir=d)
        result=classify_job(music(),references=r['references'])
        self.assertNotIn('c41type',values(result));self.assertEqual(len(result['references']),1)

    def test_invalid_or_secret_bearing_catalog_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'data.json'
            for value in ({'records':[{'kind':'adult','url':'https://example.com/?token=secret'}]},
                          {'records':[{'kind':'music','url':'https://example.com/','password':'secret'}]}):
                p.write_text(json.dumps(value))
                with self.assertRaises(ValueError):load_catalog_records(p)

    def test_only_public_safe_https_reference_urls(self):
        for url in ['http://example.com','https://user:pass@example.com','https://example.com/?passkey=secret',
                    'https://127.0.0.1/private','https://localhost/data','https://example.com/?X-Amz-Signature=secret']:
            self.assertIsNone(public_source(url))
        self.assertEqual(public_source('https://example.com/details?id=123'),'https://example.com/details?id=123')
