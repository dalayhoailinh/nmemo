"""Minimal MCP server over stdio (newline-delimited JSON-RPC 2.0), standard library only.

The cloud model calls these tools; only what it asks for leaves the machine.
"""

from __future__ import annotations

import json
import sys
import time
import traceback

from . import __version__, views
from . import text as T
from .store import Store

KINDS = ["user", "assistant", "tool_call", "tool_result", "file", "note", "thinking", "summary", "context", "image"]

INSTRUCTIONS = (
    "nmemo is the user's local, lossless memory of past AI sessions: their messages, your replies, the "
    "commands you ran, tool results, and full contents of files you created or edited. Use it whenever the "
    "user refers to earlier work or asks what was said/done/decided before. Start with `sessions` or "
    "`search`, then `read` the ids you need. Quote memory only from `read` output, and cite ids (i123)."
)

_PAGE = {
    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
    "offset": {"type": "integer", "minimum": 0, "default": 0},
}

TOOLS = [
    {
        "name": "search",
        "description": ("Full-text search over everything recorded: user messages, assistant replies, tool calls "
                        "(shell commands, file writes/edits with full content), tool results, file snapshots, notes. "
                        "Accents optional (\"duong\" finds \"đường\"). Returns one line per hit with its id and the "
                        "total hit count; page with offset when you need all hits. Empty query lists the most recent items."),
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Words that must all appear, or \"an exact phrase\"; word* for prefix."},
            "kind": {"type": "array", "items": {"type": "string", "enum": KINDS},
                     "description": "Only these kinds (default: all but context/image)."},
            "session": {"type": "string", "description": "Only this session, e.g. s12."},
            "since": {"type": "string", "description": "From this day, YYYY-MM-DD."},
            "until": {"type": "string", "description": "Up to and including this day, YYYY-MM-DD."},
            "path": {"type": "string", "description": "Only items whose file path contains this."},
            **_PAGE}},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "read",
        "description": ("Exact stored content of an id. i<n> = an item (prompt, reply, tool call, tool result, file "
                        "version, note); e<n> = the raw transcript line, byte-for-byte; s<n> = a session overview. "
                        "Long content is paged by characters; `around` adds neighbouring items."),
        "inputSchema": {"type": "object", "required": ["id"], "properties": {
            "id": {"type": "string", "description": "i123, e45 or s6"},
            "offset": {"type": "integer", "minimum": 0, "default": 0},
            "limit": {"type": "integer", "minimum": 100, "maximum": 100000, "default": views.READ_LIMIT},
            "around": {"type": "integer", "minimum": 0, "maximum": 20, "default": 0}}},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "sessions",
        "description": "List recorded sessions, newest first: id, time, folder, title, counts of prompts/commands/files.",
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Filter by words in the title or folder."},
            "cwd": {"type": "string", "description": "Only sessions run in exactly this folder."},
            **_PAGE}},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "timeline",
        "description": "What happened in one session, in order, one line per item (asked, replied, ran, wrote).",
        "inputSchema": {"type": "object", "required": ["session"], "properties": {
            "session": {"type": "string", "description": "s12"},
            "kind": {"type": "array", "items": {"type": "string", "enum": KINDS},
                     "description": "Default: user, assistant, tool_call, file, note, summary."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            "offset": {"type": "integer", "minimum": 0, "default": 0}}},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "remember",
        "description": "Save a durable note (decision, fact, preference) the user wants kept; searchable later as kind 'note'.",
        "inputSchema": {"type": "object", "required": ["text"], "properties": {
            "text": {"type": "string"}}},
    },
]


def _session(value) -> int | None:
    if value in (None, ""):
        return None
    kind, num = views.parse_id(value if str(value)[:1].isalpha() else f"s{value}")
    if kind != "s":
        raise ValueError("session phải có dạng s12")
    return num


def call(store: Store, name: str, args: dict) -> str:
    page = {"limit": int(args.get("limit", 10)), "offset": int(args.get("offset", 0))}
    if name == "search":
        return views.search(store, args.get("query", ""), kinds=args.get("kind") or None,
                            session=_session(args.get("session")), since=T.parse_day(args.get("since")),
                            until=T.parse_day(args.get("until"), end=True), path=args.get("path"),
                            limit=min(page["limit"], 50), offset=page["offset"])
    if name == "read":
        return views.read(store, args["id"], offset=int(args.get("offset", 0)),
                          limit=int(args.get("limit", views.READ_LIMIT)), around=int(args.get("around", 0)))
    if name == "sessions":
        return views.sessions(store, query=args.get("query"), cwd=args.get("cwd"), **page)
    if name == "timeline":
        return views.timeline(store, _session(args["session"]), kinds=args.get("kind") or None,
                              limit=int(args.get("limit", 50)), offset=page["offset"])
    if name == "remember":
        text = str(args["text"]).strip()
        if not text:
            raise ValueError("text trống")
        with store.tx():
            sha = store.put_blob(text.encode("utf-8"))
            iid = store.add_item(session=None, ts=time.time(), kind="note", text=text, blob=sha)
        return f"Đã lưu ghi chú i{iid}."
    raise ValueError(f"không có công cụ {name}")


def _reply(msg_id, result=None, error=None) -> None:
    out = {"jsonrpc": "2.0", "id": msg_id}
    out["error" if error else "result"] = error or result
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()


def serve(home: str | None = None) -> int:
    store = Store(home)
    for raw in sys.stdin.buffer:
        if not raw.strip():
            continue
        try:
            msg = json.loads(raw)
        except ValueError:
            _reply(None, error={"code": -32700, "message": "parse error"})
            continue
        if not isinstance(msg, dict) or "id" not in msg:
            continue  # notifications (e.g. notifications/initialized) need no answer
        method, params = msg.get("method"), msg.get("params") or {}
        if method == "initialize":
            _reply(msg["id"], {
                "protocolVersion": params.get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "nmemo", "version": __version__},
                "instructions": INSTRUCTIONS,
            })
        elif method == "ping":
            _reply(msg["id"], {})
        elif method == "tools/list":
            _reply(msg["id"], {"tools": TOOLS})
        elif method == "tools/call":
            try:
                text, is_error = call(store, params.get("name"), params.get("arguments") or {}), False
            except (KeyError, ValueError) as e:
                text, is_error = f"Lỗi: {e}", True
            except Exception:
                text, is_error = "Lỗi nội bộ nmemo:\n" + traceback.format_exc(limit=3), True
            _reply(msg["id"], {"content": [{"type": "text", "text": text}], "isError": is_error})
        else:
            _reply(msg["id"], error={"code": -32601, "message": f"method not found: {method}"})
    store.close()
    return 0
