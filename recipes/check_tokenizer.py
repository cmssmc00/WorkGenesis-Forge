#!/usr/bin/env python3
"""Check actual local chat-template/tool serialization and assistant-only masks."""
from __future__ import annotations
import argparse
from pathlib import Path
import json
import os
import sys
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wgforge.common import ForgeError, read_json, safe_path, sha256
from wgforge.training import encode_example


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tokenizer-dir", required=True)
    parser.add_argument("--dataset", help="Optional qualified export directory; never download model weights")
    args = parser.parse_args()
    root = Path(args.tokenizer_dir)
    if not root.is_dir():
        raise ForgeError("Provide an existing local tokenizer directory")
    pin = read_json(root / "PIN.json") if (root / "PIN.json").exists() else None
    if pin:
        for name, expected in pin["files"].items():
            if sha256(safe_path(root, name, True)) != expected:
                raise ForgeError("Pinned tokenizer asset changed: " + name)
    from transformers import AutoTokenizer, AutoConfig
    tokenizer = AutoTokenizer.from_pretrained(root, local_files_only=True, use_fast=True, trust_remote_code=False)
    model_config = AutoConfig.from_pretrained(root, local_files_only=True, trust_remote_code=False)
    if args.dataset:
        dataset = Path(args.dataset)
        manifest = read_json(dataset / "manifest.json")
        if manifest.get("schema_version") != "workgenesis-forge/sft-v1" or "train.jsonl" not in manifest.get("files", {}):
            raise ForgeError("Expected sealed SFT export with train.jsonl hash")
        for name, expected in manifest["files"].items():
            if sha256(safe_path(dataset, name, True)) != expected:
                raise ForgeError("Dataset integrity failed")
        rows = [json.loads(line) for line in safe_path(dataset, "train.jsonl", True).read_text().splitlines() if line.strip()]
        mode = "qualified_dataset_tokenization_only"
    else:
        # This local canary is a serialization test, not a generated training row.
        rows = [{"metadata": {"mode": "live_qualified"}, "messages": [
            {"role": "system", "content": "SYSTEM_MASK_CANARY"}, {"role": "user", "content": "USER_MASK_CANARY"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "test1", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"input/source.csv"}'}}]},
            {"role": "tool", "tool_call_id": "test1", "content": "TOOL_MASK_CANARY"},
            {"role": "assistant", "content": "ASSISTANT_LABEL_CANARY"}],
            "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a local input", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}]
        mode = "offline_synthetic_mask_test_not_training_data"
    counts = []
    for row in rows:
        encoded = encode_example(tokenizer, row, 262144)
        labeled = tokenizer.decode([token for token in encoded["labels"] if token != -100])
        if not args.dataset and ("ASSISTANT_LABEL_CANARY" not in labeled or any(x in labeled for x in ("SYSTEM_MASK_CANARY", "USER_MASK_CANARY", "TOOL_MASK_CANARY"))):
            raise ForgeError("Assistant-only masking leaked prompts/tool results or lost the assistant answer")
        counts.append({"tokens": len(encoded["input_ids"]), "assistant_tokens": sum(x != -100 for x in encoded["labels"])})
    print(json.dumps({"mode": mode, "pin": pin and {"id": pin.get("id"), "revision": pin.get("revision")},
                      "architectures": getattr(model_config, "architectures", None), "counts": counts,
                      "weights_loaded": False, "training_executed": False, "native_template_check_passed": True}, indent=2))

if __name__ == "__main__":
    try:
        main()
    except (ValueError, ImportError, OSError) as error:
        print("Error: " + str(error), file=sys.stderr)
        raise SystemExit(2)
