import copy,json,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from content_analysis.store import Store,ROOT,now
from content_analysis import profiles,features,service,statool
from content_analysis.tools import invoke

class ProfileToolTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.s=Store(Path(self.tmp.name)/'data')
 def tearDown(self):self.s.close();self.tmp.cleanup()
 def inventory(self,n=12,complete=True,platform='instagram'):
  items=[]
  for i in range(n):
   url=f'https://instagram.com/p/test{i}' if platform=='instagram' else f'https://x.com/test/status/{10000+i}'
   r=service.ingest(self.s,url,text=('Learn concrete examples now.\n\nUseful detailed examples.' if i%2 else 'Look at this short example.'))
   items.append({'reference_id':r['id'],'published_at':'2026-11-01T00:00:00Z','media_type':'reel' if i%2 else 'carousel'})
  url='https://instagram.com/test' if platform=='instagram' else 'https://x.com/test'
  return profiles.inventory_import(self.s,url,items,'test fixture export',scope='owned_export',expected_total=n if complete else 100,exhausted=complete)
 def metrics(self,inv,unit='count',denominator=None,access='public',name='views',captured='2026-11-20T00:00:00Z',basis='lifetime'):
  return profiles.metrics_import(self.s,inv['id'],rows=[{'reference_id':m['reference_id'],'metric_name':name,'value':(i+1)*10,'metric_basis':basis,'metric_unit':unit,'denominator':denominator} for i,m in enumerate(inv['members'])],source='fixture native export',access=access,captured_at=captured)
 def run_analysis(self,inv,snapshots,**kw):return profiles.run_profile(self.s,inv['id'],'views',[s['id'] for s in snapshots],top_k=3,bootstrap_iterations=100,**kw)
 def test_shared_ids_rankings_and_exact_lexical_evidence(self):
  inv=self.inventory();metrics=self.metrics(inv);run=self.run_analysis(inv,[metrics]);ranked=run['analytics']['ranked'];self.assertEqual(len(ranked),12);self.assertEqual(ranked[0]['piece_id'],inv['members'][-1]['reference_id']);self.assertEqual(ranked[0]['value'],120);self.assertEqual(ranked[-1]['relative_percentile'],0)
  term=run['lexical']['ngrams']['1'][0];example=term['examples'][0];ref=self.s.get_revision(example['reference_id'],example['reference_revision']);text=Path(next(a['path'] for a in ref['assets'] if a['role']=='text')).read_text();self.assertEqual(text[example['start']:example['end']],example['text']);self.assertEqual(example['source_sha256'],next(a['sha256'] for a in ref['assets'] if a['role']=='text'))
 def test_denominators_and_units_never_mix(self):
  inv=self.inventory();a=self.metrics(inv,unit='percent',denominator='reach',name='engagement_rate');b=self.metrics(inv,unit='ratio',denominator='impressions',name='engagement_rate')
  run=profiles.run_profile(self.s,inv['id'],'engagement_rate',[a['id'],b['id']],bootstrap_iterations=100);self.assertEqual(len(run['analytics']['coverage']['available_metric_signatures']),2)
  self.assertEqual(a['metrics'][0]['metric_value'],.1);self.assertEqual(a['metrics'][0]['raw_unit'],'percent');self.assertIn('denominator=reach',a['metrics'][0]['metric_basis'])
 def test_missing_metrics_and_incomplete_inventory_cannot_be_confident(self):
  inv=self.inventory(12,False);m=self.metrics(inv);run=self.run_analysis(inv,[m]);self.assertFalse(run['analysis_limits']['profile_conclusions_supported']);self.assertTrue(all(f['status']=='insufficient_evidence' for f in run['analytics']['findings']))
  empty=self.run_analysis(inv,[]);self.assertEqual(empty['analytics']['coverage']['eligible_pieces'],0);self.assertEqual(empty['analytics']['findings'][0]['status'],'insufficient_evidence')
 def test_partial_metric_coverage_cannot_be_confident(self):
  inv=self.inventory();m=self.metrics(inv);m['metrics']=m['metrics'][:2];self.s.save(m);run=self.run_analysis(inv,[m]);self.assertFalse(run['analysis_limits']['profile_conclusions_supported']);self.assertEqual(run['analytics']['coverage']['eligible_pieces'],2)
 def test_private_metrics_require_explicit_provenance(self):
  inv=self.inventory(1)
  with self.assertRaisesRegex(ValueError,'Private metric'):self.metrics(inv,name='saves')
  m=self.metrics(inv,name='saves',access='owned_export');self.assertEqual(m['metrics'][0]['access'],'owned_export')
 def test_invalid_metrics_rejected_before_snapshot_created(self):
  inv=self.inventory(1);row={'reference_id':inv['members'][0]['reference_id'],'metric_name':'views','value':3,'metric_basis':'lifetime','metric_unit':'count'};before=len(self.s.list())
  for field,value in [('value',True),('value',float('nan')),('value',-1),('metric_basis',''),('metric_unit','ratio'),('reference_id','ca-0000000000000000')]:
   with self.subTest(field=field,value=value):
    bad={**row,field:value}
    with self.assertRaises(ValueError):profiles.metrics_import(self.s,inv['id'],[bad],source='fixture')
  self.assertEqual(len(self.s.list()),before)
 def test_capture_dates_and_time_units(self):
  inv=self.inventory(1);m=self.metrics(inv,unit='minutes',name='avg_watch_time',access='owned_export');self.assertEqual(m['metrics'][0]['metric_value'],600)
  with self.assertRaisesRegex(ValueError,'timezone'):self.metrics(inv,captured='2026-11-20T00:00:00')
 def test_as_of_uses_dated_observations(self):
  inv=self.inventory(3);old=self.metrics(inv,captured='2026-11-10T00:00:00Z');new=self.metrics(inv,captured='2026-11-20T00:00:00Z');run=self.run_analysis(inv,[old,new],as_of='2026-11-15T00:00:00Z');self.assertTrue(all(row['captured_at'].startswith('2026-11-10') for row in run['analytics']['ranked']))
 def test_repeat_runs_are_immutable_and_corrections_reopen(self):
  inv=self.inventory(3);metrics=self.metrics(inv);first=self.run_analysis(inv,[metrics]);second=self.run_analysis(inv,[metrics]);self.assertNotEqual(first['id'],second['id']);before=copy.deepcopy(first['analytics']);eid=first['evidence'][0]['id'];r=service.annotate(self.s,first['id'],{'observations':[{'id':'o-interpret','label':'inferred','text':'Possible pattern.','evidence_ids':[eid]}]},first['revision']);r=service.correct(self.s,r['id'],'o-interpret','A limited pattern.','Narrower coverage',r['revision']);rid=r['id'];self.s.close();self.s=Store(Path(self.tmp.name)/'data');reopened=self.s.get(rid);self.assertEqual(reopened['analytics'],before);self.assertEqual(len(reopened['corrections']),1)
 def test_evidence_resolves_historical_passage_after_source_changes(self):
  inv=self.inventory(1);m=self.metrics(inv);run=self.run_analysis(inv,[m]);event=next(e for e in run['evidence'] if e['kind']=='passage');rid=event['reference_id'];service.ingest(self.s,self.s.get(rid)['source'],text='Entirely changed source')
  resolved=invoke(self.s,'evidence.resolve',{'reference_id':run['id'],'evidence_id':event['id']})['data'];self.assertEqual(resolved['source']['evidence'][0]['text'],event['text']);self.assertNotEqual(resolved['source']['revision'],self.s.get(rid)['revision'])
 def test_metric_correction_preserves_prior_snapshot(self):
  inv=self.inventory(1);old=self.metrics(inv);new=profiles.metrics_import(self.s,inv['id'],[{**old['metrics'][0],'value':20,'metric_basis':'lifetime'}],source='corrected fixture',supersedes=old['id'],reason='Export correction');run=self.run_analysis(inv,[old,new]);self.assertEqual(run['analytics']['ranked'][0]['value'],20);self.assertEqual(self.s.get(old['id'])['metrics'][0]['metric_value'],10)
 def test_owned_csv_explicit_mapping_preserves_provenance(self):
  inv=self.inventory(1);p=Path(self.tmp.name)/'owned.csv';rid=inv['members'][0]['reference_id'];p.write_text('Post,Reach,Ignored\n'+rid+',12,private note\n')
  # Metric semantics must be supplied rather than guessed from "Reach".
  with self.assertRaisesRegex(ValueError,'name, basis and unit'):profiles.metrics_import(self.s,inv['id'],path=str(p),columns={'reference_id':'Post','value':'Reach'},source='owned analytics export',access='owned_export')
  p.write_text('Post,Value,Metric,Basis,Unit,Ignored\n'+rid+',12,reach,lifetime,count,private note\n');m=profiles.metrics_import(self.s,inv['id'],path=str(p),columns={'reference_id':'Post','value':'Value','metric_name':'Metric','metric_basis':'Basis','metric_unit':'Unit'},source='owned analytics export',access='owned_export');self.assertTrue(m['provenance']['import_file_sha256']);self.assertNotIn('private note',json.dumps(m))
 def test_wide_owned_export_normalizes_multiple_explicit_metrics(self):
  inv=self.inventory(2);p=Path(self.tmp.name)/'wide.csv';a,b=[m['reference_id'] for m in inv['members']]
  p.write_text('Post,Reach,Engagement rate,Ignored\n'+a+',50,20,private note\n'+b+',,10,private note\n')
  result=invoke(self.s,'metrics.import',{'inventory_id':inv['id'],'path':str(p),'columns':{'reference_id':'Post'},'metric_specs':[{'column':'Reach','metric_name':'reach','metric_unit':'count','metric_basis':'lifetime'},{'column':'Engagement rate','metric_name':'engagement_rate','metric_unit':'percent','metric_basis':'lifetime','denominator':'reach'}],'source':'owned fixture export','access':'owned_export'})['data']
  self.assertEqual(result['metric_total'],3);self.assertNotIn('private note',json.dumps(result));self.assertEqual(next(r['metric_value'] for r in result['metrics'] if r['reference_id']==a and r['metric_name']=='engagement_rate'),.2)
 def test_seeded_intervals_and_format_age_controls_are_reproducible(self):
  inv=self.inventory(40);metrics=self.metrics(inv)
  a=profiles.run_profile(self.s,inv['id'],'views',[metrics['id']],top_k=10,bootstrap_iterations=100,seed=7)
  b=profiles.run_profile(self.s,inv['id'],'views',[metrics['id']],top_k=10,bootstrap_iterations=100,seed=7)
  self.assertEqual(a['analytics'],b['analytics']);self.assertTrue(a['analysis_limits']['profile_conclusions_supported'])
  self.assertTrue(any(f.get('interval') for f in a['analytics']['findings']))
  self.assertTrue(all(f.get('caveat','').find('causal')>=0 for f in a['analytics']['findings']))
 def test_profile_results_are_paged_and_originals_not_overwritten(self):
  inv=self.inventory(25);metrics=self.metrics(inv);before={m['reference_id']:self.s.get(m['reference_id']) for m in inv['members']}
  result=invoke(self.s,'profile.analyze',{'inventory_id':inv['id'],'objective':'views','metric_snapshot_ids':[metrics['id']],'bootstrap_iterations':100})
  self.assertEqual(len(result['data']['analytics']['ranked']),20);rid=result['data']['id']
  page=invoke(self.s,'analysis.result',{'reference_id':rid,'section':'ranked','offset':20,'limit':10})['data'];self.assertEqual(len(page['items']),5)
  for key,old in before.items():self.assertEqual(self.s.get(key),old)
 def test_unknown_labels_are_not_counted_as_absent_evidence(self):
  inv=self.inventory(20);metrics=self.metrics(inv);rid=inv['members'][0]['reference_id'];r=self.s.get(rid)
  service.annotate(self.s,rid,{'observations':[{'label':'inferred','dimension':'topics','value':'example','text':'Example topic','evidence_ids':[r['evidence'][0]['id']]}],'semantic_status':'partial'},r['revision'])
  run=profiles.run_profile(self.s,inv['id'],'views',[metrics['id']],top_k=5,bootstrap_iterations=100)
  topic=[f for f in run['analytics']['findings'] if f['feature'].startswith('topics=')];self.assertTrue(topic);self.assertTrue(all(f['status']=='insufficient_evidence' for f in topic))
 def test_public_inventory_fallback_preserves_unknown_coverage(self):
  from content_analysis.retrieve import RetrievalError
  with patch('content_analysis.retrieve.meta_discovery',side_effect=RetrievalError('credential unavailable')),patch('content_analysis.profiles.public_profile_inventory',return_value=[{'source_url':'https://instagram.com/p/fallback','media_type':'video'}]):
   result=invoke(self.s,'inventory.collect',{'profile_url':'https://instagram.com/test'})
  self.assertEqual(result['status'],'partial');self.assertEqual(result['data']['inventory_coverage']['collected'],1);self.assertFalse(result['data']['inventory_coverage']['collection_complete'])
 def test_source_profile_scope_and_unknown_inputs(self):
  inv=self.inventory(1)
  with self.assertRaises(ValueError):profiles.inventory_import(self.s,'https://instagram.com/test',[{'source_url':'https://x.com/test/status/123'}],'fixture')
  with self.assertRaises(ValueError):invoke(self.s,'profile.analyze',{'inventory_id':inv['id'],'objective':'views','unrecognized':1})
 def test_collection_failure_is_structured_and_persisted(self):
  from content_analysis.retrieve import RetrievalError
  with patch('content_analysis.retrieve.meta_discovery',side_effect=RetrievalError('HTTP 403')), patch('content_analysis.profiles.public_profile_inventory',side_effect=RetrievalError('Public profile unavailable')):
   result=invoke(self.s,'inventory.collect',{'profile_url':'https://instagram.com/test'})
  self.assertEqual(result['status'],'blocked');self.assertFalse(result['data']['inventory_coverage']['collection_complete']);self.assertIn('HTTP 403',result['data']['inventory_coverage']['limitations'])
 def test_single_shot_cli_has_no_application_or_export_commands(self):
  p=subprocess.run([sys.executable,'-m','content_analysis','--help'],cwd=ROOT,capture_output=True,text=True,check=True)
  self.assertNotIn('serve',p.stdout);self.assertNotIn('review',p.stdout)
  result=invoke(self.s,'tools.list',{});self.assertFalse(result['data']['application_required']);self.assertFalse(result['data']['document_export_required'])
  check=subprocess.run([sys.executable,'-c',"import sys; from content_analysis.statool import load; load(); assert 'content_reference_toolkit.reporting' not in sys.modules"],cwd=ROOT,capture_output=True,text=True);self.assertEqual(check.returncode,0,check.stderr)

