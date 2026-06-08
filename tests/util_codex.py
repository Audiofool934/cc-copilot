"""Fixtures for building synthetic Codex rollout transcripts in tests."""

import json
import os
import tempfile

from tests.util import iso


def envelope(typ, payload, ago=0):
    return {"timestamp": iso(ago), "type": typ, "payload": payload}


def session_meta(cwd="/test/proj", sid="019ea000-0000-7000-8000-000000000abc",
                 model="openai", big_instructions=True, ago=0):
    payload = {"id": sid, "cwd": cwd, "model_provider": model,
               "cli_version": "0.137.0", "originator": "codex-tui"}
    if big_instructions:
        # Codex embeds the full system prompt here; routinely > 16 KB, which is
        # exactly the case that breaks a fixed byte-window head read.
        payload["base_instructions"] = {"text": "You are Codex. " + ("x" * 20000)}
    return envelope("session_meta", payload, ago)


def turn_context(cwd="/test/proj", model="gpt-5", approval="never", ago=0):
    return envelope("turn_context",
                    {"cwd": cwd, "model": model, "approval_policy": approval}, ago)


def umsg(text, ago=0):
    return envelope("response_item",
                    {"type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": text}]}, ago)


def amsg(text, ago=0):
    return envelope("response_item",
                    {"type": "message", "role": "assistant",
                     "content": [{"type": "output_text", "text": text}]}, ago)


def dev(text, ago=0):
    return envelope("response_item",
                    {"type": "message", "role": "developer",
                     "content": [{"type": "input_text", "text": text}]}, ago)


def reasoning(text, ago=0):
    return envelope("response_item",
                    {"type": "reasoning",
                     "summary": [{"type": "summary_text", "text": text}],
                     "encrypted_content": "opaque"}, ago)


def exec_call(cmd, call_id, ago=0, workdir="/test/proj"):
    return envelope("response_item",
                    {"type": "function_call", "name": "exec_command",
                     "call_id": call_id,
                     "arguments": json.dumps({"cmd": cmd, "workdir": workdir})}, ago)


def exec_out(call_id, exit_code=0, body="done", ago=0):
    out = f"Wall time: 0.1 seconds\nProcess exited with code {exit_code}\nOutput:\n{body}"
    return envelope("response_item",
                    {"type": "function_call_output", "call_id": call_id, "output": out}, ago)


def patch_call(files, call_id, ago=0):
    """``files`` = [(verb, path)] with verb in Add/Update/Delete."""
    lines = ["*** Begin Patch"]
    for verb, path in files:
        lines.append(f"*** {verb} File: {path}")
        lines.append("+content" if verb != "Delete" else "-content")
    lines.append("*** End Patch")
    return envelope("response_item",
                    {"type": "custom_tool_call", "name": "apply_patch",
                     "call_id": call_id, "input": "\n".join(lines)}, ago)


def patch_out(call_id, exit_code=0, ago=0):
    out = f"Exit code: {exit_code}\nOutput:\nSuccess. Updated the following files:"
    return envelope("response_item",
                    {"type": "custom_tool_call_output", "call_id": call_id, "output": out}, ago)


def update_plan(steps, call_id="call_plan", ago=0):
    """``steps`` = [(text, status)]."""
    plan = [{"step": t, "status": s} for t, s in steps]
    return envelope("response_item",
                    {"type": "function_call", "name": "update_plan", "call_id": call_id,
                     "arguments": json.dumps({"plan": plan, "explanation": "go"})}, ago)


def token_count(ago=0):
    """An event_msg that duplicates content — the parser must ignore it."""
    return envelope("event_msg", {"type": "token_count", "total": 1234}, ago)


def write_rollout(events, dir=None, name=None):
    """Write events to a rollout-*.jsonl file and return its path."""
    sid = "019ea000-0000-7000-8000-000000000abc"
    fname = name or f"rollout-2026-06-07T10-00-00-{sid}.jsonl"
    d = dir or tempfile.mkdtemp(prefix="cccodex-")
    p = os.path.join(d, fname)
    with open(p, "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return p
