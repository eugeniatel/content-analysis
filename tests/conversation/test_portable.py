import json, os, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from content_analysis.retrieve import meta_discovery, RetrievalError

class PortableTests(unittest.TestCase):
    def test_discovery_needs_no_store_or_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            env={**os.environ,'CONTENT_ANALYSIS_DATA':str(Path(tmp)/'unused')}
            p=subprocess.run([sys.executable,'-m','content_analysis','tool','tools.list'],env=env,capture_output=True,text=True,check=True)
            self.assertEqual(json.loads(p.stdout)['tool'],'tools.list')
            self.assertFalse((Path(tmp)/'unused').exists())
    def test_meta_configuration_is_required(self):
        with patch.dict(os.environ,{},clear=True),patch('content_analysis.retrieve.public_url'):
            with self.assertRaisesRegex(RetrievalError,'Authorized Meta access requires'):
                meta_discovery('example')
    def test_meta_identity_and_caller_configuration(self):
        config={'CONTENT_ANALYSIS_META_ACCESS_TOKEN':'test-only-placeholder',
                'CONTENT_ANALYSIS_META_ACCOUNT_ID':'123456',
                'CONTENT_ANALYSIS_META_API_VERSION':'v27.0'}
        body={'business_discovery':{'username':'example','media_count':1,'media':{'data':[{'id':'post1'}]}}}
        with patch.dict(os.environ,config),patch('content_analysis.retrieve.public_url'),patch('content_analysis.retrieve.fetch',return_value=(json.dumps(body).encode(),'application/json')) as fetch:
            posts,total,complete=meta_discovery('example')
            self.assertEqual(total,1);self.assertTrue(complete);self.assertEqual(len(posts),1)
            self.assertIn('/v27.0/123456?',fetch.call_args.args[0])
            self.assertNotIn('test-only-placeholder',fetch.call_args.args[0])
    def test_wrong_identity_is_rejected(self):
        config={'CONTENT_ANALYSIS_META_ACCESS_TOKEN':'test-only-placeholder',
                'CONTENT_ANALYSIS_META_ACCOUNT_ID':'123456',
                'CONTENT_ANALYSIS_META_API_VERSION':'v27.0'}
        body={'business_discovery':{'username':'wrong','media':{'data':[]}}}
        with patch.dict(os.environ,config),patch('content_analysis.retrieve.public_url'),patch('content_analysis.retrieve.fetch',return_value=(json.dumps(body).encode(),'application/json')):
            with self.assertRaisesRegex(RetrievalError,'identity'):
                meta_discovery('example')
