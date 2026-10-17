"""Local-first command line interface; live stages are always explicit."""
from __future__ import annotations
import argparse
import importlib.metadata
import json
from pathlib import Path
import shutil
import sys
from .common import ForgeError, read_json, write_json


def _config(path, approve):
    config = read_json(path)
    config["approve_live"] = approve
    return config


def doctor():
    dependencies = {}
    for name in ("openpyxl", "python-docx", "python-pptx", "reportlab", "pypdf", "Pillow"):
        try:
            dependencies[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            dependencies[name] = "MISSING: pip install -r requirements.lock"
    return {"python": sys.version.split()[0], "python_supported": sys.version_info >= (3, 11),
            "dependencies": dependencies, "docker_executable": shutil.which("docker"),
            "pdf_renderer": shutil.which("pdftoppm"), "office_renderer": shutil.which("libreoffice") or shutil.which("soffice"),
            "live_calls_made": False, "note": "Docker executable presence does not establish a running daemon or an available pinned image. Offline demo needs no Docker or credentials."}


def parser():
    p = argparse.ArgumentParser(prog="workgenesis-forge", description="Source-grounded occupational scenario pipeline. Offline demo is never production training data.")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Check local dependencies without contacting a model")
    demo = sub.add_parser("demo", aliases=["smoke"], help="Run real-source, deterministic offline checks")
    demo.add_argument("--out", required=True)
    demo.add_argument("--package-root")
    plan = sub.add_parser("plan", help="Sample occupational activity slots from configured O*NET inventory")
    plan.add_argument("--config", required=True); plan.add_argument("--out", required=True)
    plan.add_argument("--count", type=int, help="Override the configured number of task slots")
    preflight = sub.add_parser("preflight", help="Check the paper profile offline; no model calls or containers")
    preflight.add_argument("--config", required=True); preflight.add_argument("--count", type=int)
    search = sub.add_parser("discover", help="Discover direct or SerpAPI public anchors; no download or rights inference")
    search.add_argument("--query", required=True); search.add_argument("--config", required=True); search.add_argument("--out", required=True)
    fetch = sub.add_parser("fetch", help="Download and seal discovered public anchors")
    fetch.add_argument("--candidates", required=True); fetch.add_argument("--config", required=True); fetch.add_argument("--out", required=True)
    gen = sub.add_parser("generate", help="Author from real source manifest, then independently execute/audit/repair")
    gen.add_argument("--sources", required=True); gen.add_argument("--plan", required=True); gen.add_argument("--plan-index", type=int, default=0)
    gen.add_argument("--config", required=True); gen.add_argument("--out", required=True); gen.add_argument("--approve-live", action="store_true")
    gen.add_argument("--author-only", action="store_true")
    campaign = sub.add_parser("campaign", help="Run O*NET planning, bounded acquisition, synthesis, audit, repair, and lineage deduplication")
    campaign.add_argument("--config", required=True); campaign.add_argument("--out", required=True); campaign.add_argument("--approve-live", action="store_true")
    campaign.add_argument("--count", type=int, help="Override the target accepted task count; use the same value when resuming")
    evolve = sub.add_parser("evolve", help="Resume bounded independent execution and scenario repair")
    evolve.add_argument("--scenario", required=True); evolve.add_argument("--config", required=True); evolve.add_argument("--out", required=True); evolve.add_argument("--approve-live", action="store_true")
    evolve.add_argument("--campaign-root", help="Shared budget directory containing --out and prior attempts; defaults to --out")
    run = sub.add_parser("run", help="Collect actual independent solver/teacher/student tool trajectories")
    run.add_argument("--scenario", required=True); run.add_argument("--config", required=True); run.add_argument("--out", required=True)
    run.add_argument("--role", choices=["solver", "teacher", "student"], default="teacher"); run.add_argument("--approve-live", action="store_true")
    audit = sub.add_parser("audit", help="Itemwise independent scoring and Agent/Task/Rubric attribution")
    audit.add_argument("--scenario", required=True); audit.add_argument("--run", required=True); audit.add_argument("--config", required=True)
    audit.add_argument("--out", required=True); audit.add_argument("--approve-live", action="store_true")
    cal = sub.add_parser("calibrate", help="Optional student-aware adaptation; not a published paper stage")
    cal.add_argument("--scenario", required=True); cal.add_argument("--config", required=True); cal.add_argument("--out", required=True)
    cal.add_argument("--count", type=int, default=4); cal.add_argument("--approve-live", action="store_true")
    review = sub.add_parser("review", help="Human operator signs exact artifact review; never run by a model")
    for arg in ("scenario", "run", "audit", "out", "reviewer"):
        review.add_argument("--" + arg, required=True)
    review.add_argument("--key-env", default="WG_REVIEW_KEY")
    for arg in ("source-rights", "semantic-correctness", "privacy-safety", "no-benchmark-overlap"):
        review.add_argument("--confirm-" + arg, action="store_true", required=True)
    qual = sub.add_parser("qualify", help="Admit a reviewed real final-teacher trajectory")
    for arg in ("scenario", "run", "audit", "review", "out"):
        qual.add_argument("--" + arg, required=True)
    qual.add_argument("--key-env", default="WG_REVIEW_KEY"); qual.add_argument("--min-score", type=float)
    qual.add_argument("--split", choices=["train", "validation", "test"], default="train")
    export = sub.add_parser("export", help="Export verified real trajectories; reject fixtures and cross-split source leakage")
    export.add_argument("--records", nargs="+", required=True); export.add_argument("--out", required=True)
    export.add_argument("--key-env", default="WG_REVIEW_KEY"); export.add_argument("--heldout-manifest")
    evaluation = sub.add_parser("evaluate-local", help="Frozen paired heldout artifact evaluation, not official GDPval")
    for arg in ("eval-manifest", "train-manifest", "base-config", "tuned-config", "out"):
        evaluation.add_argument("--" + arg, required=True)
    evaluation.add_argument("--approve-live", action="store_true")
    inspect = sub.add_parser("inspect", help="Verify immutable scenario and list public/private boundaries")
    inspect.add_argument("--scenario", required=True)
    stats = sub.add_parser("stats", help="Show conservative durable request/cost accounting")
    stats.add_argument("--root", required=True)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "doctor":
            result = doctor()
        elif args.command in {"demo", "smoke"}:
            from .demo import run_demo
            result = run_demo(args.out, args.package_root)
        elif args.command == "plan":
            from .planning import plan_tasks, compact_plans
            from .paper_profile import with_task_count
            result = compact_plans(plan_tasks(with_task_count(read_json(args.config), args.count))); write_json(args.out, result)
        elif args.command == "preflight":
            from .paper_profile import preflight, with_task_count
            result = preflight(with_task_count(read_json(args.config), args.count))
        elif args.command == "discover":
            from .sources import discover_sources
            result = {"candidates": discover_sources(args.query, read_json(args.config))}; write_json(args.out, result)
        elif args.command == "fetch":
            from .sources import download_source
            cfg, candidates = read_json(args.config), read_json(args.candidates)
            output = Path(args.out); output.mkdir(parents=True, exist_ok=True)
            result = {"sources": [download_source(r, output, cfg) for r in candidates["candidates"]]}
            write_json(output / "manifest.json", result)
        elif args.command == "generate":
            from .sources import load_source_manifest
            from .workflow import author_scenario, evolve_scenario
            cfg = _config(args.config, args.approve_live)
            from .planning import hydrate_plan
            packet = read_json(args.plan)
            plans = packet.get("plans", packet) if isinstance(packet, dict) else packet
            if not isinstance(plans, list) or not 0 <= args.plan_index < len(plans):
                raise ForgeError("Plan index is outside the supplied plan list")
            manifest = load_source_manifest(Path(args.sources))
            selected_plan = hydrate_plan(plans[args.plan_index], packet.get("playbooks", {}) if isinstance(packet, dict) else {})
            initial = author_scenario(selected_plan, manifest, Path(args.sources).parent, Path(args.out) / "synthesis", cfg, campaign_root=args.out)
            result = {"scenario": str(initial), "status": "authored_unverified"} if args.author_only else evolve_scenario(initial, Path(args.out) / "evolution", cfg, campaign_root=args.out)
        elif args.command == "campaign":
            from .campaign import run_campaign
            from .paper_profile import with_task_count
            result = run_campaign(with_task_count(_config(args.config, args.approve_live), args.count), args.out)
        elif args.command == "evolve":
            from .workflow import evolve_scenario
            result = evolve_scenario(args.scenario, args.out, _config(args.config, args.approve_live), campaign_root=args.campaign_root)
        elif args.command == "run":
            from .workflow import solver_role
            result = solver_role(_config(args.config, args.approve_live), args.role, args.scenario, args.out, args.out)
        elif args.command == "audit":
            from .workflow import audit_run
            result = audit_run(args.scenario, args.run, args.out, _config(args.config, args.approve_live))
        elif args.command == "calibrate":
            from .dataset import calibrate
            result = calibrate(args.scenario, args.out, _config(args.config, args.approve_live), args.count)
        elif args.command == "review":
            from .dataset import sign_review
            result = sign_review(args.scenario, args.run, args.audit, args.out, args.reviewer, args.key_env,
                                  {k: getattr(args, "confirm_" + k) for k in ("source_rights", "semantic_correctness", "privacy_safety", "no_benchmark_overlap")})
        elif args.command == "qualify":
            from .dataset import qualify
            result = qualify(args.scenario, args.run, args.audit, args.review, args.out, args.key_env, args.min_score, args.split)
        elif args.command == "export":
            from .dataset import export_sft
            result = export_sft(args.records, args.out, args.key_env, args.heldout_manifest)
        elif args.command == "evaluate-local":
            from .evaluation import evaluate_local
            result = evaluate_local(args.eval_manifest, args.train_manifest, _config(args.base_config, args.approve_live),
                                    _config(args.tuned_config, args.approve_live), args.out)
        elif args.command == "inspect":
            from .materialize import verify_bundle
            doc = verify_bundle(args.scenario)
            result = {"scenario_id": doc["scenario_id"], "revision": doc["revision"], "deliverables": doc["request"]["deliverables"],
                      "anchor_count": len(doc["anchor_sources"]), "companion_count": len(doc["companions"]), "criterion_count": len(doc["rubric"]["items"]),
                      "integrity": "verified", "semantic_acceptance": "Not inferred from integrity"}
        elif args.command == "stats":
            from .workflow import aggregate_usage
            result = aggregate_usage(args.root)
        print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
        return 0
    except (ValueError, OSError, KeyError, IndexError, TypeError, ImportError) as exc:
        # Provider failures use redacted errors. Never print arbitrary tracebacks or secret-bearing configs.
        print("Error: " + str(exc) + "\nUse --help for the command's required arguments. Existing checkpoints are preserved.", file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
