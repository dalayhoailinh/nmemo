"""Compact text views shared by the MCP tools, the CLI and the SessionStart context."""

from __future__ import annotations

import base64
import hashlib
import math
import os
import re

from . import text as T
from .store import Store

READ_LIMIT = 20_000


def parse_id(value: str) -> tuple[str, int]:
    m = re.fullmatch(r"\s*\[?([iesIES])(\d+)\]?\s*", str(value))
    if not m:
        raise ValueError(f"id không hợp lệ: {value!r} (dạng i123, e45, s6)")
    return m.group(1).lower(), int(m.group(2))


def _path(store: Store, row) -> str | None:
    """File paths relative to the session folder: shorter for the model, same meaning."""
    path = row["path"]
    if not path or not row["session"]:
        return path
    cache = store.__dict__.setdefault("_cwd_cache", {})
    if row["session"] not in cache:
        srow = store.session(row["session"])
        cache[row["session"]] = (srow["cwd"] or "").rstrip("/\\") if srow else ""
    cwd = cache[row["session"]]
    return path[len(cwd) + 1:] if cwd and path.startswith(cwd) and path[len(cwd):len(cwd) + 1] in "/\\" else path


def _label(store: Store, row) -> str:
    return " ".join(x for x in (row["kind"], row["tool"], _path(store, row)) if x)


def _shown(row, text: str) -> str:
    """Drop the 'Write /path' header a tool-call rendering repeats from the label."""
    if row["kind"] == "tool_call" and row["tool"] and text.startswith(row["tool"] + " "):
        text = text[len(row["tool"]) + 1:]
        if row["path"] and text.startswith(row["path"]):
            text = text[len(row["path"]):]
    return text


def item_line(store: Store, row, query: str = "", width: int = 200) -> str:
    text = row["preview"] or ""
    if query and row["size"] > len(text):
        text = store.item_text(row)
    snip = T.snippet(_shown(row, text), query, width) if text else ""
    sess = f"s{row['session']} " if row["session"] else ""
    return f"[i{row['id']}] {T.fmt_ts(row['ts'])} {sess}{_label(store, row)}: {snip}"


def search(store: Store, query: str = "", *, kinds=None, session=None, since=None, until=None, path=None,
           limit: int = 10, offset: int = 0) -> str:
    total, rows = store.search(query, kinds=kinds, session=session, since=since, until=until, path=path,
                               limit=limit, offset=offset)
    what = f'"{query}"' if query else "mục gần nhất"
    if not rows:
        return f"Không thấy {what} ({total} kết quả)."
    head = f"{what}: {total} kết quả, hiện {offset + 1}–{offset + len(rows)}."
    if offset + len(rows) < total:
        head += f" Xem tiếp: offset={offset + len(rows)}."
    return "\n".join([head] + [item_line(store, r, query) for r in rows] + ["Nguyên văn: read(id)."])


def read(store: Store, ident: str, *, offset: int = 0, limit: int = READ_LIMIT, around: int = 0) -> str:
    kind, num = parse_id(ident)
    if kind == "s":
        return session_overview(store, num)
    if kind == "e":
        data = store.event_bytes(num)
        row = store.event(num)
        body = data.decode("utf-8", errors="replace")
        head = (f"[e{num}] s{row['session']} dòng {row['line']} · {T.fmt_ts(row['ts'])} · {row['type']} · "
                f"{len(data):,} byte · sha256 {hashlib.sha256(data).hexdigest()[:16]}… (nguyên văn từng byte)")
        return _page(head, body, offset, limit, ident)
    row = store.item(num)
    if row is None:
        raise KeyError(f"không có i{num}")
    body = store.item_text(row)
    sha = (row["blob"].hex() if row["blob"] is not None
           else hashlib.sha256(body.encode()).hexdigest())[:16]
    src = f" · từ e{row['event']}" if row["event"] else ""
    head = (f"[i{num}] s{row['session']} · {T.fmt_ts(row['ts'])} · "
            f"{' '.join(x for x in (row['kind'], row['tool'], row['path']) if x)}{src} · sha256 {sha}…")
    out = _page(head, body, offset, limit, ident)
    if around and row["session"]:
        before = store.db.execute(
            "SELECT * FROM item WHERE session=? AND (ts<? OR (ts=? AND id<?)) AND kind NOT IN ('context','image')"
            " ORDER BY ts DESC, id DESC LIMIT ?", (row["session"], row["ts"], row["ts"], num, around)).fetchall()
        after = store.db.execute(
            "SELECT * FROM item WHERE session=? AND (ts>? OR (ts=? AND id>?)) AND kind NOT IN ('context','image')"
            " ORDER BY ts, id LIMIT ?", (row["session"], row["ts"], row["ts"], num, around)).fetchall()
        ctx = [item_line(store, r) for r in reversed(before)] + ["   ^ trước / sau v"] + [item_line(store, r) for r in after]
        out += "\nNgữ cảnh:\n" + "\n".join(ctx)
    return out


