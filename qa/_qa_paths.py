"""Portable repository and optional local-only tokenizer locations for QA."""
from pathlib import Path
import os
here=Path(__file__).resolve().parent
candidates=[Path(os.environ['WGFORGE_REPO'])] if os.environ.get('WGFORGE_REPO') else [here.parent,Path.cwd(),Path.cwd()/'workgenesis_forge']
REPO=next((p.resolve() for p in candidates if (p/'wgforge').is_dir()),None)
if REPO is None:
 raise RuntimeError('Set WGFORGE_REPO to the WorkGenesis Forge checkout, or run this copied qa/ directory inside that checkout.')
QA_OUTPUT=Path(os.environ.get('WGFORGE_QA_OUTPUT',str(here/'runs'))).resolve()
QA_OUTPUT.mkdir(parents=True,exist_ok=True)
TOKENIZER=Path(os.environ.get('WGFORGE_TOKENIZER',str(REPO.parent/'taskforge_pipeline_v02/assets/student_tokenizer'))).resolve()

def protocol_path():
 """Create a fresh mocked integration run if a standalone test has none yet."""
 import json
 marker=QA_OUTPUT/'latest_protocol.json'
 if not marker.exists():
  from test_full_protocol_acceptance import FullProtocolAcceptance
  FullProtocolAcceptance.setUpClass()
 return Path(json.loads(marker.read_text())['path'])
