"""Wire a NemoClaw Hermes sandbox to canvasmind: install the skill and set Hermes's own config keys.

Idempotent; rerun after `nemohermes <sandbox> rebuild`, which regenerates the sandbox config. The MCP server itself
is registered once with `nemohermes <sandbox> mcp add` (see the README), because that stores the bearer token in
OpenShell's provider store.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from importlib import resources
from pathlib import Path

SKILL_DIR = "/sandbox/.hermes/skills/canvasmind"

# Hermes-owned keys (adopted on gateway restart). Why each one:
# - image_input_mode=text: the main model (Nemotron 3 Super) is text-only and OpenShell routes every inference call
#   to it, so photos must not be attached as image input (Token Factory answers 400). In text mode the agent gets the
#   photo's cached path, and canvasmind's VL caption (on the host) becomes its description.
# - tool_search off: otherwise MCP tools are deferred behind a search bridge and the model reaches for web search.
# - a smaller Telegram toolset: fewer tool schemas per turn (cost), no browser/image-gen/vision/delegation.
TOOLSETS = ["web", "terminal", "file", "skills", "memory", "session_search", "cronjob", "todo", "nemoclaw"]
PATCH = r"""
import json, pathlib, sys, yaml
p = pathlib.Path("/sandbox/.hermes/config.yaml")
cfg = yaml.safe_load(p.read_text())
toolsets = json.loads(sys.argv[1])
cfg.setdefault("agent", {})["image_input_mode"] = "text"
cfg.setdefault("tools", {}).setdefault("tool_search", {})["enabled"] = False
for platform in ("telegram", "api_server"):
    cfg.setdefault("platform_toolsets", {})[platform] = toolsets
server = cfg.get("mcp_servers", {}).get("canvasmind")
if server is not None:
    server["tools"] = {"resources": False, "prompts": False}
p.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))
print(json.dumps({"canvasmind_mcp": server is not None, "image_input_mode": "text", "tool_search": False, "toolsets": toolsets}))
"""


def _run(cmd: list[str], *, check: bool = True) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    out = "\n".join(line for line in (r.stdout + r.stderr).splitlines() if "OCSF" not in line)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:4])} … failed:\n{out[-1500:]}")
    return out


def setup(sandbox: str = "canvasmind", nemohermes: str = "nemohermes", openshell: str = "openshell") -> dict:
    for tool in (nemohermes, openshell):
        if not shutil.which(tool):
            raise RuntimeError(f"{tool} not found on PATH; install NemoClaw first (see README)")
    exec_ = [nemohermes, sandbox, "exec", "--no-tty", "--timeout", "120", "--"]

    with tempfile.TemporaryDirectory() as tmp:  # upload nests the folder under DEST; flatten it afterwards
        src = Path(tmp) / "canvasmind"
        with resources.as_file(resources.files("canvasmind") / "skill") as skill:
            shutil.copytree(skill, src, ignore=shutil.ignore_patterns("__pycache__"))
        _run(exec_ + ["rm", "-rf", SKILL_DIR])
        _run([openshell, "sandbox", "upload", sandbox, str(src), SKILL_DIR])
    _run(exec_ + ["sh", "-c", f"if [ -d {SKILL_DIR}/canvasmind ]; then mv {SKILL_DIR}/canvasmind/* {SKILL_DIR}/ "
                              f"&& rmdir {SKILL_DIR}/canvasmind; fi; "
                              # the persona tells the model the library is its memory of the user's work
                              f"[ -f /sandbox/.hermes/SOUL.md.hermes ] || cp /sandbox/.hermes/SOUL.md /sandbox/.hermes/SOUL.md.hermes; "
                              f"mv {SKILL_DIR}/SOUL.md /sandbox/.hermes/SOUL.md; "
                              # the gateway resolves the agent's home from its session DB, which NemoClaw keeps in
                              # runtime/ (state.db is a symlink there), so Hermes reads SOUL.md from runtime/ too
                              f"cp /sandbox/.hermes/SOUL.md /sandbox/.hermes/runtime/SOUL.md"])

    out = _run(exec_ + ["/opt/hermes/.venv/bin/python", "-c", PATCH, json.dumps(TOOLSETS)])
    result = json.loads(next(line for line in out.splitlines() if line.startswith("{")))
    _run([nemohermes, sandbox, "gateway", "restart", "--quiet"])
    result["skill"] = SKILL_DIR
    return result
