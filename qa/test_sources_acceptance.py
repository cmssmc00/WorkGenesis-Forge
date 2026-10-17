"""Source retrieval boundary tests, using in-memory HTTPS transport mocks only."""
from pathlib import Path
from unittest.mock import patch
import tempfile,unittest,sys,json,hashlib,socket,os,shutil
from _qa_paths import REPO, QA_OUTPUT, TOKENIZER
sys.path.insert(0,str(REPO))
from wgforge import sources as s
class Response:
 def __init__(self,body=b'id,value\nA,12\n',status=200,headers=None):self.body=body;self.status=status;self.headers={'Content-Type':'text/csv','Content-Length':str(len(body)),**(headers or {})};self.closed=False
 def getheader(self,name,default=None):return self.headers.get(name,default)
 def read1(self,limit):out=self.body[:limit];self.body=self.body[limit:];return out
 def close(self):self.closed=True
class Connection:
 def __init__(self,response):self.response=response;self.sock=None;self.requests=[]
 def request(self,*args,**kw):self.requests.append((args,kw))
 def getresponse(self):return self.response
 def close(self):pass
class SourceAcceptance(unittest.TestCase):
 def setUp(self):self.t=tempfile.TemporaryDirectory();self.root=Path(self.t.name);self.cfg={'allowed_hosts':['example.org'],'max_bytes':1000,'timeout_seconds':3}
 def tearDown(self):self.t.cleanup()
 def fetch(self,response,config=None):
  with patch.object(s,'_public_addresses',return_value=['93.184.216.34']),patch.object(s,'_PinnedHTTPSConnection',return_value=Connection(response)):
   return s._fetch_https('https://example.org/data.csv',config or self.cfg)
 def test_https_allowlist_and_credentials(self):
  for url in ['http://example.org/x','https://evil.example.org/x','https://example.org.evil.net/x','https://user:pass@example.org/x','https://example.org:444/x','https://example.org/x#fragment','https://example.org/\nx','file:///etc/passwd','https://127.0.0.1/x']:
   with self.subTest(url=url),self.assertRaises(s.SourceError):s.validate_url(url,['example.org','127.0.0.1'],resolve=False)
 def test_private_dns_is_rejected(self):
  for addr in ['127.0.0.1','10.1.2.3','169.254.169.254','::1','fc00::1']:
   with self.subTest(addr=addr),patch.object(socket,'getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',(addr,443))]),self.assertRaises(s.SourceError):s.validate_url('https://example.org/x',['example.org'])
 def test_mixed_public_private_dns_rejected(self):
  ans=[(socket.AF_INET,socket.SOCK_STREAM,6,'',(a,443)) for a in ['93.184.216.34','127.0.0.1']]
  with patch.object(socket,'getaddrinfo',return_value=ans),self.assertRaises(s.SourceError):s.validate_url('https://example.org/x',['example.org'])
 def test_mocked_real_byte_fetch_and_metadata(self):
  data,meta=self.fetch(Response());self.assertEqual(data,b'id,value\nA,12\n');self.assertEqual(meta['retrieval_method'],'pinned_https')
 def test_wrong_type_html_empty_truncated_and_compressed_rejected(self):
  cases=[Response(body=b''),Response(body=b'<html>login</html>'),Response(headers={'Content-Type':'text/html'}),Response(headers={'Content-Length':'999'}),Response(headers={'Content-Encoding':'gzip'})]
  for r in cases:
   with self.subTest(headers=r.headers),self.assertRaises(s.SourceError):self.fetch(r)
 def test_oversize_rejected(self):
  with self.assertRaises(s.SourceError):self.fetch(Response(body=b'x'*1001))
 def test_redirect_revalidated(self):
  with self.assertRaises(s.SourceError):self.fetch(Response(status=302,headers={'Location':'https://127.0.0.1/x'}),{**self.cfg,'max_redirects':1,'allowed_hosts':['example.org','127.0.0.1']})
 def test_download_hashes_actual_bytes_and_ignores_record_path(self):
  payload=b'id,value\nA,12\n';meta={'url':'https://example.org/a.csv','final_url':'https://example.org/a.csv','mime_type':'text/csv','retrieval_method':'qa_mock'}
  with patch.object(s,'_fetch_https',return_value=(payload,meta)):
   rec=s.download_source({'id':'source-a','url':meta['url'],'path':'../../outside.csv','title':'QA fixture'},self.root,self.cfg)
  self.assertEqual(rec['sha256'],hashlib.sha256(payload).hexdigest());self.assertEqual((self.root/rec['path']).read_bytes(),payload);self.assertTrue(rec['path'].startswith('raw/'));self.assertEqual(rec['rights_status'],'pending')
  with self.assertRaises(s.SourceError):s.require_production_rights(rec)
  p=self.root/rec['path'];p.chmod(0o600);p.write_bytes(b'changed')
  with self.assertRaises(s.SourceError):s.verify_source_record(rec,self.root)
 def test_approved_status_alone_is_insufficient(self):
  rec={'rights_status':'approved','license':'Public','synthetic':False,'provenance_kind':'exact-public'}
  with self.assertRaises(s.SourceError):s.require_production_rights(rec)
 def test_direct_search_is_pending_and_paid_search_opt_in(self):
  rows=s.discover_sources('ignored',{**self.cfg,'direct_urls':['https://example.org/a.csv']});self.assertEqual(rows[0]['rights_status'],'pending')
  with patch.object(s,'_fetch_https') as req,self.assertRaises(s.SourceError):s.discover_sources('public report',{**self.cfg,'adapter':'serpapi'})
  req.assert_not_called()
