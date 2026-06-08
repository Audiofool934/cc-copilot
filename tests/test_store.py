"""Tests for the persistent copilot-history store (stdlib only).

Everything is redirected to a temp state home via $CC_COPILOT_STATE_DIR so no
test touches the real home or ~/.claude.
"""

import json
import os
import stat
import tempfile
import threading
import unittest

from cccopilot import store as ST


class _Base(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="ccstate-")
        self._old = os.environ.get("CC_COPILOT_STATE_DIR")
        os.environ["CC_COPILOT_STATE_DIR"] = self.home

    def tearDown(self):
        if self._old is None:
            os.environ.pop("CC_COPILOT_STATE_DIR", None)
        else:
            os.environ["CC_COPILOT_STATE_DIR"] = self._old


class _Tr:
    def __init__(self, sid="sess-uuid", cwd="/proj", title="", raw_lines=10):
        self.session_id, self.cwd, self.title, self.raw_lines = sid, cwd, title, raw_lines


class _St:
    def __init__(self, tr, status="running"):
        self.tr, self.status = tr, status


class TestRoundTrip(_Base):
    def test_roundtrip_order_and_verbatim(self):
        s = ST.Store.open_for("/x/sess-uuid.jsonl", enabled=True, tr=_Tr())
        st = _St(_Tr())
        s.record_turn("问题一 🙋", "答案一 ✅", st=st, backend="codex", model=None)
        s.record_turn("q2", "a2", st=st, backend="codex")
        s.record_turn("q3", "a3", st=st, backend="codex")
        hist = ST.Store.open_for("/x/sess-uuid.jsonl", enabled=True).load_history()
        self.assertEqual(hist, [
            ("user", "问题一 🙋"), ("assistant", "答案一 ✅"),
            ("user", "q2"), ("assistant", "a2"),
            ("user", "q3"), ("assistant", "a3"),
        ])

    def test_head_written_once(self):
        s = ST.Store.open_for("/x/sess-uuid.jsonl", enabled=True, tr=_Tr())
        for i in range(4):
            s.record_turn(f"q{i}", f"a{i}", st=_St(_Tr()))
        kinds = []
        with open(s.turns_path, encoding="utf-8") as fh:
            for line in fh:
                kinds.append(json.loads(line)["kind"])
        self.assertEqual(kinds[0], "head")
        self.assertEqual(kinds.count("head"), 1)
        self.assertEqual(kinds.count("turn"), 4)

    def test_meta_count_authoritative_across_instances(self):
        # Two Store objects on the SAME conv_id (simulating two cockpits) must
        # not drift the turn count — it is re-derived from the log under lock.
        a = ST.Store.open_for("/x/sess-uuid.jsonl", enabled=True, tr=_Tr())
        b = ST.Store.open_for("/x/sess-uuid.jsonl", enabled=True, tr=_Tr())
        a.record_turn("q1", "a1", st=_St(_Tr()))
        b.record_turn("q2", "a2", st=_St(_Tr()))
        a.record_turn("q3", "a3", st=_St(_Tr()))
        with open(a.meta_path, encoding="utf-8") as fh:
            meta = json.load(fh)
        self.assertEqual(meta["turns"], 3)
        self.assertEqual(meta["title"], "q1")           # first question, kept
        self.assertEqual(meta["last_q"], "q3")

    def test_compaction_writes_durable_memory_without_truncating_raw_log(self):
        s = ST.Store.open_for("/x/sess-uuid.jsonl", enabled=True, tr=_Tr())
        st = _St(_Tr())
        for i in range(8):
            s.record_turn(f"should we ship option {i}?", f"answer {i} [sess:L{i + 1}]", st=st)

        memory, recent = s.compact_memory(max_raw_chars=260)

        self.assertIn("Deterministic compaction", memory)
        self.assertIn("Open Questions", memory)
        self.assertIn("[sess:L1]", memory)
        self.assertLess(len(recent), len(s.load_history()))
        self.assertEqual(len(s.load_history()), 16)
        self.assertEqual(s.load_memory(), memory)
        self.assertTrue(os.path.exists(s.memory_path))

    def test_truncate_deletes_stale_memory(self):
        s = ST.Store.open_for("/x/sess-uuid.jsonl", enabled=True, tr=_Tr())
        st = _St(_Tr())
        for i in range(8):
            s.record_turn(f"q{i}", f"a{i} [sess:L{i + 1}]", st=st)
        s.compact_memory(max_raw_chars=120)
        self.assertTrue(os.path.exists(s.memory_path))

        self.assertTrue(s.truncate(2))

        self.assertFalse(os.path.exists(s.memory_path))
        self.assertEqual(len(s.load_history()), 4)


