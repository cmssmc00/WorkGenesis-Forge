"""Bounded Chat Completions SSE decoding; partial tool calls are never returned."""
from __future__ import annotations

import copy
import hashlib
import http.client
import json
import time

from .sandbox import RuntimeErrorBase, canonical


class StreamError(RuntimeErrorBase):
    pass


def read_completion(response, max_bytes, deadline, progress=None, max_message_bytes=4_000_000):
    """Require a terminal choice and final usage; [DONE] is optional at clean EOF.

    MiniMax's own M3 compatibility tests document missing [DONE]. Transport
    errors or EOF before terminal evidence remain uncertain, never executable.
    The optional callback persists bounded partial state for diagnosis only.
    """
    message = {"role": "assistant", "content": None}
    calls, details = {}, {}
    response_id, finish, usage = "", None, None
    received, checksum, event = 0, hashlib.sha256(), []
    chunks, final_usage, done = 0, False, False
    started, last_saved = time.monotonic(), None

    def snapshot():
        partial = copy.deepcopy(message)
        if calls:
            partial["tool_calls"] = [copy.deepcopy(calls[index]) for index in sorted(calls)]
        if details:
            partial["reasoning_details"] = [copy.deepcopy(details[index]) for index in sorted(details)]
        return partial

    def valid_usage(value):
        if not isinstance(value, dict):
            return False
        values = [value.get(key) for key in ("prompt_tokens", "completion_tokens")]
        return all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in values)

    def publish(termination, force=False):
        nonlocal last_saved
        if progress is None:
            return
        now = time.monotonic()
        if not force and last_saved is not None and now - last_saved < 5:
            return
        partial = snapshot()
        bounded = len(canonical(partial).encode()) <= max_message_bytes
        observed = ({key: usage[key] for key in ("prompt_tokens", "completion_tokens")}
                    if valid_usage(usage) else None)
        progress({"received_bytes": received, "data_events": chunks, "wire_sha256": checksum.hexdigest(),
                  "finish_reason": finish, "final_usage_received": final_usage, "observed_usage": observed,
                  "done_marker": done, "termination": termination, "elapsed_seconds": round(now - started, 3),
                  "partial_message_saved": bounded}, partial if bounded else None)
        last_saved = now

    def fixed(target, key, value):
        if key in target and target[key] != value:
            raise StreamError("Conflicting streaming response metadata.")
        target[key] = copy.deepcopy(value)

    def append(target, key, value):
        if value is None:
            return
        if not isinstance(value, str):
            raise StreamError("Streaming text delta must be a string.")
        target[key] = (target.get(key) or "") + value

    def indexed(value, maximum):
        index = value.get("index")
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < maximum:
            raise StreamError("Invalid streaming delta index.")
        return index

    def consume(data):
        nonlocal response_id, finish, usage, chunks, final_usage
        if data == "[DONE]":
            return True
        chunks += 1
        try:
            chunk = json.loads(data)
            canonical(chunk)
        except (ValueError, TypeError):
            raise StreamError("Malformed streaming JSON; partial response rejected.") from None
        if not isinstance(chunk, dict) or chunk.get("error"):
            raise StreamError("Provider reported a streaming error; partial response rejected.")
        base = chunk.get("base_resp")
        if isinstance(base, dict) and base.get("status_code", 0) != 0:
            raise StreamError("Provider reported a streaming error; partial response rejected.")
        if chunk.get("id"):
            if response_id and chunk["id"] != response_id:
                raise StreamError("Streaming response ID changed.")
            response_id = chunk["id"]
        reported_usage = chunk.get("usage")
        if reported_usage is not None:
            usage = reported_usage  # Cumulative usage, never sum individual chunks.
        choices = chunk.get("choices", [])
        if not isinstance(choices, list) or len(choices) > 1:
            raise StreamError("Expected one streaming choice.")
        for choice in choices:
            if not isinstance(choice, dict) or choice.get("index", 0) != 0:
                raise StreamError("Unexpected streaming choice index.")
            delta = choice.get("delta", {})
            if delta is None:
                delta = {}
            if not isinstance(delta, dict):
                raise StreamError("Invalid streaming message delta.")
            if finish is not None and any(k != "role" and v not in (None, "", [], {}) for k, v in delta.items()):
                raise StreamError("Streaming content arrived after completion.")
            if delta.get("role") not in (None, "assistant"):
                raise StreamError("Expected assistant role in stream.")
            for field in ("content", "reasoning_content"):
                if field in delta:
                    append(message, field, delta[field])
            for part in delta.get("reasoning_details") or []:
                if not isinstance(part, dict):
                    raise StreamError("Invalid streaming reasoning metadata.")
                target = details.setdefault(indexed(part, 4096), {})
                for key, value in part.items():
                    if key in {"text", "signature", "data"}:
                        append(target, key, value)
                    else:
                        fixed(target, key, value)
            for call in delta.get("tool_calls") or []:
                if not isinstance(call, dict):
                    raise StreamError("Invalid streaming tool call.")
                target = calls.setdefault(indexed(call, 16), {"type": "function", "function": {}})
                for key in ("id", "type"):
                    if call.get(key) is not None:
                        fixed(target, key, call[key])
                function = call.get("function", {})
                if not isinstance(function, dict):
                    raise StreamError("Invalid streaming function delta.")
                for key in ("name", "arguments"):
                    if key in function:
                        append(target["function"], key, function[key])
            reason = choice.get("finish_reason")
            if reason is not None:
                if not isinstance(reason, str) or not reason or (finish is not None and finish != reason):
                    raise StreamError("Invalid streaming finish reason.")
                finish = reason
        if reported_usage is not None:
            final_usage = finish is not None and valid_usage(reported_usage)
        return False

    try:
        while not done:
            if time.time() > deadline:
                raise StreamError("Streaming request exceeded the run deadline; partial response rejected.")
            raw = response.readline(max_bytes - received + 1)
            if not raw:
                # Some providers omit both [DONE] and the final blank separator.
                # Parse only complete JSON; no repair of truncated frames.
                if event:
                    done = consume("\n".join(event))
                    event = []
                break
            received += len(raw)
            checksum.update(raw)
            if received > max_bytes:
                raise StreamError("Provider stream exceeds configured size limit.")
            try:
                line = raw.decode("utf-8").rstrip("\r\n")
            except UnicodeError:
                raise StreamError("Invalid UTF-8 in provider stream.") from None
            if not line:
                if event:
                    done = consume("\n".join(event))
                    event = []
            elif line.startswith("data:"):
                event.append(line[5:].removeprefix(" "))
            elif not line.startswith((":", "event:", "id:", "retry:")):
                raise StreamError("Expected a Chat Completions event stream.")
            publish("receiving")
        if finish is None or not final_usage:
            raise StreamError("Provider stream ended without a terminal finish_reason and final usage; completion is uncertain.")
        if not done and finish != "length":
            for call in calls.values():
                try:
                    function = call["function"]
                    arguments = json.loads(function["arguments"])
                    if not isinstance(arguments, dict) or not call.get("id") or not function.get("name"):
                        raise ValueError()
                    canonical(arguments)
                except (KeyError, ValueError, TypeError):
                    raise StreamError("Provider stream closed with incomplete tool-call arguments; completion is uncertain.") from None
        publish("done_marker" if done else "eof_after_finish", force=True)
        return {"id": response_id, "choices": [{"message": snapshot(), "finish_reason": finish}], "usage": usage}, checksum.hexdigest()
    except (OSError, http.client.HTTPException):
        publish("transport_error", force=True)
        raise StreamError("Provider stream transport failed before a clean close; completion is uncertain.") from None
    except StreamError:
        publish("rejected", force=True)
        raise
