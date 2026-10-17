# Training and evaluation: separate experiments

## Reported paper recipe versus the supplied adapter

`recipes/paper_reported_training.json` records the published SFT settings: 20,000 GLM-5.2 trajectories, Qwen3.6-35B-A3B, 32 H100 GPUs, Adam betas 0.9/0.98, learning rate 5e-5 decaying to 1e-6, 10% warmup, weight decay 0.1, 1,537 updates, and 256,000-token packing. Distributed parallelism and recomputation values are included. This is a reported recipe, not a runnable reconstruction of the undisclosed original framework.

`recipes/train_sft.py` is an actual Transformers/PEFT training adapter for qualified exports. It is deliberately a different experiment: smaller configurable context, assistant-only loss, optional LoRA, epoch-based training, and optional packing. No GPU training has been run. `requirements-training.txt` contains minimum API interfaces, **not a tested reproducible GPU lockfile**. Pin and validate an architecture-compatible training environment before scaling.

The user's locally pinned Qwen3.8-27B config declares a conditional-generation architecture. Do not silently load it as a plain CausalLM. The script requires explicit `--model-loader image-text-to-text` for that architecture; its language-only model forward/backward behavior remains unverified. Tool-template tokenization has been checked with the actual local tokenizer without downloading or loading weights. Architecture loading and training are separate tests.

## 1. Collect genuine final-teacher trajectories

After a scenario is accepted, collect a new independent teacher run, then audit it:

```bash
python -m wgforge run --scenario SCENARIO --role teacher --config configs/my-live.json --out runs/teacher-001 --approve-live
python -m wgforge audit --scenario SCENARIO --run runs/teacher-001 --config configs/my-live.json --out runs/teacher-audit-001 --approve-live
```

Replace `SCENARIO` with the accepted revision path in `workflow.json`. Do not use demo controls, reference files, author text, or mocked transport tests as teacher trajectories.

The paper does not disclose a final trajectory quality-filter threshold. This implementation's human review and optional `--min-score` are conservative operator adaptations. A valid scenario may still produce an inadequate training demonstration; these are different decisions.

## 2. Review and qualify exact bytes

A human operator inspects source rights, factual/semantic correctness, privacy/safety, and known benchmark separation. Set `WG_REVIEW_KEY` privately to a random value of at least 32 characters; keep it outside model-accessible configuration and files. Then use the explicit `review` flags and your reviewer identifier:

```bash
python -m wgforge review --scenario SCENARIO --run runs/teacher-001 \
  --audit runs/teacher-audit-001/audit.json --out runs/review-001.json \
  --reviewer YOUR_REVIEWER_ID --confirm-source-rights --confirm-semantic-correctness \
  --confirm-privacy-safety --confirm-no-benchmark-overlap
python -m wgforge qualify --scenario SCENARIO --run runs/teacher-001 \
  --audit runs/teacher-audit-001/audit.json --review runs/review-001.json \
  --out runs/qualified-001.json --split train
```

The commands bind approval to exact file hashes. A cryptographic signature proves the local attestation was not edited; it does not prove a correct human judgment. Never auto-sign model-produced approvals.

## 3. Optional student calibration

`calibrate` performs independent student runs and audits, records mean score, and labels the task too hard, selected, or too easy under explicitly configured thresholds. It never repairs a valid task to make the student pass. This is an adaptation absent from the paper. Use development-only task lineages, not official benchmark outcomes, and keep them separate from the frozen test evaluation.

## 4. Export and check the actual tokenizer

```bash
python -m wgforge export --records runs/qualified-001.json --out runs/sft \
  --heldout-manifest /path/to/real-heldout-exclusions.json
python -m pip install -r recipes/requirements-tokenizer.txt
python recipes/check_tokenizer.py --tokenizer-dir /path/to/pinned/local/tokenizer --dataset runs/sft
```

Export checks source rights separately for training, redistribution, and public access; actual artifacts and complete tool pairing; human review signatures; lineage/source split integrity; exact duplicate traces; and supplied heldout source/lineage/13-gram exclusions. An omitted or empty exclusion corpus is explicitly **not a comprehensive contamination check**. No process here has access to a private benchmark corpus. The empty example file is not evidence of a clean dataset.

The native model template is used, including tools. OpenAI JSON-string function arguments are normalized to objects in a deep copy for Qwen template rendering; the original trace remains unchanged. Token offsets label only assistant-generated content/tool calls and mask prompts/environment outputs. Templates that rewrite earlier history or fail prefix consistency stop with an actionable error rather than silently producing bad labels. Available real provider reasoning must not be fabricated; the runtime preserves only fields it actually supports.

## 5. GPU adaptation trial

Validate before executing:

```bash
python recipes/train_sft.py --dataset runs/sft --model /path/to/local/model --output runs/adapter-trial --max-length 8192 --lora
```

Only after creating a compatible GPU environment, validating the model architecture, checking actual token masks, and confirming memory capacity, add `--execute`. For conditional-generation checkpoints, explicitly choose the architecture loader described above. Remote model identifiers require an immutable `--revision`. `trust_remote_code` remains false.

The optional `--pack` concatenates whole examples and permits cross-example causal attention. It is not claimed to reproduce block-isolated packed attention or the paper's original packing. No sample is silently truncated or cut across a tool trace. A 27B model and long contexts will require substantial GPU memory and possibly an operator-provided distributed/sharded configuration; this package does not promise they fit a Mac or one GPU.

## 6. Frozen local evaluation

Freeze a separate local scenario manifest before training. `evaluate-local`:

- Rejects shared source hashes or lineage with the training export
- Requires identical frozen judges for base and tuned models
- Runs both models against the same immutable scenarios
- Never repairs an evaluation scenario based on model results
- Reports paired local rubric deltas only

```bash
python -m wgforge evaluate-local \
  --eval-manifest /path/to/local-heldout.json --train-manifest runs/sft/manifest.json \
  --base-config configs/base-eval.json --tuned-config configs/tuned-eval.json \
  --out runs/paired-evaluation --approve-live
```

## 7. Official GDPval experiment

Keep any official benchmark checkout, source files, rubrics, expert answers, and evaluator credentials outside this generation workspace. Never feed them to search, synthesis, source selection, student calibration, or rubric repair. Use the benchmark owner's supported runner and a pinned authorized task/evaluator revision. Record base and trained checkpoint revisions, harness image/commit, tool interface, context/compaction, judge, exclusions, and raw paired results.

The paper references a GDPval-AA-v2 snapshot; current methodology has since changed. Without the original snapshot and calibration/Elo state, a current official run is a different comparison. This package supplies isolation checks and a local rubric experiment; it does not implement a guessed official Elo conversion or claim an official GDPval result.
