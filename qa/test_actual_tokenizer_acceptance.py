"""Uses only existing local Qwen tokenizer bytes. No model weights or network."""
import os
os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
import sys,json,copy,unittest,hashlib
from pathlib import Path
from _qa_paths import REPO, QA_OUTPUT, TOKENIZER, protocol_path
sys.path.insert(0,str(REPO))
try:
 from transformers import AutoTokenizer
except ImportError:
 AutoTokenizer=None
from wgforge.training import assistant_spans,encode_example
from wgforge.common import ForgeError
ASSETS=TOKENIZER
TOOLS=[{'type':'function','function':{'name':'read_file','description':'Read a file','parameters':{'type':'object','properties':{'path':{'type':'string'}},'required':['path']}}}]
def messages():return [{'role':'system','content':'SYSTEM_MASK_CANARY_9182'},{'role':'user','content':'USER_MASK_CANARY_3645'},{'role':'assistant','content':None,'tool_calls':[{'id':'c1','type':'function','function':{'name':'read_file','arguments':'{"path":"input/data.csv"}'}}]},{'role':'tool','tool_call_id':'c1','content':'TOOL_MASK_CANARY_7721'},{'role':'assistant','content':'ASSISTANT_LABEL_CANARY_6441'}]
@unittest.skipUnless(AutoTokenizer is not None and ASSETS.is_dir(), 'Optional local tokenizer assets and transformers/tokenizers/Jinja2 are required; no model download is attempted')
class ActualTokenizerAcceptance(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.t=AutoTokenizer.from_pretrained(ASSETS,local_files_only=True,use_fast=True,trust_remote_code=False)
 def test_local_asset_hashes_match_original_pin(self):
  pin=json.loads((ASSETS/'PIN.json').read_text())
  for name,expected in pin['files'].items():
   with self.subTest(name=name):self.assertEqual(hashlib.sha256((ASSETS/name).read_bytes()).hexdigest(),expected)
 def test_simple_assistant_mask(self):
  rows=[{'role':'system','content':'SYSTEM_MASK_CANARY_9182'},{'role':'user','content':'USER_MASK_CANARY_3645'},{'role':'assistant','content':'ASSISTANT_LABEL_CANARY_6441'}]
  result=encode_example(self.t,{'messages':rows,'metadata':{'mode':'live_qualified'}},8192);text=self.t.decode([i for i in result['labels'] if i!=-100]);self.assertIn('ASSISTANT_LABEL_CANARY_6441',text);self.assertNotIn('SYSTEM_MASK_CANARY',text);self.assertNotIn('USER_MASK_CANARY',text)
 def test_actual_tool_template_normalizes_args_without_mutating_trace(self):
  rows=messages();saved=copy.deepcopy(rows);result=encode_example(self.t,{'messages':rows,'tools':TOOLS,'metadata':{'mode':'live_qualified'}},8192);text=self.t.decode([i for i in result['labels'] if i!=-100]);self.assertIn('ASSISTANT_LABEL_CANARY_6441',text);self.assertIn('read_file',text);self.assertNotIn('SYSTEM_MASK_CANARY',text);self.assertNotIn('USER_MASK_CANARY',text);self.assertNotIn('TOOL_MASK_CANARY',text);self.assertEqual(rows,saved)
 def test_full_actual_orchestrator_trace_is_encodable(self):
  root=protocol_path();trace=json.loads((root/'workflow/round_001/executor/trajectory.json').read_text());result=encode_example(self.t,{**trace,'metadata':{'mode':'live_qualified'}},32768);self.assertGreater(len(result['input_ids']),1000);self.assertGreater(sum(v!=-100 for v in result['labels']),100)
 def test_reasoning_field_stays_in_assistant_mask_only(self):
  rows=messages();rows[2]['reasoning_content']='SYNTHETIC_REASONING_MASK_CANARY_9401'
  result=encode_example(self.t,{'messages':rows,'tools':TOOLS,'metadata':{'mode':'live_qualified'}},8192);text=self.t.decode([i for i in result['labels'] if i!=-100]);self.assertIn('SYNTHETIC_REASONING_MASK_CANARY_9401',text);self.assertNotIn('TOOL_MASK_CANARY',text)
 def test_fixture_record_rejected(self):
  with self.assertRaises(ForgeError):encode_example(self.t,{'messages':messages(),'tools':TOOLS,'metadata':{'mode':'mock'}},8192)
if __name__=='__main__':unittest.main(verbosity=2)
