"""Runs inside the sandbox for "Try in a scene", so the plugin is only ever imported there.

    python scene.py system         → out/system.txt   the planner's system prompt as this environment builds it
    python scene.py check DOC      → out/scene.json   {ok, errors, warnings, verbs, tracks}
"""

import json
import sys
import traceback
from pathlib import Path

OUT = Path("out")
OUT.mkdir(exist_ok=True)

if sys.argv[1] == "system":
    from motionlang.agent.prompts import generation_system

    (OUT / "system.txt").write_text(generation_system())
else:
    result = {"ok": False, "errors": [], "warnings": [], "verbs": [], "tracks": None}
    try:
        from motionlang import check

        doc = json.loads(Path(sys.argv[2]).read_text())

        def verbs(node):
            if isinstance(node, dict):
                if "verb" in node:
                    yield node["verb"]
                for v in node.values():
                    yield from verbs(v)
            elif isinstance(node, list):
                for v in node:
                    yield from verbs(v)

        result["verbs"] = sorted(set(verbs(doc.get("timeline"))))
        r = check(doc)
        result.update(ok=r.ok, errors=[str(e) for e in r.report.errors], warnings=[str(w) for w in r.report.warnings],
                      tracks=r.baked.to_json() if r.baked is not None else None)
    except Exception:
        result["errors"] = [traceback.format_exc()[-1500:]]
    (OUT / "scene.json").write_text(json.dumps(result))
