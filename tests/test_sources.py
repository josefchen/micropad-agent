"""Run: ./run.sh -m unittest discover -s tests"""
import importlib
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(ts))


class SourcesTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        os.environ["HOME"] = self.home
        import sources
        self.s = importlib.reload(sources)
        self.now = time.time()

    def write_jsonl(self, path, records):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

    def claude_transcript(self, sid, records):
        self.write_jsonl(os.path.join(self.home, ".claude", "projects", "-proj", f"{sid}.jsonl"), records)

    def claude_app(self, sid, **fields):
        d = os.path.join(self.home, "Library", "Application Support", "Claude", "claude-code-sessions", "a", "b")
        os.makedirs(d, exist_ok=True)
        rec = {"sessionId": f"local_{sid}", "cliSessionId": sid, "title": "T", "cwd": "/p",
               "lastActivityAt": self.now * 1000, "permissionMode": "bypassPermissions"}
        rec.update(fields)
        with open(os.path.join(d, f"local_{sid}.json"), "w") as f:
            json.dump(rec, f)

    def tool_use(self, ts, name="Bash"):
        return {"type": "assistant", "timestamp": iso(ts), "cwd": "/p",
                "message": {"stop_reason": "tool_use", "content": [{"type": "tool_use", "name": name}]}}

    def end_turn(self, ts):
        return {"type": "assistant", "timestamp": iso(ts), "cwd": "/p",
                "message": {"stop_reason": "end_turn", "content": [{"type": "text", "text": "ok"}]}}

    def by_sid(self):
        return {o.sid: o for o in self.s.scan(self.now)}

    def test_claude_states_from_transcript(self):
        self.claude_transcript("work", [self.tool_use(self.now - 2)])
        self.claude_transcript("done", [self.end_turn(self.now - 2)])
        self.claude_transcript("ask", [self.tool_use(self.now - 2, "AskUserQuestion")])
        got = self.by_sid()
        self.assertEqual(got["work"].state, "working")
        self.assertEqual(got["done"].state, "done")
        self.assertEqual(got["ask"].state, "waiting")

    def test_app_record_supplies_link_title_and_hides_archived(self):
        self.claude_transcript("a1", [self.end_turn(self.now - 5)])
        self.claude_app("a1", title="Sidebar title")
        self.claude_transcript("gone", [self.end_turn(self.now - 5)])
        self.claude_app("gone", isArchived=True)
        got = self.by_sid()
        self.assertEqual(got["a1"].app_id, "local_a1")
        self.assertEqual(got["a1"].title, "Sidebar title")
        self.assertNotIn("gone", got)

    def test_open_turn_in_app_beats_stale_transcript(self):
        self.claude_transcript("run", [self.end_turn(self.now - 3600)])
        self.claude_app("run", lastAssistantUuid="x", lastActivityAt=(self.now - 20) * 1000)
        self.assertEqual(self.by_sid()["run"].state, "working")

    def test_permission_prompt_only_in_prompting_modes(self):
        old = self.now - 60
        self.claude_transcript("prompt", [self.tool_use(old)])
        self.claude_app("prompt", permissionMode="default", lastActivityAt=old * 1000)
        self.claude_transcript("bypass", [self.tool_use(old)])
        self.claude_app("bypass", permissionMode="bypassPermissions", lastActivityAt=old * 1000)
        self.claude_transcript("reading", [self.tool_use(old, "Read")])
        self.claude_app("reading", permissionMode="default", lastActivityAt=old * 1000)
        got = self.by_sid()
        self.assertEqual(got["prompt"].state, "waiting")
        self.assertEqual(got["bypass"].state, "working")
        self.assertEqual(got["reading"].state, "working")

    def test_app_error_marks_session(self):
        self.claude_transcript("err", [self.end_turn(self.now - 30)])
        self.claude_app("err", error="boom", errorAt=(self.now - 10) * 1000)
        self.assertEqual(self.by_sid()["err"].state, "error")

    def cursor_db(self, headers):
        path = self.s.CURSOR_DB
        os.makedirs(os.path.dirname(path), exist_ok=True)
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE composerHeaders (composerId TEXT, workspaceId TEXT, createdAt INTEGER, "
                    "lastUpdatedAt INTEGER, isArchived INTEGER, isSubagent INTEGER, recency INTEGER, "
                    "checkpointAt INTEGER, value TEXT, subagentTypeName TEXT)")
        for cid, h in headers.items():
            t = int(h.get("conversationCheckpointLastUpdatedAt", self.now * 1000))
            con.execute("INSERT INTO composerHeaders VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (cid, "w", t, t, 0, 0, t, t, json.dumps(h), None))
        con.commit()
        con.close()

    def cursor_transcript(self, sid, records, mtime):
        path = os.path.join(self.home, ".cursor", "projects", "Users-x", "agent-transcripts", sid, f"{sid}.jsonl")
        self.write_jsonl(path, records)
        os.utime(path, (mtime, mtime))

    def test_cursor_header_drives_title_waiting_and_working(self):
        user = {"role": "user", "message": {"content": [{"type": "text", "text": "hi"}]}}
        ended = {"type": "turn_ended", "status": "success"}
        self.cursor_transcript("live", [user, ended], self.now - 300)
        self.cursor_transcript("fin", [user, ended], self.now - 5)
        self.cursor_transcript("ask", [user], self.now - 5)
        ws = {"uri": {"fsPath": "/real/ws"}}
        self.cursor_db({
            "live": {"name": "Live chat", "workspaceIdentifier": ws,
                     "conversationCheckpointLastUpdatedAt": (self.now - 10) * 1000},
            "fin": {"name": "Finished", "conversationCheckpointLastUpdatedAt": (self.now - 6) * 1000},
            "ask": {"name": "Asks", "hasBlockingPendingActions": True,
                    "conversationCheckpointLastUpdatedAt": (self.now - 6) * 1000},
        })
        got = self.by_sid()
        self.assertEqual((got["live"].state, got["live"].title, got["live"].workspace),
                         ("working", "Live chat", "/real/ws"))
        self.assertEqual(got["fin"].state, "done")
        self.assertEqual(got["ask"].state, "waiting")

    def test_health_flags_unreadable_source(self):
        self.claude_transcript("junk", [{"type": "something-new"}])
        self.s.scan(self.now)
        warnings = self.s.health()["warnings"]
        self.assertTrue(any(w.startswith("claude:") for w in warnings), warnings)


if __name__ == "__main__":
    unittest.main()