class TestConcurrency(_Base):
    def test_flock_serializes_multikb_writers(self):
        # Two threads, each appending 30 multi-KB turns (>> PIPE_BUF) to one log.
        # If flock weren't load-bearing, lines would interleave and fail to parse.
        cid = "shared-conv"
        big = "x" * 3000
        errs = []

        def worker(tag):
            s = ST.Store(cid, enabled=True)
            s.transcript = "/x/shared.jsonl"
            try:
                for i in range(30):
                    s.record_turn(f"{tag}-{i}", big, st=_St(_Tr(sid=cid)))
            except Exception as e:                       # pragma: no cover
                errs.append(e)

        t1 = threading.Thread(target=worker, args=("A",))
        t2 = threading.Thread(target=worker, args=("B",))
        t1.start(); t2.start(); t1.join(); t2.join()
        self.assertEqual(errs, [])

        path = os.path.join(self.home, "conversations", cid, "turns.jsonl")
        heads = turns = 0
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                obj = json.loads(line)                   # must NOT raise → no torn lines
                heads += obj["kind"] == "head"
                turns += obj["kind"] == "turn"
        self.assertEqual(heads, 1)
        self.assertEqual(turns, 60)


class TestTolerance(_Base):
    def test_torn_final_line_tolerated(self):
        s = ST.Store.open_for("/x/sess-uuid.jsonl", enabled=True, tr=_Tr())
        s.record_turn("q1", "a1", st=_St(_Tr()))
        s.record_turn("q2", "a2", st=_St(_Tr()))
        with open(s.turns_path, "a", encoding="utf-8") as fh:
            fh.write('{"kind":"turn","q":"half')          # torn, no newline / close
        hist = s.load_history()
        self.assertEqual(hist, [("user", "q1"), ("assistant", "a1"),
                                ("user", "q2"), ("assistant", "a2")])

    def test_unknown_kind_ignored(self):
        s = ST.Store.open_for("/x/sess-uuid.jsonl", enabled=True, tr=_Tr())
        s.record_turn("q1", "a1", st=_St(_Tr()))
        with open(s.turns_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"kind": "event", "what": "future"}) + "\n")
        s.record_turn("q2", "a2", st=_St(_Tr()))
        self.assertEqual(s.load_history(),
                         [("user", "q1"), ("assistant", "a1"),
                          ("user", "q2"), ("assistant", "a2")])


