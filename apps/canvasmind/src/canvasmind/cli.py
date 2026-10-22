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
    env_file = load_env()
    p = argparse.ArgumentParser(prog="canvasmind", description="CanvasMind studio library and MCP server")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the MCP server on 127.0.0.1 (put `tailscale serve` in front of it)")
    s.add_argument("--port", type=int)
    s.add_argument("--per-user", action="store_true", help="public demo: a separate seeded library per user")
    s.add_argument("--demo", action="store_true", help="the public demo server: --per-user on 8771, ~/CanvasMindDemo, "
                   "CANVASMIND_DEMO_MCP_TOKEN, CANVASMIND_PUBLIC_HOST:8443, no watch folders")
    sub.add_parser("serve-all", help="start the private and demo servers unless already listening (via their services if installed)")
    sub.add_parser("doctor", help="health check: Docker, both servers through Tailscale, both sandboxes")
    sv = sub.add_parser("service", help="launchd (macOS) or systemd (Linux) user services for both servers")
    sv.add_argument("action", choices=["install", "uninstall", "print"])
    sv.add_argument("--replace", action="store_true", help="install: stop hand-started servers holding the ports first")
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
    h.add_argument("--demo", action="store_true", help="the public demo bot: open to all, per-user limits, no cron")
    args = p.parse_args(argv)

    if args.cmd in ("serve-all", "doctor", "service"):
        return _ops(args, env_file.parent if env_file else Path.cwd())
    if args.cmd == "serve" and args.demo:
        from .ops import apply_demo_env

        apply_demo_env()
        args.per_user = True
    cfg = Config.from_env()
    studio = Studio(Library(cfg.home), watch=cfg.watch)

    if args.cmd == "serve":
        import uvicorn

        from .mcp_server import create_app

        port = args.port or cfg.port
        tenants = None
        if args.per_user:  # the public demo bot: one seeded library per Telegram user
            from .demo import DEFAULT_FIXTURES
            from .tenants import Tenants

            tenants = Tenants(cfg.home, DEFAULT_FIXTURES)
        app = create_app(studio, cfg.token, cfg.public_host, port, tenants)
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

        print(json.dumps(setup(args.sandbox, demo=args.demo), indent=1))
        return 0
    if args.cmd == "status":
        print(json.dumps({"home": str(cfg.home), "counts": studio.lib.count(),
                          "recent": [i.brief() for i in studio.lib.recent(5)]}, indent=1, ensure_ascii=False))
        return 0
    return 2


def _ops(args: argparse.Namespace, root: Path) -> int:
    from . import ops

    if args.cmd == "serve-all":
        lines = ops.serve_all(root)
        print("\n".join(lines))
        return 1 if any("NOT listening" in ln for ln in lines) else 0
    if args.cmd == "doctor":
        checks = ops.doctor()
        for status, name, detail in checks:
            print(f"{status}  {name:24} {detail}")
        return 1 if any(status == "FAIL" for status, _, _ in checks) else 0
    if args.action == "print":
        for path, text in ops.service_files(root).items():
            print(f"# {path}\n{text}")
        return 0
    if args.action == "uninstall":
        print("\n".join(ops.service_uninstall()))
        return 0
    replace = args.replace
    found = ops.outside_listeners()
    if found and not replace and sys.stdin.isatty():
        for srv, pid, cmd in found:
            print(f"port {srv.port} ({srv.name}) is held by pid {pid}: {cmd}")
        replace = input("Stop these so the services can take over? [y/N] ").strip().lower() == "y"
    print("\n".join(ops.service_install(root, replace=replace)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
