"""Text helpers: Vietnamese-aware folding for search, FTS5 query building, snippets, times."""

from __future__ import annotations

import re
import time
import unicodedata
from datetime import datetime

# SQLite's remove_diacritics leaves "đ" alone, so "duong" would never find "đường".
_SPECIAL = {"đ": "d", "Đ": "d"}
_TABLE: dict[int, str] = {}


def _fold_char(c: str) -> str:
    if c in _SPECIAL:
        return _SPECIAL[c]
    base = unicodedata.normalize("NFD", c)[0].lower()
    return base[0] if base else c


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def fold(text: str) -> str:
    """Lowercase and strip accents, one output char per input char (positions stay aligned)."""
    if text.isascii():
        return text.lower()
    for c in set(text):
        if ord(c) not in _TABLE:
            _TABLE[ord(c)] = _fold_char(c)
    return text.translate(_TABLE)


_TERM = re.compile(r'"([^"]*)"|(\S+)')


def fts_query(query: str) -> str:
    """User query -> FTS5 expression: every word or "quoted phrase" must match,
    with or without accents (exact-accent hits rank higher via column weights)."""
    parts = []
    for m in _TERM.finditer(nfc(query)):
        phrase, word = m.group(1), m.group(2)
        text = phrase if phrase is not None else word
        prefix = phrase is None and text.endswith("*")
        text = text.rstrip("*") if prefix else text
        if not any(ch.isalnum() for ch in text):
            continue
        star = "*" if prefix else ""
        exact = text.replace('"', '""')
        folded = fold(text).replace('"', '""')
        alts = [f'body : "{exact}"{star}', f'folded : "{folded}"{star}']
        if folded != exact.lower():  # accented query word: also match plain-ASCII text
            alts.insert(1, f'body : "{folded}"{star}')
        parts.append("(" + " OR ".join(alts) + ")")
    return " AND ".join(parts)


def query_terms(query: str) -> list[str]:
    return [fold(w) for w in re.findall(r"\w+", nfc(query))]


# Function words that say nothing about what a message is about (Vietnamese keeps its accents here:
# folded, "ma" would also drop "mã" = code).
STOPWORDS = frozenset("""
là và của có không cho với này đó thì được cái gì nào để một những các đã sẽ đang lại nữa rồi thế
như vậy ơi nhé nha ạ à ừ mình bạn tôi tớ ở ra vào lên khi nếu mà hay hoặc rằng từ trong trên sau
trước rất quá cũng vẫn đều chỉ giúp hãy xin nhỉ vâng dạ thôi kia đây ấy nhưng vì nên bị
a an and are as at be but by can could do does for from had has have how i if in into is it its me
my no not of on or our please should so that the their them then there these they this to us was
we were what when where which who why will with would you your
""".split())


def terms(text: str, limit: int = 16) -> list[str]:
    """The distinct meaningful words of a message, in order of appearance."""
    out, seen = [], set()
    for word in re.findall(r"\w+", nfc(text[:4000]).lower()):
        key = fold(word)
        if len(word) < 2 or word in STOPWORDS or key in seen:
            continue
        seen.add(key)
        out.append(word)
        if len(out) >= limit:
            break
    return out


def fts_any(words: list[str]) -> str:
    """FTS5 expression matching items that contain ANY of the words (with or without accents)."""
    alts = []
    for word in words:
        exact, folded = word.replace('"', '""'), fold(word).replace('"', '""')
        alts += [f'body : "{exact}"', f'folded : "{folded}"'] + ([f'body : "{folded}"'] if folded != exact else [])
    return " OR ".join(alts)


def compact(text: str) -> str:
    return " ".join(text.split())


def snippet(text: str, query: str = "", width: int = 220) -> str:
    """A one-line excerpt around the first query hit (or the start)."""
    text = nfc(text)
    pos = -1
    if query:
        folded = fold(text)
        hits = [p for p in (folded.find(t) for t in query_terms(query)) if p >= 0]
        pos = min(hits) if hits else -1
    start = 0 if pos < 0 else max(0, pos - width // 3)
    end = min(len(text), start + width * 2)  # extra room: whitespace gets squeezed below
    out = compact(text[start:end])[:width]
    return ("…" if start > 0 else "") + out + ("…" if end < len(text) or len(out) == width else "")


def parse_ts(value) -> float | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def parse_day(value: str | None, end: bool = False) -> float | None:
    """'2026-11-02' or full ISO time -> epoch seconds (local time for bare dates)."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"không hiểu ngày '{value}', dùng dạng YYYY-MM-DD") from None
    return dt.timestamp() + (86400 if end and len(value) <= 10 else 0)


def fmt_ts(ts: float | None, with_date: bool = True) -> str:
    if ts is None:
        return "?"
    return time.strftime("%Y-%m-%d %H:%M" if with_date else "%H:%M", time.localtime(ts))
