"""Three independent campaign processes with a durable, disjoint occupation schedule."""
from __future__ import annotations

import argparse
from copy import deepcopy
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

from .common import ForgeError, digest, now, read_json, write_json
from .file_lock import file_lock
from .paper_profile import validate_paper_profile, with_task_count
from .planning import compact_plans, plan_tasks


def prepare_batch(config, out, count=200):
    """Offline only: plan once, then distribute consecutive slots round-robin."""
    config = with_task_count(deepcopy(config), count)
    config.pop("approve_live", None)
    config["campaign_mode"] = "author_only"
    config.setdefault("trajectory_collection", {})["enabled"] = False
    validate_paper_profile(config)
    plans = plan_tasks(config)
    batch_id = "parallel-" + uuid.uuid4().hex
    config["lineage_id"] = batch_id
    lanes = [{"name": "worker-%d" % (i + 1), "schedule": compact_plans(plans[i::3])}
             for i in range(min(3, len(plans)))]
    packet = {"version": 1, "batch_id": batch_id, "created_at": now(),
              "count": count, "config": config, "workers": lanes,
              "image_id": os.environ.get("WGFORGE_PAPER_IMAGE")}
    packet["digest"] = digest(packet)
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    with file_lock(out / ".batch.lock", blocking=False):
        if any(p.name != ".batch.lock" for p in out.iterdir()):
            raise ForgeError("Batch directory is not empty; use --resume or a new directory")
        write_json(out / "config.json", config)
        write_json(out / "batch_plan.json", packet)
        refresh_status(out, packet)
    return packet


def load_batch(out):
    packet = read_json(Path(out) / "batch_plan.json")
    if packet.get("digest") != digest({k: v for k, v in packet.items() if k != "digest"}):
        raise ForgeError("Saved batch plan/config changed; restore it before resuming")
    if read_json(Path(out) / "config.json") != packet["config"]:
        raise ForgeError("Saved batch config differs from its occupation schedule")
    return packet


def refresh_status(out, packet=None, exits=None, *, persist=True):
    out = Path(out)
    packet = packet or load_batch(out)
    if exits is None:
        previous = out / "batch_status.json"
        exits = {row["name"]: row.get("exit_code") for row in read_json(previous)["workers"]} if previous.exists() else {}
    workers = []
    for lane in packet["workers"]:
        root = out / lane["name"]
        path = root / "campaign.json"
        saved = read_json(path) if path.exists() else {}
        counts = saved.get("counts", {})
        workers.append({"name": lane["name"], "status": saved.get("status", "not_started"),
                        "target_tasks": len(lane["schedule"]["plans"]),
                        "authored_tasks": counts.get("authored_tasks", 0),
                        "accepted_tasks": counts.get("accepted_tasks", 0),
                        "finished_plans": len(saved.get("finished_plan_ids", [])),
                        "completed_trajectories": counts.get("completed_trajectories", 0),
                        "exit_code": exits.get(lane["name"]),
                        "log": lane["name"] + ".log", "directory": lane["name"]})
    complete = all(w["status"] == "completed" for w in workers)
    status = {"batch_id": packet["batch_id"], "updated_at": now(),
              "campaign_mode": packet["config"].get("campaign_mode", "full"),
              "status": "completed" if complete else "incomplete",
              "target_tasks": packet["count"],
              "accepted_tasks": sum(w["accepted_tasks"] for w in workers),
              "authored_tasks": sum(w["authored_tasks"] for w in workers),
              "finished_plans": sum(w["finished_plans"] for w in workers),
              "completed_trajectories": sum(w["completed_trajectories"] for w in workers),
              "workers": workers}
    # Failed integrity verification must never become a successful batch result.
    if any(code not in (None, 0) for code in exits.values()):
        status["status"] = "incomplete"
    if persist:
        write_json(out / "batch_status.json", status)
    return status


def run_worker(out, name):
    from .paper_campaign import run_paper_campaign
    out = Path(out).resolve()
    packet = load_batch(out)
    lane = next((row for row in packet["workers"] if row["name"] == name), None)
    if lane is None:
        raise ForgeError("Unknown worker")
    config = deepcopy(packet["config"])
    config["approve_live"] = True
    if packet.get("image_id"):
        os.environ["WGFORGE_PAPER_IMAGE"] = packet["image_id"]
    state = run_paper_campaign(config, out / name, schedule=lane["schedule"],
                               registry_path=out / "lineage_registry.json")
    print(name + ": " + state["status"], flush=True)
    return 0 if state["status"] == "completed" else 2


