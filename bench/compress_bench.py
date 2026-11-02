#!/usr/bin/env python3
"""Đo xem văn bản của bạn nén được bao nhiêu, và lấy lại một trang bất kỳ nhanh, đúng cỡ nào.

    pip install zstandard          # bắt buộc
    pip install brotli pyppmd      # tùy chọn, để so sánh thêm

    python bench/compress_bench.py sach.txt --page 678   # chế độ sách, in nguyên văn trang 678
    python bench/compress_bench.py --messages chat.txt   # mỗi dòng là một tin nhắn

Chế độ sách: nếu file có ký tự ngắt trang (\\f, như output của `pdftotext sach.pdf sach.txt`)
thì tách trang theo đó, nếu không thì cắt mỗi trang --page-chars ký tự.
Script chỉ đọc file trên máy, không gửi gì lên mạng.
"""

from __future__ import annotations

import argparse
import bz2
import hashlib
import lzma
import random
import time
import zlib
from pathlib import Path

import zstandard as zstd

try:
    import brotli
except ImportError:
    brotli = None
try:
    import pyppmd
except ImportError:
    pyppmd = None

DISK_BYTES = 512 * 10**9  # ổ 512 GB
BLOCK_PAGES = 16          # số trang gộp chung một khung nén
FETCHES = 2000            # số lần đọc ngẫu nhiên để đo tốc độ


def pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:5.1f}%"


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1000:
            return f"{n:,.0f} {unit}" if unit == "B" else f"{n:,.1f} {unit}"
        n /= 1000
    return f"{n:,.1f} TB"


def codecs():
    zd = zstd.ZstdDecompressor()
    out = [
        ("gzip -9", lambda d: zlib.compress(d, 9), zlib.decompress),
        ("bzip2 -9", lambda d: bz2.compress(d, 9), bz2.decompress),
        ("xz -9e", lambda d: lzma.compress(d, preset=9 | lzma.PRESET_EXTREME), lzma.decompress),
        ("zstd -3", zstd.ZstdCompressor(level=3).compress, zd.decompress),
        ("zstd -19", zstd.ZstdCompressor(level=19).compress, zd.decompress),
        ("zstd -22", zstd.ZstdCompressor(level=22).compress, zd.decompress),
    ]
    if brotli:
        out.append(("brotli -11", lambda d: brotli.compress(d, quality=11, lgwin=24), brotli.decompress))
    if pyppmd:
        opts = dict(max_order=8, mem_size=256 << 20, variant="I")
        out.append(("ppmd -o8", lambda d: pyppmd.compress(d, **opts), lambda c: pyppmd.decompress(c, **opts)))
    return out


def timed(fn, *args):
    t = time.perf_counter()
    result = fn(*args)
    return result, time.perf_counter() - t


def whole_file(data: bytes) -> None:
    print(f"\n[1] Nén nguyên khối ({human(len(data))})")
    print(f"    {'thuật toán':<11} {'còn lại':>10} {'tỉ lệ':>7} {'nén':>9} {'giải nén':>9}")
    for name, comp, decomp in codecs():
        packed, t_c = timed(comp, data)
        restored, t_d = timed(decomp, packed)
        assert restored == data, f"{name}: giải nén không khớp bản gốc!"
        print(f"    {name:<11} {human(len(packed)):>10} {pct(len(packed), len(data))} "
              f"{t_c * 1000:7.0f}ms {t_d * 1000:7.1f}ms")


