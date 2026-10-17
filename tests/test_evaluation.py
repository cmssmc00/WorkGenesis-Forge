"""Frozen local-evaluation guards; no model requests are made."""
from pathlib import Path
import copy
import tempfile
import unittest
from unittest.mock import patch
from wgforge.common import ForgeError, read_json, write_json
from wgforge.demo import run_demo
from wgforge.evaluation import evaluate_local

ROOT = Path(__file__).resolve().parents[1]

class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        run_demo(self.root/'demo',ROOT);self.scenario=self.root/'demo/scenario'
        self.manifest=self.root/'eval.json';write_json(self.manifest,{'purpose':'local_heldout_evaluation','scenarios':[{'path':'demo/scenario','bundle_digest':read_json(self.scenario/'manifest.json')['bundle_digest']}]})
        self.train=self.root/'train.json';write_json(self.train,{'schema_version':'workgenesis-forge/sft-v1','anchor_hashes':[],'lineage_ids':[]})
        self.base={'roles':{'student':{'model':'base-test'},'judge':{'model':'judge-test'},'attributor':{'model':'judge-test'}}}
        self.tuned=copy.deepcopy(self.base);self.tuned['roles']['student']['model']='tuned-test'
    def tearDown(self):self.temp.cleanup()
    def test_overlap_blocks_before_paid_calls(self):
        anchors=read_json(self.scenario/'manifest.json')['anchor_hashes']
        write_json(self.train,{'schema_version':'workgenesis-forge/sft-v1','anchor_hashes':list(anchors.values()),'lineage_ids':[]})
        with patch('wgforge.evaluation.solver_role',side_effect=AssertionError('must not call')),self.assertRaises(ForgeError):
            evaluate_local(self.manifest,self.train,self.base,self.tuned,self.root/'out')
    def test_judge_must_remain_identical(self):
        self.tuned['roles']['judge']['model']='changed'
        with self.assertRaises(ForgeError):evaluate_local(self.manifest,self.train,self.base,self.tuned,self.root/'out')
    def test_paired_metric_is_not_official(self):
        audits=[{'defects':[],'normalized_score':.3},{'defects':[],'normalized_score':.6}]
        with patch('wgforge.evaluation.solver_role'),patch('wgforge.evaluation.audit_run',side_effect=audits):
            result=evaluate_local(self.manifest,self.train,self.base,self.tuned,self.root/'out')
        self.assertFalse(result['official_gdpval']);self.assertAlmostEqual(result['mean_delta'],.3)
    def test_eval_defects_never_trigger_repair(self):
        with patch('wgforge.evaluation.solver_role'),patch('wgforge.evaluation.audit_run',return_value={'defects':[{'criterion_id':'c1'}]}),self.assertRaises(ForgeError):
            evaluate_local(self.manifest,self.train,self.base,self.tuned,self.root/'out')

if __name__=='__main__':unittest.main()
