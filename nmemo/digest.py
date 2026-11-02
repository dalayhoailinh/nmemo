"""Digest: turn raw sessions into understanding, so the cloud model thinks with it, not just stores it.

After a session ends, a cheap model (through the `claude` CLI the user already has: no API key,
no tools, no saved session) reads a compact view of the session and writes:
  - a digest: title, what was done and why, decisions, open items   -> item kind "summary"
  - durable facts about the person (preferences, goals, projects)   -> items kind "fact"
Then it updates profile.md: a short portrait of the person that every new session starts with.
Raw data is never changed; old digests/facts/profiles are kept as *_old.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time

from . import text as T
from .store import Store

MATERIAL_CHARS = 30_000
PROFILE_CHARS = 2_500

DIGEST_SCHEMA = {
    "type": "object",
    "required": ["title", "summary", "decisions", "open_items", "facts"],
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "decisions": {"type": "array", "items": {"type": "string"}},
        "open_items": {"type": "array", "items": {"type": "string"}},
        "facts": {"type": "array", "items": {
            "type": "object", "required": ["text", "kind"],
            "properties": {
                "text": {"type": "string"},
                "kind": {"type": "string", "enum": ["preference", "goal", "project", "decision", "fact", "habit"]},
                "evidence": {"type": "array", "items": {"type": "string"}}}}},
    },
}
PROFILE_SCHEMA = {"type": "object", "required": ["profile"], "properties": {"profile": {"type": "string"}}}

DIGEST_SYSTEM = """You maintain a person's long-term memory. You receive one recorded work session between \
the person and an AI assistant: the person's messages, the assistant's replies, commands it ran and files it \
wrote, each tagged with an id like [i123]. Write a digest that lets a future assistant pick up exactly where \
this left off and understand the person better.
- Write in the language the person used.
- Be concrete: names, file paths, commands, numbers, results.
- State only what the record shows. Put the ids that prove a point in brackets, e.g. (i12, i15).
- Text ending in "…[rút gọn]" was shortened only for you; the full record exists. Never report it as cut or missing.
- title: at most 12 words. summary: 2-5 sentences (goal, what was done, outcome).
- decisions: choices about the work itself (what to build, how, names, priorities) made or accepted by the \
person. Skip routine tool mechanics: permission prompts, retries, command flags, errors already worked around.
- open_items: real unfinished work or next steps for the person, not tool hiccups.
- facts: durable things about the PERSON (preferences, goals, projects, constraints, habits, decisions they \
made) with evidence ids. Not code trivia, not the assistant's behaviour. Empty list if none."""

PROFILE_SYSTEM = """You keep a short profile of a person so that any AI assistant understands them from the \
first message of a new conversation. You get the current profile (it may have been edited by the person: \
their edits win) and new session digests. Return the full updated profile in Markdown, at most 350 words, \
in the person's language, with these sections (headings translated into that language): who they are; \
projects and goals; how they like to work; key decisions; open items. Keep what is still true, update what \
changed (note the date), drop trivia. End each line with evidence ids like (i12). Mark guesses as guesses.
It is about the PERSON: who they are, what they work on and want, how they like to be helped. Leave out the \
assistant's own tool errors and permission prompts, paths of throwaway test files and other environment \
details. Fewer, more important lines are better than many small ones."""


class DigestError(RuntimeError):
    pass


def ask_claude(system: str, material: str, schema: dict, home) -> tuple[dict, float | None]:
    """One tool-less, unsaved `claude -p` call with structured output. nmemo's own hooks are off inside it."""
    exe = os.environ.get("NMEMO_CLAUDE") or shutil.which("claude")
    if not exe:
        raise DigestError("không tìm thấy lệnh `claude` (đặt NMEMO_CLAUDE nếu nó nằm chỗ khác)")
    work = os.path.join(str(home), "work")
    os.makedirs(work, exist_ok=True)
    cmd = [exe, "-p", "Follow the system prompt. The material is on stdin.",
           "--model", os.environ.get("NMEMO_DIGEST_MODEL", "haiku"), "--effort", "low",
           "--system-prompt", system, "--tools", "", "--strict-mcp-config", "--no-session-persistence",
           "--output-format", "json", "--json-schema", json.dumps(schema)]
    proc = subprocess.run(cmd, input=material, capture_output=True, text=True, encoding="utf-8",
                          timeout=int(os.environ.get("NMEMO_DIGEST_TIMEOUT", "300")), cwd=work,
                          env={**os.environ, "NMEMO_DISABLE": "1"})
    if proc.returncode != 0:
        raise DigestError((proc.stderr or proc.stdout).strip()[-600:] or f"claude thoát mã {proc.returncode}")
    try:
        out = json.loads(proc.stdout)
    except ValueError:
        raise DigestError("claude trả về không phải JSON: " + proc.stdout[:300]) from None
    data = out.get("structured_output")
    if not isinstance(data, dict):
        data = _json_object(out.get("result") or "")
    if not isinstance(data, dict):
        raise DigestError("không đọc được kết quả có cấu trúc: " + str(out.get("result"))[:300])
    return data, out.get("total_cost_usd")


