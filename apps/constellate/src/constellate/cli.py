"""constellate: sketch or say a drone light show, then make it flyable.

    constellate brief "Marina Green, San Francisco" --date "Oct 31 2026, 9 pm" -o brief.json   # Tavily → cited limits
    constellate plan "a heart that spins, then our logo" --svg logo=logo.svg -o show.json   # Nemotron writes the script
    constellate fly show.json --design-wind 8 --mp4 show.mp4   # repair loop + wind sweep + certificate + night-sky video
    constellate compile SHOW.json -o show.motion.json --preview show.html   # compile + flight check only
    constellate sweep SHOW.json       # wind sweep + certificate, no model calls
    constellate serve                 # the web app: http://127.0.0.1:8780/
    constellate jobs-check            # can this machine start Nebius Serverless Jobs? (dry run, no cost)
    constellate jobs-check --run      # start a tiny CPU hello-world job (billed while it runs)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from motionlang.agent.llm import load_env

from . import agent, jobs
from .pipeline import load_show, run
from .show import Show


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


def _base(args: argparse.Namespace) -> Show:
    svgs = {}
    for spec in args.svg or []:
        sid, _, path = spec.partition("=")
        svgs[sid] = Path(path).read_text()
    return Show.model_validate({"formations": {"x": {"kind": "preset", "shape": "ring"}},
                                "steps": [{"kind": "form", "id": "x", "formation": "x"}],
                                "drones": args.drones, "sources": {"svgs": svgs}})


def _print_event(e: dict) -> None:
    if e["type"] != "call_start":
        print(f"  {e['type']:>8}: {e['msg']}", flush=True)
        for i in e.get("issues", []) if e["type"] in ("flight", "sim") else []:
            if i.get("severity", "error") == "error":
                print(f"            ✖ {i['code']} {i.get('block', '')}: {i.get('hint', '')}")
        for op in e.get("ops", []):
            print(f"            ✎ {json.dumps(op)}")


def cmd_plan(args: argparse.Namespace) -> int:
    from motionlang.agent.llm import LLM

    llm = LLM()
    show = agent.plan(args.intent, _with_limits(_base(args), args), llm, emit=_print_event)
    raw = show.model_dump(mode="json", exclude={"limits"} if not args.brief else None)
    raw["title"] = raw.get("title") or args.intent[:60]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(raw, indent=2))
    print(f"wrote {args.out}")
    return 0


def _write_outputs(args, show: Show, checked, sw) -> None:
    if args.mp4 and checked.baked is not None:
        from .flight import tracks_m
        from .sim import Wind, simulate
        from .video import baked_colors, write_mp4

        times, plan = tracks_m(checked.baked, show)
        wind = Wind(speed=show.limits.design_wind, direction_deg=60)
        r = simulate(times, plan, show.limits.max_flight_time, wind, seed=0, alert=show.limits.near_miss)
        out = write_mp4(args.mp4, r.pos, baked_colors(checked.baked, show.drones), show.limits)
        print(f"wrote {out} (simulated flight in {wind.speed:g} m/s wind)")
    if args.report:
        rep = checked.summary()
        if sw:
            rep["sweep"] = sw.to_dict()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(rep, indent=2, default=float))
    if getattr(args, "save", None):
        args.save.parent.mkdir(parents=True, exist_ok=True)
        args.save.write_text(json.dumps(show.model_dump(mode="json"), indent=2))  # sources inlined, so it stands alone
        print(f"wrote {args.save}")


def _print_cert(cert: dict | None) -> None:
    if cert:
        print(f"certificate: {'FLYABLE' if cert['flyable'] else 'not certified'} up to {cert['certified_wind']} m/s "
              f"(design wind {cert['design_wind']} m/s, {cert['runs']} runs, worst separation {cert['worst_separation']} m, "
              f"max drift {cert['max_tracking_error']} m, battery left {cert['battery_left']:.0%}, "
              f"≥ {cert.get('closest_to_crowd')} m from the crowd); {cert['note']}")


def _with_limits(show: Show, args) -> Show:
    d = show.model_dump(mode="json")
    if getattr(args, "brief", None):
        d["limits"].update(json.loads(args.brief.read_text())["limits"])
    if getattr(args, "design_wind", None) is not None:
        d["limits"]["design_wind"] = args.design_wind
    return Show.model_validate(d)


def cmd_brief(args: argparse.Namespace) -> int:
    from motionlang.agent.llm import LLM

    from .briefing import brief

    llm = LLM()
    b = brief(args.venue, args.date, llm, emit=_print_event)
    for f in b.findings:
        src = f" — {f.source['title']} <{f.source['url']}>" if f.source else ""
        mark = "✓" if f.verified else "?"
        val = f"{f.value:g} {f.unit}" if f.value is not None else "not found"
        print(f"  {mark} {f.topic}: {val}{' (applied)' if f.applied else ''}; {f.note}{src}")
        if f.quote:
            print(f"      “{f.quote}”")
    for a in b.airspace:
        print(f"  ! {a['note']}" + (f" — <{a['source']['url']}>" if a.get("source") else ""))
    print(f"  limits: {json.dumps(b.limits)}")
    if b.summary:
        print(f"  {b.summary}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(b.to_dict(), indent=2))
    print(f"wrote {args.out}")
    return 0


def cmd_fly(args: argparse.Namespace) -> int:
    from motionlang.agent.llm import LLM

    llm = LLM()
    show = _with_limits(load_show(args.show), args)
    out = agent.make_flyable(show, llm, emit=_print_event, max_rounds=args.rounds)
    print(f"{'flyable' if out.ok else 'NOT flyable'} after {len(out.rounds)} repair rounds; model calls:")
    for c in llm.calls:
        print(f"  {c}")
    _print_cert(out.certificate)
    _write_outputs(args, out.show, out.checked, out.sweep)
    return 0 if out.ok else 2


def cmd_sweep(args: argparse.Namespace) -> int:
    from .sweep import sim_issues, sweep

    show = _with_limits(load_show(args.show), args)
    c = run(show)
    if not c.flight.ok:
        print("fix the flight check first:")
        for i in c.flight.issues:
            print(f"  {i}")
        return 2
    sw = sweep(show, c.compiled, c.baked)
    for w, v in sw.by_wind().items():
        print(f"  {w:4g} m/s: {v['passed']}/{v['runs']} pass, max drift {v['max_err']} m, min separation {v['min_sep']} m")
    for i in sim_issues(show, c.compiled, c.baked, sw):
        print(f"  ✖ {i}")
    _print_cert(sw.certificate())
    args.save = None
    _write_outputs(args, show, c, sw)
    return 0 if sw.certificate()["flyable"] else 2


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import serve

    serve(args.host, args.port, open_browser=not args.no_browser)
    return 0


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
    sc = sub.add_parser("serve", help="run the web app")
    sc.add_argument("--host", default="127.0.0.1")
    sc.add_argument("--port", type=int, default=8780)
    sc.add_argument("--no-browser", action="store_true")
    sc.set_defaults(func=cmd_serve)
    bc = sub.add_parser("brief", help="Tavily site briefing: venue + date → cited limits")
    bc.add_argument("venue")
    bc.add_argument("--date", required=True, help='e.g. "Oct 31 2026, 9 pm"')
    bc.add_argument("-o", "--out", type=Path, required=True)
    bc.set_defaults(func=cmd_brief)
    pc = sub.add_parser("plan", help="Nemotron writes a show script from a request")
    pc.add_argument("--brief", type=Path, help="apply a site briefing's limits")
    pc.add_argument("intent")
    pc.add_argument("--svg", action="append", help="ID=PATH artwork the show may use (repeatable)")
    pc.add_argument("--drones", type=int, default=100)
    pc.add_argument("-o", "--out", type=Path, required=True)
    pc.set_defaults(func=cmd_plan)
    for name, fn, hlp in (("fly", cmd_fly, "repair loop until flyable, wind sweep, certificate"),
                          ("sweep", cmd_sweep, "wind sweep and certificate (no model calls)")):
        fc = sub.add_parser(name, help=hlp)
        fc.add_argument("show", type=Path)
        fc.add_argument("--design-wind", type=float, help="m/s the show must be certified for (default 6)")
        fc.add_argument("--brief", type=Path, help="apply a site briefing's limits (before --design-wind)")
        fc.add_argument("--mp4", type=Path, help="night-sky video of the simulated flight at the design wind")
        fc.add_argument("--report", type=Path, help="JSON report (flight check, sweep, certificate)")
        if name == "fly":
            fc.add_argument("--rounds", type=int, default=6)
            fc.add_argument("--save", type=Path, help="write the repaired show script")
        fc.set_defaults(func=fn)
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