class TestListingAndHealing(_Base):
    def test_self_heal_rebuilds_missing_meta(self):
        s = ST.Store.open_for("/x/sess-uuid.jsonl", enabled=True, tr=_Tr(cwd="/proj"))
        s.record_turn("first q", "a1", st=_St(_Tr(cwd="/proj")))
        s.record_turn("second q", "a2", st=_St(_Tr(cwd="/proj")))
        os.remove(s.meta_path)
        rows = ST.list_conversations()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].turns, 2)
        self.assertEqual(rows[0].cwd, "/proj")
        self.assertEqual(rows[0].title, "first q")

    def test_list_filters_by_cwd_and_sorts(self):
        ST.Store.open_for("/x/aaaa.jsonl", True, _Tr(sid="aaaa", cwd="/p1")).record_turn(
            "qa", "a", st=_St(_Tr(sid="aaaa", cwd="/p1")))
        ST.Store.open_for("/x/bbbb.jsonl", True, _Tr(sid="bbbb", cwd="/p2")).record_turn(
            "qb", "b", st=_St(_Tr(sid="bbbb", cwd="/p2")))
        self.assertEqual([h.conv_id for h in ST.list_conversations("/p1")], ["aaaa"])
        self.assertEqual(len(ST.list_conversations()), 2)

    def test_transcript_present_flag(self):
        real = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
        real.write(b'{"sessionId":"live"}\n'); real.close()
        ST.Store.open_for(real.name, True, _Tr(sid="live", cwd="/p")).record_turn(
            "q", "a", st=_St(_Tr(sid="live", cwd="/p")))
        present = {h.conv_id: h.transcript_present for h in ST.list_conversations()}
        self.assertTrue(present.get("live"))
        os.remove(real.name)
        present = {h.conv_id: h.transcript_present for h in ST.list_conversations()}
        self.assertFalse(present.get("live"))