def _page(head: str, body: str, offset: int, limit: int, ident: str) -> str:
    chunk = body[offset:offset + limit]
    end = offset + len(chunk)
    info = f"ký tự {offset}–{end} / {len(body):,}"
    if end < len(body):
        info += f" · còn tiếp: read({ident!r}, offset={end})"
    return f"{head}\n{info}\n-----\n{chunk}"


def session_line(row) -> str:
    title = (row["title"] or "").replace("\n", " ")[:90]
    span = T.fmt_ts(row["started"])
    if row["ended"] and row["started"] and row["ended"] - row["started"] > 60:
        same_day = T.fmt_ts(row["started"])[:10] == T.fmt_ts(row["ended"])[:10]
        span += "→" + T.fmt_ts(row["ended"], with_date=not same_day)
    return (f"[s{row['id']}] {span} · {row['cwd'] or '?'} · {row['prompts']} lượt hỏi, "
            f"{row['calls']} lệnh, {row['files']} file · \"{title}\"")


def sessions(store: Store, *, query=None, cwd=None, limit: int = 10, offset: int = 0) -> str:
    total, rows = store.sessions(query=query, cwd=cwd, limit=limit, offset=offset)
    st = store.stats()
    head = (f"{total} phiên{' khớp' if query or cwd else ''} (cả kho: {st['events']:,} sự kiện, "
            f"{st['items']:,} mục, {st['file_versions']:,} phiên bản file). Hiện {offset + 1}–{offset + len(rows)}:")
    if not rows:
        return f"Chưa có phiên nào{' khớp' if query or cwd else ''}."
    return "\n".join([head] + [session_line(r) for r in rows] + ["Chi tiết: timeline(session) hoặc read('s<id>')."])


def timeline(store: Store, session: int, *, kinds=None, limit: int = 50, offset: int = 0) -> str:
    srow = store.session(session)
    if srow is None:
        raise KeyError(f"không có s{session}")
    total, rows = store.timeline(session, kinds=kinds, limit=limit, offset=offset)
    head = f"[s{session}] {srow['cwd'] or '?'} · {T.fmt_ts(srow['started'])} · {total} mục, hiện {offset + 1}–{offset + len(rows)}"
    if offset + len(rows) < total:
        head += f" · xem tiếp: offset={offset + len(rows)}"
    lines = [head]
    for r in rows:
        snip = T.snippet(_shown(r, r["preview"] or ""), "", 160)
        lines.append(f"[i{r['id']}] {T.fmt_ts(r['ts'], with_date=False)} {_label(store, r)}: {snip}")
    return "\n".join(lines)


