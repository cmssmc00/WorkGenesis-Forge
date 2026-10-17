# Independent acceptance QA

These tests were written separately from the implementation. They use real parsers, generated native artifacts, the saved public USGS bytes, and explicitly mocked provider/transport responses. They never make a paid model request or start Docker. A simulated protocol run is software coverage, not a teacher trajectory or evidence of model competence.

From the repository root after installing core requirements:

```bash
python qa/run_suite.py
```

The runner orders the full mocked protocol before checks that inspect its saved trace. Outputs stay under `qa/runs/`; set `WGFORGE_QA_OUTPUT` to use another scratch directory. To test a different checkout, set `WGFORGE_REPO`.

The optional actual-tokenizer tests require `recipes/requirements-tokenizer.txt` and existing local tokenizer assets. Set `WGFORGE_TOKENIZER` to that directory. They use offline/local-only loading, verify supplied `PIN.json` hashes, and do not load weights. Without dependencies/assets they are explicitly skipped.

```bash
python -m pip install -r recipes/requirements-tokenizer.txt
WGFORGE_TOKENIZER=/path/to/pinned/tokenizer python qa/run_suite.py
```

To recompute every demo scalar, all daily aggregates, and retained source rows independently of the pipeline's reference calculation:

```bash
python qa/recompute_source_control.py runs/first-demo
```

`ACCEPTANCE_REPORT.md` records the final tested scope, failures fixed, remaining unrun work, and test counts. Docker flags and malicious return-channel cases are mocked here; the repository's separate real-Docker test remains skipped unless an approved pinned local image and working daemon are explicitly available.
