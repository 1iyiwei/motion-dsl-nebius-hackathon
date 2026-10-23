"""constellate: sketch or say a drone light show, then make it flyable.

    constellate compile SHOW.json -o show.motion.json --preview show.html   # compile + flight check
    constellate jobs-check            # can this machine start Nebius Serverless Jobs? (dry run, no cost)
    constellate jobs-check --run      # start a tiny CPU hello-world job (billed while it runs)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from motionlang.agent.llm import load_env

from . import jobs
from .pipeline import load_show, run


def cmd_compile(args: argparse.Namespace) -> int:
    c = run(load_show(args.show), seed=args.seed)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(c.compiled.doc))
    if args.preview and c.baked is not None:
        from motionlang.preview import preview_html

        args.preview.parent.mkdir(parents=True, exist_ok=True)
        args.preview.write_text(preview_html(c.baked.to_json(), title=c.show.title))
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(c.summary(), indent=2))
    for i in c.motion.errors:
        print(f"motionlang: {i}")
    for i in c.flight.issues:
        print(f"{'✖' if i.severity == 'error' else '!'} {i}")
    st = c.flight.stats
    print(f"{'flyable' if c.ok else 'NOT flyable'}: {st.get('drones')} drones, {st.get('duration')} s, "
          f"peak {st.get('peak_speed')} m/s, {st.get('peak_accel')} m/s², min separation {st.get('min_separation')} m")
    return 0 if c.ok else 2


def cmd_jobs_check(args: argparse.Namespace) -> int:
    project = jobs.project_id()
    if not project:
        print("NEBIUS_CLOUD_PROJECT_ID is not set (needs an AI Cloud project-… id); the sweep will run locally.")
        return 1
    if not jobs.cli_path():
        print("nebius CLI not found; install it or set NEBIUS_CLI.")
        return 1
    r = jobs.run_cli(jobs.create_args(jobs.hello_spec(), project, dry_run=not args.run))
    out = (r.stdout + r.stderr).replace(project, "<project>")
    print(out.strip())
    print("ok" if r.returncode == 0 else f"failed (exit {r.returncode})")
    return r.returncode


def main(argv: list[str] | None = None) -> int:
    load_env()
    ap = argparse.ArgumentParser(prog="constellate", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    cc = sub.add_parser("compile", help="compile a show script to MotionLang and check that it is flyable")
    cc.add_argument("show", type=Path)
    cc.add_argument("-o", "--out", type=Path, help="write the MotionLang document")
    cc.add_argument("--preview", type=Path, help="write a self-contained HTML player")
    cc.add_argument("--report", type=Path, help="write the flight report as JSON")
    cc.add_argument("--seed", type=int, default=0)
    cc.set_defaults(func=cmd_compile)
    jc = sub.add_parser("jobs-check", help="check Nebius Serverless Jobs access")
    jc.add_argument("--run", action="store_true", help="really start the hello-world job (billed)")
    jc.set_defaults(func=cmd_jobs_check)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
