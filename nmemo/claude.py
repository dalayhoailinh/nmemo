"""Claude Code transcripts (~/.claude/projects/<project>/<session>.jsonl): one JSON object per line.

`parts(obj)` turns one line into searchable parts. The raw line itself is stored byte-for-byte
elsewhere; a part only remembers where it lives inside that line (`ptr`), so reading a part back
re-derives it from the raw line with the same code.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

PATH_KEYS = ("file_path", "notebook_path", "path")


@dataclass
class Part:
    ptr: str            # "c" = message.content (string), "c3" = message.content[3], "summary"
    kind: str           # user | assistant | thinking | tool_call | tool_result | image | summary | context
    text: str
    tool: str | None = None
    path: str | None = None
    ref: str | None = None   # tool_use_id linking a call, its result and file snapshots


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=1)


def render_tool_use(name: str, inp: dict) -> str:
    """Readable and complete rendering of a tool call (the raw JSON stays in the event)."""
    if not isinstance(inp, dict):
        return f"{name} {_dump(inp)}"
    if name == "Bash":
        desc = inp.get("description")
        return f"$ {inp.get('command', '')}" + (f"\n# {desc}" if desc else "")
    if name == "Write":
        return f"Write {inp.get('file_path')}\n{inp.get('content', '')}"
    if name == "Edit":
        return (f"Edit {inp.get('file_path')}"
                + (" (replace_all)" if inp.get("replace_all") else "")
                + f"\n<<<<<<< cũ\n{inp.get('old_string', '')}\n=======\n{inp.get('new_string', '')}\n>>>>>>> mới")
    if name == "MultiEdit":
        chunks = [f"<<<<<<< cũ\n{e.get('old_string', '')}\n=======\n{e.get('new_string', '')}\n>>>>>>> mới"
                  for e in inp.get("edits", []) if isinstance(e, dict)]
        return f"MultiEdit {inp.get('file_path')}\n" + "\n".join(chunks)
    return f"{name} {_dump(inp)}"


def _result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for block in content:
            if not isinstance(block, dict):
                out.append(str(block))
            elif block.get("type") == "text":
                out.append(block.get("text", ""))
            elif block.get("type") == "image":
                out.append("[ảnh]")
            else:
                out.append(_dump(block))
        return "\n".join(out)
    return "" if content is None else _dump(content)


def input_path(inp) -> str | None:
    if isinstance(inp, dict):
        for key in PATH_KEYS:
            if isinstance(inp.get(key), str):
                return inp[key]
    return None


def parts(obj: dict) -> list[Part]:
    kind_of_line = obj.get("type")
    message = obj.get("message") if isinstance(obj.get("message"), dict) else {}
    content = message.get("content")
    out: list[Part] = []

    if kind_of_line == "user":
        meta = bool(obj.get("isMeta"))
        if isinstance(content, str):
            out.append(Part("c", "context" if meta else "user", content))
        elif isinstance(content, list):
            for i, block in enumerate(content):
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    out.append(Part(f"c{i}", "context" if meta else "user", block.get("text", "")))
                elif btype == "tool_result":
                    text = _result_text(block.get("content"))
                    if block.get("is_error"):
                        text = "[lỗi] " + text
                    out.append(Part(f"c{i}", "tool_result", text, ref=block.get("tool_use_id")))
                elif btype == "image":
                    out.append(Part(f"c{i}", "image", "[ảnh]"))
    elif kind_of_line == "assistant" and isinstance(content, list):
        for i, block in enumerate(content):
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text" and block.get("text"):
                out.append(Part(f"c{i}", "assistant", block["text"]))
            elif btype == "thinking" and block.get("thinking"):
                out.append(Part(f"c{i}", "thinking", block["thinking"]))
            elif btype in ("tool_use", "server_tool_use"):
                name, inp = block.get("name", "?"), block.get("input")
                out.append(Part(f"c{i}", "tool_call", render_tool_use(name, inp),
                                tool=name, path=input_path(inp), ref=block.get("id")))
            elif btype and btype.endswith("_tool_result"):
                out.append(Part(f"c{i}", "tool_result", _result_text(block.get("content")),
                                ref=block.get("tool_use_id")))
    elif kind_of_line == "summary" and isinstance(obj.get("summary"), str):
        out.append(Part("summary", "summary", obj["summary"]))
    elif kind_of_line == "system" and isinstance(obj.get("content"), str):
        out.append(Part("system", "context", obj["content"]))
    return out


def extract(obj: dict, ptr: str) -> Part | None:
    for part in parts(obj):
        if part.ptr == ptr:
            return part
    return None


def session_fields(obj: dict) -> dict:
    """Metadata worth keeping on the session row."""
    fields = {}
    for src, dst in (("sessionId", "uuid"), ("cwd", "cwd"), ("gitBranch", "branch")):
        if isinstance(obj.get(src), str) and obj[src]:
            fields[dst] = obj[src]
    if obj.get("isSidechain") and isinstance(obj.get("agentId"), str):
        fields["agent"] = obj["agentId"]
    return fields


_NOT_TYPED = ("[Request interrupted", "<command-", "<local-command-", "Caveat: ")


def is_human_prompt(part: Part) -> bool:
    """A prompt the person typed (not an interruption marker or slash-command plumbing)."""
    return part.kind == "user" and not part.text.lstrip().startswith(_NOT_TYPED)
