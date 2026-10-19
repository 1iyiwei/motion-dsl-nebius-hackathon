"""Install a plugin's source tree onto a target dir with a minimal dist-info, so importlib.metadata finds its
`motionlang.verbs` entry point. Used by the local backend (no pip, no network); Sandboxes uses real pip.

    python shim_install.py PLUGIN_DIR TARGET_DIR
"""

import shutil
import sys
import tomllib
from pathlib import Path

plugin, target = Path(sys.argv[1]), Path(sys.argv[2])
meta = tomllib.loads((plugin / "pyproject.toml").read_text())
proj = meta["project"]
target.mkdir(parents=True, exist_ok=True)
for pkg in (plugin / "src").iterdir():
    if pkg.is_dir():
        shutil.copytree(pkg, target / pkg.name, dirs_exist_ok=True)
info = target / f"{proj['name'].replace('-', '_')}-{proj['version']}.dist-info"
info.mkdir(exist_ok=True)
(info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {proj['name']}\nVersion: {proj['version']}\n")
lines = []
for group, eps in proj.get("entry-points", {}).items():
    lines.append(f"[{group}]")
    lines += [f"{k} = {v}" for k, v in eps.items()]
(info / "entry_points.txt").write_text("\n".join(lines) + "\n")
print(f"installed {proj['name']} {proj['version']} (shim)")
