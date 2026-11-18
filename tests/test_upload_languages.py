import copy
import json
import tempfile
import unittest
from pathlib import Path

from upload_languages import track_languages
from upload_classifier import classify_job
from category_review import correction_plan
from metadata_providers import load_catalog_records
from test_upload_classifier import music, values


def track(kind, language='English'):
    return kind + '\nID : 2\nFormat : UTF-8\nLanguage : ' + language + '\n'


def film(description):
    return {'metadata': {'category': 'ภาพยนตร์', 'title': 'Sample', 'description': description},
            'source_zone': 'normal', 'target_site': 'DEDBIT', 'rule': {'fields': {}},
            'form_schema': {'fields': {'c3dub': {'options': None}, 'c3sub': {'options': None}}}}


class TrackLanguageTests(unittest.TestCase):
    def test_audio_and_subtitles_are_separate_and_deduplicated(self):
        text = track('Audio #1') + track('Audio #2', 'Thai') + track('Text #1', 'Thai') + track('Text #2', 'Thai')
        self.assertEqual(track_languages(text), {'c3dub': 'อังกฤษ / ไทย', 'c3sub': 'ไทย'})

    def test_formatting_and_nonbreaking_spaces_are_supported(self):
        self.assertEqual(track_languages('[b]Audio[/b]\nID : 1\nFormat : AAC\nLanguage\u00a0 : Thai\n'), {'c3dub': 'ไทย'})

    def test_unknown_or_missing_track_language_blocks_only_its_own_field(self):
        for bad in (track('Audio #2', 'Undetermined'), 'Audio #2\nID : 3\nFormat : AAC\n'):
            with self.subTest(bad=bad):
                self.assertEqual(track_languages(track('Audio #1') + bad + track('Text', 'Thai')), {'c3sub': 'ไทย'})

    def test_no_language_guess_from_title_country_prose_or_audio_absence(self):
        for text in ('Thai English film with subtitles', 'Language : Thai', 'Audio\nLanguage : Thai', ''):
            self.assertEqual(track_languages(text), {})
        row=film(''); row['metadata']['title']='[ฝรั่ง] [พากย์ไทย บรรยายไทย]'
        self.assertEqual(values(classify_job(row)), {})

    def test_language_fields_can_be_repaired_but_other_text_cannot(self):
        row=film(track('Audio', 'Thai') + track('Text', 'English'))
        report=classify_job(row)
        changes, issues=correction_plan(row, report, True)
        self.assertEqual(changes, {'c3dub': 'ไทย', 'c3sub': 'อังกฤษ'}); self.assertEqual(issues, [])
        row['form_schema']['fields']['description']={'options': None}
        report['suggestions'].append({'field':'description','value':'invented','confidence':'high','reasons':['catalog'],'evidence':[]})
        self.assertNotIn('description', correction_plan(row, report, True)[0])

    def test_existing_manual_language_is_preserved_and_conflict_blocks_repair(self):
        row=film(track('Audio', 'Thai')); row['field_overrides']={'fields': {'c3dub':'อังกฤษ'}}
        changes,issues=correction_plan(row,classify_job(row),True)
        self.assertEqual(changes, {}); self.assertIn('existing_value_conflicts_with_evidence:c3dub',issues)


class ArtistEvidenceTests(unittest.TestCase):
    def record(self, **kw):
        return {'kind':'music','scope':'artist','artist':'Example Artist','music_region':'สากล',
                'provider':'verified artist profile','url':'https://example.com/artists/example',**kw}

    def test_discography_requires_manifest_support_for_high_confidence(self):
        row=music('Example Artist - Discography 2000-2020 FLAC')
        files=[{'path':'2000 - First/01.flac'},{'path':'2005 - Second/01.flac'}]
        report=classify_job(row,files=files)
        self.assertEqual(next(s for s in report['suggestions'] if s['field']=='c41type')['confidence'],'high')
        self.assertEqual(next(s for s in classify_job(row)['suggestions'] if s['field']=='c41type')['confidence'],'medium')

    def test_artist_evidence_fills_region_without_asserting_an_album_edition(self):
        row=music('Example Artist - Discography 2000-2020 FLAC')
        report=classify_job(row,records=[self.record()])
        self.assertEqual(values(report)['c4genre'],'สากล')
        self.assertNotIn('catalog_album',[r for s in report['suggestions'] for r in s['reasons']])
        wrong=classify_job(row,records=[self.record(artist='Example Artists')])
        self.assertNotIn('c4genre',values(wrong))

    def test_artist_disagreement_requires_review(self):
        row=music('Example Artist - Discography 2000-2020 FLAC')
        report=classify_job(row,records=[self.record(), self.record(music_region='เอเชีย')])
        self.assertIn('catalog_identity_ambiguous',report['review_flags'])
        self.assertNotIn('c4genre',values(report))

    def test_catalog_rejects_album_facts_on_artist_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'catalog.json'
            p.write_text(json.dumps({'records':[self.record()]}))
            self.assertEqual(load_catalog_records(p)[0]['scope'],'artist')
            for extra in ({'release_type':'Album'}, {'year':2000}, {'scope':'unknown'}, {'kind':'adult'}):
                p.write_text(json.dumps({'records':[self.record(**extra)]}))
                with self.subTest(extra=extra),self.assertRaises(ValueError):load_catalog_records(p)

    def test_image_country_uses_explicit_source_category_or_form_other_option(self):
        from test_upload_classifier import adult
        row=adult();row['form_schema']['fields']={'c184cat':{'options':['ไทย','ญี่ปุ่น','อื่นๆ']}}
        self.assertEqual(values(classify_job(row))['c184cat'],'ญี่ปุ่น')
        row['metadata']['category']='รูปภาพ'
        report=classify_job(row)
        self.assertEqual(values(report)['c184cat'],'อื่นๆ')
        self.assertEqual(next(s for s in report['suggestions'] if s['field']=='c184cat')['reasons'],
                         ['source_category_has_no_listed_country'])
        row['rule']['fields']['c184cat']='ญี่ปุ่น'
        self.assertNotIn('c184cat',values(classify_job(row)))
