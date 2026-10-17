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

    sub.add_parser("schema", help="print the document JSON Schema")
    sub.add_parser("prompt", help="print the LLM prompt reference")

    args = ap.parse_args(argv)

    if args.cmd == "schema":
        _write(json.dumps(document_schema(), indent=2), None)
        return 0
    if args.cmd == "prompt":
        _write(prompt_reference(), None)
        return 0
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


if __name__ == "__main__":
    raise SystemExit(main())
