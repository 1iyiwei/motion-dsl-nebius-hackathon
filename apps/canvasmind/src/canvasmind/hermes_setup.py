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
PLUGIN = "canvasmind_telegram"  # two Telegram fixes, see hermes_plugin/canvasmind_telegram
PLUGIN_DIR = f"/sandbox/.hermes/plugins/{PLUGIN}"

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
enabled = cfg.setdefault("plugins", {}).setdefault("enabled", [])
enabled[:] = [p for p in enabled if p != "canvasmind_svg"]
if "canvasmind_telegram" not in enabled:
    enabled.append("canvasmind_telegram")
cfg.setdefault("tools", {}).setdefault("tool_search", {})["enabled"] = False
for platform in ("telegram", "api_server"):
    cfg.setdefault("platform_toolsets", {})[platform] = toolsets
server = cfg.get("mcp_servers", {}).get("canvasmind")
if server is not None:
    server["tools"] = {"resources": False, "prompts": False}
p.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))
print(json.dumps({"canvasmind_mcp": server is not None, "image_input_mode": "text", "tool_search": False, "toolsets": toolsets}))
"""


DIGEST_PROMPT = ("Run this week's CanvasMind motion digest: call mcp__canvasmind__digest and send me its digest text "
                 "and the GIF exactly as returned, with no extra commentary.")
JOB_IDS = r"""
import json, pathlib
p = pathlib.Path("/sandbox/.hermes/cron/jobs.json")
data = json.loads(p.read_text()) if p.exists() else {}
jobs = data.get("jobs", data) if isinstance(data, dict) else data
jobs = jobs.values() if isinstance(jobs, dict) else jobs
print(json.dumps([j.get("id") for j in jobs if isinstance(j, dict) and str(j.get("name", "")).startswith("canvasmind-")]))
"""


def _cron(exec_: list[str], chat_id: str) -> list[str]:
    """(Re)create the two CanvasMind jobs: the hourly studio scan (a script, no model, silent when nothing is new)
    and the Monday 9:00 digest (an agent turn that calls the digest tool). Both deliver to the user's Telegram chat."""
    hermes = exec_ + ["env", "HERMES_HOME=/sandbox/.hermes", "/usr/local/bin/hermes", "cron"]
    out = _run(exec_ + ["/opt/hermes/.venv/bin/python", "-c", JOB_IDS])
    for job_id in json.loads(next(line for line in out.splitlines() if line.startswith("["))):
        _run(hermes + ["remove", job_id], check=False)
    target = f"telegram:{chat_id}" if chat_id else "telegram"
    _run(exec_ + ["sh", "-c", f"mkdir -p /sandbox/.hermes/scripts && cp {SKILL_DIR}/scripts/cm_scan.py /sandbox/.hermes/scripts/"])
    _run(hermes + ["create", "every 1h", "--name", "canvasmind-studio-scan", "--no-agent", "--script", "cm_scan.py",
                   "--deliver", target, "--failure-deliver", "local"])
    import time

    hour = (9 - round(-time.timezone / 3600 + (time.localtime().tm_isdst > 0))) % 24  # Hermes schedules in UTC
    _run(hermes + ["create", f"0 {hour} * * 1", DIGEST_PROMPT, "--name", "canvasmind-digest", "--deliver", target])
    return ["canvasmind-studio-scan (every 1h)", f"canvasmind-digest (Mon 09:00 local = {hour:02d}:00 UTC)"]


def _run(cmd: list[str], *, check: bool = True) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    out = "\n".join(line for line in (r.stdout + r.stderr).splitlines() if "OCSF" not in line)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:4])} … failed:\n{out[-1500:]}")
    return out


def _upload(exec_: list[str], openshell: str, sandbox: str, package_dir: str, dest: str) -> None:
    """Copy a folder of this package to `dest` in the sandbox (upload nests it one level; flatten afterwards)."""
    name = Path(dest).name
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / name
        with resources.as_file(resources.files("canvasmind").joinpath(*package_dir.split("/"))) as folder:
            shutil.copytree(folder, src, ignore=shutil.ignore_patterns("__pycache__"))
        _run(exec_ + ["rm", "-rf", dest])
        _run([openshell, "sandbox", "upload", sandbox, str(src), dest])
    _run(exec_ + ["sh", "-c", f"if [ -d {dest}/{name} ]; then mv {dest}/{name}/* {dest}/ && rmdir {dest}/{name}; fi"])


def setup(sandbox: str = "canvasmind", nemohermes: str = "nemohermes", openshell: str = "openshell") -> dict:
    for tool in (nemohermes, openshell):
        if not shutil.which(tool):
            raise RuntimeError(f"{tool} not found on PATH; install NemoClaw first (see README)")
    exec_ = [nemohermes, sandbox, "exec", "--no-tty", "--timeout", "120", "--"]

    for package_dir, dest in (("skill", SKILL_DIR), (f"hermes_plugin/{PLUGIN}", PLUGIN_DIR)):
        _upload(exec_, openshell, sandbox, package_dir, dest)
    # the persona tells the model the library is its memory of the user's work
    _run(exec_ + ["sh", "-c", f"[ -f /sandbox/.hermes/SOUL.md.hermes ] || cp /sandbox/.hermes/SOUL.md /sandbox/.hermes/SOUL.md.hermes; "
                              f"mv {SKILL_DIR}/SOUL.md /sandbox/.hermes/SOUL.md; "
                              # the gateway resolves the agent's home from its session DB, which NemoClaw keeps in
                              # runtime/ (state.db is a symlink there), so Hermes reads SOUL.md from runtime/ too
                              f"cp /sandbox/.hermes/SOUL.md /sandbox/.hermes/runtime/SOUL.md"])

    out = _run(exec_ + ["/opt/hermes/.venv/bin/python", "-c", PATCH, json.dumps(TOOLSETS)])
    result = json.loads(next(line for line in out.splitlines() if line.startswith("{")))
    import os

    result["cron"] = _cron(exec_, (os.environ.get("TELEGRAM_ALLOWED_IDS", "").split(",")[0]).strip())
    _run([nemohermes, sandbox, "gateway", "restart", "--quiet"])
    result["skill"] = SKILL_DIR
    result["plugin"] = PLUGIN_DIR
    return result
