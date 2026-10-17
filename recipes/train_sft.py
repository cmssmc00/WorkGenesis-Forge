#!/usr/bin/env python3
"""Runnable Transformers/PEFT adaptation, not the undisclosed paper trainer."""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wgforge.common import ForgeError, read_json, sha256, safe_path
from wgforge.training import encode_example, pack_examples


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset", required=True, help="Sealed export directory")
    p.add_argument("--model", required=True, help="Local model path or verified Hugging Face repository")
    p.add_argument("--revision", help="Exact model revision when using a remote model")
    p.add_argument("--output", required=True)
    p.add_argument("--max-length", type=int, default=8192)
    p.add_argument("--learning-rate", type=float, default=5e-5)
    p.add_argument("--epochs", type=float, default=1)
    p.add_argument("--gradient-accumulation", type=int, default=8)
    p.add_argument("--lora", action="store_true")
    p.add_argument("--model-loader", choices=["causal-lm", "image-text-to-text"], default="causal-lm", help="Explicit architecture loader; conditional-generation models require image-text-to-text")
    p.add_argument("--pack", action="store_true")
    p.add_argument("--execute", action="store_true", help="Actually load the model and train; otherwise validate only")
    args = p.parse_args()
    if not 128 <= args.max_length <= 262144:
        raise ForgeError("max-length must be between 128 and 262144")
    if args.gradient_accumulation < 1 or not math.isfinite(args.epochs) or args.epochs <= 0 or not math.isfinite(args.learning_rate) or not 0 < args.learning_rate < 1:
        raise ForgeError("Use positive finite epochs, gradient accumulation >= 1, and 0 < learning-rate < 1")
    root = Path(args.dataset)
    manifest = read_json(root / "manifest.json")
    if manifest.get("schema_version") != "workgenesis-forge/sft-v1" or "train.jsonl" not in manifest.get("files", {}):
        raise ForgeError("A sealed SFT export manifest with a train.jsonl digest is required")
    for name, expected in manifest["files"].items():
        if sha256(safe_path(root, name, True)) != expected:
            raise ForgeError("Export file hash mismatch: " + name)
    rows = [json.loads(line) for line in safe_path(root, "train.jsonl", True).read_text().splitlines() if line.strip()]
    if not rows or any(r.get("metadata", {}).get("mode") != "live_qualified" for r in rows):
        raise ForgeError("Training requires nonempty qualified real training records")
    report = {"records": len(rows), "model": args.model, "revision": args.revision, "max_length": args.max_length,
              "method": "Transformers adaptation, assistant-only labels, tool/prompt tokens masked", "training_executed": False, "packing_attention": "Cross-example causal attention (adaptation)" if args.pack else "Unpacked independent examples",
              "warning": "This is not a reconstruction of the paper's undisclosed distributed trainer. Checkpoint architecture and chat template must be verified by the operator."}
    if not args.execute:
        print(json.dumps(report, indent=2)); return
    if args.max_length < 128 or args.max_length > 262144:
        raise ForgeError("max-length must be between 128 and 262144")
    if not Path(args.model).exists() and not args.revision:
        raise ForgeError("Remote model loads require an explicit immutable --revision")
    from datasets import Dataset
    from transformers import AutoTokenizer, AutoConfig, AutoModelForCausalLM, AutoModelForImageTextToText, Trainer, TrainingArguments, DataCollatorForSeq2Seq
    import torch
    if not torch.cuda.is_available():
        raise ForgeError("This adaptation requires a CUDA training environment; the Mac offline demo does not train a 27B model")
    tokenizer = AutoTokenizer.from_pretrained(args.model, revision=args.revision, use_fast=True, trust_remote_code=False)
    if not tokenizer.is_fast:
        raise ForgeError("A fast tokenizer is required for verified assistant-token offsets")
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    encoded = [encode_example(tokenizer, row, args.max_length) for row in rows]
    if args.pack:
        encoded = pack_examples(encoded, args.max_length)
    architecture = AutoConfig.from_pretrained(args.model, revision=args.revision, trust_remote_code=False)
    conditional = any("ForConditionalGeneration" in name for name in getattr(architecture, "architectures", []))
    if conditional and args.model_loader == "causal-lm":
        raise ForgeError("Checkpoint is conditional-generation, not plain CausalLM. Verify its language-only training support and explicitly select --model-loader image-text-to-text; this route is not validated by the offline demo.")
    loader = AutoModelForImageTextToText if args.model_loader == "image-text-to-text" else AutoModelForCausalLM
    try:
        model = loader.from_pretrained(args.model, revision=args.revision, torch_dtype=torch.bfloat16, trust_remote_code=False)
    except (ValueError, KeyError) as error:
        raise ForgeError("Installed Transformers does not support the selected checkpoint architecture/loader. Pin a compatible official release and verify a tiny forward/backward pass before scaling.") from None
    if args.lora:
        from peft import LoraConfig, get_peft_model
        model = get_peft_model(model, LoraConfig(r=32, lora_alpha=64, lora_dropout=.05, target_modules="all-linear", task_type="CAUSAL_LM"))
    model.config.use_cache = False
    training = TrainingArguments(output_dir=args.output, num_train_epochs=args.epochs, learning_rate=args.learning_rate,
        adam_beta1=.9, adam_beta2=.98, weight_decay=.1, warmup_ratio=.1, lr_scheduler_type="cosine",
        per_device_train_batch_size=1, gradient_accumulation_steps=args.gradient_accumulation, gradient_checkpointing=True,
        bf16=True, logging_steps=1, save_steps=100, report_to="none", remove_unused_columns=False)
    trainer = Trainer(model=model, args=training, train_dataset=Dataset.from_list(encoded),
                      data_collator=DataCollatorForSeq2Seq(tokenizer, model=model, label_pad_token_id=-100, pad_to_multiple_of=8))
    trainer.train()
    trainer.save_model(args.output); tokenizer.save_pretrained(args.output)
    report["training_executed"] = True
    Path(args.output, "adaptation_report.json").write_text(json.dumps(report, indent=2))

if __name__ == "__main__":
    try:
        main()
    except (ForgeError, ImportError) as error:
        print("Error: " + str(error), file=sys.stderr)
        raise SystemExit(2)
