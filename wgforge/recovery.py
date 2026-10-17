"""Explicit offline fork of a read-only solver prefix after one HTTP 504.

This is not ordinary resume or an automatic retry. The failed receipt remains
charged, the original run is immutable, and the fork records its provenance.
"""
from __future__ import annotations

import copy
from pathlib import Path

from .sandbox import RuntimeErrorBase, Sandbox, SYSTEM_PROMPT, canonical, digest, file_hash, tree_hashes, atomic_json, reject_symlink_ancestry


def inherited_requests(state, campaign_root=None):
    """Validate immutable ancestry before acknowledging or deduplicating receipts."""
    recovery = state.get("gateway_recovery")
    if not recovery:
        return 0
    from . import runtime as rt
    if not isinstance(recovery, dict) or recovery.get("version") != 1 or state.get("role") != "solver":
        raise RuntimeErrorBase("Invalid gateway recovery provenance.")
    source = Path(recovery["source_checkpoint"])
    root = Path(recovery["campaign_root"])
    reject_symlink_ancestry(source)
    reject_symlink_ancestry(root)
    if (not source.is_absolute() or not root.is_absolute() or ".." in source.parts or ".." in root.parts or not source.is_relative_to(root)
            or (campaign_root is not None and root != Path(campaign_root).absolute())):
        raise RuntimeErrorBase("Recovery ancestry must share the same campaign budget root.")
    if not source.is_file() or file_hash(source) != recovery["source_checkpoint_sha256"]:
        raise RuntimeErrorBase("Recovery source checkpoint changed or is missing.")
    parent = rt._read_checkpoint(source)
    receipts = parent.get("requests", [])
    count = len(receipts)
    if (parent.get("gateway_recovery") or parent.get("role") != "solver" or parent.get("completed")
            or count < 2 or recovery.get("inherited_request_count") != count
            or state.get("requests", [])[:count] != receipts
            or any(r.get("status") != "succeeded" for r in receipts[:-1])
            or receipts[-1].get("status") != "uncertain" or receipts[-1].get("http_status") != 504
            or recovery.get("acknowledged_request_index") != count - 1):
        raise RuntimeErrorBase("Recovery must preserve every receipt, including the single HTTP 504 reservation.")
    if any(receipt.get("index") != index for index, receipt in enumerate(receipts)):
        raise RuntimeErrorBase("Recovery receipt indices are invalid.")
    return count