def session_overview(store: Store, sid: int) -> str:
    s = store.session(sid)
    if s is None:
        raise KeyError(f"không có s{sid}")
    counts = store.db.execute("SELECT kind, count(*) FROM item WHERE session=? GROUP BY kind ORDER BY 2 DESC",
                              (sid,)).fetchall()
    files = store.db.execute(
        "SELECT path, count(*) FROM item WHERE session=? AND kind='file' GROUP BY path ORDER BY max(id) DESC LIMIT 30",
        (sid,)).fetchall()
    prompts = store.db.execute(
        "SELECT id, preview FROM item WHERE session=? AND kind='user' ORDER BY ts, id LIMIT 8", (sid,)).fetchall()
    subs = store.db.execute("SELECT id, agent FROM session WHERE parent=?", (sid,)).fetchall()
    digest = store.one("SELECT * FROM item WHERE session=? AND kind='summary' AND tool='digest' ORDER BY id DESC LIMIT 1", sid)
    lines = [
        f"[s{sid}] {s['title'] or ''}",
        f"thư mục: {s['cwd']} · nhánh git: {s['branch']} · {T.fmt_ts(s['started'])} → {T.fmt_ts(s['ended'])}",
        f"transcript gốc: {s['key']}",
        "đếm: " + ", ".join(f"{k} {n}" for k, n in counts),
    ]
    if digest:
        lines.append(f"tóm tắt (i{digest['id']}):\n" + store.item_text(digest))
    if subs:
        lines.append("subagent: " + ", ".join(f"s{r['id']} ({r['agent']})" for r in subs))
    if files:
        lines.append("file đã lưu: " + ", ".join(f"{p} (×{n})" for p, n in files))
    lines.append("các câu hỏi đầu:")
    lines += [f"  [i{r['id']}] {r['preview'][:150]}" for r in prompts]
    lines.append(f"Toàn bộ diễn biến: timeline({sid}).")
    return "\n".join(lines)


RECALL_KINDS = ("user", "assistant", "thinking", "tool_call", "file", "note", "fact", "summary")
DISTILLED = ("summary", "fact", "note")  # understanding beats raw fragments when both match
IMAGE_LIMIT = 5 * 1024 * 1024


def recall(store: Store, prompt: str, *, exclude_session=None, skip=(), budget: int = 1500,
           max_hits: int = 5) -> tuple[str, list[int]]:
    """Memories from earlier sessions that share enough meaningful words with a new message.

    Words that occur in a large share of all items say little, so they are ignored; an item must
    contain at least ~30% of the remaining words (at least 2, at most 4) to be shown."""
    total = store.one("SELECT count(*) FROM item")[0] or 1
    common = max(50, 0.15 * total)
    freq = {w: store.doc_freq(w) for w in T.terms(prompt)}
    words = [w for w, df in freq.items() if df <= common]
    if not words or (len(words) == 1 and (len(words[0]) < 4 or freq[words[0]] > 20)):
        return "", []
    need = 1 if len(words) == 1 else min(4, max(2, math.ceil(0.3 * len(words))))
    wanted = {T.fold(w) for w in words}
    picked = []
    for row in store.search_any(words, kinds=RECALL_KINDS, exclude_session=exclude_session):
        if row["id"] in skip:
            continue
        text = row["preview"] or ""
        if row["size"] > len(text):
            text = store.item_text(row)[:50_000]
        found = set(re.findall(r"\w+", T.fold(" ".join(x for x in (row["tool"], row["path"], text) if x))))
        hits = len(wanted & found)
        if hits >= need:
            rank = hits + (1 if row["kind"] in DISTILLED else 0)
            picked.append((-rank, row["score"], -(row["ts"] or 0), row["id"], row, text))
    picked.sort(key=lambda p: p[:4])
    lines, ids, per_session, used = [], [], {}, 0
    for *_, row, text in picked:
        if len(lines) >= max_hits:
            break
        if per_session.get(row["session"], 0) >= 3:
            continue
        label = " ".join(x for x in (row["kind"], row["tool"], row["path"]) if x)
        sess = f"s{row['session']} " if row["session"] else ""
        line = f"[i{row['id']}] {T.fmt_ts(row['ts'])} {sess}{label}: {T.snippet(_shown(row, text), ' '.join(words), 180)}"
        if used + len(line) > budget:
            break
        lines.append(line)
        ids.append(row["id"])
        used += len(line)
        per_session[row["session"]] = per_session.get(row["session"], 0) + 1
    if not lines:
        return "", []
    for sid in list(per_session)[:3]:  # what those sessions produced is often what the user means
        files = _session_files(store, sid)
        if sid and files:
            lines.append(f"  file của s{sid}: " + ", ".join(f"{p} (i{i})" for p, i in files))
    head = ("[nmemo] Tự tìm trong bộ nhớ theo tin nhắn này; có thể liên quan "
            "(xem nguyên văn: read(id), tìm thêm: search):")
    return head + "\n" + "\n".join(lines), ids