class TestPermsAndOptOut(_Base):
    def test_restrictive_perms(self):
        s = ST.Store.open_for("/x/sess-uuid.jsonl", enabled=True, tr=_Tr())
        s.record_turn("q", "a", st=_St(_Tr()))
        self.assertEqual(stat.S_IMODE(os.stat(s.dir).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(s.turns_path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(s.meta_path).st_mode), 0o600)

    def test_disabled_is_noop(self):
        s = ST.Store.open_for("/x/sess-uuid.jsonl", enabled=False, tr=_Tr())
        self.assertFalse(s.record_turn("q", "a", st=_St(_Tr())))
        self.assertFalse(os.path.exists(s.turns_path))
        self.assertEqual(s.load_history(), [])

    def test_disabled_store_does_not_read_existing(self):
        # opt-out must NOT replay prior saved plaintext (privacy guarantee)
        ST.Store.open_for("/x/sess-uuid.jsonl", enabled=True, tr=_Tr()).record_turn(
            "secret q", "secret a", st=_St(_Tr()))
        self.assertEqual(
            ST.Store.open_for("/x/sess-uuid.jsonl", enabled=False).load_history(), [])

    def test_keying_precedence(self):
        self.assertEqual(ST.conv_id_for("/x/the-uuid.jsonl"), "the-uuid")
        self.assertEqual(ST.conv_id_for("/x/the-uuid.jsonl", _Tr(sid="real-sid")), "real-sid")


class TestCrashRecovery(_Base):
    def test_append_after_torn_line_preserves_both(self):
        # the critical mid-answer-crash case: a torn final line must not swallow
        # the next good turn when we append.
        s = ST.Store.open_for("/x/sess-uuid.jsonl", enabled=True, tr=_Tr())
        s.record_turn("q1", "a1", st=_St(_Tr()))
        s.record_turn("q2", "a2", st=_St(_Tr()))
        with open(s.turns_path, "a", encoding="utf-8") as fh:
            fh.write('{"kind":"turn","q":"half')           # crash: no newline
        s.record_turn("q3", "a3", st=_St(_Tr()))
        self.assertEqual(s.load_history(), [
            ("user", "q1"), ("assistant", "a1"),
            ("user", "q2"), ("assistant", "a2"),
            ("user", "q3"), ("assistant", "a3")])
        with open(s.meta_path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["turns"], 3)

    def test_torn_multibyte_tail_does_not_break_writer(self):
        s = ST.Store.open_for("/x/sess-uuid.jsonl", enabled=True, tr=_Tr())
        s.record_turn("q1", "a1", st=_St(_Tr()))
        with open(s.turns_path, "ab") as fh:
            fh.write(b'{"kind":"turn","q":"\xe4\xbd')        # truncated UTF-8 ('你')
        s.record_turn("q2", "a2", st=_St(_Tr()))            # must not raise
        self.assertEqual(s.load_history(),
                         [("user", "q1"), ("assistant", "a1"),
                          ("user", "q2"), ("assistant", "a2")])

    def test_reserved_conv_id_does_not_escape(self):
        self.assertEqual(ST.conv_id_for("/x/...jsonl"), "unknown")   # basename '..'
        self.assertEqual(ST.conv_id_for("/x/..jsonl"), "unknown")    # basename '.'
        s = ST.Store.open_for("/x/...jsonl", enabled=True)
        s.record_turn("q", "a", st=_St(_Tr()))
        self.assertEqual(os.path.basename(os.path.dirname(s.turns_path)), "unknown")
        self.assertEqual(os.path.dirname(os.path.dirname(s.turns_path)),
                         os.path.join(self.home, "conversations"))
        self.assertEqual(len(ST.list_conversations(None)), 1)

    def test_non_serializable_field_is_best_effort(self):
        import contextlib
        import io
        s = ST.Store.open_for("/x/sess-uuid.jsonl", enabled=True, tr=_Tr())
        with contextlib.redirect_stderr(io.StringIO()):     # expected warn_once
            ok = s.record_turn("q", "a", st=_St(_Tr()), model=object())  # not JSON-able
        self.assertFalse(ok)                                # returns False, no raise
        self.assertEqual(s.load_history(), [])              # nothing half-written

    def test_flock_runtime_failure_degrades_to_single_writer(self):
        if not ST._HAVE_FLOCK:
            self.skipTest("no fcntl on this platform")
        real = ST.fcntl.flock

        def boom(fd, op):
            if op == ST.fcntl.LOCK_EX:
                raise OSError("ENOLCK: no locks available")
            return real(fd, op)

        ST.fcntl.flock = boom
        try:
            s = ST.Store.open_for("/x/sess-uuid.jsonl", enabled=True, tr=_Tr())
            ok = s.record_turn("q", "a", st=_St(_Tr()))     # NFS-style lock failure
        finally:
            ST.fcntl.flock = real
        self.assertTrue(ok)                                 # persisted via _PROC_LOCK
        self.assertEqual(s.load_history(), [("user", "q"), ("assistant", "a")])


class TestTruncate(_Base):
    def test_truncate_keeps_first_n(self):
        s = ST.Store.open_for("/x/sess-uuid.jsonl", enabled=True, tr=_Tr())
        for i in range(3):
            s.record_turn(f"q{i}", f"a{i}", st=_St(_Tr()))
        self.assertTrue(s.truncate(1))
        self.assertEqual(s.load_history(), [("user", "q0"), ("assistant", "a0")])
        with open(s.turns_path, encoding="utf-8") as fh:
            kinds = [json.loads(l)["kind"] for l in fh]
        self.assertEqual(kinds, ["head", "turn"])           # head preserved, one turn
        with open(s.meta_path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["turns"], 1)

    def test_truncate_to_zero_clears(self):
        s = ST.Store.open_for("/x/sess-uuid.jsonl", enabled=True, tr=_Tr())
        s.record_turn("q", "a", st=_St(_Tr()))
        self.assertTrue(s.truncate(0))
        self.assertEqual(s.load_history(), [])
        # and we can keep appending afterward (fork continues cleanly)
        s.record_turn("q2", "a2", st=_St(_Tr()))
        self.assertEqual(s.load_history(), [("user", "q2"), ("assistant", "a2")])


class TestDurability(_Base):
    def test_fsync_called_for_log_and_meta(self):
        calls = []
        real = os.fsync
        os.fsync = lambda fd: calls.append(fd)
        try:
            s = ST.Store.open_for("/x/sess-uuid.jsonl", enabled=True, tr=_Tr())
            s.record_turn("q", "a", st=_St(_Tr()))
        finally:
            os.fsync = real
        self.assertGreaterEqual(len(calls), 2)            # log fd + meta tmp fd


if __name__ == "__main__":
    unittest.main()
