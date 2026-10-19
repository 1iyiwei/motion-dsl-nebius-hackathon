"""Runs inside the sandbox after the tests: loads the plugin through its entry point, checks it is wired
into the schema and prompt reference, bakes each example and the demo scene, and bundles everything the
host needs (test reports, logs, tracks) into out/result.json. The host never imports the plugin itself.
"""

import copy
import json
import os
import traceback
from pathlib import Path

OUT = Path("out")
VERB = os.environ["MH_VERB"]


def read(name: str, limit: int = 200_000) -> str:
    p = OUT / name
    return p.read_text(errors="replace")[-limit:] if p.exists() else ""


def rc(name: str):
    p = OUT / name
    try:
        return int(p.read_text().strip())
    except (OSError, ValueError):
        return None


result = {"verb": VERB, "contract": {"rc": rc("contract.rc"), "xml": read("contract.xml"), "log": read("contract.log", 30_000)},
          "task": {"rc": rc("task.rc"), "xml": read("task.xml"), "log": read("task.log", 30_000)},
          "install": {"rc": rc("install.rc"), "log": read("install.log", 10_000)}}
probe = result["probe"] = {}
try:
    from motionlang import check, load_plugins
    from motionlang.testing import make_doc
    from motionlang.schema import document_schema, prompt_reference

    verbs = load_plugins()
    v = verbs.get(VERB)
    probe["registered"] = v is not None
    probe["plugin_verbs"] = sorted(n for n in verbs if "." in n)
    if v is not None:
        probe["summary"] = v.summary()
        probe["params_schema"] = v.Params.model_json_schema()
        probe["in_schema"] = VERB in json.dumps(document_schema())
        probe["in_prompt"] = VERB in prompt_reference()
        probe["examples"] = []
        probe["n_examples"] = len(v.examples)
        for ex in v.examples:
            block = {"id": "x", "verb": VERB, **ex}
            if v.needs_actor and not v.fixed_actor:
                block.setdefault("actor", "a")
            r = check(make_doc(block))
            probe["examples"].append({"params": ex, "ok": r.ok, "issues": [str(i) for i in r.report.errors]})
    demo_path = Path("demo.motion.json")
    if demo_path.exists():
        demo = json.loads(demo_path.read_text())
        r = check(copy.deepcopy(demo))
        probe["demo"] = {"ok": r.ok, "issues": [str(i) for i in r.report.errors],
                         "tracks": r.baked.to_json() if r.baked is not None else None}
except Exception:
    probe["error"] = traceback.format_exc()[-4000:]

OUT.mkdir(exist_ok=True)
(OUT / "result.json").write_text(json.dumps(result))
print("probe done")
