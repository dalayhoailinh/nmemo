"""Run: python3 -m unittest discover -s tests -v   (standard library only)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nmemo import digest, hooks, ingest, mcp, views  # noqa: E402
from nmemo import text as T  # noqa: E402
from nmemo.store import Store  # noqa: E402


def line(**obj) -> str:
    return json.dumps(obj, ensure_ascii=False) + "\n"


def user(uuid, text, ts="2026-11-02T01:00:00Z", **extra):
    return line(type="user", uuid=uuid, sessionId="sess-1", cwd="/proj", timestamp=ts,
                message={"role": "user", "content": text}, **extra)


def assistant(uuid, blocks, ts="2026-11-02T01:00:01Z"):
    return line(type="assistant", uuid=uuid, sessionId="sess-1", cwd="/proj", timestamp=ts,
                message={"role": "assistant", "content": blocks})


WRITE = {"type": "tool_use", "id": "toolu_1", "name": "Write",
         "input": {"file_path": "/proj/duong.py", "content": "# tính đường đi\nprint('xin chào')\n"}}
RESULT = line(type="user", uuid="u2", sessionId="sess-1", timestamp="2026-11-02T01:00:02Z",
              message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1",
                                                      "content": "File created successfully"}]})


class Base(unittest.TestCase):
    def setUp(self):
        # tests never start background work or call a real model
        self.env = mock.patch.dict(os.environ, {"NMEMO_DIGEST": "0", "NMEMO_BACKFILL": "0"})
        self.env.start()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.store = Store(self.dir / "home")
        self.transcript = self.dir / "sess-1.jsonl"

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()
        self.env.stop()

    def write(self, *lines, mode="a"):
        with open(self.transcript, mode, encoding="utf-8") as f:
            f.write("".join(lines))


class IngestTests(Base):
    def test_incremental_and_partial_line(self):
        self.write(user("u1", "Viết hàm tính đường đi ngắn nhất"), assistant("a1", [WRITE]))
        self.write('{"type":"user","uuid":"u2","mess')  # still being written
        r = ingest.ingest_transcript(self.store, self.transcript)
        self.assertEqual(r["events"], 2)
        self.write(mode="w", *[user("u1", "Viết hàm tính đường đi ngắn nhất"), assistant("a1", [WRITE]), RESULT])
        r = ingest.ingest_transcript(self.store, self.transcript)
        self.assertEqual(r["events"], 1)
        kinds = [row["kind"] for row in self.store.db.execute("SELECT kind FROM item ORDER BY id")]
        self.assertEqual(kinds, ["user", "tool_call", "tool_result"])
        result = self.store.one("SELECT * FROM item WHERE kind='tool_result'")
        self.assertEqual((result["tool"], result["path"]), ("Write", "/proj/duong.py"))

    def test_byte_exact_export_and_verify(self):
        self.write(user("u1", "chào 👋"), assistant("a1", [WRITE]), RESULT, "\n", '{"not": "a message"}\n')
        r = ingest.ingest_transcript(self.store, self.transcript)
        with self.store.tx():
            self.store.seal(force=True)
        exported = b"".join(b + b"\n" for b in self.store.session_lines(r["session"]))
        self.assertEqual(exported, self.transcript.read_bytes())
        checked, bad = self.store.verify()
        self.assertEqual(bad, [])
        self.assertGreater(checked, 0)

    def test_item_text_is_exact(self):
        self.write(user("u1", "x"), assistant("a1", [WRITE]))
        ingest.ingest_transcript(self.store, self.transcript)
        row = self.store.one("SELECT * FROM item WHERE kind='tool_call'")
        self.assertIn(WRITE["input"]["content"], self.store.item_text(row))

    def test_long_tool_output_saved_beside_transcript(self):
        full = self.dir / "sess-1" / "tool-results" / "abc.txt"
        full.parent.mkdir(parents=True)
        full.write_text("đầu ra rất dài\n" * 3000 + "dòng cuối cùng hiếm gặp\n", encoding="utf-8")
        outside = self.dir.parent / "secret.txt"
        preview = (f"<persisted-output>\nOutput too large (42.9KB). Full output saved to: {full}\n\n"
                   "Preview (first 2KB):\nđầu ra rất dài\n")
        bogus = f"Full output saved to: {outside}\n"
        self.write(assistant("a1", [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "x"}}]),
                   line(type="user", uuid="u2", timestamp="2026-11-02T01:00:02Z", message={"role": "user", "content": [
                       {"type": "tool_result", "tool_use_id": "t1", "content": preview},
                       {"type": "tool_result", "tool_use_id": "t1", "content": bogus}]}))
        ingest.ingest_transcript(self.store, self.transcript)
        total, rows = self.store.search("hiem gap")
        self.assertEqual(total, 1)
        self.assertEqual(self.store.item_text(rows[0]), full.read_text(encoding="utf-8"))
        self.assertEqual(self.store.search("rat dai")[0], 1)  # the 2 KB preview is not indexed twice
        self.assertEqual(self.store.one("SELECT count(*) FROM item WHERE blob IS NOT NULL")[0], 1)

    def test_resumed_copy_is_not_indexed_twice(self):
        self.write(user("u1", "câu hỏi gốc"), assistant("a1", [{"type": "text", "text": "trả lời"}]))
        ingest.ingest_transcript(self.store, self.transcript)
        copy = self.dir / "sess-2.jsonl"
        copy.write_bytes(self.transcript.read_bytes() + user("u9", "câu hỏi mới").encode())
        r = ingest.ingest_transcript(self.store, copy)
        self.assertEqual(r["events"], 3)  # raw lines kept for both files...
        self.assertEqual(r["items"], 1)   # ...but only the new message becomes searchable again
        self.assertEqual(self.store.one("SELECT count(*) FROM event WHERE dup_of IS NOT NULL")[0], 2)


class SearchTests(Base):
    def setUp(self):
        super().setUp()
        self.write(user("u1", "Hôm nay đi đường Nguyễn Văn Linh, không kẹt xe"),
                   assistant("a1", [{"type": "text", "text": "plain ascii duong here"}]))
        ingest.ingest_transcript(self.store, self.transcript)

    def hits(self, query):
        return [r["preview"] for r in self.store.search(query)[1]]

    def test_accent_insensitive(self):
        self.assertEqual(len(self.hits("khong ket xe")), 1)
        self.assertEqual(len(self.hits("nguyen van linh")), 1)

    def test_d_with_stroke(self):
        self.assertEqual(len(self.hits("duong")), 2)    # "đường" and ascii "duong"
        self.assertEqual(len(self.hits("đường")), 2)
        self.assertTrue(self.hits("đường")[0].startswith("Hôm nay"))  # exact accents rank first

    def test_phrase_and_snippet(self):
        self.assertEqual(len(self.hits('"van linh"')), 1)
        self.assertEqual(len(self.hits('"linh van"')), 0)
        self.assertIn("đường", T.snippet("x " * 300 + "đi đường xa", "duong", 40))

    def test_fold_keeps_length(self):
        s = "Đường đến trường, ĐẸP lắm!"
        self.assertEqual(len(T.fold(s)), len(s))
        self.assertEqual(T.fold(s), "duong den truong, dep lam!")


class HookTests(Base):
    def payload(self, event, **extra):
        return {"hook_event_name": event, "session_id": "sess-1", "transcript_path": str(self.transcript),
                "cwd": str(self.dir / "proj"), **extra}

    @mock.patch.dict(os.environ, {"NMEMO_BACKFILL": "0"})
    def test_full_turn(self):
        proj = self.dir / "proj"
        proj.mkdir()
        self.write(user("u1", "tạo file giúp mình"))
        out = hooks.handle(self.payload("SessionStart", source="startup"), self.store)
        self.assertIn("nmemo", out["hookSpecificOutput"]["additionalContext"])

        hooks.handle(self.payload("UserPromptSubmit", prompt="tạo file giúp mình"), self.store)
        (proj / "a.py").write_text("print(1)\n")
        tool = {"tool_name": "Write", "tool_use_id": "toolu_1", "tool_input": {"file_path": "a.py"}}
        hooks.handle(self.payload("PostToolUse", **tool), self.store)
        hooks.handle(self.payload("PostToolUse", **tool), self.store)  # unchanged: no new version
        (proj / "made_by_shell.txt").write_text("kết quả\n")
        self.write(assistant("a1", [{"type": "text", "text": "Xong"}]))
        hooks.handle(self.payload("Stop", stop_hook_active=False), self.store)
        hooks.handle(self.payload("SessionEnd", reason="exit"), self.store)

        files = {Path(r["path"]).name: r for r in self.store.db.execute("SELECT * FROM item WHERE kind='file'")}
        self.assertEqual(set(files), {"a.py", "made_by_shell.txt"})
        self.assertEqual(self.store.item_text(files["made_by_shell.txt"]), "kết quả\n")
        self.assertEqual(self.store.one("SELECT count(*) FROM pending")[0], 0)  # all packed at SessionEnd
        overview = views.read(self.store, "s1")
        self.assertIn("a.py", overview)

    def test_subagent_links_to_parent(self):
        self.write(user("u1", "chạy subagent"))
        ingest.ingest_transcript(self.store, self.transcript)
        sub = self.dir / "agent-x.jsonl"
        sub.write_text(line(type="user", uuid="s1", sessionId="sess-1", isSidechain=True, agentId="x",
                            timestamp="2026-11-02T01:00:03Z", message={"role": "user", "content": "việc con"}))
        hooks.handle(self.payload("SubagentStop", agent_id="x", agent_transcript_path=str(sub),
                                  agent_type="general-purpose", stop_hook_active=False), self.store)
        row = self.store.one("SELECT * FROM session WHERE agent='x'")
        self.assertEqual(row["parent"], 1)


PNG_1PX = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


class RecallTests(Base):
    """A new message automatically brings back matching memories from earlier sessions."""

    def setUp(self):
        super().setUp()
        self.write(user("u1", "Tạo file in ra dòng chữ xin chào, rồi chạy nó bằng python3"),
                   assistant("a0", [{"type": "tool_use", "id": "t1", "name": "Write",
                                     "input": {"file_path": "/proj/hello.py", "content": "print('xin chào')\n"}}]),
                   assistant("a1", [{"type": "text", "text": "Mình đã tạo file, chạy ra: xin chào"}]),
                   user("u2", "Món phở bò hôm qua ngon quá", ts="2026-11-02T02:00:00Z"))
        ingest.ingest_transcript(self.store, self.transcript)
        self.current = self.dir / "sess-2.jsonl"
        self.current.write_text(line(type="user", uuid="n1", sessionId="sess-2", timestamp="2026-11-03T01:00:00Z",
                                     message={"role": "user", "content": "chạy lại file chào hôm trước"}))
        ingest.ingest_transcript(self.store, self.current)

    def payload(self, prompt):
        return {"hook_event_name": "UserPromptSubmit", "session_id": "sess-2", "prompt": prompt,
                "transcript_path": str(self.current), "cwd": str(self.dir)}

    def test_relevant_memory_is_injected_once(self):
        out = hooks.handle(self.payload("Chạy lại giúp mình cái file in lời chào hôm trước"), self.store)
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("hello.py", ctx)
        self.assertNotIn("phở", ctx)                     # unrelated memory stays out
        self.assertNotIn("chạy lại file chào hôm trước", ctx)  # the current session is already in context
        again = hooks.handle(self.payload("Chạy lại giúp mình cái file in lời chào hôm trước"), self.store)
        self.assertIsNone(again)                          # same memories are not paid for twice

    def test_unrelated_or_empty_message_adds_nothing(self):
        self.assertIsNone(hooks.handle(self.payload("ok"), self.store))
        self.assertIsNone(hooks.handle(self.payload("thời tiết Đà Lạt tuần sau"), self.store))

    def test_can_be_turned_off(self):
        with mock.patch.dict(os.environ, {"NMEMO_RECALL": "0"}):
            self.assertIsNone(hooks.handle(self.payload("chạy lại file hello.py"), self.store))


class DigestTests(Base):
    """Sessions become digests + facts + a profile; a fake model stands in for `claude -p`."""

    def setUp(self):
        super().setUp()
        self.write(user("u1", "Mình muốn làm app ghi chú bằng Flutter, ưu tiên chạy offline"),
                   assistant("a1", [WRITE]), RESULT,
                   user("u3", "Đặt tên app là nmemo-note nhé", ts="2026-11-02T01:05:00Z"),
                   assistant("a2", [{"type": "text", "text": "Đã đổi tên thành nmemo-note."}], ts="2026-11-02T01:06:00Z"))
        self.sid = ingest.ingest_transcript(self.store, self.transcript)["session"]
        self.calls = []

    def fake(self, system, material, schema, home):
        self.calls.append(material)
        if schema is digest.PROFILE_SCHEMA:
            return {"profile": "## Về bạn\n- Đang làm app ghi chú Flutter chạy offline (i1)"}, 0.001
        return {"title": "Khởi động app ghi chú nmemo-note", "summary": "Bắt đầu app Flutter offline.",
                "decisions": ["Tên app: nmemo-note (i4)"], "open_items": ["Viết màn hình danh sách"],
                "facts": [{"text": "Thích ứng dụng chạy offline", "kind": "preference", "evidence": ["i1"]}]}, 0.002

    def test_material_has_ids_prompts_and_commands(self):
        text = digest.material(self.store, self.sid)
        self.assertIn("NGƯỜI DÙNG: Mình muốn làm app ghi chú bằng Flutter", text)
        self.assertIn("LỆNH Write /proj/duong.py", text)
        self.assertRegex(text, r"\[i\d+\]")
        self.assertIn("lược", digest.material(self.store, self.sid, budget=200))

    def test_digest_facts_profile_and_reuse(self):
        self.assertEqual(digest.pending(self.store, days=None), [self.sid])
        result = digest.run(self.store, [self.sid], ask=self.fake, log=lambda m: None)
        self.assertEqual(len(result["digests"]), 1)
        self.assertEqual(digest.pending(self.store, days=None), [])            # done until the session grows
        self.assertIn("nmemo-note", self.store.session(self.sid)["title"])
        self.assertEqual(self.store.search("offline", kinds=["fact"])[0], 1)
        self.assertIn("Flutter", (self.store.home / "profile.md").read_text(encoding="utf-8"))

        ctx = views.start_context(self.store, {"cwd": "/elsewhere", "transcript_path": ""})
        self.assertIn("Hồ sơ người dùng", ctx)                                  # every new session starts with it
        self.assertIn("Quyết định", views.read(self.store, f"s{self.sid}"))

        self.write(user("u9", "Thêm chế độ tối nữa", ts="2026-11-02T02:00:00Z"))
        ingest.ingest_transcript(self.store, self.transcript)
        self.assertEqual(digest.pending(self.store, days=None), [self.sid])     # grew: digest again
        digest.run(self.store, [self.sid], ask=self.fake, log=lambda m: None)
        kinds = dict(self.store.db.execute("SELECT kind, count(*) FROM item WHERE tool='digest' GROUP BY kind"))
        self.assertEqual(kinds, {"summary": 1, "summary_old": 1, "profile": 1, "profile_old": 1})
        self.assertIn("Hồ sơ hiện tại\n## Về bạn", self.calls[-1])              # profile updates build on the last one

    def test_recall_prefers_the_digest(self):
        digest.run(self.store, [self.sid], ask=self.fake, log=lambda m: None)
        text, _ = views.recall(self.store, "tiếp tục app ghi chú nmemo-note offline")
        self.assertIn("summary digest", text.splitlines()[1])

    def test_model_failure_is_skipped_not_fatal(self):
        def broken(*a):
            raise digest.DigestError("hết hạn mức")
        logs = []
        self.assertEqual(digest.run(self.store, [self.sid], ask=broken, log=logs.append)["digests"], [])
        self.assertIn("bỏ qua", logs[0])

    def test_lock_allows_one_run(self):
        with digest.Lock(self.store.home) as a, digest.Lock(self.store.home) as b:
            self.assertTrue(a.held)
            self.assertFalse(b.held)


class McpTests(Base):
    def test_read_returns_a_picture_the_user_sent(self):
        self.write(line(type="user", uuid="u1", timestamp="2026-11-02T01:00:00Z", message={"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PNG_1PX}},
            {"type": "text", "text": "ảnh chụp màn hình lỗi"}]}))
        ingest.ingest_transcript(self.store, self.transcript)
        row = self.store.one("SELECT id FROM item WHERE kind='image'")
        self.assertEqual(views.image_payload(self.store, f"i{row[0]}"), (PNG_1PX, "image/png"))
        self.assertIsNone(views.image_payload(self.store, "i999"))

    def test_roundtrip_over_stdio(self):
        self.write(user("u1", "nhớ giúp mình mật mã là cá vàng"))
        ingest.ingest_transcript(self.store, self.transcript)
        env = {**os.environ, "NMEMO_HOME": str(self.store.home)}
        proc = subprocess.Popen([sys.executable, str(ROOT / "bin" / "nmemo"), "mcp"], env=env, text=True,
                                encoding="utf-8", stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "search", "arguments": {"query": "ca vang"}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "read", "arguments": {"id": "i1"}}},
        ]
        out, _ = proc.communicate("".join(json.dumps(m) + "\n" for m in msgs), timeout=30)
        replies = {r["id"]: r for r in map(json.loads, out.splitlines())}
        self.assertEqual(replies[1]["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual({t["name"] for t in replies[2]["result"]["tools"]},
                         {"search", "read", "sessions", "timeline", "remember"})
        self.assertIn("[i1]", replies[3]["result"]["content"][0]["text"])
        self.assertTrue(replies[4]["result"]["content"][0]["text"].endswith("nhớ giúp mình mật mã là cá vàng"))

    def test_remember_and_errors(self):
        self.assertIn("i1", mcp.call(self.store, "remember", {"text": "Thích trả lời ngắn"}))
        self.assertIn("i1", mcp.call(self.store, "search", {"query": "thich tra loi", "kind": ["note"]}))
        with self.assertRaises(ValueError):
            mcp.call(self.store, "read", {"id": "zz"})
        self.assertIn("Thích trả lời ngắn", mcp.call(self.store, "read", {"url": "i1"}))  # wrong name, still clear
        with self.assertRaisesRegex(ValueError, "thiếu tham số 'id'"):
            mcp.call(self.store, "read", {})


if __name__ == "__main__":
    unittest.main()
