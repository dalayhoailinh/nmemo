"""Bring data into the store: Claude Code transcripts (incrementally) and file snapshots."""

from __future__ import annotations

import json
import os
import re
import stat
import time
from pathlib import Path

from . import claude
from . import text as T
from .store import Store, decode_text

BATCH_LINES = 2000  # commit every N lines so hooks of other sessions never wait long
SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache",
             ".pytest_cache", ".ruff_cache", ".tox", "dist", "build", ".next", ".nuxt", "target",
             ".gradle", ".terraform", ".cache", ".idea"}


def claude_projects_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude").expanduser() / "projects"


def max_file_bytes() -> int:
    return int(float(os.environ.get("NMEMO_MAX_FILE_MB", "20")) * 1024 * 1024)


def fs_path(path: str) -> str:
    """The form of an absolute path to hand to the OS: Windows rejects 260+ chars without \\\\?\\."""
    return "\\\\?\\" + path if os.name == "nt" and len(path) >= 260 and not path.startswith("\\\\") else path


_PERSISTED = re.compile(r"Full output saved to: (.+?)[ \t]*(?:\r?\n|$)")


def persisted_output(text: str, transcript: str) -> str | None:
    """Claude Code moves very long tool output to <session>/tool-results/*.txt and keeps only a
    preview in the transcript. Return that file when it exists inside Claude's own folders."""
    m = _PERSISTED.search(text[:4000])
    if not m:
        return None
    full = os.path.abspath(m.group(1).strip())
    for base in (os.path.dirname(transcript), str(claude_projects_dir())):
        try:
            inside = os.path.commonpath([full, os.path.abspath(base)]) == os.path.abspath(base)
        except ValueError:  # different drives on Windows
            inside = False
        if inside and os.path.isfile(fs_path(full)):
            return full
    return None


def ingest_transcript(store: Store, path: str | Path, *, agent: str | None = None,
                      parent_uuid: str | None = None) -> dict:
    """Store every new complete line of a JSONL transcript and index its parts. Safe to call
    again and again: it continues from where the previous call stopped."""
    path = os.path.abspath(os.fspath(path))
    disk_path = fs_path(path)
    totals = {"events": 0, "items": 0, "session": None}
    if not os.path.isfile(disk_path):
        return totals
    while True:
        with store.tx():
            sid = store.session_for(path)
            totals["session"] = sid
            src = store.source(path)
            offset, line_no = (src["offset"], src["lines"]) if src else (0, 0)
            if os.path.getsize(disk_path) < offset:  # rewritten from scratch: line numbers start over
                offset, line_no = 0, 0
            last_ts = store.last_ts(sid)
            first_ts, title, fields = None, None, {}
            done = True
            with open(disk_path, "rb") as f:
                f.seek(offset)
                for count, raw in enumerate(f):
                    if count >= BATCH_LINES:
                        done = False
                        break
                    if not raw.endswith(b"\n"):  # last line still being written
                        break
                    offset += len(raw)
                    line, this_line = raw[:-1], line_no
                    line_no += 1
                    try:
                        obj = json.loads(line) if line.strip() else {}
                    except ValueError:
                        obj = {}
                    obj = obj if isinstance(obj, dict) else {}
                    ts = T.parse_ts(obj.get("timestamp")) or last_ts or time.time()
                    last_ts = ts
                    eid = store.add_event(sid, this_line, line, uuid=obj.get("uuid"), type=obj.get("type"), ts=ts)
                    if eid is None:
                        continue
                    totals["events"] += 1
                    first_ts = first_ts or ts
                    fields.update(claude.session_fields(obj))
                    if obj.get("uuid") and store.uuid_seen(obj["uuid"], eid):
                        continue  # same message already indexed (e.g. history copied into a resumed session)
                    for part in claude.parts(obj):
                        tool, fpath = part.tool, part.path
                        full = None
                        if part.kind == "tool_result":
                            if part.ref:
                                call = store.call_for_ref(part.ref)
                                tool, fpath = (call["tool"], call["path"]) if call else (None, None)
                            full = persisted_output(part.text, path)
                        store.add_item(session=sid, ts=ts, kind=part.kind, text=part.text, tool=tool, path=fpath,
                                       event=eid, ptr=part.ptr, ref=part.ref,
                                       index=part.kind not in ("image", "context") and full is None)
                        totals["items"] += 1
                        if full:  # the transcript only kept a preview: store the whole output too
                            with open(fs_path(full), "rb") as f:
                                data = f.read()
                            text = decode_text(data)
                            store.add_item(session=sid, ts=ts, kind="tool_result", tool=tool, path=fpath,
                                           blob=store.put_blob(data), ref=part.ref,
                                           text=text if text is not None else f"[nhị phân {len(data):,} byte]")
                            totals["items"] += 1
                        if title is None and claude.is_human_prompt(part):
                            title = T.compact(part.text)[:120]
            if agent:
                fields["agent"] = agent
            if fields.get("agent"):
                fields["parent"] = store.session_by_uuid(parent_uuid or fields.get("uuid"))
            store.update_session(sid, first_ts=first_ts, last_ts=last_ts if first_ts else None,
                                 title=title, **fields)
            store.set_source(path, sid, offset, line_no)
            store.seal()
        if done:
            return totals