def prepare_gateway_recovery(public_dir, source_run, run_dir, config, campaign_root):
    """Prepare, without HTTP, a same-task/same-settings solver fork using streaming.

    Restricted to a fully consumed, read-only prefix and a final HTTP 504. It
    cannot migrate generated outputs, interrupted tools, or a changed task/model.
    """
    from . import runtime as rt
    public, source, out, root = map(lambda p: Path(p).absolute(), (public_dir, source_run, run_dir, campaign_root))
    for path in (public, source, out, root):
        reject_symlink_ancestry(path)
        if ".." in path.parts:
            raise RuntimeErrorBase("Recovery paths must not contain parent traversal.")
    if (not source.is_relative_to(root) or not out.is_relative_to(root)
            or out.is_relative_to(source) or source.is_relative_to(out)):
        raise RuntimeErrorBase("Recovery requires disjoint run directories inside the same campaign root.")
    rt.validate_config(config)
    if config.get("stream") is not True or config.get("code", {}).get("enable_code"):
        raise RuntimeErrorBase("Gateway recovery requires streaming and the existing read-only prefix.")
    if out.exists() and {p.name for p in out.iterdir()} - {"role_config.json"}:
        raise RuntimeErrorBase("Recovery output already contains work; use its prepared checkpoint or a new directory.")
    with rt._run_lock(source), rt._run_lock(out):
        source_checkpoint = source / "checkpoint.json"
        parent = rt._read_checkpoint(source_checkpoint)
        old_config = rt._read_checkpoint(source / "role_config.json")["effective_config"]
        report = rt._read_checkpoint(source / "run.json")
        trajectory = rt._read_checkpoint(source / "trajectory.json")
        candidate = copy.deepcopy(config)
        candidate.pop("stream", None)
        if (old_config.get("stream", False) or candidate != old_config
                or rt.config_fingerprint(old_config) != parent.get("config_digest")):
            raise RuntimeErrorBase("Gateway recovery may change only stream=false to stream=true; model, budgets and other settings must match.")
        task, hashes = rt._public_task(public, config)
        prompt = SYSTEM_PROMPT + " Your isolated role is solver. All references are under input/anchors/ and input/companions/."
        broker = Sandbox(out / "workspace", config.get("limits"), config.get("code"))
        if (parent.get("public_digest") != digest(hashes) or parent.get("task_id") != task.get("task_id", public.parent.name)
                or tree_hashes(source / "workspace" / "input") != hashes
                or parent.get("output_hashes") != {} or tree_hashes(source / "workspace" / "output")
                or parent.get("messages", [])[:2] != [{"role": "system", "content": prompt}, {"role": "user", "content": canonical(task)}]
                or trajectory.get("messages") != parent.get("messages") or trajectory.get("tools") != broker.tools
                or report.get("trajectory_digest") != file_hash(source / "trajectory.json")
                or report.get("config_digest") != parent.get("config_digest")
                or report.get("runtime_digest") != parent.get("runtime_digest")):
            raise RuntimeErrorBase("Recovery requires identical public inputs, prompts, tool contracts and unmodified source evidence.")
        receipts = parent.get("requests", [])
        if parent.get("consumed_requests") != list(range(len(receipts) - 1)) or parent.get("context_compactions"):
            raise RuntimeErrorBase("Recovery requires a fully consumed prefix without context compaction.")
        pending, seen = set(), set()
        for message in parent["messages"][2:]:
            if message.get("role") == "assistant":
                if pending or not message.get("tool_calls"):
                    raise RuntimeErrorBase("Recovery prefix has an incomplete assistant turn.")
                for call in message["tool_calls"]:
                    function, call_id = call["function"], call["id"]
                    cached = parent.get("tool_cache", {}).get(call_id, {})
                    if (call_id in seen or function["name"] not in {"list_files", "read_file", "read_table"}
                            or cached.get("status") != "done" or cached.get("signature") != digest(function)):
                        raise RuntimeErrorBase("Recovery supports only completed read-only tool calls.")
                    pending.add(call_id)
                    seen.add(call_id)
            elif message.get("role") == "tool":
                call_id = message["tool_call_id"]
                if call_id not in pending or message["content"] != canonical(parent["tool_cache"][call_id]["result"]):
                    raise RuntimeErrorBase("Recovery tool result does not match its saved receipt.")
                pending.remove(call_id)
            else:
                raise RuntimeErrorBase("Unexpected recovery transcript message.")
        if pending or seen != set(parent.get("tool_cache", {})):
            raise RuntimeErrorBase("Recovery prefix has pending or untracked tools.")
        state = copy.deepcopy(parent)
        state["gateway_recovery"] = {
            "version": 1, "source_checkpoint": str(source_checkpoint),
            "source_checkpoint_sha256": file_hash(source_checkpoint), "campaign_root": str(root),
            "source_config_digest": parent["config_digest"], "source_runtime_digest": parent["runtime_digest"],
            "source_started_at_epoch": parent["started_at_epoch"], "started_at_epoch": None,
            "inherited_request_count": len(receipts), "acknowledged_request_index": len(receipts) - 1,
            "change": "Explicit fork after HTTP 504; enable streaming; retain unknown request reservation; reset attempt deadline on first start."
        }
        inherited_requests(state, root)
        broker.copy_public(public)
        if tree_hashes(broker.root / "input") != hashes:
            raise RuntimeErrorBase("Public inputs changed while preparing recovery.")
        state.update(config_digest=rt.config_fingerprint(config), runtime_digest=rt._runtime_fingerprint())
        state.pop("error", None)
        atomic_json(out / "checkpoint.json", state)
        return rt._publish_run(out, config, state, broker.tools)
