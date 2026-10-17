"""Independent repair state-machine tests. All role results are explicit mocks."""
from pathlib import Path
import sys,unittest,tempfile,copy,hashlib
from unittest.mock import patch
from _qa_paths import REPO, QA_OUTPUT, TOKENIZER
sys.path.insert(0,str(REPO))
from wgforge import workflow as w
from wgforge.materialize import render_scenario,verify_bundle
from wgforge.common import ForgeError,read_json
from test_schema_acceptance import scenario
class WorkflowAcceptance(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.root=Path(self.t.name);self.base=self.root/'anchors';self.base.mkdir();p=self.base/'values.csv';p.write_text('id,value\nA,12\nB,8\n');self.s=scenario();self.s['anchor_sources'][0]['sha256']=hashlib.sha256(p.read_bytes()).hexdigest();self.initial=self.root/'initial';render_scenario(self.s,self.base,self.initial);self.out=self.root/'evolve';self.cfg={'repair':{'max_rounds':6,'repeated_defect_limit':3}}
 def tearDown(self):self.t.cleanup()
 def audit(self,labels=None,criterion='calc'):
  rows=[] if labels is None else [{'criterion_id':criterion,'labels':labels,'explanation':'QA mock attribution','evidence':['request']}]
  return {'defects':[r for r in rows if set(r['labels']) & {'Task','Rubric'}],'attribution':rows}
 def callbacks(self,audits,review=True,mutate=None):
  seen={'solver':[],'audits':[],'roles':[]};seq=list(audits)
  def solver(config,role,current,stage,campaign):seen['solver'].append((str(current),verify_bundle(current)['revision']))
  def audit(current,run,stage,config,campaign):seen['audits'].append(verify_bundle(current)['revision']);return seq.pop(0)
  def role(config,role,payload,stage,campaign):
   seen['roles'].append(role)
   if role=='repair_reviewer':return {'result':{'approved':review,'weakened_criteria':[] if review else ['calc'],'reasons':['QA mock independent review']}}
   s=copy.deepcopy(payload['scenario']);s['revision']+=1;s['request']['context']+=' Clarified reference scope.'
   if mutate:mutate(s)
   return {'result':{'scenario':s,'changes':[{'criterion_id':payload['defects'][0]['criterion_id'],'change':'Clarify request'}]}}
  return seen,solver,audit,role
 def execute(self,audits,review=True,mutate=None):
  seen,solver,audit,role=self.callbacks(audits,review,mutate)
  with patch.object(w,'solver_role',side_effect=solver),patch.object(w,'audit_run',side_effect=audit),patch.object(w,'json_role',side_effect=role):result=w.evolve_scenario(self.initial,self.out,self.cfg)
  return result,seen
 def test_agent_only_failure_accepts_scenario_without_repair(self):
  result,seen=self.execute([self.audit(['Agent'])]);self.assertEqual(result['status'],'accepted');self.assertEqual(len(seen['solver']),1);self.assertEqual(seen['roles'],[]);self.assertEqual(result['rounds'][0]['agent_only_failures'],['calc'])
 def test_repair_has_fresh_solver_and_reaudit(self):
  result,seen=self.execute([self.audit(['Task']),self.audit()]);self.assertEqual(result['status'],'accepted');self.assertEqual(seen['audits'],[0,1]);self.assertEqual([x[1] for x in seen['solver']],[0,1]);self.assertEqual(seen['roles'],['repairer','repair_reviewer'])
 def test_same_defect_discarded_after_three_audits(self):
  result,seen=self.execute([self.audit(['Task'])]*3);self.assertEqual(result['status'],'discarded');self.assertEqual(seen['audits'],[0,1,2]);self.assertEqual(seen['roles'].count('repairer'),2)
 def test_overall_cap_stops_rotating_defects(self):
  self.cfg['repair']['max_rounds']=3;result,seen=self.execute([self.audit(['Task'],'calc'),self.audit(['Rubric'],'misrep'),self.audit(['Task'],'calc')]);self.assertEqual(result['status'],'discarded');self.assertIn('budget',result['reason']);self.assertEqual(len(seen['audits']),3)
 def test_repair_reviewer_rejection_prevents_acceptance(self):
  result,seen=self.execute([self.audit(['Task'])],review=False);self.assertEqual(result['status'],'discarded');self.assertEqual(len(seen['audits']),1)
 def test_weight_weakening_is_rejected_before_reviewer(self):
  with self.assertRaises(ForgeError):self.execute([self.audit(['Task'])],mutate=lambda s:s['rubric']['items'][0].update(weight=1))
 def test_modified_anchor_is_rejected(self):
  p=self.initial/'public'/'anchors'/'public-a.csv';p.write_text('forged')
  with self.assertRaises(ForgeError):w.evolve_scenario(self.initial,self.out,self.cfg)
if __name__=='__main__':unittest.main(verbosity=2)