class SourceFeatureTests(unittest.TestCase):
 def test_missing_sources_stay_unavailable(self):
  with tempfile.TemporaryDirectory() as tmp:
   s=Store(tmp);r,_=s.create('https://instagram.com/p/missing');f=features.extract_features(s,r['id']);self.assertIsNone(f['flat']['caption_word_count']);self.assertIsNone(f['flat']['words_per_minute']);self.assertEqual(f['reel_windows'],[]);s.close()
 def test_sample_gaps_never_become_full_video_pauses_or_pace(self):
  from content_analysis.store import digest
  with tempfile.TemporaryDirectory() as tmp:
   s=Store(Path(tmp)/'data');p=Path(tmp)/'video.mp4';p.write_bytes(b'fixture source identity');r=service.ingest(s,'https://instagram.com/p/sampled',local_media=str(p));media=r['assets'][0];folder=s.folder(r['id'])/'analysis'/digest(media['sha256']+str(False)+str(True))[:12];folder.mkdir(parents=True);manifest=folder/'manifest.json'
   manifest.write_text(json.dumps({'transcripts':[{'complete':True,'segments':[{'start':0,'end':2,'text':'one two','words':[{'start':0,'end':.4,'word':'one'},{'start':1,'end':1.4,'word':'two'}]}]},{'complete':True,'segments':[{'start':8,'end':10,'text':'three four','words':[{'start':8,'end':8.4,'word':'three'},{'start':9,'end':9.4,'word':'four'}]}]}]}));r['analysis_manifest']=str(manifest);r['coverage'].update(signal_spans=[[0,2],[8,10]],sampled=True);s.save(r)
   f=features.extract_features(s,r['id']);self.assertEqual(len(f['reel_windows']),2);self.assertIsNone(f['flat']['words_per_minute']);self.assertIsNone(f['flat']['pause_count_1000ms'])
   self.assertTrue(all(e['duration_seconds']<2 for window in f['reel_windows'] for e in window['pauses']['gaps']));s.close()
 def test_ordered_carousel_ocr_has_source_binding(self):
  with tempfile.TemporaryDirectory() as tmp:
   s=Store(Path(tmp)/'data');r,_=s.create('https://instagram.com/p/slides');r['kind']='visual'
   from content_analysis.retrieve import asset
   for index in range(2):
    p=Path(tmp)/('slide'+str(index)+'.png');Image.new('RGB',(100,100),'white').save(p);r['assets'].append(asset(p,'media',{'method':'test fixture'}))
   s.save(r);r=service.process(s,r['id']);missing=features.extract_features(s,r['id']);self.assertIsNone(missing['carousel']['total_word_count']);self.assertIsNone(missing['flat']['text_density']);images=[e for e in r['evidence'] if e['kind']=='image'];r=features.import_ocr(s,r['id'],[{'evidence_id':images[1]['id'],'blocks':[{'text':'2. Close','bbox':[0,0,40,20]}]},{'evidence_id':images[0]['id'],'blocks':[{'text':'Why this works?','bbox':[0,0,70,20]}]}],method='manual_transcription',source='fixture inspection',expected_revision=r['revision']);f=features.extract_features(s,r['id']);self.assertEqual([x['slide_index'] for x in f['carousel']['slides']],[1,2]);self.assertEqual(f['coverage']['ocr_slides'],2)
   with self.assertRaises(ValueError):features.import_ocr(s,r['id'],[{'evidence_id':'wrong','blocks':[]}],method='ocr',source='fixture')
   with self.assertRaises(ValueError):features.import_ocr(s,r['id'],[{'evidence_id':images[0]['id'],'blocks':[{'text':'bad','bbox':[90,0,40,20]}]}],method='ocr',source='fixture')
   s.close()

if __name__=='__main__':unittest.main()