def _stop_children(children):
    live = [p for p in children.values() if p.poll() is None]
    for sig, timeout in ((signal.SIGINT, 30), (signal.SIGTERM, 5), (signal.SIGKILL, 5)):
        for process in live:
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + timeout
        for process in live:
            try:
                process.wait(timeout=max(0.01, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                pass
        live = [p for p in live if p.poll() is None]
        if not live:
            break


def run_batch(out):
    out = Path(out).resolve()
    packet = load_batch(out)
    children, streams, exits = {}, [], {}
    with file_lock(out / ".batch.lock", blocking=False):
        try:
            # Start every lane before waiting for any lane. Each has its own
            # campaign budget, sandboxes, receipts, checkpoints and output tree.
            for lane in packet["workers"]:
                name = lane["name"]
                log = (out / (name + ".log")).open("a", encoding="utf-8")
                streams.append(log)
                log.write("\n=== Launch/resume " + now() + " ===\n")
                log.flush()
                children[name] = subprocess.Popen(
                    [sys.executable, "-u", "-m", "wgforge.parallel", "worker", "--out", str(out),
                     "--name", name, "--approve-live"], stdout=log, stderr=subprocess.STDOUT,
                    start_new_session=True, env={**os.environ, "PYTHONUNBUFFERED": "1"})
                first = lane["schedule"]["plans"][0]
                print(f"{name}: {len(lane['schedule']['plans'])} task(s); starts with {first['profession']}; "
                      f"log: {out / (name + '.log')}", flush=True)
            last_report = -float("inf")
            while True:
                for name, process in children.items():
                    code = process.poll()
                    if code is not None and name not in exits:
                        exits[name] = code
                        print(f"{name} exited ({code}); other workers continue.", flush=True)
                status = refresh_status(out, packet, exits)
                if time.monotonic() - last_report >= 30:
                    print(_progress(status), flush=True)
                    last_report = time.monotonic()
                if len(exits) == len(children):
                    return status
                time.sleep(2)
        finally:
            _stop_children(children)
            for log in streams:
                log.close()
            refresh_status(out, packet, {name: p.returncode for name, p in children.items()})


def _progress(status):
    if status["campaign_mode"] == "author_only":
        return f"Authored {status['authored_tasks']}/{status['target_tasks']} | unevaluated | author-only"
    return (f"Completed {status['finished_plans']}/{status['target_tasks']} | "
            f"accepted {status['accepted_tasks']} | teacher traces {status['completed_trajectories']}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    new = commands.add_parser("new")
    new.add_argument("--config", required=True)
    new.add_argument("--count", type=int, default=200, help="Total tasks across all three workers (default: 200)")
    new.add_argument("--out", required=True)
    new.add_argument("--prepare-only", action="store_true", help="Save the schedule without model/search calls")
    new.add_argument("--approve-live", action="store_true")
    resume = commands.add_parser("resume")
    resume.add_argument("--out", required=True)
    resume.add_argument("--approve-live", action="store_true")
    resume.add_argument("--author-only", action="store_true", help="Reject a saved full-pipeline batch")
    for command in ("status", "count"):
        command_parser = commands.add_parser(command)
        command_parser.add_argument("--out", required=True)
        if command == "count":
            command_parser.add_argument("--author-only", action="store_true")
    worker = commands.add_parser("worker", help=argparse.SUPPRESS)
    worker.add_argument("--out", required=True)
    worker.add_argument("--name", required=True)
    worker.add_argument("--approve-live", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command in {"new", "resume", "worker"} and not args.approve_live and not getattr(args, "prepare_only", False):
            raise ForgeError("Live parallel campaigns require --approve-live")
        if getattr(args, "author_only", False) and load_batch(args.out)["config"].get("campaign_mode") != "author_only":
            raise ForgeError("This is a full-pipeline batch. Preserve it and start a new author-only batch.")
        if args.command == "count":
            print(load_batch(args.out)["count"])
            return 0
        if args.command == "status":
            status = refresh_status(args.out, persist=False)
            print(f"Batch: {Path(args.out).resolve()}\n" + _progress(status))
            for row in status["workers"]:
                print(f"{row['name']}: {row['status']} ({row['finished_plans']}/{row['target_tasks']}); {row['log']}")
            return 0
        if args.command == "worker":
            return run_worker(args.out, args.name)
        if args.command == "new":
            prepare_batch(read_json(args.config), args.out, args.count)
            if args.prepare_only:
                print("Schedule saved; no model/search calls started: " + str(Path(args.out).resolve()))
                return 0
        print("Batch directory: " + str(Path(args.out).resolve()), flush=True)
        result = run_batch(args.out)
        print(f"Batch {result['status']}: {result['finished_plans']}/{result['target_tasks']} completed.")
        return 0 if result["status"] == "completed" else 2
    except KeyboardInterrupt:
        print("Stopped. Saved tasks and checkpoints remain in " + str(args.out), file=sys.stderr)
        return 130
    except (ForgeError, OSError) as exc:
        print("Error: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