def _session_files(store: Store, sid, limit: int = 4) -> list[tuple[str, int]]:
    """Latest files a session wrote: snapshots if hooks ran, else the paths of its Write/Edit calls."""
    rows = store.db.execute(
        "SELECT path, max(id) FROM item WHERE session=? AND kind='file' GROUP BY path ORDER BY max(id) DESC LIMIT ?",
        (sid, limit)).fetchall()
    if not rows:
        rows = store.db.execute(
            "SELECT path, max(id) FROM item WHERE session=? AND kind='tool_call' AND path IS NOT NULL"
            " AND tool IN ('Write','Edit','MultiEdit','NotebookEdit') GROUP BY path ORDER BY max(id) DESC LIMIT ?",
            (sid, limit)).fetchall()
    return [(r[0], r[1]) for r in rows]


def _sniff_image(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def image_payload(store: Store, ident: str) -> tuple[str, str] | None:
    """(base64, media type) when an item is a picture: one the user sent, or an image file."""
    try:
        kind, num = parse_id(ident)
    except ValueError:
        return None
    row = store.item(num) if kind == "i" else None
    if row is None:
        return None
    if row["kind"] == "image" and row["event"] and (row["ptr"] or "")[1:].isdigit():
        content = ((store.event_obj(row["event"]) or {}).get("message") or {}).get("content")
        idx = int(row["ptr"][1:])
        block = content[idx] if isinstance(content, list) and idx < len(content) else {}
        src = block.get("source") if isinstance(block, dict) else None
        if isinstance(src, dict) and src.get("type") == "base64" and src.get("data"):
            return src["data"], src.get("media_type") or "image/png"
    if row["blob"] is not None:
        data = store.get_blob(row["blob"])
        media = _sniff_image(data)
        if media and len(data) <= IMAGE_LIMIT:
            return base64.b64encode(data).decode("ascii"), media
    return None


def start_context(store: Store, payload: dict) -> str:
    """Short memory map injected at session start, so the model knows what it did before."""
    cwd = payload.get("cwd")
    current = store.source(payload.get("transcript_path") or "")
    current_id = current["session"] if current else None
    _, rows = store.sessions(cwd=cwd, limit=6)
    rows = [r for r in rows if r["id"] != current_id][:5]
    where = "ở thư mục này"
    if not rows:
        _, rows = store.sessions(limit=6)
        rows = [r for r in rows if r["id"] != current_id][:5]
        where = "gần đây"
    lines = ["[nmemo] Bộ nhớ cục bộ đang lưu nguyên văn phiên này: tin nhắn, câu trả lời, lệnh đã chạy, "
             "nội dung file đã tạo/sửa" + (f" (phiên hiện tại: s{current_id})." if current_id else ".")]
    profile_path = store.home / "profile.md"
    if profile_path.exists():
        limit = int(os.environ.get("NMEMO_PROFILE_CHARS", "2500"))
        profile = profile_path.read_text(encoding="utf-8").strip()
        if profile:
            cut = profile[:limit] + ("\n…" if len(profile) > limit else "")
            lines.append("Hồ sơ người dùng (tự cập nhật sau mỗi phiên; người dùng có thể sửa profile.md):\n" + cut)
    if rows:
        lines.append(f"Phiên trước {where}:")
        lines += ["- " + session_line(r) for r in rows]
    lines.append("Khi cần biết trước đây đã nói/làm gì: dùng công cụ nmemo (sessions, search, timeline, read); "
                 "trích nguyên văn bằng read, đừng đoán.")
    return "\n".join(lines)
