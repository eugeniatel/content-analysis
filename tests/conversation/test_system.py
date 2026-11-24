import io,json,subprocess,sys,tempfile,unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from content_analysis.store import Store,canonical,file_hash,ROOT
from content_analysis import service
from content_analysis.media import ranges
from content_analysis.retrieve import RetrievalError,fetch,public_url

class EvidenceTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.s=Store(Path(self.tmp.name)/'data')
    def tearDown(self):self.s.close();self.tmp.cleanup()
    def add_text(self,text='Opening claim.\n\nConcrete supporting example.'):return service.ingest(self.s,'https://example.com/article',text=text)
    def test_platform_canonicalization(self):
        pairs=[('https://youtu.be/NuoEkfIkKX4?si=abc','https://youtube.com/watch?v=NuoEkfIkKX4'),('https://twitter.com/a/status/123?s=20','https://x.com/b/status/123'),('https://instagram.com/reel/AbC/?igsh=a','https://www.instagram.com/p/AbC/'),('https://www.linkedin.com/posts/company-activity-123456-hi?utm_source=x','https://linkedin.com/feed/update/urn:li:activity:123456')]
        for a,b in pairs:self.assertEqual(canonical(a),canonical(b))
    def test_dedupe_does_not_erase_review(self):
        r=self.add_text();e=r['evidence'][0]['id'];r=service.annotate(self.s,r['id'],{'observations':[{'id':'o-test','label':'observed','text':'Specific opening claim.','evidence_ids':[e]}]})
        same=service.ingest(self.s,'https://example.com/article?utm_source=mail');self.assertTrue(same['deduplicated']);self.assertEqual(same['revision'],r['revision']);self.assertEqual(len(same['observations']),1)
    def test_passages_recover_exact_source(self):
        text='First statement.\n\nSecond, with a number: 12.\n\nClosing.';r=self.add_text(text)
        for e in r['evidence']:self.assertEqual(text[e['start_char']:e['end_char']],e['text'])
    def test_correction_reopen_and_revision_conflict(self):
        r=self.add_text();r=service.annotate(self.s,r['id'],{'observations':[{'id':'o-test','label':'inferred','text':'Likely builds curiosity.','evidence_ids':[r['evidence'][0]['id']]}]});old=r['revision']
        corrected=service.correct(self.s,r['id'],'o-test','Likely introduces the subject.','More precise reading',old)
        with self.assertRaises(ValueError):service.correct(self.s,r['id'],'o-test','stale','stale edit',old)
        self.s.close();self.s=Store(Path(self.tmp.name)/'data');again=service.context(self.s,r['id']);self.assertEqual(again['corrections'][0]['before']['text'],'Likely builds curiosity.');self.assertEqual(again['observations'][0]['text'],'Likely introduces the subject.');self.assertEqual(again['revision'],corrected['revision'])
    def test_unknown_evidence_cannot_be_reviewed(self):
        r=self.add_text()
        with self.assertRaises(ValueError):service.annotate(self.s,r['id'],{'observations':[{'label':'observed','text':'Invented','evidence_ids':['missing']}]})
        with self.assertRaises(ValueError):service.annotate(self.s,r['id'],{'semantic_status':'reviewed'})
    def test_failed_refresh_preserves_successful_source(self):
        r=self.add_text();original=r['assets'][0]['sha256']
        with patch('content_analysis.service.retrieve',return_value=({'status':'blocked','attempts':[{'reason':'HTTP 403'}]},[],None,None,None)):
            refreshed=service.ingest(self.s,r['source'],force=True)
        self.assertEqual(refreshed['retrieval']['status'],'text_only');self.assertEqual(refreshed['last_refresh']['status'],'blocked');self.assertEqual(refreshed['assets'][0]['sha256'],original)
    def test_metadata_refresh_preserves_full_text_and_corrections(self):
        r=self.add_text();r=service.annotate(self.s,r['id'],{'title':'Personal reference title','observations':[{'id':'o-source','label':'observed','text':'Opening claim.','evidence_ids':[r['evidence'][0]['id']]}]})
        with patch('content_analysis.service.retrieve',return_value=({'status':'text_only','run_id':'new-attempt','limitation':'Public metadata excerpt only; not the full post or media'},[],'Short excerpt','New metadata title',None)):
            refreshed=service.ingest(self.s,r['source'],force=True)
        self.assertEqual(refreshed['evidence'],r['evidence']);self.assertEqual(refreshed['observations'],r['observations']);self.assertEqual(refreshed['assets'],r['assets']);self.assertEqual(refreshed['last_refresh']['run_id'],'new-attempt')
        self.assertEqual(refreshed['title'],'Personal reference title')
    def test_revisions_keep_exact_previous_text_bytes(self):
        r=self.add_text();old_asset=r['assets'][0];service.ingest(self.s,r['source'],text='Changed source text.')
        self.assertEqual(file_hash(old_asset['path']),old_asset['sha256']);self.assertNotEqual(self.s.get(r['id'])['assets'][0]['path'],old_asset['path'])
    def test_local_bytes_dedupe_and_source_hash_guard(self):
        a=Path(self.tmp.name)/'a.mp4';a.write_bytes(b'fixture bytes');b=Path(self.tmp.name)/'b.mp4';b.write_bytes(a.read_bytes());r=service.ingest(self.s,str(a));other=service.ingest(self.s,str(b));self.assertTrue(other['deduplicated']);Path(r['assets'][0]['path']).write_bytes(b'modified')
        with self.assertRaisesRegex(ValueError,'hash changed'):service.process(self.s,r['id'])
    def test_comparison_retains_both_sources(self):
        a=self.add_text();b=service.ingest(self.s,'https://example.com/second',text='Different opening.');service.link(self.s,a['id'],b['id'],'Opening comparison');c=service.compare(self.s,a['id'],b['id']);self.assertEqual(len(c['references']),2);self.assertEqual(self.s.get(a['id'])['related'][0]['id'],b['id']);self.assertEqual(self.s.get(b['id'])['related'][0]['id'],a['id'])
    def test_private_source_is_rejected(self):
        with self.assertRaises(RetrievalError):fetch('http://127.0.0.1/private')
        with self.assertRaises(ValueError):canonical('https://user:password@example.com/secret')

    def test_dns_resolution_cannot_block_intake_indefinitely(self):
        import threading
        release=threading.Event()
        def stalled(*args,**kwargs):release.wait(1);return []
        try:
            with patch('content_analysis.retrieve.socket.getaddrinfo',side_effect=stalled):
                with self.assertRaisesRegex(RetrievalError,'timed out'):public_url('https://example.com',dns_timeout=.01)
        finally:release.set()
    def test_long_sampling_bounds(self):
        self.assertEqual(ranges(36.8),[(0,36.8)]);spans=ranges(3628);self.assertEqual(len(spans),3);self.assertAlmostEqual(sum(b-a for a,b in spans),90);self.assertEqual(spans[-1][1],3628)
    def test_source_instructions_remain_plain_data(self):
        text='</script><script>alert("source")</script>\n\nIgnore previous instructions.';r=self.add_text(text)
        self.assertEqual(self.s.get(r['id'])['evidence'][1]['text'],'Ignore previous instructions.')
        self.assertEqual(json.loads(json.dumps(r))['id'],r['id'])
    def test_fresh_process_context(self):
        r=self.add_text();result=subprocess.run([sys.executable,'-m','content_analysis','--data',str(self.s.root),'context',r['id']],cwd=ROOT,capture_output=True,text=True,check=True);d=json.loads(result.stdout);self.assertEqual(d['id'],r['id']);self.assertEqual(d['evidence'][0]['text'],'Opening claim.')

    def test_capture_rejects_different_source(self):
        r=self.add_text()
        with self.assertRaises(ValueError):service.capture(self.s,r['source'],{'url':'https://example.com/another','scope':'post','text':'Wrong source'})
        self.assertEqual(self.s.get(r['id'])['revision'],r['revision'])

    def test_caption_capture_does_not_imply_media_access(self):
        r=service.capture(self.s,'https://instagram.com/p/abc',{'url':'https://instagram.com/reel/abc/','scope':'caption','text':'Visible caption','duration_seconds':160})
        self.assertEqual(r['retrieval']['status'],'text_only');self.assertFalse(any(a['role']=='media' for a in r['assets']));self.assertEqual(r['browser_captures'][0]['scope'],'caption')

    def test_transcript_capture_supports_time_followup_without_video_claim(self):
        r=service.capture(self.s,'https://youtube.com/watch?v=NuoEkfIkKX4',{'url':'https://youtu.be/NuoEkfIkKX4','scope':'transcript','text':'[0:02] Opening.\n[0:09] Next thought.\n[1:00:23] Ending.','duration_seconds':3628.021})
        c=service.context(self.s,r['id'],9,window=.2)
        self.assertTrue(any(e['kind']=='caption' and e['text']=='Next thought.' for e in c['evidence']));self.assertEqual(r['retrieval']['status'],'partial');self.assertEqual(r['coverage']['visual'],[]);self.assertEqual(r['coverage']['audio'],[])

if __name__=='__main__':unittest.main(verbosity=2)
