"""motionlang command line: validate, bake, preview, edit, schema, prompt."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .edits import apply_edits
from .preview import preview_html
from .schema import document_schema, prompt_reference
from .validate import check


def _load(path: str) -> dict:
    return json.loads(sys.stdin.read() if path == "-" else Path(path).read_text())


def _write(text: str, out: str | None) -> None:
    if out:
        Path(out).write_text(text)
    else:
        sys.stdout.write(text if text.endswith("\n") else text + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="motionlang", description="Motion DSL tools")
    sub = ap.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="check a document; exit 1 on errors")
    v.add_argument("file")
    v.add_argument("--json", action="store_true", help="machine-readable report")

    b = sub.add_parser("bake", help="sample a document into player tracks")
    b.add_argument("file")
    b.add_argument("-o", "--out")

    p = sub.add_parser("preview", help="write a standalone HTML player")
    p.add_argument("file")
    p.add_argument("-o", "--out", required=True)

    e = sub.add_parser("edit", help="apply edit ops (JSON list) and print the new document")
    e.add_argument("file")
    e.add_argument("ops", help="JSON file with a list of ops")
    e.add_argument("-o", "--out")

    g = sub.add_parser("generate", help="write a document from a sentence with Nemotron (needs NEBIUS_API_KEY)")
    g.add_argument("intent")
    g.add_argument("-o", "--out", required=True, help="where to write the .motion.json")
    g.add_argument("--preview", help="also write an HTML preview here")
    g.add_argument("--strokes", help="JSON file: {id: {points: [[x, y], ...]}} drawn by the user")
    g.add_argument("--size", default="1280x720", help="canvas WxH")
    g.add_argument("--repairs", type=int, default=3)

    t = sub.add_parser("tweak", help="change a document with a short instruction (fast edit model, escalates if needed)")
    t.add_argument("file")
    t.add_argument("instruction")
    t.add_argument("-o", "--out", required=True)
    t.add_argument("--preview", help="also write an HTML preview here")
    for sp in (g, t):
        sp.add_argument("-v", "--verbose", action="store_true", help="show the model's raw replies")

    sub.add_parser("schema", help="print the document JSON Schema")
    sub.add_parser("prompt", help="print the LLM prompt reference")

    args = ap.parse_args(argv)

    if args.cmd == "schema":
        _write(json.dumps(document_schema(), indent=2), None)
        return 0
    if args.cmd == "prompt":
        _write(prompt_reference(), None)
        return 0
    if args.cmd in ("generate", "tweak"):
        return _agent(args)
    if args.cmd == "edit":
        doc, issues = apply_edits(_load(args.file), _load(args.ops))
        if issues:
            print("\n".join(str(i) for i in issues), file=sys.stderr)
            return 1
        _write(json.dumps(doc, indent=2), args.out)
        report = check(doc).report
        if not report.ok:
            print(report.summary(), file=sys.stderr)
        return 0 if report.ok else 1

    result = check(_load(args.file))
    report = result.report
    if args.cmd == "validate":
        _write(json.dumps(report.to_dict(), indent=2) if args.json else report.summary(), None)
        return 0 if report.ok else 1
    if result.baked is None:
        print(report.summary(), file=sys.stderr)
        return 1
    if not report.ok:
        print(report.summary(), file=sys.stderr)  # still bake: useful for seeing what's wrong
    tracks = result.baked.to_json()
    if args.cmd == "bake":
        _write(json.dumps(tracks, separators=(",", ":")), args.out)
    else:
        title = result.parsed.doc.meta.get("title") or Path(args.file).stem  # type: ignore[union-attr]
        _write(preview_html(tracks, title), args.out)
    return 0


def _agent(args: argparse.Namespace) -> int:
    from .agent import generate, tweak  # needs the optional `agent` extra

    if args.cmd == "generate":
        w, h = (float(v) for v in args.size.lower().split("x"))
        strokes = _load(args.strokes) if args.strokes else None
        print(f"generating: {args.intent!r}", file=sys.stderr)
        out = generate(args.intent, canvas={"width": w, "height": h}, strokes=strokes, max_repairs=args.repairs)
    else:
        print(f"tweaking: {args.instruction!r}", file=sys.stderr)
        out = tweak(_load(args.file), args.instruction)
    for i, a in enumerate(out.attempts, 1):
        print(f"  {i}. {a}", file=sys.stderr)
        if not a.ok:
            for p in a.problems()[:6]:
                print(f"       - {p}", file=sys.stderr)
        if args.verbose:
            print("       reply: " + a.raw[:1500].replace("\n", " "), file=sys.stderr)
    print(f"{'ok' if out.ok else 'FAILED'} via {out.path} in {out.seconds:.1f}s", file=sys.stderr)
    if out.note:
        print(out.note, file=sys.stderr)
    if not out.ok:
        best = out.best
        if best is not None:  # keep it for inspection, but never where a valid doc is expected
            rejected = Path(args.out).with_name(Path(args.out).name.replace(".motion.json", "") + ".rejected.motion.json")
            rejected.write_text(json.dumps(best.doc, indent=2) + "\n")
            print(f"{args.out} left unchanged; closest attempt ({len(best.problems())} problem(s)) saved to {rejected}", file=sys.stderr)
        return 1
    Path(args.out).write_text(json.dumps(out.doc, indent=2) + "\n")
    print(f"wrote {args.out}", file=sys.stderr)
    if args.preview:
        result = check(out.doc)
        if result.baked is not None:
            title = out.doc.get("meta", {}).get("title") or Path(args.out).stem
            Path(args.preview).write_text(preview_html(result.baked.to_json(), title))
            print(f"wrote {args.preview}", file=sys.stderr)
    return 0 if out.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