def _json_object(text: str):
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        return None


def material(store: Store, sid: int, budget: int = MATERIAL_CHARS) -> str:
    """A compact, id-tagged view of a session: every prompt, replies, one line per command, files."""
    s = store.session(sid)
    rows = store.db.execute(
        "SELECT * FROM item WHERE session=? AND kind IN ('user','assistant','thinking','tool_call','tool_result','file')"
        " ORDER BY ts, id", (sid,)).fetchall()
    caps = {"user": 2500, "assistant": 1500, "thinking": 400, "tool_call": 300, "tool_result": 400, "file": 200}
    labels = {"user": "NGƯỜI DÙNG", "assistant": "AI", "thinking": "AI (tiến độ)", "tool_call": "LỆNH",
              "tool_result": "KẾT QUẢ", "file": "FILE"}
    lines = []
    for r in rows:
        text = r["preview"] or ""
        if r["size"] > len(text) and r["kind"] in ("user", "assistant"):
            text = store.item_text(r)
        label = " ".join(x for x in (labels[r["kind"]], r["tool"], r["path"]) if x)
        body = T.compact(text)
        if len(body) > caps[r["kind"]]:
            body = body[:caps[r["kind"]]] + "…[rút gọn]"
        lines.append(f"[i{r['id']}] {T.fmt_ts(r['ts'], with_date=False)} {label}: {body}")
    head = f"Phiên s{sid} · thư mục {s['cwd']} · {T.fmt_ts(s['started'])} → {T.fmt_ts(s['ended'])}"
    if sum(len(ln) + 1 for ln in lines) > budget:  # keep the start and the end: the goal and the outcome
        first, last, size_first = [], [], 0
        for ln in lines:
            if size_first + len(ln) > budget * 0.45:
                break
            first.append(ln)
            size_first += len(ln) + 1
        size_last = 0
        for ln in reversed(lines[len(first):]):
            if size_last + len(ln) > budget * 0.45:
                break
            last.insert(0, ln)
            size_last += len(ln) + 1
        skipped = len(lines) - len(first) - len(last)
        lines = first + [f"… (lược {skipped} mục ở giữa; tra bằng nmemo nếu cần) …"] + last
    return "\n".join([head] + lines)


def _supersede(store: Store, sid: int) -> None:
    store.db.execute("UPDATE item SET kind='summary_old' WHERE session=? AND kind='summary' AND tool='digest'", (sid,))
    store.db.execute("UPDATE item SET kind='fact_old' WHERE session=? AND kind='fact'", (sid,))


def _bullets(title: str, items) -> list[str]:
    items = [str(x).strip() for x in items or [] if str(x).strip()]
    return [f"{title}:"] + [f"- {x}" for x in items] if items else []


def save_digest(store: Store, sid: int, data: dict) -> int:
    s = store.session(sid)
    files = [p for (p,) in store.db.execute(
        "SELECT DISTINCT path FROM item WHERE session=? AND kind='file' ORDER BY path", (sid,)).fetchall()]
    title = T.compact(str(data.get("title") or s["title"] or ""))[:150]
    lines = [f"Phiên s{sid} · {T.fmt_ts(s['started'])} · {s['cwd']} · {title}",
             "Tóm tắt: " + str(data.get("summary", "")).strip()]
    lines += _bullets("Quyết định", data.get("decisions"))
    lines += _bullets("Còn dang dở", data.get("open_items"))
    if files:
        lines.append("File: " + ", ".join(files[:20]))
    text = "\n".join(lines)
    now = time.time()
    with store.tx():
        _supersede(store, sid)
        iid = store.add_item(session=sid, ts=s["ended"] or now, kind="summary", tool="digest", text=text,
                             blob=store.put_blob(text.encode("utf-8")), ref=f"s{sid}")
        for fact in data.get("facts") or []:
            if not isinstance(fact, dict) or not str(fact.get("text", "")).strip():
                continue
            ev = ", ".join(str(e) for e in fact.get("evidence") or [] if str(e).strip())
            ftext = f"{str(fact['text']).strip()}" + (f" ({ev})" if ev else "") + f" [s{sid}]"
            store.add_item(session=sid, ts=s["ended"] or now, kind="fact", tool=str(fact.get("kind") or "fact"),
                           text=ftext, blob=store.put_blob(ftext.encode("utf-8")), ref=f"s{sid}")
        if title:
            store.db.execute("UPDATE session SET title=? WHERE id=?", (title, sid))
        store.meta_set(f"digest:{sid}", str(_size(store, sid)))
    return iid


