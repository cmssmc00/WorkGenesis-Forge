"""Native Anthropic Messages wire format; no SDK and no network side effects.

The runtime keeps a common tool transcript and the original provider blocks.
In particular signed thinking blocks are returned unmodified on later turns.
Reference: https://platform.claude.com/docs/en/build-with-claude/streaming
"""
from __future__ import annotations

import base64
import copy
import hashlib
import http.client
import json
import re
import time

from .sandbox import canonical
from .streaming import StreamError


class AnthropicProtocolError(ValueError):
    """Invalid local or provider data; messages never include provider text."""


def _integer(value, label):
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise AnthropicProtocolError("Invalid Anthropic " + label + ".")
    return value


def _content(value):
    """Translate text and embedded images, without fetching any image URL."""
    if isinstance(value, str):
        return [{"type": "text", "text": value}] if value else []
    if value is None:
        return []
    if not isinstance(value, list):
        raise AnthropicProtocolError("Message content must be text or a content-block list.")
    result = []
    for part in value:
        if not isinstance(part, dict):
            raise AnthropicProtocolError("Invalid message content block.")
        if part.get("type") == "text" and isinstance(part.get("text"), str):
            result.append({"type": "text", "text": part["text"]})
        elif part.get("type") == "image_url":
            source = part.get("image_url", {})
            url = source.get("url") if isinstance(source, dict) else None
            match = re.fullmatch(r"data:(image/(?:png|jpeg|gif|webp));base64,([A-Za-z0-9+/=]+)", url or "")
            if not match:
                raise AnthropicProtocolError("Images must be embedded base64 PNG, JPEG, GIF or WebP data.")
            try:
                base64.b64decode(match[2], validate=True)
            except ValueError:
                raise AnthropicProtocolError("Malformed image base64 data.") from None
            result.append({"type": "image", "source": {"type": "base64", "media_type": match[1], "data": match[2]}})
        elif part.get("type") == "image":
            source = part.get("source", {})
            if (not isinstance(source, dict) or source.get("type") != "base64"
                    or source.get("media_type") not in {"image/png", "image/jpeg", "image/gif", "image/webp"}
                    or not isinstance(source.get("data"), str)):
                raise AnthropicProtocolError("Only embedded base64 image sources are supported.")
            try:
                base64.b64decode(source["data"], validate=True)
            except ValueError:
                raise AnthropicProtocolError("Malformed image base64 data.") from None
            result.append(copy.deepcopy(part))
        else:
            raise AnthropicProtocolError("Unsupported input content block.")
    return result


