"""Offline checks for the paper protocol; no credentials or paid endpoints are queried."""
from __future__ import annotations

from copy import deepcopy
import importlib.metadata
import importlib.util
import math
import os
import re
import sys

from .common import ForgeError

PAPER_ROLES = ("researcher", "companion_author", "task_author", "solver", "judge", "attributor", "repairer", "teacher")


def with_task_count(config, count=None):
    result = deepcopy(config)
    if count is not None:
        if type(count) is not int or not 1 <= count <= 100000:
            raise ForgeError("Task count must be an integer between 1 and 100000")
        result.setdefault("planning", {})["count"] = count
    return result


def validate_paper_profile(config):
    from .stirrup_runtime import validate_stirrup_config
    from .runtime import PRIVATE_ROLES, validate_config
    from .sandbox import RuntimeErrorBase
    if config.get("synthesis", {}).get("protocol") != "paper_v1":
        raise ForgeError("Expected synthesis.protocol=paper_v1")
    if config.get("planning", {}).get("adapter") != "paper_occupations":
        raise ForgeError("Paper profile requires the complete 65-occupation playbook adapter")
    if config.get("repair", {}).get("repeated_defect_limit", 3) != 3:
        raise ForgeError("The disclosed same-criterion discard threshold is three rounds")
    if (config.get("repair", {}).get("independent_review", False)
            or config.get("repair", {}).get("independent_repair_review", False)):
        raise ForgeError("An extra repair-review acceptance gate is not part of paper_v1")
    roles = config.get("roles", {})
    if not isinstance(roles, dict):
        raise ForgeError("Paper roles must be an object")
    for name in PAPER_ROLES:
        if name not in roles:
            raise ForgeError("Missing paper role: " + name)
    models, providers = set(), set()
    for name, role in roles.items():
        if not isinstance(role, dict) or role.get("harness") != "stirrup":
            raise ForgeError("Paper role requires official Stirrup tool execution: " + name)
        pair = (role.get("provider"), role.get("model"))
        if pair not in (("anthropic", "claude-opus-5-5"), ("claude_subscription", "claude-opus-5-5"),
                        ("codex_subscription", "gpt-6-astra")):
            raise ForgeError("Use Claude Opus 5.5 with its native API/subscription, or GPT-6 Astra with Codex subscription, across all roles")
        if role.get("provider") == "codex_subscription" and role.get("effort") != "xhigh":
            raise ForgeError("The requested Codex profile requires gpt-6-astra with effort=xhigh for every role")
        if any(key in role for key in ("api_key", "token", "headers", "authorization")):
            raise ForgeError("Use environment variables for credentials, never inline configuration secrets")
        models.add(role["model"])
        providers.add(role["provider"])
        try:
            validate_config(role, private=name in PRIVATE_ROLES, offline=True)
        except RuntimeErrorBase as exc:
            raise ForgeError("Invalid paper role " + name + ": " + str(exc)) from None
        settings = validate_stirrup_config(role)
        sandbox = settings["sandbox"]
        field = "image" if sandbox["backend"] == "docker" else "template"
        if field + "_env" in sandbox:
            _environment_name(sandbox[field + "_env"], field + "_env")
        if not isinstance(settings.get("web_enabled", False), bool):
            raise ForgeError("Stirrup web_enabled must be a boolean")
        sources = settings.get("sources", {})
        if settings.get("web_enabled") and sources.get("adapter") == "serpapi":
            _environment_name(sources.get("serpapi_key_env", "SERPAPI_API_KEY"), "serpapi_key_env")
    if len(models) != 1:
        raise ForgeError("The requested experiment uses one model across all roles")
    if len(providers) != 1:
        raise ForgeError("Use one model transport across paper roles; subscription runs cannot silently use an API fallback")
    for key in ("max_cost_usd", "max_requests", "max_tokens"):
        value = config.get("campaign_budget", {}).get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ForgeError("An explicit positive campaign budget is required: " + key)
        if key in {"max_requests", "max_tokens"} and not isinstance(value, int):
            raise ForgeError("Campaign request and token limits must be integers: " + key)
    return config


def _environment_name(value, field):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ForgeError(field + " must name one environment variable")
    return value