class PackagedSourceAcceptance(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.root=Path(self.t.name)
  self.assets=self.root/'trusted-package-assets'
  shutil.copytree(REPO/'wgforge/data/anchors',self.assets)
 def tearDown(self):self.t.cleanup()
 def test_installer_hardlinks_copied_but_untrusted_import_still_rejected(self):
  from wgforge.bundled import _copy_trusted_package_assets
  rec=json.loads((self.assets/'usgs_manifest.json').read_text())['sources'][0]
  raw=self.assets/rec['path'];os.link(raw,self.root/'installer-cache-link')
  self.assertEqual(raw.stat().st_nlink,2)
  with self.assertRaises(s.SourceError):s.load_source_manifest(self.assets/'usgs_manifest.json')
  out=_copy_trusted_package_assets(self.assets,self.root/'private-copy')
  s.load_source_manifest(out/'usgs_manifest.json');s.load_source_manifest(out/'onet_manifest.json')
  for original in self.assets.rglob('*'):
   if original.is_file():
    copied=out/original.relative_to(self.assets)
    self.assertEqual(copied.stat().st_nlink,1)
    self.assertEqual(copied.read_bytes(),original.read_bytes())
  self.assertEqual(raw.stat().st_nlink,2)
 def test_trusted_package_copy_rejects_symlink_and_special_files(self):
  from wgforge.bundled import _copy_trusted_package_assets
  from wgforge.common import ForgeError
  bad=self.assets/'000_bad'
  bad.symlink_to(self.assets/'usgs_manifest.json')
  with self.assertRaises(ForgeError):_copy_trusted_package_assets(self.assets,self.root/'symlink-output')
  bad.unlink();os.mkfifo(bad)
  with self.assertRaises(ForgeError):_copy_trusted_package_assets(self.assets,self.root/'fifo-output')
 def test_trusted_package_copy_reverifies_source_hashes(self):
  from wgforge.bundled import _copy_trusted_package_assets
  rec=json.loads((self.assets/'usgs_manifest.json').read_text())['sources'][0]
  raw=self.assets/rec['path'];raw.chmod(0o600);raw.write_bytes(b'tampered installed resource')
  with self.assertRaises(s.SourceError):_copy_trusted_package_assets(self.assets,self.root/'tampered-output')
if __name__=='__main__':unittest.main(verbosity=2)
