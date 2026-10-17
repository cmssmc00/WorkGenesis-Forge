"""Reject unsupported source choices before scenario acceptance."""
import unittest,tempfile,copy,sys,hashlib
from pathlib import Path
from unittest.mock import patch
from _qa_paths import REPO, QA_OUTPUT, TOKENIZER
sys.path.insert(0,str(REPO))
from wgforge import workflow as w
from wgforge.common import ForgeError,read_json
class SourceSelectionAcceptance(unittest.TestCase):
 def setUp(self):
  self.e=[{'record':{'id':'source-a'},'extracted_text':'A detailed professional source excerpt with records, clear units and enough source context.'}];self.a=[{'source_id':'source-a','usable':True,'occupation_fit':'An analyst in this occupation uses these source values to compare and reconcile operating records.','professional_operations':['Compare and reconcile detailed operating records'],'evidence_excerpt':self.e[0]['extracted_text'][:60],'limitations':'Single source snapshot; no future projections.'}]
 def test_valid_evidence_tied_assessment(self):self.assertEqual(w.validate_source_assessment(self.a,self.e),self.a)
 def test_missing_or_duplicate_source_decision_rejected(self):
  for rows in [[],self.a*2,[{**self.a[0],'source_id':'invented'}]]:
   with self.subTest(rows=rows),self.assertRaises(ForgeError):w.validate_source_assessment(rows,self.e)
 def test_fabricated_evidence_excerpt_rejected(self):
  a=copy.deepcopy(self.a);a[0]['evidence_excerpt']='This plausible-looking sentence was never contained in the actual source file.'
  with self.assertRaises(ForgeError):w.validate_source_assessment(a,self.e)
 def test_unusable_source_does_not_become_accepted(self):
  a=copy.deepcopy(self.a);a[0]['usable']=False
  with self.assertRaises(ForgeError):w.validate_source_assessment(a,self.e)
 def test_missing_occupation_operations_or_limitations_rejected(self):
  for changed in [{'occupation_fit':'looks good'},{'professional_operations':[]},{'limitations':None}]:
   a=copy.deepcopy(self.a);a[0].update(changed)
   with self.subTest(changed=changed),self.assertRaises(ForgeError):w.validate_source_assessment(a,self.e)
 def test_stub_source_rejected_before_any_model_call(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);p=root/'short.csv';p.write_text('id,value\nA,1\n');m=read_json(REPO/'examples/anchors/usgs_manifest.json');m['sources'][0].update(path='short.csv',bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest())
   with patch.object(w,'json_role') as role,self.assertRaises(w.UnsuitableSource):w.author_scenario({'profession':'Analyst'},m,root,root/'authored',{})
   role.assert_not_called();self.assertFalse((root/'authored/initial').exists())
if __name__=='__main__':unittest.main(verbosity=2)
