"""Validate trace admission using preserved mock-protocol messages, no export signed."""
import sys,json,copy,unittest
from pathlib import Path
from _qa_paths import REPO, QA_OUTPUT, TOKENIZER, protocol_path
sys.path.insert(0,str(REPO))
from wgforge.dataset import validate_trajectory
from wgforge.common import ForgeError
class ExportTraceAcceptance(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  root=protocol_path();cls.trace=json.loads((root/'workflow/round_001/executor/trajectory.json').read_text());cls.receipt=json.loads((root/'workflow/round_001/executor/run.json').read_text());cls.task=json.loads((root/'workflow/revisions/001/public/task.json').read_text())
 def mutate(self):return copy.deepcopy(self.trace),copy.deepcopy(self.receipt),copy.deepcopy(self.task)
 def test_well_formed_transport_trace_passes_schema_only(self):self.assertTrue(validate_trajectory(self.trace,self.receipt,self.task))
 def test_mock_mode_rejected(self):
  t,r,p=self.mutate();t['mode']='mock'
  with self.assertRaises(ForgeError):validate_trajectory(t,r,p)
 def test_compacted_trace_is_not_flat_exported(self):
  t,r,p=self.mutate();t['context_events']=[{'strategy':'deterministic_tool_results/v1','id':'qa-event'}]
  with self.assertRaises(ForgeError):validate_trajectory(t,r,p)
 def test_wrong_public_prompt_rejected(self):
  t,r,p=self.mutate();t['messages'][1]['content']='{"rubric":"leaked"}'
  with self.assertRaises(ForgeError):validate_trajectory(t,r,p)
 def test_unpaired_tool_call_rejected(self):
  t,r,p=self.mutate();del t['messages'][3]
  with self.assertRaises(ForgeError):validate_trajectory(t,r,p)
 def test_orphan_tool_result_rejected(self):
  t,r,p=self.mutate();t['messages'].insert(2,{'role':'tool','tool_call_id':'orphan','content':'{}'})
  with self.assertRaises(ForgeError):validate_trajectory(t,r,p)
 def test_duplicate_call_rejected(self):
  t,r,p=self.mutate();t['messages'][4]['tool_calls'][0]['id']=t['messages'][2]['tool_calls'][0]['id']
  with self.assertRaises(ForgeError):validate_trajectory(t,r,p)
 def test_missing_finish_rejected(self):
  t,r,p=self.mutate();t['messages']=t['messages'][:-2]
  with self.assertRaises(ForgeError):validate_trajectory(t,r,p)
 def test_postfinish_message_rejected(self):
  t,r,p=self.mutate();t['messages'].append({'role':'assistant','content':'Another message'})
  with self.assertRaises(ForgeError):validate_trajectory(t,r,p)
 def test_finish_artifact_binding_rejected(self):
  t,r,p=self.mutate();last=json.loads(t['messages'][-1]['content']);last['artifact_hashes']={};t['messages'][-1]['content']=json.dumps(last)
  with self.assertRaises(ForgeError):validate_trajectory(t,r,p)
 def test_argument_nonobject_rejected(self):
  t,r,p=self.mutate();t['messages'][2]['tool_calls'][0]['function']['arguments']='[]'
  with self.assertRaises(ForgeError):validate_trajectory(t,r,p)
 def test_private_role_injection_rejected(self):
  t,r,p=self.mutate();t['messages'].insert(2,{'role':'user','content':'Hidden repair instructions'})
  with self.assertRaises(ForgeError):validate_trajectory(t,r,p)
if __name__=='__main__':unittest.main(verbosity=2)
