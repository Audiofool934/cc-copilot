"""Codex adapter: rollout parsing → normalized records → deterministic state."""

import os
import unittest

from cccopilot import state as S
from cccopilot.sources import codex as CX
from tests import util_codex as U


def _state(events, **meta):
    p = U.write_rollout([U.session_meta(**meta)] + events)
    return CX.CodexSource().parse(p), p


class TestCodexParse(unittest.TestCase):
    def test_messages_and_reasoning_map_to_kinds(self):
        tr, _ = _state([
            U.dev("<permissions instructions> ignore me"),
            U.umsg("fix the failing test", ago=60),
            U.reasoning("I will run pytest", ago=55),
            U.amsg("Done — tests pass.", ago=10),
        ])
        kinds = [r.kind for r in tr.records]
        self.assertEqual(kinds, ["human", "agent_thinking", "agent_text"])
        self.assertEqual(tr.records[0].text, "fix the failing test")
        self.assertEqual(tr.cwd, "/test/proj")
        self.assertTrue(tr.session_id)

    def test_developer_and_injected_user_are_not_human(self):
        tr, _ = _state([
            U.dev("system instructions"),
            U.umsg("<environment_context> cwd=/x"),  # injected wrapper, not a prompt
            U.umsg("real question"),
        ])
        humans = [r for r in tr.records if r.kind == "human"]
        self.assertEqual([h.text for h in humans], ["real question"])

    def test_exec_command_maps_to_bash_command(self):
        tr, _ = _state([
            U.exec_call("pytest -q", "c1", ago=30),
            U.exec_out("c1", exit_code=0, ago=29),
        ])
        st = S.build(tr)
        self.assertEqual(len(st.commands), 1)
        self.assertEqual(st.commands[0].cmd, "pytest -q")
        self.assertEqual(st.commands[0].status, "ok")
        self.assertEqual(st.tool_counts.get("Bash"), 1)

    def test_nonzero_exit_is_failure(self):
        tr, _ = _state([
            U.exec_call("make", "c1", ago=30),
            U.exec_out("c1", exit_code=2, body="error: boom", ago=29),
        ])
        st = S.build(tr)
        self.assertEqual(st.commands[0].status, "fail")
        self.assertEqual(len(st.failures), 1)
        self.assertEqual(st.failures[0].tool, "Bash")

    def test_shell_array_command_extracts_real_command(self):
        # ["bash","-lc","<cmd>"] form
        import json
        ev = U.envelope("response_item", {
            "type": "function_call", "name": "shell", "call_id": "c1",
            "arguments": json.dumps({"command": ["bash", "-lc", "ls -la"]})})
        tr, _ = _state([ev, U.exec_out("c1", 0)])
        st = S.build(tr)
        self.assertEqual(st.commands[0].cmd, "ls -la")

    def test_apply_patch_expands_to_per_file_changes(self):
        tr, _ = _state([
            U.patch_call([("Add", "a.py"), ("Update", "b.py")], "p1", ago=20),
            U.patch_out("p1", exit_code=0, ago=19),
        ])
        st = S.build(tr)
        self.assertEqual(set(st.files), {"a.py", "b.py"})
        self.assertEqual(st.files["a.py"].writes, 1)   # Add → write
        self.assertEqual(st.files["b.py"].edits, 1)    # Update → edit

    def test_failed_patch_credits_no_files(self):
        tr, _ = _state([
            U.patch_call([("Update", "b.py")], "p1", ago=20),
            U.patch_out("p1", exit_code=1, ago=19),
        ])
        st = S.build(tr)
        self.assertEqual(st.files, {})  # a failed edit changed nothing

    def test_empty_patch_pairs_call_and_result(self):
        # an apply_patch with no parseable file ops must still pair its result
        # by the bare call_id — no dangling pending tool, failure recorded
        tr, _ = _state([U.patch_call([], "p1", ago=20),
                        U.patch_out("p1", exit_code=1, ago=19)])
        st = S.build(tr)
        self.assertEqual(len(st.failures), 1)
        self.assertIsNone(st.pending_tool)

    def test_update_plan_maps_to_todos(self):
        tr, _ = _state([
            U.update_plan([("step one", "completed"), ("step two", "in_progress")]),
        ])
        st = S.build(tr)
        self.assertEqual([t["content"] for t in st.todos], ["step one", "step two"])
        self.assertEqual(st.todos[1]["status"], "in_progress")

    def test_event_msg_is_ignored_for_records(self):
        tr, _ = _state([
            U.umsg("hi", ago=10),
            U.token_count(ago=9),     # event_msg — must not double-count
            U.amsg("hello", ago=8),
        ])
        # exactly one human + one agent_text, nothing from the event_msg
        self.assertEqual([r.kind for r in tr.records], ["human", "agent_text"])

    def test_pending_tool_marks_running_status(self):
        # a tool call with no output == mid-execution
        tr, _ = _state([U.umsg("go", ago=5), U.exec_call("sleep 1", "c1", ago=1)])
        st = S.build(tr)
        self.assertIsNotNone(st.pending_tool)
        self.assertIn(st.status, ("running", "stalled"))


