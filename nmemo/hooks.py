"""Claude Code hook entry point: `nmemo hook` reads one event as JSON on stdin.

SessionStart       ingest what exists, start a one-time background import of old transcripts,
                   inject a short memory map as additionalContext
UserPromptSubmit   remember when the turn started (to find files changed during the turn)
PostToolUse        snapshot the full content of the file a Write/Edit/MultiEdit/NotebookEdit touched
Stop               ingest the turn from the transcript + snapshot other files changed during the turn
SubagentStop       ingest the subagent's own transcript, linked to its parent session
PreCompact         ingest everything before the context is compacted
SessionEnd         ingest the rest and pack pending lines into compressed frames

A hook must never break the session: every error is logged to <NMEMO_HOME>/nmemo.log and the
process still exits 0.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

from . import claude, ingest, views
from . import text as T
from .store import Store, default_home

LAUNCHER = Path(__file__).resolve().parent.parent / "bin" / "nmemo"


def log(message: str, home: str | None = None) -> None:
    try:
        home = Path(home).expanduser() if home else default_home()
        home.mkdir(parents=True, exist_ok=True)
        with open(home / "nmemo.log", "a", encoding="utf-8") as f:
            f.write(f"{T.fmt_ts(time.time())} {message}\n")
    except OSError:
        pass


def start_backfill(store: Store) -> None:
    """Import transcripts that existed before nmemo was installed, once, in the background."""
    if os.environ.get("NMEMO_BACKFILL") == "0" or store.meta_get("backfill"):
        return
    store.meta_set("backfill", T.fmt_ts(time.time()))
    projects = ingest.claude_projects_dir()
    if projects.is_dir():
        subprocess.Popen([sys.executable, str(LAUNCHER), "--home", str(store.home), "import", str(projects)],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)


def handle(payload: dict, store: Store) -> dict | None:
    event = payload.get("hook_event_name")
    transcript = payload.get("transcript_path") or ""
    cwd = payload.get("cwd") or os.getcwd()

    if event == "SessionStart":
        if transcript:
            ingest.ingest_transcript(store, transcript)
        start_backfill(store)
        if os.environ.get("NMEMO_CONTEXT") == "0":
            return None
        return {"hookSpecificOutput": {"hookEventName": "SessionStart",
                                       "additionalContext": views.start_context(store, payload)}}

    if event == "UserPromptSubmit":
        store.meta_set(f"turn:{transcript}", str(time.time()))
        return None

    if event == "PostToolUse":
        path = claude.input_path(payload.get("tool_input"))
        if path:
            path = path if os.path.isabs(path) else os.path.join(cwd, path)
            with store.tx():
                sid = store.session_for(os.path.abspath(transcript) if transcript else f"session:{payload.get('session_id')}")
                ingest.snapshot_file(store, path, session=sid, tool=payload.get("tool_name"),
                                     ref=payload.get("tool_use_id"))
        return None

    if event in ("Stop", "PreCompact", "SessionEnd"):
        result = ingest.ingest_transcript(store, transcript) if transcript else {"session": None}
        if event == "Stop" and result["session"] and os.environ.get("NMEMO_SCAN") != "0":
            key = f"turn:{transcript}"
            since = float(store.meta_get(key) or 0)
            if since:
                with store.tx():
                    ingest.scan_changed(store, cwd, since - 1, session=result["session"])
                    store.meta_set(key, str(time.time()))
        if event == "SessionEnd":
            with store.tx():
                store.seal(force=True)
            store.checkpoint()
        return None

    if event == "SubagentStop":
        path = payload.get("agent_transcript_path")
        if path:
            ingest.ingest_transcript(store, path, agent=payload.get("agent_id"),
                                     parent_uuid=payload.get("session_id"))
        return None
    return None


def main(home: str | None = None) -> int:
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
        if os.environ.get("NMEMO_DISABLE") == "1":
            return 0
        store = Store(home)
        try:
            out = handle(payload, store)
        finally:
            store.close()
        if out:
            sys.stdout.write(json.dumps(out))
    except Exception:  # never break the user's session
        log("hook error:\n" + traceback.format_exc(), home)
    return 0