def assistant_message(blocks):
    """Expose text/tools to the shared loop and retain native content losslessly."""
    if not isinstance(blocks, list):
        raise AnthropicProtocolError("Anthropic content must be a list.")
    texts, calls = [], []
    for block in blocks:
        if not isinstance(block, dict):
            raise AnthropicProtocolError("Invalid Anthropic content block.")
        kind = block.get("type")
        if kind == "text":
            if not isinstance(block.get("text"), str):
                raise AnthropicProtocolError("Invalid Anthropic text block.")
            texts.append(block["text"])
        elif kind == "tool_use":
            if (not isinstance(block.get("id"), str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", block["id"])
                    or not isinstance(block.get("name"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", block["name"])
                    or not isinstance(block.get("input"), dict)):
                raise AnthropicProtocolError("Invalid Anthropic tool-use block.")
            calls.append({"id": block["id"], "type": "function", "function": {
                "name": block["name"], "arguments": canonical(block["input"])}})
        elif kind == "thinking":
            if not isinstance(block.get("thinking"), str) or not isinstance(block.get("signature"), str):
                raise AnthropicProtocolError("Invalid Anthropic thinking block.")
        elif kind == "redacted_thinking":
            if not isinstance(block.get("data"), str):
                raise AnthropicProtocolError("Invalid Anthropic redacted-thinking block.")
        else:
            # No server tools or fallback models were requested. Do not silently
            # erase an unsupported block and pretend the result is complete.
            raise AnthropicProtocolError("Unsupported Anthropic response block.")
    if len(calls) > 16 or len({call["id"] for call in calls}) != len(calls):
        raise AnthropicProtocolError("Duplicate or excessive Anthropic tool calls.")
    result = {"role": "assistant", "content": "".join(texts) if texts else None,
              "provider_content": copy.deepcopy(blocks)}
    if calls:
        result["tool_calls"] = calls
    return result


def request_payload(config, messages, tools=None, json_mode=False):
    system, turns = [], []
    for message in messages:
        role = message.get("role")
        if role == "system":
            if turns:
                raise AnthropicProtocolError("System instructions must precede conversation messages.")
            blocks = _content(message.get("content"))
            if any(block["type"] != "text" for block in blocks):
                raise AnthropicProtocolError("System content must contain only text.")
            system.extend(blocks)
            continue
        if role == "tool":
            block = {"type": "tool_result", "tool_use_id": message.get("tool_call_id"),
                     "content": _content(message.get("content"))}
            if not isinstance(block["tool_use_id"], str) or not block["tool_use_id"]:
                raise AnthropicProtocolError("Tool results require a tool-call ID.")
            if message.get("is_error") is True:
                block["is_error"] = True
            # Anthropic requires all results from one assistant turn together.
            if turns and turns[-1]["role"] == "user" and all(p.get("type") == "tool_result" for p in turns[-1]["content"]):
                turns[-1]["content"].append(block)
            else:
                turns.append({"role": "user", "content": [block]})
        elif role == "assistant":
            if "provider_content" in message:
                normalized = assistant_message(message["provider_content"])
                if (normalized["content"] != message.get("content")
                        or normalized.get("tool_calls", []) != message.get("tool_calls", [])):
                    raise AnthropicProtocolError("Native assistant blocks disagree with the tool transcript.")
                blocks = copy.deepcopy(message["provider_content"])
            else:
                blocks = _content(message.get("content"))
                for call in message.get("tool_calls", []):
                    try:
                        arguments = json.loads(call["function"]["arguments"])
                        if not isinstance(arguments, dict):
                            raise ValueError()
                        blocks.append({"type": "tool_use", "id": call["id"],
                                       "name": call["function"]["name"], "input": arguments})
                    except (KeyError, ValueError, TypeError):
                        raise AnthropicProtocolError("Malformed canonical tool call.") from None
            turns.append({"role": "assistant", "content": blocks})
        elif role == "user":
            turns.append({"role": "user", "content": _content(message.get("content"))})
        else:
            raise AnthropicProtocolError("Unsupported conversation role.")
    if not turns or turns[0]["role"] != "user":
        raise AnthropicProtocolError("Anthropic conversations must begin with a user message.")
    result = {"model": config["model"], "messages": turns,
              "max_tokens": config["budget"]["max_output_tokens"], "stream": config.get("stream", False),
              "thinking": {"type": "adaptive", "display": "omitted"}}
    if system:
        result["system"] = system
    if config.get("effort"):
        result["output_config"] = {"effort": config["effort"]}
    if tools:
        result["tools"] = []
        for tool in tools:
            function = tool.get("function", {})
            if tool.get("type") != "function" or not isinstance(function, dict):
                raise AnthropicProtocolError("Only explicit client function tools are supported.")
            result["tools"].append({"name": function["name"], "description": function.get("description", ""),
                                    "input_schema": copy.deepcopy(function["parameters"])})
        result["tool_choice"] = {"type": "auto", "disable_parallel_tool_use": True}
    # JSON roles already carry a strict object-only instruction. Do not force a
    # tool (unsupported by Opus 5.5) or claim json_object response_format exists.
    if config.get("prompt_cache"):
        result["cache_control"] = {"type": "ephemeral", "ttl": config["prompt_cache"]["ttl"]}
    return result


def usage_counts(usage):
    if not isinstance(usage, dict):
        raise AnthropicProtocolError("Anthropic usage must be an object.")
    counts = {key: _integer(usage.get(key), key) for key in ("input_tokens", "output_tokens")}
    for key in ("cache_creation_input_tokens", "cache_read_input_tokens"):
        counts[key] = _integer(usage.get(key, 0), key)
    creation = usage.get("cache_creation")
    if creation is not None:
        if not isinstance(creation, dict):
            raise AnthropicProtocolError("Invalid cache creation usage.")
        counts["cache_creation"] = {key: _integer(creation.get(key, 0), key)
            for key in ("ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens")}
        if sum(counts["cache_creation"].values()) != counts["cache_creation_input_tokens"]:
            raise AnthropicProtocolError("Cache creation breakdown disagrees with total usage.")
    return counts


def normalize_response(result, expected_model):
    if not isinstance(result, dict) or result.get("type") != "message" or result.get("role") != "assistant":
        raise AnthropicProtocolError("Provider returned no Anthropic assistant message.")
    if result.get("model") != expected_model:
        raise AnthropicProtocolError("Provider returned a different model; model substitution is forbidden.")
    if not isinstance(result.get("id"), str) or not result["id"]:
        raise AnthropicProtocolError("Provider returned no message ID.")
    counts = usage_counts(result.get("usage"))
    reason = result.get("stop_reason")
    reasons = {"end_turn": "stop", "stop_sequence": "stop", "tool_use": "tool_calls", "max_tokens": "length",
               "model_context_window_exceeded": "length", "refusal": "refusal", "pause_turn": "pause_turn"}
    if reason not in reasons:
        raise AnthropicProtocolError("Provider returned no supported terminal stop reason.")
    message = assistant_message(result.get("content"))
    if reason == "tool_use" and not message.get("tool_calls"):
        raise AnthropicProtocolError("Tool-use stop reason has no tool-use block.")
    if message.get("tool_calls") and reason not in {"tool_use", "max_tokens", "model_context_window_exceeded", "refusal"}:
        raise AnthropicProtocolError("Tool-use blocks conflict with the provider stop reason.")
    prompt = counts["input_tokens"] + counts["cache_creation_input_tokens"] + counts["cache_read_input_tokens"]
    return {"id": result["id"], "choices": [{"message": message, "finish_reason": reasons[reason]}],
            "usage": {"prompt_tokens": prompt, "completion_tokens": counts["output_tokens"]},
            "provider_usage": counts, "provider_stop_reason": reason, "provider_model": result["model"]}


def input_rate_bound(budget):
    base = budget["input_usd_per_million"]
    return max(base, budget.get("cache_read_usd_per_million", base),
               budget.get("cache_write_5m_usd_per_million", base * 1.25),
               budget.get("cache_write_1h_usd_per_million", base * 2))


def input_bound(payload):
    """Byte-bound text plus a conservative per-image visual token allowance.

    Base64 is wire encoding, not text seen by the model. Anthropic documents a
    4,784 visual-token ceiling for high-resolution images, including Opus 5.5.
    Reserve 256 extra per image for framing, plus the usual transcript overhead.
    https://platform.claude.com/docs/en/build-with-claude/vision
    """
    images = 0
    def without_image_data(value):
        nonlocal images
        if isinstance(value, list):
            return [without_image_data(item) for item in value]
        if isinstance(value, dict):
            if value.get("type") == "image" and isinstance(value.get("source"), dict) and value["source"].get("type") == "base64":
                images += 1
                return {**value, "source": {**value["source"], "data": ""}}
            return {key: without_image_data(item) for key, item in value.items()}
        return value
    text_only = without_image_data(payload)
    return len(canonical(text_only).encode()) + 1024 + 32 * len(payload["messages"]) + images * (4784 + 256)


def usage_cost(counts, budget):
    """Creation/read/uncached tokens are disjoint; output includes thinking."""
    base = budget["input_usd_per_million"]
    cost = counts["input_tokens"] * base + counts["output_tokens"] * budget["output_usd_per_million"]
    cost += counts["cache_read_input_tokens"] * budget.get("cache_read_usd_per_million", base)
    creation = counts.get("cache_creation")
    if creation is None:
        cost += counts["cache_creation_input_tokens"] * input_rate_bound(budget)
    else:
        cost += creation["ephemeral_5m_input_tokens"] * budget.get("cache_write_5m_usd_per_million", base * 1.25)
        cost += creation["ephemeral_1h_input_tokens"] * budget.get("cache_write_1h_usd_per_million", base * 2)
    return cost / 1_000_000


def read_message(response, max_bytes, deadline, progress=None, max_message_bytes=4_000_000):
    """Decode native SSE only after complete blocks, final usage and message_stop.

    No partial tools execute, and no truncated/error stream is automatically
    replayed. The shared runtime retains the full uncertain reservation.
    """
    message, blocks, partial_json, open_blocks = None, {}, {}, set()
    event, event_name, received = [], None, 0
    checksum, chunks, stopped, final_usage = hashlib.sha256(), 0, False, False
    last_saved, started = None, time.monotonic()

    def snapshot():
        result = copy.deepcopy(message) if message else {}
        result["content"] = [copy.deepcopy(blocks[i]) for i in sorted(blocks)]
        return result

    def publish(termination, force=False):
        nonlocal last_saved
        if progress is None:
            return
        now = time.monotonic()
        if not force and last_saved is not None and now - last_saved < 5:
            return
        partial = snapshot()
        bounded = len(canonical(partial).encode()) <= max_message_bytes
        progress({"received_bytes": received, "data_events": chunks, "wire_sha256": checksum.hexdigest(),
                  "provider_stop_reason": (message or {}).get("stop_reason"), "final_usage_received": final_usage,
                  "message_stop": stopped, "termination": termination, "elapsed_seconds": round(now - started, 3),
                  "partial_message_saved": bounded}, partial if bounded else None)
        last_saved = now

    def consume(data, name):
        nonlocal message, chunks, stopped, final_usage
        chunks += 1
        try:
            item = json.loads(data)
            canonical(item)
        except (ValueError, TypeError):
            raise StreamError("Malformed Anthropic stream JSON; partial response rejected.") from None
        if not isinstance(item, dict) or not isinstance(item.get("type"), str):
            raise StreamError("Malformed Anthropic stream event.")
        kind = item["type"]
        if name is not None and name != kind:
            raise StreamError("Anthropic event name disagrees with its data.")
        if kind == "error":
            raise StreamError("Provider reported an Anthropic streaming error; completion is uncertain.")
        if kind == "ping":
            return
        if kind == "message_start":
            incoming = item.get("message")
            if message is not None or not isinstance(incoming, dict) or incoming.get("content") != []:
                raise StreamError("Invalid Anthropic message_start event.")
            message = copy.deepcopy(incoming)
            return
        if kind not in {"content_block_start", "content_block_delta", "content_block_stop", "message_delta", "message_stop"}:
            return  # Forward-compatible informational events have no executable content.
        if message is None:
            raise StreamError("Anthropic stream event arrived before message_start.")
        if kind.startswith("content_block_"):
            index = item.get("index")
            if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < 1024:
                raise StreamError("Invalid Anthropic content block index.")
            if final_usage:
                raise StreamError("Content block arrived after final Anthropic usage.")
            if kind == "content_block_start":
                block = item.get("content_block")
                if index != len(blocks) or not isinstance(block, dict):
                    raise StreamError("Duplicate or unordered Anthropic content block.")
                blocks[index] = copy.deepcopy(block)
                open_blocks.add(index)
            elif index not in open_blocks:
                raise StreamError("Anthropic delta references a closed or missing content block.")
            elif kind == "content_block_delta":
                delta = item.get("delta")
                if not isinstance(delta, dict):
                    raise StreamError("Malformed Anthropic content delta.")
                key = {"text_delta": "text", "thinking_delta": "thinking", "signature_delta": "signature",
                       "input_json_delta": "partial_json"}.get(delta.get("type"))
                block_kind = blocks[index].get("type")
                if (key is None or not isinstance(delta.get(key), str)
                        or (key == "text" and block_kind != "text")
                        or (key in {"thinking", "signature"} and block_kind != "thinking")
                        or (key == "partial_json" and block_kind != "tool_use")):
                    raise StreamError("Unsupported or malformed Anthropic content delta.")
                if key == "partial_json":
                    partial_json[index] = partial_json.get(index, "") + delta[key]
                else:
                    prior = blocks[index].get(key, "")
                    if not isinstance(prior, str):
                        raise StreamError("Invalid Anthropic text block state.")
                    blocks[index][key] = prior + delta[key]
            else:
                if index in partial_json:
                    try:
                        arguments = json.loads(partial_json[index])
                        if not isinstance(arguments, dict):
                            raise ValueError()
                        canonical(arguments)
                    except (ValueError, TypeError):
                        raise StreamError("Anthropic tool input is incomplete; partial tools rejected.") from None
                    blocks[index]["input"] = arguments
                    del partial_json[index]
                open_blocks.remove(index)
        elif kind == "message_delta":
            delta, usage = item.get("delta"), item.get("usage")
            if open_blocks or not isinstance(delta, dict) or not isinstance(usage, dict):
                raise StreamError("Invalid final Anthropic message delta.")
            for key in ("stop_reason", "stop_sequence"):
                if key in delta:
                    old = message.get(key)
                    if old is not None and old != delta[key]:
                        raise StreamError("Conflicting Anthropic stop metadata.")
                    message[key] = delta[key]
            if not isinstance(message.get("usage"), dict):
                raise StreamError("Invalid initial Anthropic usage.")
            # message_delta counts are cumulative, not additive.
            message["usage"].update(copy.deepcopy(usage))
            final_usage = isinstance(usage.get("output_tokens"), int) and not isinstance(usage.get("output_tokens"), bool)
        else:
            if open_blocks or not final_usage or message.get("stop_reason") is None:
                raise StreamError("Anthropic message stopped without terminal metadata and complete blocks.")
            stopped = True
        if len(canonical(snapshot()).encode()) + sum(len(v.encode()) for v in partial_json.values()) > max_message_bytes:
            raise StreamError("Decoded Anthropic response exceeds configured size limit.")

    try:
        while not stopped:
            if time.time() > deadline:
                raise StreamError("Anthropic stream exceeded the run deadline; completion is uncertain.")
            raw = response.readline(max_bytes - received + 1)
            if not raw:
                if event:
                    consume("\n".join(event), event_name)
                break
            received += len(raw)
            checksum.update(raw)
            if received > max_bytes:
                raise StreamError("Anthropic stream exceeds configured size limit.")
            try:
                line = raw.decode("utf-8").rstrip("\r\n")
            except UnicodeError:
                raise StreamError("Invalid UTF-8 in Anthropic stream.") from None
            if not line:
                if event:
                    consume("\n".join(event), event_name)
                event, event_name = [], None
            elif line.startswith("data:"):
                event.append(line[5:].removeprefix(" "))
            elif line.startswith("event:"):
                event_name = line[6:].strip()
            elif not line.startswith((":", "id:", "retry:")):
                raise StreamError("Expected an Anthropic event stream.")
            publish("receiving")
        if not stopped:
            raise StreamError("Anthropic stream ended before message_stop; completion is uncertain.")
        publish("message_stop", force=True)
        return snapshot(), checksum.hexdigest()
    except (OSError, http.client.HTTPException):
        publish("transport_error", force=True)
        raise StreamError("Anthropic stream transport failed; completion is uncertain.") from None
    except StreamError:
        publish("rejected", force=True)
        raise