def _size(store: Store, sid: int) -> int:
    return store.one("SELECT count(*) FROM item WHERE session=? AND kind IN ('user','assistant','tool_call')", sid)[0]


def pending(store: Store, *, days: float | None = 7, limit: int = 5) -> list[int]:
    """Sessions worth digesting: real work (>= 2 prompts or >= 3 commands), new or grown since last digest."""
    since = time.time() - days * 86400 if days else 0
    rows = store.db.execute(
        """SELECT s.id FROM session s WHERE s.agent IS NULL AND coalesce(s.ended, 0) >= ?
           AND ((SELECT count(*) FROM item WHERE session=s.id AND kind='user') >= 2
             OR (SELECT count(*) FROM item WHERE session=s.id AND kind='tool_call') >= 3)
           ORDER BY s.ended DESC""", (since,)).fetchall()
    out = []
    for (sid,) in rows:
        done = store.meta_get(f"digest:{sid}")
        if done is None or int(done) < _size(store, sid):
            out.append(sid)
        if len(out) >= limit:
            break
    return out


def current_profile(store: Store) -> str:
    path = store.home / "profile.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    row = store.one("SELECT * FROM item WHERE kind='profile' ORDER BY id DESC LIMIT 1")
    return store.item_text(row) if row else ""


def update_profile(store: Store, digest_ids: list[int], ask=ask_claude) -> str:
    digests = [store.item_text(store.item(i)) for i in digest_ids]
    facts = [store.item_text(r) for r in store.db.execute(
        f"SELECT * FROM item WHERE kind='fact' AND session IN (SELECT session FROM item WHERE id IN "
        f"({','.join('?' * len(digest_ids))}))", tuple(digest_ids)).fetchall()]
    material_text = ("# Hồ sơ hiện tại\n" + (current_profile(store) or "(chưa có)") +
                     "\n\n# Tóm tắt các phiên mới\n" + "\n\n".join(digests) +
                     "\n\n# Sự thật mới về người dùng\n" + ("\n".join(f"- {f}" for f in facts) or "(không có)"))
    data, _ = ask(PROFILE_SYSTEM, material_text, PROFILE_SCHEMA, store.home)
    profile = str(data.get("profile", "")).strip()
    if not profile:
        raise DigestError("hồ sơ trả về rỗng")
    with store.tx():
        store.db.execute("UPDATE item SET kind='profile_old' WHERE kind='profile'")
        store.add_item(session=None, ts=time.time(), kind="profile", tool="digest", text=profile,
                       blob=store.put_blob(profile.encode("utf-8")))
    tmp = store.home / "profile.md.tmp"
    tmp.write_text(profile + "\n", encoding="utf-8")
    os.replace(tmp, store.home / "profile.md")
    return profile


def run(store: Store, sessions: list[int], *, ask=ask_claude, log=print) -> dict:
    made, cost = [], 0.0
    for sid in sessions:
        try:
            data, c = ask(DIGEST_SYSTEM, material(store, sid), DIGEST_SCHEMA, store.home)
        except (DigestError, subprocess.TimeoutExpired) as e:
            log(f"s{sid}: bỏ qua ({e})")
            continue
        made.append(save_digest(store, sid, data))
        cost += c or 0
        log(f"s{sid}: đã tóm tắt → i{made[-1]}")
    if made:
        try:
            update_profile(store, made, ask=ask)
            log("đã cập nhật profile.md")
        except (DigestError, subprocess.TimeoutExpired) as e:
            log(f"hồ sơ: bỏ qua ({e})")
    return {"digests": made, "cost_usd": round(cost, 5)}


class Lock:
    """One digest run at a time (hooks of several sessions may all ask for one)."""

    def __init__(self, home):
        self.path = os.path.join(str(home), "digest.lock")

    def __enter__(self):
        try:
            if time.time() - os.path.getmtime(self.path) > 1800:
                os.remove(self.path)  # stale: a previous run died
        except OSError:
            pass
        try:
            os.close(os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            self.held = True
        except FileExistsError:
            self.held = False
        return self

    def __exit__(self, *exc):
        if self.held:
            try:
                os.remove(self.path)
            except OSError:
                pass
