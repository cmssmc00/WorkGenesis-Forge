#!/usr/bin/env python3
"""Rebuild paper occupation playbooks from cached official O*NET source files.

No network or model calls. All source byte hashes and attribution are validated.
"""
from pathlib import Path
import argparse
import json
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wgforge.planning import build_paper_playbooks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--occupation-catalog", type=Path, default=ROOT / "configs/paper_occupations.json")
    parser.add_argument("--source-manifest", type=Path, default=ROOT / "wgforge/data/anchors/onet_manifest.json")
    parser.add_argument("--out", type=Path, default=ROOT / "wgforge/data/occupation_playbooks.json")
    args = parser.parse_args()
    catalog = json.loads(args.occupation_catalog.read_text())
    reference = os.path.relpath(args.source_manifest.resolve(), args.out.resolve().parent)
    if ".." in Path(reference).parts:
        parser.error("Output must contain its source manifest within its directory tree.")
    payload = build_paper_playbooks(catalog, args.source_manifest, reference)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(f"Rebuilt {len(payload['playbooks'])} verified occupation playbooks: {args.out}")


if __name__ == "__main__":
    main()
