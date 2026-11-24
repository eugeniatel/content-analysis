import contextlib,io,json,sqlite3,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from content_analysis.store import Store,ROOT,DATA_ROOT,file_hash
from content_analysis import service
from content_analysis.tools import invoke

class ReadOnlyTests(unittest.TestCase):
 def test_lookup_creates_no_files_and_cannot_write(self):
  with tempfile.TemporaryDirectory() as tmp:
   s=Store(tmp);r=service.ingest(s,'https://example.com/readonly',text='Exact retained passage.');s.close()
   before={str(p.relative_to(tmp)):file_hash(p) for p in Path(tmp).rglob('*') if p.is_file()}
   s=Store(tmp,read_only=True);result=invoke(s,'reference.list',{'query':'Exact retained'});self.assertEqual(result['data'][0]['id'],r['id']);self.assertTrue(service.context(s,r['id'])['evidence'])
   with self.assertRaises(PermissionError):s.save(r)
   s.folder('ca-0000000000000000');self.assertFalse((Path(tmp)/'refs/ca-0000000000000000').exists());s.close()
   after={str(p.relative_to(tmp)):file_hash(p) for p in Path(tmp).rglob('*') if p.is_file()};self.assertEqual(before,after)
 def test_active_wal_is_read_without_immutable_stale_results(self):
  with tempfile.TemporaryDirectory() as tmp:
   writer=Store(tmp);r=service.ingest(writer,'https://example.com/latest',text='Committed in active WAL.')
   wal=Path(tmp)/'index.sqlite-wal';self.assertTrue(wal.stat().st_size>0)
   reader=Store(tmp,read_only=True);self.assertEqual(reader.get(r['id'])['evidence'][0]['text'],'Committed in active WAL.');reader.close();writer.close()
 def test_cli_lookup_uses_supported_readonly_path(self):
  with tempfile.TemporaryDirectory() as tmp:
   s=Store(tmp);r=service.ingest(s,'https://example.com/cli',text='Read-only CLI proof.');s.close()
   result=subprocess.run([sys.executable,'-m','content_analysis','--data',tmp,'tool','reference.list','--input','{"query":"CLI proof"}'],cwd=ROOT,capture_output=True,text=True,check=True)
   self.assertEqual(json.loads(result.stdout)['data'][0]['id'],r['id'])
 def test_write_failure_names_only_existing_required_data_directory(self):
  from content_analysis.__main__ import main
  error=io.StringIO()
  with patch.object(sys,'argv',['ca','tool','reference.add','--input','{"source":"https://example.com"}']),patch('content_analysis.__main__.Store',side_effect=sqlite3.OperationalError('unable to open database file')),contextlib.redirect_stderr(error):
   with self.assertRaises(SystemExit):main()
  result=json.loads(error.getvalue());self.assertEqual(result['status'],'error');self.assertEqual(result['required_permission']['file_system']['write'],[str(DATA_ROOT)])

if __name__=='__main__':unittest.main()
