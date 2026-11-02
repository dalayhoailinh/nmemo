"""The local store: one SQLite file.

- event: every transcript line, byte-for-byte, with its SHA-256. New lines wait uncompressed in
  the `pending` table; once ~64 KB have gathered they are packed into one compressed `frame`, so
  reading a single line back only means decompressing one small frame.
- blob: file contents addressed by SHA-256 (the same content is stored once).
- item: searchable pieces derived from events and blobs (a prompt, a reply, a tool call...).
  Items hold only a pointer and a short preview; full text is always re-read from the raw data.
- item_fts: full-text index with an accent-folded copy, so "duong" finds "đường".
"""

from __future__ import annotations

import hashlib
import json
import lzma
import os
import sqlite3
import zlib
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path

from . import claude
from . import text as T

FRAME_TARGET = 64 * 1024
INDEX_CHARS = 300_000
PREVIEW_CHARS = 240
HIDDEN_KINDS = ("context", "image", "summary_old", "fact_old", "profile_old")

CODECS = {
    "raw": (lambda b: b, lambda b: b),
    "xz": (lambda b: lzma.compress(b, preset=6), lzma.decompress),
    "zlib": (lambda b: zlib.compress(b, 9), zlib.decompress),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS session(
  id INTEGER PRIMARY KEY, key TEXT UNIQUE NOT NULL, uuid TEXT, agent TEXT, parent INTEGER,
  app TEXT, cwd TEXT, branch TEXT, title TEXT, started REAL, ended REAL);
CREATE INDEX IF NOT EXISTS session_uuid ON session(uuid);
CREATE TABLE IF NOT EXISTS source(
  path TEXT PRIMARY KEY, session INTEGER NOT NULL, offset INTEGER NOT NULL, lines INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS frame(
  id INTEGER PRIMARY KEY, codec TEXT NOT NULL, raw_size INTEGER NOT NULL, data BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS event(
  id INTEGER PRIMARY KEY, session INTEGER NOT NULL, line INTEGER NOT NULL, uuid TEXT, type TEXT,
  ts REAL, frame INTEGER, off INTEGER, len INTEGER NOT NULL, dup_of INTEGER,
  sha256 BLOB NOT NULL, UNIQUE(session, line));
CREATE INDEX IF NOT EXISTS event_uuid ON event(uuid) WHERE uuid IS NOT NULL;
CREATE INDEX IF NOT EXISTS event_sha ON event(sha256);
CREATE TABLE IF NOT EXISTS pending(event INTEGER PRIMARY KEY, data BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS blob(
  sha256 BLOB PRIMARY KEY, size INTEGER NOT NULL, codec TEXT NOT NULL, data BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS item(
  id INTEGER PRIMARY KEY, session INTEGER, ts REAL, kind TEXT NOT NULL, tool TEXT, path TEXT,
  event INTEGER, ptr TEXT, blob BLOB, ref TEXT, size INTEGER NOT NULL, preview TEXT);
CREATE INDEX IF NOT EXISTS item_session ON item(session, ts);
CREATE INDEX IF NOT EXISTS item_ts ON item(ts);
CREATE INDEX IF NOT EXISTS item_path ON item(path) WHERE path IS NOT NULL;
CREATE INDEX IF NOT EXISTS item_ref ON item(ref) WHERE ref IS NOT NULL;
CREATE VIRTUAL TABLE IF NOT EXISTS item_fts USING fts5(
  body, folded, content='', tokenize='unicode61 remove_diacritics 0');
CREATE VIRTUAL TABLE IF NOT EXISTS item_vocab USING fts5vocab('item_fts', 'row');
"""


def default_home() -> Path:
    return Path(os.environ.get("NMEMO_HOME") or Path.home() / ".nmemo").expanduser()


def decode_text(data: bytes) -> str | None:
    if b"\x00" in data[:8192]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


class Store:
    def __init__(self, home: str | Path | None = None):
        self.home = Path(home).expanduser() if home else default_home()
        self.home.mkdir(parents=True, exist_ok=True)
        self.path = self.home / "nmemo.db"
        fresh = not self.path.exists()
        self.db = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        if fresh:
            for p, mode in ((self.home, 0o700), (self.path, 0o600)):
                try:
                    os.chmod(p, mode)
                except OSError:
                    pass
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout=30000")
        if fresh:
            self.db.execute("PRAGMA auto_vacuum=INCREMENTAL")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        try:
            self.db.executescript(SCHEMA)
        except sqlite3.OperationalError as e:
            if "fts5" in str(e).lower():
                raise RuntimeError("SQLite của Python này không có FTS5; hãy dùng Python từ python.org hoặc Homebrew") from e
            raise
        self.codec = os.environ.get("NMEMO_CODEC", "xz")
        if self.codec not in CODECS:
            raise ValueError(f"NMEMO_CODEC phải là một trong {sorted(CODECS)}")
        self._frames: OrderedDict[int, bytes] = OrderedDict()

    def close(self) -> None:
        self.db.close()

    def checkpoint(self) -> None:
        """Give freed pages back to the disk and fold the write-ahead log into the main file."""
        self.db.execute("PRAGMA incremental_vacuum").fetchall()  # each step frees one page
        self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    @contextmanager
    def tx(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        self.db.execute("COMMIT")

    def one(self, sql: str, *args):
        return self.db.execute(sql, args).fetchone()

    # ---- meta -------------------------------------------------------------------------------
    def meta_get(self, key: str, default: str | None = None) -> str | None:
        row = self.one("SELECT value FROM meta WHERE key=?", key)
        return row[0] if row else default

    def meta_set(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))

    # ---- sessions ---------------------------------------------------------------------------
    def session_for(self, key: str, app: str = "claude-code") -> int:
        row = self.one("SELECT id FROM session WHERE key=?", key)
        if row:
            return row[0]
        return self.db.execute("INSERT INTO session(key, app) VALUES (?, ?)", (key, app)).lastrowid

    def session_by_uuid(self, uuid: str | None) -> int | None:
        if not uuid:
            return None
        row = self.one("SELECT id FROM session WHERE uuid=? AND agent IS NULL ORDER BY id LIMIT 1", uuid)
        return row[0] if row else None

    def update_session(self, sid: int, *, first_ts=None, last_ts=None, title=None, **fields) -> None:
        sets, args = [], []
        for key in ("uuid", "agent", "parent", "cwd", "branch"):
            if fields.get(key) is not None:
                sets.append(f"{key}=?")
                args.append(fields[key])
        if first_ts is not None:
            sets.append("started=min(coalesce(started, ?), ?)")
            args += [first_ts, first_ts]
        if last_ts is not None:
            sets.append("ended=max(coalesce(ended, ?), ?)")
            args += [last_ts, last_ts]
        if title:
            sets.append("title=coalesce(title, ?)")
            args.append(title)
        if sets:
            self.db.execute(f"UPDATE session SET {', '.join(sets)} WHERE id=?", (*args, sid))

    def session(self, sid: int):
        return self.one("SELECT * FROM session WHERE id=?", sid)

    def sessions(self, *, query: str | None = None, cwd: str | None = None, limit: int = 10, offset: int = 0):
        where, args = ["agent IS NULL"], []
        if cwd:
            where.append("cwd=?")
            args.append(cwd)
        if query:
            where.append("(title LIKE ? OR cwd LIKE ? OR key LIKE ?)")
            args += [f"%{query}%"] * 3
        cond = " AND ".join(where)
        total = self.one(f"SELECT count(*) FROM session WHERE {cond}", *args)[0]
        rows = self.db.execute(
            f"""SELECT s.*,
                  (SELECT count(*) FROM item WHERE session=s.id AND kind='user') AS prompts,
                  (SELECT count(*) FROM item WHERE session=s.id AND kind='tool_call') AS calls,
                  (SELECT count(DISTINCT path) FROM item WHERE session=s.id AND kind='file') AS files
                FROM session s WHERE {cond}
                ORDER BY coalesce(s.ended, s.started, 0) DESC LIMIT ? OFFSET ?""",
            (*args, limit, offset)).fetchall()
        return total, rows

    # ---- raw events -------------------------------------------------------------------------
    def source(self, path: str):
        return self.one("SELECT * FROM source WHERE path=?", path)

    def set_source(self, path: str, session: int, offset: int, lines: int) -> None:
        self.db.execute("INSERT OR REPLACE INTO source(path, session, offset, lines) VALUES (?,?,?,?)",
                        (path, session, offset, lines))

    def add_event(self, session: int, line: int, raw: bytes, *, uuid=None, type=None, ts=None) -> int | None:
        sha = hashlib.sha256(raw).digest()
        dup = self.one("SELECT id FROM event WHERE sha256=? AND dup_of IS NULL LIMIT 1", sha)
        cur = self.db.execute(
            "INSERT OR IGNORE INTO event(session, line, uuid, type, ts, len, dup_of, sha256) VALUES (?,?,?,?,?,?,?,?)",
            (session, line, uuid, type, ts, len(raw), dup[0] if dup else None, sha))
        if cur.rowcount != 1:
            return None
        if not dup:
            self.db.execute("INSERT INTO pending(event, data) VALUES (?, ?)", (cur.lastrowid, raw))
        return cur.lastrowid

    def uuid_seen(self, uuid: str, before_event: int) -> bool:
        return self.one("SELECT 1 FROM event WHERE uuid=? AND id<? LIMIT 1", uuid, before_event) is not None

    def last_ts(self, session: int) -> float | None:
        row = self.one("SELECT max(ts) FROM event WHERE session=?", session)
        return row[0] if row else None

    def _pack(self, data: bytes) -> tuple[bytes, str]:
        if self.codec != "raw" and len(data) >= 64:
            packed = CODECS[self.codec][0](data)
            if len(packed) < len(data) * 0.95:
                return packed, self.codec
        return data, "raw"

    def seal(self, force: bool = False) -> int:
        """Pack pending events into compressed frames of ~FRAME_TARGET bytes."""
        rows = self.db.execute("SELECT event, data FROM pending ORDER BY event").fetchall()
        if not rows or (not force and sum(len(r[1]) for r in rows) < FRAME_TARGET):
            return 0
        made, batch, size = 0, [], 0
        for rid, data in rows:
            batch.append((rid, bytes(data)))
            size += len(data)
            if size >= FRAME_TARGET:
                self._write_frame(batch)
                made, batch, size = made + 1, [], 0
        if batch and force:
            self._write_frame(batch)
            made += 1
        return made

    def _write_frame(self, batch: list[tuple[int, bytes]]) -> None:
        raw = b"".join(data for _, data in batch)
        packed, codec = self._pack(raw)
        fid = self.db.execute("INSERT INTO frame(codec, raw_size, data) VALUES (?,?,?)",
                              (codec, len(raw), packed)).lastrowid
        off = 0
        for rid, data in batch:
            self.db.execute("UPDATE event SET frame=?, off=? WHERE id=?", (fid, off, rid))
            self.db.execute("DELETE FROM pending WHERE event=?", (rid,))
            off += len(data)

    def _frame(self, fid: int) -> bytes:
        if fid in self._frames:
            self._frames.move_to_end(fid)
            return self._frames[fid]
        row = self.one("SELECT codec, data FROM frame WHERE id=?", fid)
        data = CODECS[row["codec"]][1](bytes(row["data"]))
        self._frames[fid] = data
        if len(self._frames) > 16:
            self._frames.popitem(last=False)
        return data

    def event(self, eid: int):
        return self.one("SELECT * FROM event WHERE id=?", eid)

    def event_bytes(self, eid: int) -> bytes:
        row = self.one("SELECT frame, off, len, dup_of FROM event WHERE id=?", eid)
        if row is None:
            raise KeyError(f"không có e{eid}")
        if row["dup_of"]:
            return self.event_bytes(row["dup_of"])
        if row["frame"] is None:
            return bytes(self.one("SELECT data FROM pending WHERE event=?", eid)[0])
        return self._frame(row["frame"])[row["off"]:row["off"] + row["len"]]

    def event_obj(self, eid: int) -> dict | None:
        try:
            obj = json.loads(self.event_bytes(eid))
        except ValueError:
            return None
        return obj if isinstance(obj, dict) else None

    def session_lines(self, sid: int):
        """Every stored line of a session in transcript order (for exact export)."""
        for row in self.db.execute("SELECT id FROM event WHERE session=? ORDER BY line", (sid,)).fetchall():
            yield self.event_bytes(row[0])

    # ---- blobs ------------------------------------------------------------------------------
    def put_blob(self, data: bytes) -> bytes:
        sha = hashlib.sha256(data).digest()
        if not self.one("SELECT 1 FROM blob WHERE sha256=?", sha):
            packed, codec = self._pack(data)
            self.db.execute("INSERT INTO blob(sha256, size, codec, data) VALUES (?,?,?,?)",
                            (sha, len(data), codec, packed))
        return sha

    def get_blob(self, sha: bytes) -> bytes:
        row = self.one("SELECT codec, data FROM blob WHERE sha256=?", sha)
        if row is None:
            raise KeyError(f"không có blob {sha.hex()[:16]}")
        return CODECS[row["codec"]][1](bytes(row["data"]))

    # ---- items ------------------------------------------------------------------------------
    def add_item(self, *, session, ts, kind, text, tool=None, path=None, event=None, ptr=None,
                 blob=None, ref=None, index=True) -> int:
        clean = T.nfc(text or "")
        preview = T.compact(clean[:PREVIEW_CHARS * 3])[:PREVIEW_CHARS]
        iid = self.db.execute(
            "INSERT INTO item(session, ts, kind, tool, path, event, ptr, blob, ref, size, preview)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (session, ts, kind, tool, path, event, ptr, blob, ref, len(clean), preview)).lastrowid
        if index:
            head = " ".join(x for x in (tool, path) if x)
            body = (head + "\n" if head else "") + clean[:INDEX_CHARS]
            if body.strip():
                self.db.execute("INSERT INTO item_fts(rowid, body, folded) VALUES (?,?,?)",
                                (iid, body, "" if body.isascii() else T.fold(body)))
        return iid

    def item(self, iid: int):
        return self.one("SELECT * FROM item WHERE id=?", iid)

    def item_text(self, row) -> str:
        if row["blob"] is not None:
            data = self.get_blob(row["blob"])
            text = decode_text(data)
            return text if text is not None else f"[file nhị phân {len(data):,} byte]"
        if row["event"] is not None:
            obj = self.event_obj(row["event"])
            part = claude.extract(obj, row["ptr"]) if obj else None
            return part.text if part else ""
        return row["preview"] or ""

    def call_for_ref(self, ref: str):
        return self.one("SELECT tool, path FROM item WHERE ref=? AND kind='tool_call' LIMIT 1", ref)

    def last_file_blob(self, path: str) -> bytes | None:
        row = self.one("SELECT blob FROM item WHERE kind='file' AND path=? ORDER BY id DESC LIMIT 1", path)
        return row[0] if row else None

    def search(self, query: str = "", *, kinds=None, session=None, since=None, until=None, path=None,
               limit: int = 10, offset: int = 0):
        where, args = [], []
        if kinds:
            where.append(f"item.kind IN ({','.join('?' * len(kinds))})")
            args += list(kinds)
        else:
            where.append(f"item.kind NOT IN ({','.join('?' * len(HIDDEN_KINDS))})")
            args += list(HIDDEN_KINDS)
        for cond, value in (("item.session=?", session), ("item.ts>=?", since), ("item.ts<?", until)):
            if value is not None:
                where.append(cond)
                args.append(value)
        if path:
            where.append("item.path LIKE ?")
            args.append(f"%{path}%")
        fts = T.fts_query(query) if query else ""
        if fts:
            base = "FROM item_fts JOIN item ON item.id = item_fts.rowid WHERE item_fts MATCH ?"
            base += "".join(f" AND {w}" for w in where)
            args = [fts] + args
            order = "ORDER BY bm25(item_fts, 3.0, 1.0), item.ts DESC"
        else:
            base = "FROM item WHERE " + " AND ".join(where)
            order = "ORDER BY item.ts DESC, item.id DESC"
        total = self.one(f"SELECT count(*) {base}", *args)[0]
        rows = self.db.execute(f"SELECT item.* {base} {order} LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall()
        return total, rows

    def search_any(self, words, *, kinds, exclude_session=None, limit: int = 40):
        """Items containing any of the words, best BM25 first (for automatic recall)."""
        fts = T.fts_any(words)
        if not fts:
            return []
        return self.db.execute(
            f"""SELECT item.*, bm25(item_fts, 3.0, 1.0) AS score
                FROM item_fts JOIN item ON item.id = item_fts.rowid
                WHERE item_fts MATCH ? AND item.kind IN ({','.join('?' * len(kinds))})
                  AND coalesce(item.session, -1) != ?
                ORDER BY score LIMIT ?""",
            (fts, *kinds, -1 if exclude_session is None else exclude_session, limit)).fetchall()

    def doc_freq(self, word: str) -> int:
        """In how many indexed items a word occurs (accented or folded form, whichever is larger)."""
        forms = {word, T.fold(word)}
        rows = self.db.execute(f"SELECT max(doc) FROM item_vocab WHERE term IN ({','.join('?' * len(forms))})",
                               tuple(forms)).fetchone()
        return rows[0] or 0

    def timeline(self, session: int, *, kinds=None, limit: int = 50, offset: int = 0):
        kinds = kinds or ("user", "assistant", "tool_call", "file", "note", "summary")
        cond = f"session=? AND kind IN ({','.join('?' * len(kinds))})"
        total = self.one(f"SELECT count(*) FROM item WHERE {cond}", session, *kinds)[0]
        rows = self.db.execute(f"SELECT * FROM item WHERE {cond} ORDER BY ts, id LIMIT ? OFFSET ?",
                               (session, *kinds, limit, offset)).fetchall()
        return total, rows

    # ---- health -----------------------------------------------------------------------------
    def stats(self) -> dict:
        def q(sql):
            return self.one(sql)[0] or 0
        size = sum(p.stat().st_size for p in self.home.glob("nmemo.db*") if p.is_file())
        return {
            "sessions": q("SELECT count(*) FROM session WHERE agent IS NULL"),
            "subagents": q("SELECT count(*) FROM session WHERE agent IS NOT NULL"),
            "events": q("SELECT count(*) FROM event"),
            "items": q("SELECT count(*) FROM item"),
            "file_versions": q("SELECT count(*) FROM item WHERE kind='file'"),
            "raw_bytes": q("SELECT sum(len) FROM event WHERE dup_of IS NULL") + q("SELECT sum(size) FROM blob"),
            "stored_bytes": q("SELECT sum(length(data)) FROM frame") + q("SELECT sum(length(data)) FROM pending")
            + q("SELECT sum(length(data)) FROM blob"),
            "db_bytes": size,
        }

    def verify(self) -> tuple[int, list[str]]:
        """Re-read every event and blob and check its SHA-256."""
        checked, bad = 0, []
        for eid, sha in self.db.execute("SELECT id, sha256 FROM event ORDER BY id").fetchall():
            checked += 1
            if hashlib.sha256(self.event_bytes(eid)).digest() != sha:
                bad.append(f"e{eid}")
        for (sha,) in self.db.execute("SELECT sha256 FROM blob").fetchall():
            checked += 1
            if hashlib.sha256(self.get_blob(sha)).digest() != sha:
                bad.append(f"blob {sha.hex()[:16]}")
        return checked, bad