def train_dict(samples: list[bytes]) -> zstd.ZstdCompressionDict | None:
    size = max(1024, min(110 * 1024, sum(map(len, samples)) // 10))
    try:
        return zstd.train_dictionary(size, samples)
    except zstd.ZstdError:
        return None


def page_store(pages: list[bytes], show: int | None) -> None:
    raw = sum(map(len, pages))
    digests = [hashlib.sha256(p).digest() for p in pages]
    cz, dz = zstd.ZstdCompressor(level=19), zstd.ZstdDecompressor()

    layouts = {}
    single = [cz.compress(p) for p in pages]
    layouts["mỗi trang 1 khung"] = (sum(map(len, single)), lambda i: dz.decompress(single[i]))

    dictionary = train_dict(pages)
    if dictionary:
        czd = zstd.ZstdCompressor(level=19, dict_data=dictionary)
        dzd = zstd.ZstdDecompressor(dict_data=dictionary)
        with_dict = [czd.compress(p) for p in pages]
        size = sum(map(len, with_dict)) + len(dictionary.as_bytes())
        layouts["mỗi trang + từ điển"] = (size, lambda i: dzd.decompress(with_dict[i]))

    # Cách đề xuất: gộp vài trang thành một khung zstd độc lập, nhớ (khung, offset, độ dài) của từng trang.
    frames, where = [], []
    for start in range(0, len(pages), BLOCK_PAGES):
        offset = 0
        for p in pages[start:start + BLOCK_PAGES]:
            where.append((len(frames), offset, len(p)))
            offset += len(p)
        frames.append(cz.compress(b"".join(pages[start:start + BLOCK_PAGES])))

    def read_page(i: int) -> bytes:
        frame, off, n = where[i]
        return dz.decompress(frames[frame])[off:off + n]

    layouts[f"khung {BLOCK_PAGES} trang"] = (sum(map(len, frames)), read_page)

    print(f"\n[2] Kho theo trang: {len(pages)} trang, đọc ngẫu nhiên {FETCHES} lần")
    rng = random.Random(678)
    for name, (size, fetch) in layouts.items():
        picks = [rng.randrange(len(pages)) for _ in range(FETCHES)]
        t = time.perf_counter()
        for i in picks:
            assert hashlib.sha256(fetch(i)).digest() == digests[i], "sai lệch so với bản gốc!"
        per_fetch = (time.perf_counter() - t) / FETCHES
        print(f"    {name:<20} {human(size):>10} {pct(size, raw)}   "
              f"đọc 1 trang: {per_fetch * 1e6:6.1f} µs (khớp SHA-256 100%)")

    per_page = raw / len(pages)
    book = 1000 * per_page * min(s for s, _ in layouts.values()) / raw
    print(f"\n    Ngoại suy sách 1000 trang: gốc ~{human(1000 * per_page)}, nén ~{human(book)} "
          f"-> ổ 512 GB chứa ~{DISK_BYTES / book:,.0f} cuốn")

    if show is not None:
        if not 1 <= show <= len(pages):
            print(f"\n    Không có trang {show} (chỉ có {len(pages)} trang).")
            return
        page, t = timed(read_page, show - 1)
        ok = hashlib.sha256(page).digest() == digests[show - 1]
        print(f"\n[*] Trang {show}: giải nén {t * 1e6:.0f} µs, SHA-256 {hashlib.sha256(page).hexdigest()[:16]}… "
              f"{'khớp bản gốc' if ok else 'KHÔNG KHỚP'}\n{'-' * 60}\n{page.decode()}\n{'-' * 60}")


def messages(lines: list[str]) -> None:
    msgs = [m.encode() for m in lines if m.strip()]
    history, incoming = msgs[: len(msgs) // 2], msgs[len(msgs) // 2:]
    raw = sum(map(len, incoming))
    print(f"\n[3] Tin nhắn: {len(msgs)} tin, trung bình {sum(map(len, msgs)) / len(msgs):.0f} byte/tin")
    print(f"    Học từ {len(history)} tin cũ, rồi nén lần lượt {len(incoming)} tin 'mới đến'")

    def store(compressor) -> int:
        # mỗi tin một bản ghi; nếu nén không lợi thì giữ nguyên (+1 byte cờ)
        return sum(min(len(compressor.compress(m)), len(m)) + 1 for m in incoming)

    plain = zstd.ZstdCompressor(level=19, write_checksum=False)
    rows = [("từng tin, không từ điển", store(plain))]
    dictionary = train_dict(history)
    if dictionary:
        cz = zstd.ZstdCompressor(level=19, dict_data=dictionary, write_checksum=False, write_dict_id=False)
        rows.append((f"từng tin + từ điển {human(len(dictionary.as_bytes()))}", store(cz)))
    block = 64 * 1024
    joined = b"\n".join(incoming)
    rows.append(("gộp khung 64 KB", sum(len(plain.compress(joined[i:i + block]))
                                         for i in range(0, len(joined), block))))
    for name, size in rows:
        print(f"    {name:<28} {human(size):>10} {pct(size, raw)}   {size / len(incoming):5.1f} byte/tin")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--messages", action="store_true", help="mỗi dòng là một tin nhắn")
    ap.add_argument("--page-chars", type=int, default=2000, help="số ký tự mỗi trang nếu file không có \\f")
    ap.add_argument("--page", type=int, help="in nguyên văn trang này, lấy từ kho đã nén")
    args = ap.parse_args()

    text = "\n".join(f.read_text(encoding="utf-8") for f in args.files)
    data = text.encode()
    print(f"Dữ liệu: {len(text):,} ký tự = {human(len(data))} UTF-8 ({len(data) / len(text):.2f} byte/ký tự)")
    whole_file(data)
    if args.messages:
        messages(text.splitlines())
    else:
        if "\f" in text:
            pages = text.split("\f")
        else:
            pages = [text[i:i + args.page_chars] for i in range(0, len(text), args.page_chars)]
        page_store([p.encode() for p in pages], args.page)


if __name__ == "__main__":
    main()