def preflight(config):
    from .planning import plan_tasks
    from .stirrup_runtime import STIRRUP_VERSION, STIRRUP_REVISION
    validate_paper_profile(config)
    plans = plan_tasks(config)
    counts = {}
    for plan in plans:
        counts[plan["profession"]] = counts.get(plan["profession"], 0) + 1
    pending = []
    if sys.version_info < (3, 12):
        pending.append("Python 3.12+ is required by the pinned Stirrup package")
    try:
        installed = importlib.metadata.version("stirrup")
        if installed != STIRRUP_VERSION:
            pending.append("Install the pinned Stirrup optional dependency")
    except importlib.metadata.PackageNotFoundError:
        installed = None
        pending.append("Install the pinned Stirrup optional dependency")
    def key_available(variable):
        value = os.environ.get(variable, "")
        return bool(value and "\n" not in value and "\r" not in value)

    first_role = next(iter(config["roles"].values()))
    transport = first_role["provider"]
    subscription = transport in {"claude_subscription", "codex_subscription"}
    cli_name = "Codex CLI" if transport == "codex_subscription" else "Claude Code"
    checked_cli = set()
    for role in config["roles"].values():
        if role.get("provider") in {"claude_subscription", "codex_subscription"}:
            if role["provider"] == "codex_subscription":
                from .codex_subscription import subscription_preflight
                default_executable = "codex"
            else:
                from .claude_subscription import subscription_preflight
                default_executable = "claude"
            executable = role.get("subscription", {}).get("executable", default_executable)
            identity = (role["provider"], executable)
            if identity not in checked_cli:
                pending.extend(subscription_preflight(role))
                checked_cli.add(identity)
        else:
            variable = role["api_key_env"]
            if not key_available(variable):
                pending.append("Set a valid " + variable + " in the execution terminal")
        settings = role["stirrup"]
        source_config = settings.get("sources", {})
        if settings.get("web_enabled") and source_config.get("adapter") == "serpapi":
            variable = source_config.get("serpapi_key_env", "SERPAPI_API_KEY")
            if not key_available(variable):
                pending.append("Set a valid " + variable + " for the disclosed web-search stage")
        sandbox = settings["sandbox"]
        field = "image" if sandbox["backend"] == "docker" else "template"
        variable = sandbox.get(field + "_env")
        value = sandbox.get(field) or (os.environ.get(variable, "") if variable else "")
        if not isinstance(value, str) or not value.strip() or "REPLACE" in value or "PLACEHOLDER" in value or "\n" in value or "\r" in value:
            pending.append("Configure sandbox " + (variable or field))
        dependency = "docker" if sandbox["backend"] == "docker" else "e2b_code_interpreter"
        if importlib.util.find_spec(dependency) is None:
            pending.append("Install the optional " + dependency + " Python dependency")
        if sandbox["backend"] == "e2b" and not key_available("E2B_API_KEY"):
            pending.append("Set a valid E2B_API_KEY for the isolated E2B sandbox")
    pending = list(dict.fromkeys(pending))
    result = {"protocol": "paper_v1", "code_configuration_valid": True,
            "requested_accepted_tasks": len(plans), "covered_occupations_in_this_plan": len(counts),
            "occupation_counts": counts, "model": first_role["model"], "roles": list(PAPER_ROLES),
            "context_tokens": 256000, "compaction_trigger": 0.7,
            "stirrup_version_installed": installed, "stirrup_revision_required": STIRRUP_REVISION,
            "runtime_setup_pending": pending, "ready_for_operator_live_check": not pending,
            "paid_calls_made": 0, "tasks_generated": 0, "containers_started": 0,
            "runtime_validation": "Package/key presence only; API access, daemon/image and real runs are not tested",
            "user_requested_differences": [("GPT-6 Astra (xhigh)" if transport == "codex_subscription" else "Claude Opus 5.5") + " replaces GLM-5.2",
                                            "Adjustable task count; this plan requests " + str(len(plans)) + " accepted tasks instead of the paper's 20000"],
            "undisclosed_details": ["Original occupation playbook wording and sampling seed",
                                    "Original Stirrup revision and complete sandbox image",
                                    "Original SFT filtering, serialization, and loss masks"]}
    if subscription:
        result.update(model_transport="codex_cli_subscription_bridge" if transport == "codex_subscription" else "claude_code_subscription_bridge",
                      outer_harness="official_stirrup", native_message_replay=False,
                      context_control_scope="Outer Stirrup uses 256K/70%; " + cli_name + " has additional internal context management",
                      cost_basis="Estimated API-equivalent workload only; not subscription billing or remaining quota",
                      request_limit_scope="CLI invocations; internal model calls and retries are separately observed where exposed",
                      runtime_validation="Local dependency/login presence only; model access, remaining quota, daemon/image and real tasks are not tested")
        result["user_requested_differences"].append(
            "Official " + cli_name + " subscription transport; serialized history and structured action proposals replace native API message/tool replay")
        if first_role.get("effort") is not None:
            result["effort"] = first_role["effort"]
    return result