def transcript_files(root: str | Path):
    root = Path(root).expanduser()
    if root.is_file():
        yield root
    elif root.is_dir():
        # main sessions first, so subagent transcripts can find their parent session
        yield from sorted(root.rglob("*.jsonl"), key=lambda p: (p.parent.name == "subagents", str(p)))


def import_paths(store: Store, paths, progress=None) -> dict:
    totals = {"files": 0, "events": 0, "items": 0}
    for path in paths:
        for f in transcript_files(path):
            r = ingest_transcript(store, f)
            totals["files"] += 1
            totals["events"] += r["events"]
            totals["items"] += r["items"]
            if progress and r["events"]:
                progress(f, r)
    with store.tx():
        store.seal(force=True)
    store.checkpoint()
    return totals


def snapshot_file(store: Store, file_path: str, *, session: int, tool: str | None = None,
                  ref: str | None = None) -> int | None:
    """Store the full current content of a file (deduplicated); returns the new item id or None."""
    p = os.path.abspath(file_path)
    disk_path = fs_path(p)
    try:
        st = os.stat(disk_path)
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    if st.st_size > max_file_bytes():
        if tool == "scan":
            return None
        return store.add_item(session=session, ts=time.time(), kind="file", tool=tool, path=p, ref=ref,
                              text=f"[không lưu nội dung: {st.st_size:,} byte, vượt NMEMO_MAX_FILE_MB]")
    with open(disk_path, "rb") as f:
        data = f.read()
    sha = store.put_blob(data)
    if store.last_file_blob(p) == sha:
        return None
    text = decode_text(data)
    return store.add_item(session=session, ts=time.time(), kind="file", tool=tool, path=p, blob=sha, ref=ref,
                          text=text if text is not None else f"[file nhị phân {len(data):,} byte]")


def scan_changed(store: Store, root: str, since: float, *, session: int, max_files: int = 300,
                 max_entries: int = 50_000, budget_s: float = 5.0) -> int:
    """Snapshot files under `root` modified since `since` (catches files made by shell commands)."""
    root = os.path.abspath(root)
    if root in (os.path.abspath(os.sep), str(Path.home())) and os.environ.get("NMEMO_SCAN") != "force":
        return 0
    store_dir = str(store.home.resolve())
    start, seen, saved = time.time(), 0, 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and os.path.join(dirpath, d) != store_dir]
        for name in filenames:
            seen += 1
            if seen > max_entries or saved >= max_files or time.time() - start > budget_s:
                return saved
            p = os.path.join(dirpath, name)
            try:
                st = os.lstat(p)
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode) and st.st_mtime >= since and snapshot_file(store, p, session=session, tool="scan"):
                saved += 1
    return saved
