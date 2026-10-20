"""Install, uninstall and list generated plugins in the environment MotionHarness runs in, and zip them for download.

Installing puts model-written code into this environment (it passed the contract suite, but it is not sandboxed once
installed). Apps that load plugins at startup, like SketchDynamics, need a restart to see the change.
"""

from __future__ import annotations

import importlib
import io
import json
import shutil
import subprocess
import sys
import zipfile
from importlib.metadata import distributions
from pathlib import Path


def _pip(*args: str) -> list[str]:
    """pip for *this* interpreter: uv's when available (uv venvs ship without pip)."""
    if shutil.which("uv"):
        return ["uv", "pip", *args[:1], "--python", sys.executable, *args[1:]]
    return [sys.executable, "-m", "pip", *args]


def run_info(run_dir: Path) -> dict:
    r = json.loads((run_dir / "run.json").read_text())
    return {"id": r["id"], "dist": r["task"]["dist"], "verb": r["task"]["verb"], "plugin": run_dir / "plugin"}


def installed() -> list[dict]:
    """Plugin packages that register MotionLang verbs (core excluded): [{dist, version, modules}]."""
    importlib.invalidate_caches()  # a long-running server must see installs made after it started
    out = []
    for d in distributions():
        eps = [ep for ep in d.entry_points if ep.group == "motionlang.verbs"]
        name = d.metadata["Name"] or ""
        if eps and name.lower() != "motionlang":
            out.append({"dist": name, "version": d.version, "modules": sorted(ep.value for ep in eps)})
    return sorted(out, key=lambda x: x["dist"])


def is_installed(dist: str) -> bool:
    key = dist.lower().replace("_", "-")
    return any(p["dist"].lower().replace("_", "-") == key for p in installed())


def install(run_dir: Path) -> tuple[bool, str]:
    info = run_info(run_dir)
    if not (info["plugin"] / "pyproject.toml").exists():
        return False, f"run {info['id']} has no plugin package"
    # Reinstall cleanly so a newer run of the same verb replaces the old one.
    r = subprocess.run(_pip("install", "--reinstall-package", info["dist"], str(info["plugin"])) if shutil.which("uv") else
                       _pip("install", "--force-reinstall", "--no-deps", str(info["plugin"])),
                       capture_output=True, text=True, timeout=300)
    return r.returncode == 0, (r.stderr or r.stdout).strip()[-2000:]


def uninstall(dist: str) -> tuple[bool, str]:
    if not is_installed(dist):
        return True, f"{dist} is not installed"
    args = _pip("uninstall", dist) if shutil.which("uv") else _pip("uninstall", "-y", dist)
    r = subprocess.run(args, capture_output=True, text=True, timeout=120)
    return r.returncode == 0, (r.stderr or r.stdout).strip()[-2000:]


def plugin_zip(run_dir: Path) -> bytes:
    """The plugin package as a zip whose top folder is the distribution name."""
    info = run_info(run_dir)
    root = info["plugin"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(root.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                z.write(p, f"{info['dist']}/{p.relative_to(root)}")
        for extra in ("report.md", "plugin.diff", "preview.html", "frames.png"):
            if (run_dir / extra).exists():
                z.write(run_dir / extra, f"{info['dist']}/run/{extra}")
    return buf.getvalue()
