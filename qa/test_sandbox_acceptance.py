"""No Docker is started: launcher and untrusted return channel are mocked."""
import unittest,tempfile,sys,json,base64,os
from pathlib import Path
from unittest.mock import patch
from _qa_paths import REPO, QA_OUTPUT, TOKENIZER
sys.path.insert(0,str(REPO))
from wgforge import sandbox as sb

def cfg():return {'enable_code':True,'image':'python@sha256:'+'a'*64,'timeout_seconds':2}
def wire(files=None,exit_code=0):return (0,json.dumps({'files':files or {},'log':'qa mock only','exit_code':exit_code}).encode(),b'')
class SandboxAcceptance(unittest.TestCase):
 def setUp(self):self.t=tempfile.TemporaryDirectory();self.root=Path(self.t.name);self.b=sb.Sandbox(self.root/'work',code_config=cfg());(self.b.root/'input'/'source.txt').write_text('PUBLIC_CANARY')
 def tearDown(self):self.t.cleanup()
 def run_fake(self,files=None,cleanup=0):
  calls=[]
  def fake(argv,data,timeout,cap,env):
   calls.append((argv,data,env))
   if argv[1:3]==['image','inspect']:return 0,('sha256:'+'a'*64).encode(),b''
   if argv[1:3]==['rm','-f']:return cleanup,b'',b''
   return wire(files)
  with patch.object(sb.shutil,'which',return_value='/usr/bin/docker'),patch.object(sb,'_run_bounded',side_effect=fake):r=sb.execute_python(self.b,'print("mock only")')
  return r,calls
 def test_docker_command_hardening(self):
  argv=sb.docker_command('/usr/bin/docker',self.b,cfg(),'qa-container')
  for pair in [('--network','none'),('--cap-drop','ALL'),('--security-opt','no-new-privileges:true'),('--user','65532:65532'),('--ipc','none')]:self.assertEqual(argv[argv.index(pair[0])+1],pair[1])
  for flag in ['--read-only','--pull=never','--pids-limit','--memory','--memory-swap','--cpus']:self.assertIn(flag,argv)
  mounts=[argv[i+1] for i,x in enumerate(argv) if x=='--mount'];self.assertEqual(len(mounts),1);self.assertTrue(mounts[0].endswith(',dst=/input,readonly'));self.assertNotIn('docker.sock',' '.join(argv))
 def test_docker_missing_has_no_host_fallback(self):
  with patch.object(sb.shutil,'which',return_value=None),patch.object(sb.subprocess,'Popen') as p,self.assertRaises(sb.RuntimeErrorBase):sb.execute_python(self.b,'raise Exception("must not run")')
  p.assert_not_called()
 def test_disabled_code_not_exposed(self):
  b=sb.Sandbox(self.root/'disabled')
  self.assertNotIn('execute_python',{x['function']['name'] for x in b.tools})
  with self.assertRaises(sb.RuntimeErrorBase):b.execute('execute_python',{'code':'print(1)'})
 def test_unpinned_and_resource_config_rejected(self):
  for update in [{'image':'python:latest'},{'memory_mb':5000},{'cpus':float('nan')},{'timeout_seconds':0}]:
   with self.subTest(update=update),self.assertRaises(sb.RuntimeErrorBase):sb.validate_code_config({**cfg(),**update})
 def test_private_paths_and_mutating_input_rejected(self):
  for name,args in [('read_file',{'path':'/etc/passwd'}),('read_file',{'path':'input/../../private/truth.json'}),('read_file',{'path':'private/rubric.json'}),('write_file',{'path':'input/source.txt','content':'tamper'})]:
   with self.subTest(name=name,args=args),self.assertRaises(sb.RuntimeErrorBase):self.b.execute(name,args)
 def test_symlink_and_hardlink_inputs_rejected(self):
  (self.b.root/'input'/'link').symlink_to(self.root/'secret')
  with self.assertRaises(sb.RuntimeErrorBase):self.b.execute('read_file',{'path':'input/link'})
  (self.b.root/'input'/'link').unlink();os.link(self.b.root/'input'/'source.txt',self.b.root/'input'/'hard')
  with self.assertRaises(sb.RuntimeErrorBase):self.b.execute('read_file',{'path':'input/hard'})
 def test_special_output_rejected(self):
  os.mkfifo(self.b.root/'output'/'pipe')
  with self.assertRaises(sb.RuntimeErrorBase):self.b.validate_outputs()
 def test_bounded_mock_container_import(self):
  r,calls=self.run_fake({'result.txt':base64.b64encode(b'generated data').decode()});self.assertEqual((self.b.root/'output'/'result.txt').read_text(),'generated data');self.assertEqual(r['network'],'none')
  self.assertTrue(any(c[0][1:3]==['rm','-f'] for c in calls));self.assertTrue(all('API_KEY' not in c[2] for c in calls))
 def test_escape_from_container_output_rejected_without_import(self):
  with self.assertRaises(sb.RuntimeErrorBase):self.run_fake({'safe.txt':base64.b64encode(b'safe').decode(),'../../escaped.txt':base64.b64encode(b'bad').decode()})
  self.assertFalse((self.b.root/'output'/'safe.txt').exists());self.assertFalse((self.root/'escaped.txt').exists())
 def test_cleanup_failure_is_not_success(self):
  with self.assertRaises(sb.RuntimeErrorBase):self.run_fake({},cleanup=1)
if __name__=='__main__':unittest.main(verbosity=2)
