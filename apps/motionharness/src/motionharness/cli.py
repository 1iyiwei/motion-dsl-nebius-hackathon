"""motionharness: a coding agent that writes, tests and critiques MotionLang verb plugins.

    motionharness new-verb "add a spring_follow verb: the actor chases a target with spring physics"
    motionharness serve                      # dashboard: branch tree, tests, frames, model calls
    motionharness sandbox-check              # is Token Factory Sandboxes access set up?
    motionharness runs                       # past runs
    motionharness mcp                        # MCP server (stdio) for coding agents
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import webbrowser
from pathlib import Path

from motionlang.agent.llm import load_env

from .harness import Config, Harness, tavily_search
from .plugin import Task
from .sandbox import ContreeSandbox, make_sandbox

DEFAULT_ROOT = Path(os.environ.get("MOTIONHARNESS_RUNS", "out/motionharness"))
ICON = {"start": "▶", "docs": "🔎", "sandbox": "📦", "round": "⟳", "call_start": "…", "candidate": "•", "tests": "🧪",
        "critique": "👁", "naming": "✎", "ship": "✅", "error": "✖", "end": "■"}


def print_event(e: dict) -> None:
    if e["type"] == "call_start":
        return
    print(f"{e['t']:7.1f}s {ICON.get(e['type'], ' ')} {e['msg']}", flush=True)


def cmd_new_verb(args: argparse.Namespace) -> int:
    task = Task(args.task, name=args.name or "", namespace=args.namespace)
    sandbox = make_sandbox(args.sandbox, args.out / ".sandbox")
    cfg = Config(candidates=args.candidates, models=[m for m in (args.models or "").split(",") if m], max_rounds=args.rounds,
                 critique=not args.no_critique, docs=not args.no_docs)
    harness = Harness(sandbox, root=args.out, tavily=tavily_search() if cfg.docs else None)
    print(f"MotionHarness: {task.verb}  (sandbox: {sandbox.name}, {cfg.candidates} candidates, up to {cfg.max_rounds} repair rounds)")
    if getattr(sandbox, "fallback_reason", None):
        print(f"  note: not using Token Factory Sandboxes: {sandbox.fallback_reason}")
    run = harness.run(task, cfg, on_event=print_event)
    print(f"\nrun: {run.dir}")
    if run.winner:
        print(f"plugin: {run.dir / 'plugin'}")
        print(f"  install into this workspace's venv:  uv pip install {run.dir / 'plugin'}")
        print("  (motionlang isn't on PyPI yet: elsewhere, install it first, e.g. pip install <repo>/packages/motionlang)")
        print(f"report: {run.dir / 'report.md'}   diff: {run.dir / 'plugin.diff'}   preview: {run.dir / 'preview.html'}")
    return 0 if run.status == "done" else 1


def cmd_sandbox_check(args: argparse.Namespace) -> int:
    why = ContreeSandbox.available()
    if why:
        print(f"Token Factory Sandboxes: not configured ({why}).")
        print("Add NEBIUS_PROJECT_ID (Token Factory → Project settings) to .env, then rerun. Until then runs use the local backend.")
        return 1
    status, info = ContreeSandbox.whoami(timeout=20)
    print(f"whoami: HTTP {status}")
    print(json.dumps({k: info.get(k) for k in ("permissions", "limits", "error") if k in info}, indent=1)[:1500])
    why = ContreeSandbox.access(timeout=20)
    if why:
        print(f"\nToken Factory Sandboxes: not usable: {why}.")
        print("Runs keep using the local backend (--sandbox auto) until this check passes.")
        return 1
    print("\nToken Factory Sandboxes: active.")
    if args.run:
        sb = ContreeSandbox()
        print("preparing the environment image (first time: pip install inside the sandbox, ~1–2 min)…")
        env = sb.environment()
        print(f"environment checkpoint: {env.id} ({sb.env_seconds or 0:.0f}s; cached next time)")
    return 0


def cmd_runs(args: argparse.Namespace) -> int:
    for p in sorted(args.out.glob("*/run.json"), reverse=True)[:30]:
        r = json.loads(p.read_text())
        print(f"{r['id']:40} {r['status']:8} winner={r.get('winner') or '-':4} candidates={len(r['candidates']):2} {r['task']['verb']}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .server import create_app

    url = f"http://{args.host}:{args.port}/"
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, (url,)).start()
    print(f"MotionHarness dashboard on {url} (runs in {args.out})")
    uvicorn.run(create_app(args.out, sandbox_kind=args.sandbox), host=args.host, port=args.port, log_level="warning")
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    from .mcp_server import main as serve_mcp

    serve_mcp(args.out, args.sandbox)
    return 0


def main(argv: list[str] | None = None) -> None:
    load_env()
    ap = argparse.ArgumentParser(prog="motionharness", description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=DEFAULT_ROOT, help="runs directory (default: out/motionharness)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    nv = sub.add_parser("new-verb", help="write, test and critique a new verb plugin")
    nv.add_argument("task", help='e.g. "add a spring_follow verb: the actor chases a target with spring physics"')
    nv.add_argument("--name", help="verb name (default: inferred from the task, e.g. spring_follow)")
    nv.add_argument("--namespace", default="mh", help="verb namespace (default: mh → mh.spring_follow)")
    nv.add_argument("--candidates", type=int, default=3)
    nv.add_argument("--models", help="comma-separated per candidate: super, ultra, nano or full model ids (default: super)")
    nv.add_argument("--rounds", type=int, default=3, help="repair rounds after the first (default 3)")
    nv.add_argument("--sandbox", default=os.environ.get("MOTIONHARNESS_SANDBOX", "auto"), choices=["auto", "local", "contree"])
    nv.add_argument("--no-critique", action="store_true", help="skip the vision critique")
    nv.add_argument("--no-docs", action="store_true", help="skip the Tavily doc lookup")
    nv.set_defaults(fn=cmd_new_verb)

    sc = sub.add_parser("sandbox-check", help="check Token Factory Sandboxes access")
    sc.add_argument("--run", action="store_true", help="also prepare (and cache) the environment image")
    sc.set_defaults(fn=cmd_sandbox_check)

    sub.add_parser("runs", help="list past runs").set_defaults(fn=cmd_runs)

    sv = sub.add_parser("serve", help="web dashboard")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8766)
    sv.add_argument("--no-browser", action="store_true")
    sv.add_argument("--sandbox", default=os.environ.get("MOTIONHARNESS_SANDBOX", "auto"), choices=["auto", "local", "contree"])
    sv.set_defaults(fn=cmd_serve)

    mc = sub.add_parser("mcp", help="MCP server on stdio (claude mcp add motionharness -- uv run motionharness mcp)")
    mc.add_argument("--sandbox", default=os.environ.get("MOTIONHARNESS_SANDBOX", "auto"), choices=["auto", "local", "contree"])
    mc.set_defaults(fn=cmd_mcp)

    args = ap.parse_args(argv)
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
