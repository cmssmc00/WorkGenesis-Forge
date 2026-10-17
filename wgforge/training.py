"""Model-template-preserving, assistant-only token labeling for SFT adapters."""
from __future__ import annotations
from copy import deepcopy
import json
from .common import ForgeError


def assistant_spans(tokenizer, messages, tools=None):
    """Find generated assistant suffixes only when the template is prefix-consistent.

    We never invent token IDs or assume an undocumented Qwen chat format. Fail
    closed for templates that rewrite prior turns as more messages are added.
    """
    messages = deepcopy(messages)
    # OpenAI transport uses JSON-string arguments; Qwen templates require objects.
    # This is rendering normalization only: the exported canonical trace is untouched.
    for message in messages:
        for call in message.get("tool_calls", []):
            function = call.get("function", {})
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except ValueError:
                    raise ForgeError("Malformed tool-call arguments in training trace") from None
            if not isinstance(arguments, dict):
                raise ForgeError("Tool-call arguments must decode to an object")
            function["arguments"] = arguments
    kwargs = {"tokenize": False}
    if tools:
        kwargs["tools"] = tools
    full = tokenizer.apply_chat_template(messages, add_generation_prompt=False, **kwargs)
    spans = []
    for index, message in enumerate(messages):
        if message.get("role") != "assistant":
            continue
        before = tokenizer.apply_chat_template(messages[:index], add_generation_prompt=True, **kwargs)
        through = tokenizer.apply_chat_template(messages[:index+1], add_generation_prompt=False, **kwargs)
        if not through.startswith(before) or not full.startswith(through):
            raise ForgeError("Model chat template is not prefix-consistent; supply an independently verified assistant-mask adapter")
        if len(through) > len(before):
            spans.append((len(before), len(through)))
    if not spans:
        raise ForgeError("No assistant-generated token spans")
    return full, spans


def encode_example(tokenizer, record, max_length):
    if record.get("metadata", {}).get("mode") != "live_qualified":
        raise ForgeError("Only qualified real trajectories may be prepared for training")
    text, spans = assistant_spans(tokenizer, record["messages"], record.get("tools"))
    encoded = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True, truncation=False)
    ids = encoded["input_ids"]
    if len(ids) > max_length:
        raise ForgeError("Trajectory exceeds configured context; no silent truncation of tool traces")
    offsets = encoded["offset_mapping"]
    labels = [token if end > start and any(start >= left and end <= right for left, right in spans) else -100
              for token, (start, end) in zip(ids, offsets)]
    if not any(label != -100 for label in labels):
        raise ForgeError("Assistant token mask is empty")
    return {"input_ids": ids, "attention_mask": [1] * len(ids), "labels": labels}


def pack_examples(examples, block_size):
    """Concatenate complete examples; never split a trajectory or lose its tail."""
    packed, current = [], {key: [] for key in ("input_ids", "attention_mask", "labels")}
    for example in examples:
        if len(example["input_ids"]) > block_size:
            raise ForgeError("Example exceeds packing block")
        if current["input_ids"] and len(current["input_ids"]) + len(example["input_ids"]) > block_size:
            packed.append(current)
            current = {key: [] for key in current}
        for key in current:
            current[key].extend(example[key])
    if current["input_ids"]:
        packed.append(current)
    return packed