class TestCodexHelpers(unittest.TestCase):
    def test_exit_failed(self):
        self.assertFalse(CX._exit_failed("Process exited with code 0"))
        self.assertTrue(CX._exit_failed("Process exited with code 1"))
        self.assertTrue(CX._exit_failed("Exit code: 127\nfoo"))
        self.assertFalse(CX._exit_failed("no code here"))
        # a body that merely echoes the phrase must not be read as a failure
        self.assertFalse(CX._exit_failed(
            "Process exited with code 0\nOutput:\nbuild exited with code 1"))
        # a marker mid-line (not the wrapper header) is ignored
        self.assertFalse(CX._exit_failed("see the log: Exit code: 1 was printed"))

    def test_patch_files(self):
        patch = ("*** Begin Patch\n*** Add File: x.py\n+a\n"
                 "*** Update File: y.py\n+b\n*** Delete File: z.py\n*** End Patch")
        self.assertEqual(CX._patch_files(patch),
                         [("Write", "x.py"), ("Edit", "y.py"), ("Edit", "z.py")])

    def test_patch_move_rewrites_target(self):
        patch = "*** Update File: old.py\n*** Move to: new.py\n+x"
        self.assertEqual(CX._patch_files(patch), [("Edit", "new.py")])

    def test_session_id_from_filename(self):
        p = "/x/rollout-2026-06-07T10-00-00-019ea15e-7785-7a62-af8e-3c14292faf39.jsonl"
        self.assertEqual(CX._session_id_from_name(p),
                         "019ea15e-7785-7a62-af8e-3c14292faf39")


class TestCodexDiscovery(unittest.TestCase):
    def test_head_meta_reads_cwd_past_huge_session_meta(self):
        # session_meta with a >16KB instructions blob must not hide the cwd
        p = U.write_rollout([U.session_meta(cwd="/proj/here", big_instructions=True),
                             U.umsg("hi")])
        CX._HEAD_CACHE.pop(p, None)
        cwd, model, own = CX._head_meta(p)
        self.assertEqual(cwd, "/proj/here")
        self.assertEqual(model, "openai")
        self.assertFalse(own)

    def test_list_sessions_filters_by_cwd_and_own(self):
        import tempfile
        d = tempfile.mkdtemp(prefix="cccodex-home-")
        sdir = os.path.join(d, "sessions", "2026", "06", "07")
        os.makedirs(sdir)
        # one real session for /proj/A, one for /proj/B
        U.write_rollout([U.session_meta(cwd="/proj/A", sid="019ea000-0000-7000-8000-00000000aaaa"),
                         U.umsg("a")], dir=sdir,
                        name="rollout-2026-06-07T10-00-00-019ea000-0000-7000-8000-00000000aaaa.jsonl")
        U.write_rollout([U.session_meta(cwd="/proj/B", sid="019ea000-0000-7000-8000-00000000bbbb"),
                         U.umsg("b")], dir=sdir,
                        name="rollout-2026-06-07T11-00-00-019ea000-0000-7000-8000-00000000bbbb.jsonl")
        # an own (cc-copilot narration) session for /proj/A — must be hidden
        U.write_rollout([U.session_meta(cwd="/proj/A", sid="019ea000-0000-7000-8000-00000000cccc"),
                         U.dev("read-only cockpit agent for supervising coding agents")],
                        dir=sdir,
                        name="rollout-2026-06-07T12-00-00-019ea000-0000-7000-8000-00000000cccc.jsonl")
        old = os.environ.get("CODEX_HOME")
        os.environ["CODEX_HOME"] = d
        CX._HEAD_CACHE.clear()
        try:
            refs = CX.CodexSource().list_sessions("/proj/A")
        finally:
            if old is None:
                os.environ.pop("CODEX_HOME", None)
            else:
                os.environ["CODEX_HOME"] = old
        self.assertEqual([r.session_id for r in refs],
                         ["019ea000-0000-7000-8000-00000000aaaa"])
        self.assertEqual(refs[0].agent, "codex")
        self.assertEqual(refs[0].model, "openai")

    def test_owns(self):
        src = CX.CodexSource()
        self.assertTrue(src.owns("/x/y/rollout-2026-06-07T10-00-00-abc.jsonl"))
        self.assertTrue(src.owns("/home/u/.codex/archived_sessions/rollout-z.jsonl"))
        self.assertFalse(src.owns("/home/u/.claude/projects/p/sess.jsonl"))


if __name__ == "__main__":
    unittest.main()
