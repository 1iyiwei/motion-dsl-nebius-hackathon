"""canvasmind CLI: run the MCP server, or file/recall from the terminal."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from motionlang.agent.llm import load_env

from .config import Config
from .library import Library
from .studio import Studio


def main(argv: list[str] | None = None) -> int:
    load_env()
    p = argparse.ArgumentParser(prog="canvasmind", description="CanvasMind studio library and MCP server")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the MCP server on 127.0.0.1 (put `tailscale serve` in front of it)")
    s.add_argument("--port", type=int)
    f = sub.add_parser("file", help="file local files or links into the library")
    f.add_argument("paths", nargs="+")
    f.add_argument("--note", default="")
    r = sub.add_parser("recall", help="search the library")
    r.add_argument("query")
    r.add_argument("--limit", type=int, default=5)
    sub.add_parser("scan", help="file new work from CANVASMIND_WATCH folders")
    sub.add_parser("status", help="library counts and recent items")
    d = sub.add_parser("demo-reset", help="move the library aside and file the demo fixtures (seeded style, backdated items)")
    d.add_argument("--fixtures", type=Path)
    d.add_argument("--yes", action="store_true", help="required: confirms moving the current library aside")
    h = sub.add_parser("hermes-setup", help="install the canvasmind skill and settings into a NemoClaw Hermes sandbox")
    h.add_argument("--sandbox", default="canvasmind")
    args = p.parse_args(argv)

    cfg = Config.from_env()
    studio = Studio(Library(cfg.home), watch=cfg.watch)

    if args.cmd == "serve":
        import uvicorn

        from .mcp_server import create_app

        port = args.port or cfg.port
        app = create_app(studio, cfg.token, cfg.public_host, port)
        print(f"canvasmind MCP on http://127.0.0.1:{port}/mcp (library: {cfg.home})", file=sys.stderr)
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
        return 0
    if args.cmd == "file":
        for x in args.paths:
            try:
                filed = studio.file_url(x, note=args.note) if x.startswith(("http://", "https://")) else studio.file_path(Path(x), note=args.note)
            except Exception as e:
                print(f"{x}: {e}", file=sys.stderr)
                continue
            tag = " (already filed)" if filed.duplicate else ""
            print(f"{filed.item.id}{tag}  {filed.item.title}  [{', '.join(filed.item.tags[:6])}]")
            for c in filed.calls:
                print(f"    {c}", file=sys.stderr)
        return 0
    if args.cmd == "recall":
        items, calls = studio.recall(args.query, args.limit)
        for it in items:
            print(f"{it.id}  {it.date}  {it.title}  — {it.caption}")
        for c in calls:
            print(f"    {c}", file=sys.stderr)
        return 0 if items else 1
    if args.cmd == "scan":
        print(json.dumps(studio.scan(), indent=1, ensure_ascii=False))
        return 0
    if args.cmd == "demo-reset":
        from .demo import DEFAULT_FIXTURES, reset

        if not args.yes:
            print(f"This moves {cfg.home} to {cfg.home.name}.bak-<time> and files the demo fixtures. Rerun with --yes.")
            return 1
        studio.lib.db.close()
        print(json.dumps(reset(cfg.home, args.fixtures or DEFAULT_FIXTURES), indent=1, ensure_ascii=False))
        return 0
    if args.cmd == "hermes-setup":
        from .hermes_setup import setup

        print(json.dumps(setup(args.sandbox), indent=1))
        return 0
    if args.cmd == "status":
        print(json.dumps({"home": str(cfg.home), "counts": studio.lib.count(),
                          "recent": [i.brief() for i in studio.lib.recent(5)]}, indent=1, ensure_ascii=False))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
