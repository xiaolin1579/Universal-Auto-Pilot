import copy
import unittest
from unittest.mock import AsyncMock, Mock, patch

from auto_upload import UploadBlocked, UploadWorker
from category_review import review_categories, category_settings
from test_duplicate_recovery import RecoveryFixture


class CategoryReviewTests(RecoveryFixture, unittest.IsolatedAsyncioTestCase):
    async def review(self, settings=None):
        return await review_categories(self.ledger,self.key,self.schema,self.source,
            {'enable':True,'auto_fix':True,**(settings or {})},self.root/'cache')

    async def test_fills_high_confidence_missing_enums_and_records_audit(self):
        result=await self.review()
        self.assertEqual(result['field_overrides']['fields'],{'c41type':'รวม','c41sub':'MP3'})
        self.assertEqual(result['state'],'prepared')
        self.assertEqual(result['category_review']['applied_fields'],{'c41type':'รวม','c41sub':'MP3'})
        self.assertEqual(len(result['category_corrections']),2)
        self.assertFalse(self.ledger.review(self.key)['classification']['stale'])
        self.assertNotIn('confirmations',result['field_overrides'])
        self.assertEqual(result['target_category'],self.job['target_category'])
        self.assertEqual(result['rule'],self.job['rule'])

    async def test_second_review_does_not_duplicate_correction_history(self):
        await self.review();result=await self.review()
        self.assertEqual(len(result['category_corrections']),2)
        self.assertEqual(result['category_review']['applied_fields'],{})

    async def test_valid_explicit_value_is_preserved_and_conflict_holds_publication(self):
        self.ledger.configure_job(self.key,{'fields':{'c41sub':'Lossless'}})
        result=await self.review()
        self.assertEqual(result['field_overrides']['fields']['c41sub'],'Lossless')
        self.assertIn('existing_value_conflicts_with_evidence:c41sub',result['category_review']['issues'])
        self.assertEqual(result['state'],'draft')

    async def test_invalid_explicit_value_is_preserved_for_review(self):
        self.store.update(lambda d:d['upload_jobs'][self.key].update(field_overrides={'fields':{'c41sub':'MP4'}}))
        result=await self.review()
        self.assertEqual(result['field_overrides']['fields']['c41sub'],'MP4')
        self.assertIn('explicit_invalid_value:c41sub',result['category_review']['issues'])

    async def test_invalid_rule_default_can_be_repaired(self):
        self.store.update(lambda d:d['upload_jobs'][self.key]['rule'].update(fields={'c41sub':'MP4'}))
        result=await self.review()
        self.assertEqual(result['field_overrides']['fields']['c41sub'],'MP3')
        self.assertEqual(result['rule']['fields']['c41sub'],'MP4')

    async def test_review_only_mode_does_not_fill_fields(self):
        result=await self.review({'auto_fix':False})
        self.assertNotIn('field_overrides',result)
        self.assertEqual(result['state'],'draft')
        self.assertEqual(result['category_review']['applied_fields'],{})

    async def test_medium_spelling_only_is_never_auto_applied(self):
        self.store.update(lambda d:d['upload_jobs'][self.key]['metadata'].update(title='Ambra - Sample',description=''))
        self.store.update(lambda d:d['upload_jobs'][self.key]['rule'].update(fields={'c41type':'Full Alubm'}))
        result=await self.review()
        self.assertNotIn('c41type',result.get('field_overrides',{}).get('fields',{}))
        self.assertEqual(result['state'],'draft')

    async def test_verified_live_form_without_subfields_is_accepted(self):
        self.schema['fields']={}
        self.store.update(lambda d:d['upload_jobs'][self.key]['rule'].update(fields={}))
        result=await self.review()
        self.assertEqual(result['state'],'prepared')
        self.assertEqual(result['category_review']['issues'],[])
        self.assertNotIn('destination_schema_unavailable',result['classification']['review_flags'])

    async def test_unknown_schema_options_are_not_invented(self):
        self.schema['fields']['c41sub']['options']=['AAC']
        result=await self.review()
        self.assertNotIn('c41sub',result.get('field_overrides',{}).get('fields',{}))
        self.assertIn('c41sub',result['readiness']['missing_fields'])

    async def test_source_or_state_change_is_rejected_before_mutation(self):
        before=self.store.path.read_bytes()
        with self.assertRaises(UploadBlocked):
            await review_categories(self.ledger,self.key,self.schema,self.source.replace(b'a'*20,b'b'*20),
                                    {'auto_fix':True},self.root/'cache')
        self.assertEqual(self.store.path.read_bytes(),before)
        self.ledger.transition(self.key,{'draft'},'publication_uncertain')
        with self.assertRaises(UploadBlocked):await self.review()

    async def test_metadata_change_during_provider_lookup_is_rejected(self):
        self.store.update(lambda d:d['upload_jobs'][self.key]['metadata'].update(title='Ambra - Sample - 2003'))
        def lookup(*args,**kwargs):
            self.store.update(lambda d:d['upload_jobs'][self.key]['metadata'].update(title='Changed'))
            return {'records':[],'diagnostics':[],'providers':[]}
        with patch('metadata_providers.lookup_metadata',side_effect=lookup):
            with self.assertRaisesRegex(UploadBlocked,'changed'):await self.review({'musicbrainz':True})
        self.assertNotIn('category_review',self.ledger.get(self.key))

    async def test_publish_cannot_bypass_category_conflict_or_submit(self):
        self.ledger.configure_job(self.key,{'fields':{'c41sub':'Lossless'}})
        adapter=Mock();adapter.load_form=AsyncMock(return_value=self.schema);adapter.submit=AsyncMock()
        worker=UploadWorker(self.ledger,enable_publish=True,category_review={'enable':True,'auto_fix':True})
        with self.assertRaisesRegex(UploadBlocked,'Category review'):
            await worker.publish(self.key,adapter,self.client)
        adapter.submit.assert_not_awaited()
        self.assertEqual(self.ledger.get(self.key)['state'],'draft')

    async def test_user_configure_takes_ownership_of_previously_automatic_field(self):
        await self.review()
        self.ledger.configure_job(self.key,{'fields':{'c41sub':'MP3'}})
        self.assertNotIn('c41sub',self.ledger.get(self.key)['category_auto_fields'])

    async def test_origin_review_blocks_before_reading_client_or_form(self):
        self.store.update(lambda d:d['upload_jobs'][self.key].update(source_zone='special',target_zone='special',
            target_site='BITSUSE',target_category='180'))
        self.store.update(lambda d:d['upload_jobs'][self.key]['metadata'].update(description='Uncensored Leaked'))
        adapter=Mock();adapter.load_form=AsyncMock();adapter.submit=AsyncMock()
        worker=UploadWorker(self.ledger,enable_publish=True,category_review={'enable':True,'auto_fix':True})
        with self.assertRaisesRegex(UploadBlocked,'origin_requires_review'):
            await worker.publish(self.key,adapter,self.client)
        self.client.inventory.assert_not_called();adapter.load_form.assert_not_awaited()
        adapter.submit.assert_not_awaited()

    async def test_check_job_does_not_clear_a_category_conflict(self):
        self.ledger.configure_job(self.key,{'fields':{'c41sub':'Lossless'}})
        adapter=Mock();adapter.load_form=AsyncMock(return_value=self.schema)
        adapter.check_duplicates=AsyncMock()
        worker=UploadWorker(self.ledger,category_review={'enable':True,'auto_fix':True})
        result=await worker.check_job(self.key,adapter,self.client)
        self.assertEqual(result['state'],'draft')
        self.assertFalse(result['readiness']['ready'])
        adapter.check_duplicates.assert_not_awaited()

    async def test_publication_uses_repaired_fields_after_live_form_checks(self):
        from auto_upload import effective_rule
        from upload_sites import form_readiness
        async def form(job,**kwargs):
            spec=copy.deepcopy(self.schema)
            spec.update(announce='https://tracker.cab/announce.php')
            spec['readiness']=form_readiness(spec,effective_rule(job))
            return spec
        adapter=Mock();adapter.load_form=AsyncMock(side_effect=form)
        adapter.check_duplicates=AsyncMock(return_value={'status':'clear','complete':True})
        adapter.fill=AsyncMock();adapter.submit=AsyncMock(return_value=self.listing)
        adapter.download_listing=AsyncMock(return_value=self.canonical)
        worker=UploadWorker(self.ledger,enable_publish=True,category_review={'enable':True,'auto_fix':True})
        result=await worker.publish(self.key,adapter,self.client)
        self.assertEqual(result['state'],'verified')
        filled=adapter.fill.call_args.args[0]
        self.assertEqual(effective_rule(filled)['fields'],{'c41type':'รวม','c41sub':'MP3'})
        adapter.submit.assert_awaited_once()

    async def test_live_review_repairs_language_controls_and_saves_evidence(self):
        self.schema['fields']={'c3dub':{'options':None},'c3sub':{'options':None}}
        self.store.update(lambda d:d['upload_jobs'][self.key]['metadata'].update(
            category='ภาพยนตร์',title='Example movie',
            description='Audio #1\nID : 1\nFormat : AAC\nLanguage : English\nAudio #2\nID : 2\nFormat : AAC\nLanguage : Thai\nText\nID : 3\nFormat : UTF-8\nLanguage : Thai\n'))
        before=self.ledger.get(self.key)
        result=await self.review()
        self.assertEqual(result['state'],'prepared')
        self.assertEqual(result['category_review']['applied_fields'],{'c3dub':'อังกฤษ / ไทย','c3sub':'ไทย'})
        self.assertEqual(result['rule'],before['rule'])
        self.assertEqual(result['target_category'],before['target_category'])
        self.assertEqual(len(result['category_corrections']),2)
        self.assertEqual(result['category_corrections'][0]['reasons'],['mediainfo_track_languages'])

    async def test_invalid_config_flags_fail_explicitly(self):
        for value in [True, {'enable':'true'}, {'auto_fix':1}, {'catalog_file':False}]:
            with self.subTest(value=value), self.assertRaises(UploadBlocked):
                category_settings({'category_review':value},self.root)


if __name__=='__main__':unittest.main()
