import copy,subprocess,tempfile,unittest
from pathlib import Path
from PIL import Image
from content_analysis.store import Store,now
from content_analysis.retrieve import asset,LinkedInImages
from content_analysis.verification import verify
from content_analysis.media import reference
from content_analysis import service

class MediaVerificationTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory();cls.root=Path(cls.tmp.name);cls.video=cls.root/'complete.mp4'
  subprocess.run([reference().ffmpeg_bin(),'-v','error','-y','-f','lavfi','-i','color=c=blue:s=96x64:r=10:d=2','-f','lavfi','-i','anullsrc=r=16000:cl=mono','-t','2','-c:v','libx264','-c:a','aac','-movflags','+faststart',str(cls.video)],check=True,capture_output=True)
 @classmethod
 def tearDownClass(cls):cls.tmp.cleanup()
 def record(self,path=None):
  stamp=now();source='https://x.com/i/status/123456'
  return {'canonical':source,'retrieval':{'run_id':'run-current','started_at':stamp,'expected_media_count':1,'expected_duration_seconds':2,'source_identity_verified':True},'assets':[asset(path or self.video,'media',{'source':source,'run_id':'run-current','retrieved_at':stamp,'fresh_retrieval':True,'media_index':0})]}
 def test_real_complete_video_and_audio_decode(self):
  result=verify(self.record());self.assertTrue(result['fresh_full_media_verified']);self.assertGreater(result['assets'][0]['size_bytes'],0)
 def test_single_shot_analysis_keeps_measurements_without_markdown_formatter(self):
  from content_analysis.media import analyze
  with tempfile.TemporaryDirectory() as tmp:
   result=analyze(self.video,Path(tmp),speech=False)
   self.assertEqual(len([e for e in result['evidence'] if e['kind']=='shot']),1)
   self.assertEqual(result['coverage']['signal_spans'],[[0,2.0]])
   self.assertTrue(Path(result['reports'][0]['path']).is_file())
 def test_standalone_reference_cli_formats_a_single_shot(self):
  import sys
  with tempfile.TemporaryDirectory() as tmp:
   p=subprocess.run([sys.executable,'-m','content_analysis.video_reference',str(self.video),'--whisper-model','none','--no-ocr','--out',tmp],capture_output=True,text=True)
   self.assertEqual(p.returncode,0,p.stderr)
   self.assertIn('Single shot',p.stdout)
   self.assertTrue((Path(tmp)/'report.json').exists())
 def test_decodable_shorter_wrong_asset_is_incomplete(self):
  r=self.record();r['retrieval']['expected_duration_seconds']=10
  self.assertFalse(verify(r)['source_completeness_verified'])
 def test_actual_truncated_file_does_not_pass(self):
  broken=self.root/'truncated.mp4';data=self.video.read_bytes();broken.write_bytes(data[:len(data)//2]);result=verify(self.record(broken))
  self.assertFalse(result['fresh_full_media_verified']);self.assertFalse(result['retained_bytes_valid'])
 def test_cached_media_wrong_source_and_stale_runs_do_not_pass(self):
  for change in [{'fresh_retrieval':False},{'source':'https://x.com/i/status/654321'},{'run_id':'old'},{'retrieved_at':'not-a-date'}]:
   with self.subTest(change=change):
    r=self.record();r['assets'][0]['provenance'].update(change);self.assertFalse(verify(r)['fresh_full_media_verified'])
  r=self.record();r['last_refresh']={'run_id':'new-failed','status':'blocked'};self.assertFalse(verify(r)['fresh_full_media_verified'])
 def test_missing_count_or_duration_leaves_completeness_unknown(self):
  for field in ['expected_media_count','expected_duration_seconds']:
   r=self.record();r['retrieval'].pop(field);self.assertFalse(verify(r)['source_completeness_verified'])
 def test_images_require_complete_count_and_source_order(self):
  r=self.record();r['assets']=[];r['retrieval'].update(expected_media_count=2,asset_order_established=True)
  for index,color in enumerate(['red','blue']):
   path=self.root/('image-'+str(index)+'.png');Image.new('RGB',(32,48),color).save(path)
   provenance=self.record()['assets'][0]['provenance'];provenance['media_index']=index;r['assets'].append(asset(path,'media',provenance))
  self.assertTrue(verify(r)['fresh_full_media_verified'])
  r['assets'].reverse();self.assertFalse(verify(r)['source_completeness_verified'])
  r['assets'].pop();self.assertFalse(verify(r)['asset_count_matches'])
 def test_changed_bytes_and_missing_files_are_rejected(self):
  r=self.record();r['assets'][0]['sha256']='wrong';self.assertFalse(verify(r)['retained_bytes_valid'])
  r=self.record();r['assets'][0]['path']=str(self.root/'missing.mp4');self.assertFalse(verify(r)['retained_bytes_valid'])
 def test_image_pipeline_reopens_queryable_evidence(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'asset.png';Image.new('RGB',(32,48),'white').save(path);s=Store(Path(tmp)/'data');r=service.ingest(s,'https://linkedin.com/feed/update/urn:li:activity:12345',local_media=str(path));r=service.process(s,r['id']);rid=r['id'];s.close();s=Store(Path(tmp)/'data')
   events=service.context(s,rid)['evidence'];self.assertEqual(events[0]['kind'],'image');self.assertEqual(events[0]['measurement'],{'width':32,'height':48});self.assertEqual(s.get(rid)['coverage']['visual'],[]);s.close()

class LinkedInSourceTests(unittest.TestCase):
 def test_attached_image_identity_and_order_excludes_avatars_and_other_posts(self):
  html='<article data-activity-urn="urn:li:activity:99"><ul data-test-id="feed-images-content"><img src="https://example.com/wrong.jpg"></ul></article><article data-activity-urn="urn:li:activity:12"><img src="https://example.com/avatar.jpg"><ul data-test-id="feed-images-content"><img data-delayed-url="https://example.com/one.jpg"><img src="https://example.com/two.jpg"></ul></article>'
  parser=LinkedInImages('12');parser.feed(html);self.assertTrue(parser.matched);self.assertEqual(parser.urls,['https://example.com/one.jpg','https://example.com/two.jpg']);self.assertFalse(parser.hidden)
 def test_hidden_carousel_assets_are_not_complete(self):
  parser=LinkedInImages('12');parser.feed('<article data-activity-urn="urn:li:activity:12"><ul data-test-id="feed-images-content"><img src="https://example.com/cover.jpg"><span>+3</span></ul></article>');self.assertTrue(parser.hidden)

if __name__=='__main__':unittest.main()
