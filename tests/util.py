"""Fixtures for building synthetic Claude Code transcripts in tests."""

import datetime
import json
import os
import tempfile

from cccopilot import transcript as T, state as S


def iso(ago=0):
    return (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(seconds=ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def user(text, ago=0, **extra):
    return {"type": "user", "timestamp": iso(ago),
            "message": {"role": "user", "content": text}, **extra}


def user_meta(text, ago=0):
    return user(text, ago, isMeta=True)


def asst(text, ago=0, model="claude"):
    return {"type": "assistant", "timestamp": iso(ago),
            "message": {"role": "assistant", "model": model,
                        "content": [{"type": "text", "text": text}]}}


def tool(name, inp, tid, ago=0):
    return {"type": "assistant", "timestamp": iso(ago),
            "message": {"role": "assistant", "model": "claude",
                        "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}}


def result(tid, content="ok", is_error=False, ago=0):
    return {"type": "user", "timestamp": iso(ago),
            "message": {"role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": tid,
                                     "content": content, "is_error": is_error}]}}


def write(events):
    fd, p = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    with open(p, "w") as f:
        for i, e in enumerate(events):
            e = dict(e)
            if i == 0:
                e.setdefault("sessionId", "testsess")
                e.setdefault("cwd", "/test/proj")
                e.setdefault("gitBranch", "main")
            f.write(json.dumps(e) + "\n")
    return p


def state(events):
    return S.build(T.parse(write(events)))
