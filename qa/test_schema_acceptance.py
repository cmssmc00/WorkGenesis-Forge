"""Independent trust-boundary schema assertions."""
import sys,unittest,copy
from _qa_paths import REPO, QA_OUTPUT, TOKENIZER
sys.path.insert(0,str(REPO))
from wgforge.schema import validate_scenario,public_contract,validate_scores,validate_attribution,validate_repair,relative_file,VERSION
from wgforge.common import ForgeError

def scenario():
 return {'schema_version':VERSION,'scenario_id':'scenario-a','lineage_id':'lineage-a','revision':0,
 'request':{'title':'Analysis','role':'Analyst','context':'Independent QA fixture','instructions':'Read values.csv and report total.','deliverables':[{'path':'report.md','format':'md','description':'Report total and source'}]},
 'anchor_sources':[{'id':'public-a','path':'values.csv','url':'https://example.org/values.csv','sha256':'1'*64,'synthetic':False}],
 'companions':[],
 'rubric':{'items':[{'id':'calc','criterion':'Correct total','weight':5,'evidence':['anchors/public-a.csv'],'verification':'structured'},{'id':'misrep','criterion':'No invented source facts','weight':-2,'evidence':['request'],'verification':'text'}]}}

def scores():
 return [{'criterion_id':'calc','score':5,'rationale':'Recomputed from source','evidence':['report.md: total']},{'criterion_id':'misrep','score':0,'rationale':'No invented values','evidence':['report.md: source citation']}]

class SchemaAcceptance(unittest.TestCase):
 def test_valid_dynamic_contract(self):self.assertIs(validate_scenario(scenario())['schema_version'],VERSION)
 def test_traversal_rejected(self):
  for name in ['../x','/etc/passwd','a/../b','a//b','a\\b','./x','a/.','a/']:
   with self.subTest(path=name),self.assertRaises(ForgeError):relative_file(name)
 def test_synthetic_anchor_rejected(self):
  s=scenario();s['anchor_sources'][0]['synthetic']=True
  with self.assertRaises(ForgeError):validate_scenario(s)
 def test_duplicate_deliverable_rejected(self):
  s=scenario();s['request']['deliverables']*=2
  with self.assertRaises(ForgeError):validate_scenario(s)
 def test_nonfinite_and_boolean_scores_rejected(self):
  for v in [float('nan'),float('inf'),True]:
   rows=scores();rows[0]['score']=v
   with self.subTest(value=v),self.assertRaises(ForgeError):validate_scores(scenario(),rows)
 def test_missing_duplicate_unknown_and_range_scores_rejected(self):
  allrows=[scores()[:1],scores()[:1]*2, scores(),scores(),scores()]
  allrows[2][0]['criterion_id']='not_real';allrows[3][0]['score']=6;allrows[4][1]['score']=1
  for rows in allrows:
   with self.subTest(rows=rows),self.assertRaises(ForgeError):validate_scores(scenario(),rows)
 def test_negative_penalty_needs_attribution(self):
  rows=scores();rows[1]['score']=-1
  with self.assertRaises(ForgeError):validate_attribution(scenario(),rows,[])
  a=[{'criterion_id':'misrep','labels':['Agent'],'explanation':'Invented source statement','evidence':['report.md']}]
  self.assertEqual(validate_attribution(scenario(),rows,a),a)
 def test_attribution_coverage_and_labels(self):
  rows=scores();rows[0]['score']=0
  for labels in [[],['Agent','Agent'],['teacher'],['agent']]:
   with self.subTest(labels=labels),self.assertRaises(ForgeError):validate_attribution(scenario(),rows,[{'criterion_id':'calc','labels':labels,'explanation':'bad','evidence':['report.md']}])
 def test_anchor_and_weight_changes_rejected(self):
  for kind in ['anchor','weight','delete','agent_only','output']:
   a=scenario();b=copy.deepcopy(a);b['revision']=1
   if kind=='anchor':b['anchor_sources'][0]['sha256']='2'*64
   if kind=='weight':b['rubric']['items'][0]['weight']=1
   if kind=='delete':b['rubric']['items'].pop()
   if kind=='agent_only':b['rubric']['items'][1]['criterion']='Any result passes'
   if kind=='output':b['request']['deliverables'][0]['description']='Any file'
   with self.subTest(kind=kind),self.assertRaises(ForgeError):validate_repair(a,b,{'calc'})
 def test_public_contract_strips_private_request_keys(self):
  s=scenario();s['request']['rubric']={'secret':'PRIVATE_RUBRIC_CANARY'};s['request']['private_truth']='GOLD_CANARY'
  import json
  try:r=public_contract(s)
  except ForgeError:return
  self.assertNotIn('PRIVATE_RUBRIC_CANARY',json.dumps(r));self.assertNotIn('GOLD_CANARY',json.dumps(r))
 def test_score_requires_evidence(self):
  rows=scores();rows[0]['evidence']=[]
  with self.assertRaises(ForgeError):validate_scores(scenario(),rows)
 def test_attribution_requires_evidence(self):
  rows=scores();rows[0]['score']=0
  with self.assertRaises(ForgeError):validate_attribution(scenario(),rows,[{'criterion_id':'calc','labels':['Agent'],'explanation':'No support','evidence':[]}])
if __name__=='__main__':unittest.main(verbosity=2)
