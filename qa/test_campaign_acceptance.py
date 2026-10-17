"""Lineage novelty and campaign resume/restart checks without model calls."""
import sys,tempfile,unittest,copy
from pathlib import Path
from unittest.mock import patch
from _qa_paths import REPO, QA_OUTPUT, TOKENIZER
sys.path.insert(0,str(REPO))
from wgforge import campaign as c
from wgforge.common import read_json,ForgeError
ROOT=REPO
class CampaignAcceptance(unittest.TestCase):
 def setUp(self):self.t=tempfile.TemporaryDirectory();self.root=Path(self.t.name);self.reg=self.root/'registry.json';self.source={'id':'a','sha256':'a'*64,'url':'https://example.org/a.csv','rights_status':'pending'}
 def tearDown(self):self.t.cleanup()
 def test_same_reservation_resume_and_status_transition(self):
  c.register_anchors(self.reg,'lineage','task0',[self.source]);c.register_anchors(self.reg,'lineage','task0',[self.source],'discarded');r=read_json(self.reg);self.assertEqual(len(r['entries']),1);self.assertEqual(r['entries'][0]['status'],'discarded')
 def test_hash_and_url_overlap_block_even_after_discard(self):
  c.register_anchors(self.reg,'lineage','task0',[self.source],'discarded')
  for changed in [{**self.source,'url':'https://example.org/other.csv'},{**self.source,'sha256':'b'*64}]:
   with self.subTest(source=changed),self.assertRaises(ForgeError):c.register_anchors(self.reg,'lineage','task1',[changed])
 def test_reserved_source_change_is_blocked(self):
  c.register_anchors(self.reg,'lineage','task0',[self.source])
  with self.assertRaises(ForgeError):c.register_anchors(self.reg,'lineage','task0',[{**self.source,'sha256':'b'*64}])
 def test_separate_lineage_is_recorded_separately(self):
  c.register_anchors(self.reg,'lineage0','task0',[self.source]);c.register_anchors(self.reg,'lineage1','task1',[self.source]);self.assertEqual(len(read_json(self.reg)['entries']),2)
 def config(self):return {'approve_live':True,'source_manifests':[str(ROOT/'examples/anchors/usgs_manifest.json')],'repair':{'max_restarts_per_plan':0},'lineage_id':'QA_CAMPAIGN'}
 def test_missing_live_approval_stops_before_planning_or_acquisition(self):
  cfg=self.config();cfg['approve_live']=False
  with patch.object(c,'plan_tasks') as planning,patch.object(c,'collect_anchors') as acquisition,self.assertRaises(ForgeError):c.run_campaign(cfg,self.root/'unapproved_campaign')
  planning.assert_not_called();acquisition.assert_not_called()
 def test_completed_campaign_resume_has_no_calls(self):
  cfg=self.config();out=self.root/'campaign'
  with patch.object(c,'plan_tasks',return_value=[{'plan_id':'p0'}]),patch.object(c,'author_scenario',return_value=self.root/'scenario') as author,patch.object(c,'evolve_scenario',return_value={'status':'accepted'}) as evolve,patch.object(c,'verify_terminal_workflow',return_value={'status':'accepted'}) as terminal:
   result=c.run_campaign(cfg,out);again=c.run_campaign(cfg,out)
  self.assertEqual(result['status'],'completed');self.assertEqual(again['counts'],{'accepted':1,'discarded':0});self.assertEqual(author.call_count,1);self.assertEqual(evolve.call_count,1);self.assertEqual(terminal.call_count,1);self.assertFalse((out/'.campaign.lock').exists())
 def test_discard_restart_cannot_reuse_same_source(self):
  cfg=self.config();cfg['source_manifests']*=2;cfg['repair']['max_restarts_per_plan']=1;out=self.root/'campaign'
  with patch.object(c,'plan_tasks',return_value=[{'plan_id':'p0'}]),patch.object(c,'author_scenario',return_value=self.root/'scenario'),patch.object(c,'evolve_scenario',return_value={'status':'discarded'}),self.assertRaises(ForgeError):c.run_campaign(cfg,out)
  self.assertEqual(read_json(out/'lineage_registry.json')['entries'][0]['status'],'discarded');self.assertEqual(len(read_json(out/'campaign.json')['attempts']),1)
if __name__=='__main__':unittest.main(verbosity=2)
