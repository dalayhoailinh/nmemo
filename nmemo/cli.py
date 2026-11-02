"""Command line: `nmemo <command>` (run `nmemo -h`)."""

from __future__ import annotations

import argparse
import sys
from . import __version__, digest, hooks, ingest, mcp, views
from . import text as T
from .store import Store


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1000:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000
    return f"{n:.1f} TB"


def _session_arg(value: str) -> int:
    kind, num = views.parse_id(value if value[:1].isalpha() else f"s{value}")
    if kind != "s":
        raise argparse.ArgumentTypeError("dạng s12")
    return num


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="nmemo", description="Bộ nhớ cục bộ, nguyên văn, cho các phiên làm việc với AI.")
    ap.add_argument("--home", help="thư mục dữ liệu (mặc định $NMEMO_HOME hoặc ~/.nmemo)")
    ap.add_argument("--version", action="version", version=f"nmemo {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("import", help="nhập transcript Claude Code (mặc định ~/.claude/projects)")
    p.add_argument("paths", nargs="*", default=[str(ingest.claude_projects_dir())])

    p = sub.add_parser("search", help="tìm kiếm (không cần gõ dấu)")
    p.add_argument("query", nargs="?", default="")
    p.add_argument("--kind", action="append", choices=mcp.KINDS)
    p.add_argument("--session", type=_session_arg)
    p.add_argument("--since")
    p.add_argument("--until")
    p.add_argument("--path")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--offset", type=int, default=0)

    p = sub.add_parser("read", help="xem nguyên văn một id (i…, e…, s…)")
    p.add_argument("id")
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--limit", type=int, default=views.READ_LIMIT)
    p.add_argument("--around", type=int, default=0)

    p = sub.add_parser("sessions", help="danh sách phiên")
    p.add_argument("--query")
    p.add_argument("--cwd")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--offset", type=int, default=0)

    p = sub.add_parser("timeline", help="diễn biến một phiên")
    p.add_argument("session", type=_session_arg)
    p.add_argument("--kind", action="append", choices=mcp.KINDS)
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--offset", type=int, default=0)

    p = sub.add_parser("export", help="xuất lại transcript gốc của một phiên, đúng từng byte")
    p.add_argument("session", type=_session_arg)

    p = sub.add_parser("remember", help="lưu một ghi chú")
    p.add_argument("text")

    p = sub.add_parser("digest", help="tóm tắt phiên + cập nhật hồ sơ (gọi `claude -p` với model rẻ)")
    p.add_argument("--session", type=_session_arg, action="append", help="chỉ phiên này (lặp lại được)")
    p.add_argument("--all", action="store_true", help="mọi phiên chưa tóm tắt, không chỉ 7 ngày gần đây")
    p.add_argument("--auto", action="store_true", help="(hook gọi) chạy ngầm, ghi log vào nmemo.log")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--dry-run", action="store_true", help="chỉ in tài liệu sẽ gửi cho model")

    sub.add_parser("profile", help="xem hồ sơ người dùng hiện tại")
    sub.add_parser("stats", help="thống kê kho và tỉ lệ nén")
    sub.add_parser("verify", help="kiểm tra SHA-256 toàn bộ dữ liệu gốc")
    sub.add_parser("compact", help="nén nốt các dòng đang chờ thành khung")
    sub.add_parser("hook", help="(Claude Code gọi) xử lý một sự kiện hook từ stdin")
    sub.add_parser("mcp", help="(Claude Code gọi) chạy MCP server qua stdio")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "hook":
        return hooks.main(args.home)
    if args.cmd == "mcp":
        return mcp.serve(args.home)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    store = Store(args.home)
    try:
        return run(store, args)
    except (KeyError, ValueError) as e:
        print(f"Lỗi: {e}", file=sys.stderr)
        return 1
    except BrokenPipeError:  # output piped into `head` and closed early
        return 0
    finally:
        store.close()


def run(store: Store, args) -> int:
    if args.cmd == "import":
        def progress(path, r):
            print(f"  s{r['session']}: +{r['events']} dòng, +{r['items']} mục  ({path})")
        t = ingest.import_paths(store, args.paths, progress)
        print(f"Đã đọc {t['files']} file transcript: +{t['events']:,} dòng gốc, +{t['items']:,} mục tìm kiếm.")
    elif args.cmd == "search":
        print(views.search(store, args.query, kinds=args.kind, session=args.session,
                           since=T.parse_day(args.since), until=T.parse_day(args.until, end=True),
                           path=args.path, limit=args.limit, offset=args.offset))
    elif args.cmd == "read":
        print(views.read(store, args.id, offset=args.offset, limit=args.limit, around=args.around))
    elif args.cmd == "sessions":
        print(views.sessions(store, query=args.query, cwd=args.cwd, limit=args.limit, offset=args.offset))
    elif args.cmd == "timeline":
        print(views.timeline(store, args.session, kinds=args.kind, limit=args.limit, offset=args.offset))
    elif args.cmd == "export":
        out = sys.stdout.buffer
        for line in store.session_lines(args.session):
            out.write(line + b"\n")
    elif args.cmd == "digest":
        sessions = args.session or digest.pending(store, days=None if args.all else 7,
                                                  limit=10_000 if args.all else args.limit)
        if args.dry_run:
            for sid in sessions:
                print(digest.material(store, sid) + "\n")
            return 0
        log = (lambda m: hooks.log("digest: " + m, str(store.home))) if args.auto else print
        with digest.Lock(store.home) as lock:
            if not lock.held:
                log("một lượt tóm tắt khác đang chạy")
                return 0
            if not sessions:
                log("không có phiên nào cần tóm tắt")
            else:
                result = digest.run(store, sessions, log=log)
                log(f"xong: {len(result['digests'])} bản tóm tắt, chi phí ~{result['cost_usd']} USD")
    elif args.cmd == "profile":
        print(digest.current_profile(store) or "Chưa có hồ sơ. Chạy `nmemo digest` sau vài phiên làm việc.")
    elif args.cmd == "remember":
        print(mcp.call(store, "remember", {"text": args.text}))
    elif args.cmd == "stats":
        s = store.stats()
        ratio = f" ({100 * s['stored_bytes'] / s['raw_bytes']:.1f}%)" if s["raw_bytes"] else ""
        print(f"Kho: {store.path}")
        print(f"Phiên: {s['sessions']} (+{s['subagents']} subagent) · dòng gốc: {s['events']:,} · "
              f"mục tìm được: {s['items']:,} · phiên bản file: {s['file_versions']:,}")
        print(f"Dữ liệu gốc {human(s['raw_bytes'])} → lưu đã nén {human(s['stored_bytes'])}{ratio}; "
              f"cả file SQLite kèm chỉ mục: {human(s['db_bytes'])}")
    elif args.cmd == "verify":
        checked, bad = store.verify()
        print(f"Đã kiểm {checked:,} bản ghi: " + ("tất cả khớp SHA-256." if not bad else f"{len(bad)} SAI: {bad[:20]}"))
        return 1 if bad else 0
    elif args.cmd == "compact":
        with store.tx():
            n = store.seal(force=True)
        store.checkpoint()
        print(f"Đã đóng {n} khung nén.")
    return 0
