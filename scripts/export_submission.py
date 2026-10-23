"""Export one app as a self-contained public repo for a hackathon submission.

    python scripts/export_submission.py sketchdynamics            # → ../sketchdynamics-public
    python scripts/export_submission.py sketchdynamics --dest DIR --no-verify

The export holds packages/motionlang + apps/<app> (committed files only, via `git archive HEAD`), a
generated root pyproject/README, LICENSE and .gitignore, and a fresh git history of one commit. It never
includes docs/ (private planning), demos/ or other apps. Sample artwork is kept only if the app's
samples README records its license. It then scans for secrets and private references, and (unless
--no-verify) runs `uv sync` and the tests inside the export.

Publishing is a separate, deliberate step: the script prints the `gh repo create … --public` command
instead of running it.
"""

from __future__ import annotations

import argparse
import io
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKER = ".exported-from-motion-dsl-workspace"
SECRET_PATTERNS = [
    (r"(?i)(api[_-]?key|secret|token)\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{20,}", "key-like assignment"),
    (r"\b(sk|tvly|nb)-[A-Za-z0-9_\-]{16,}", "provider key prefix"),
    (r"eyJ[A-Za-z0-9_\-]{30,}\.[A-Za-z0-9_\-]{20,}", "JWT"),
]
PRIVATE_PATTERNS = [
    (r"\bdocs/", "link into private docs/"),
    (r"/Users/[A-Za-z0-9_]+", "local home path"),
    (r"(?i)feedback log", "points to the private feedback log"),
    (r"(?i)motionharness|canvasmind", "mentions an unreleased project"),
]
# How each app starts, for the export's root README (default: `uv run <app>` on port 8765).
APP_RUN = {"motionharness": ("uv run motionharness serve", "opens http://127.0.0.1:8766/"),
           "constellate": ("uv run constellate serve", "opens http://127.0.0.1:8780/")}
APP_EXTRAS = {"motionharness": "[sandboxes]"}  # optional extras the export installs


def run(cmd: list[str], cwd: Path, quiet: bool = False) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"failed: {' '.join(cmd)}\n{r.stdout[-2000:]}{r.stderr[-2000:]}")
    if not quiet and r.stdout.strip():
        print(r.stdout.strip()[-1500:])
    return r


def archive(paths: list[str], dest: Path) -> None:
    data = subprocess.run(["git", "archive", "--format=tar", "HEAD", *paths], cwd=ROOT, capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(dest, filter="data")


def licensed_samples(samples: Path) -> tuple[set[str], list[str]]:
    """Files listed in the samples README with a license (on the same line, or under a heading that names one)."""
    readme = samples / "README.md"
    if not readme.exists():
        return set(), []
    ok, section_license = set(), None
    for line in readme.read_text().splitlines():
        if line.startswith("#"):
            m = re.search(r"\(([^)]*(?:CC0|CC-BY|CC BY|MIT|Apache|Public Domain|PD)[^)]*)\)", line, re.I)
            section_license = m.group(1) if m else None
            continue
        m = re.search(r"\]\(([^)]+\.svg)\)", line)
        if m and (section_license or re.search(r"(?i)licen[cs]e\s*:|CC0|CC-BY|CC BY|\bMIT\b|Apache|public domain", line)):
            ok.add(m.group(1))
    files = sorted(p.name for p in samples.glob("*.svg"))
    return ok, [f for f in files if f not in ok]


def root_pyproject(app: str) -> str:
    return f'''# {app} and the MotionLang library it is built on. `uv sync` installs both from this checkout.
[project]
name = "{app}-workspace"
version = "0.0.0"
requires-python = ">=3.11"
dependencies = ["motionlang[agent]", "{app}{APP_EXTRAS.get(app, "")}"]

[dependency-groups]
dev = ["pytest>=8", "hypothesis>=6.100", "jsonschema>=4.22", "httpx>=0.27"]

[tool.uv]
package = false

[tool.uv.workspace]
members = ["packages/motionlang", "apps/{app}"]

[tool.uv.sources]
motionlang = {{ workspace = true }}
{app} = {{ workspace = true }}

[tool.pytest.ini_options]
testpaths = ["packages", "apps"]
markers = ["live: calls the real Token Factory API (run with RUN_LIVE=1)"]
'''


def root_readme(app: str, title: str, blurb: str) -> str:
    steps = [("uv sync", ""), (f"cp apps/{app}/.env.example .env", "then add your NEBIUS_API_KEY"),
             APP_RUN.get(app, (f"uv run {app}", "opens http://127.0.0.1:8765/")), ("uv run pytest", "tests use a scripted model: no key needed")]
    width = max(len(c) for c, _ in steps) + 2
    cmds = "\n".join(f"{c:<{width}}# {n}" if n else c for c, n in steps)
    samples_readme = ROOT / "apps" / app / "src" / app / "static" / "samples" / "README.md"
    samples = ("" if not samples_readme.exists() else
               f" Third-party sample artwork is listed with its license in\n[`apps/{app}/src/{app}/static/samples/README.md`](apps/{app}/src/{app}/static/samples/README.md).")
    return f"""# {title}

{blurb}

- **The app**: [`apps/{app}`](apps/{app}): setup, how it uses NVIDIA Nemotron on Nebius Token Factory, and the API.
- **The language**: [`packages/motionlang`](packages/motionlang): MotionLang, the declarative motion DSL the app's
  agent writes, with its [spec](packages/motionlang/SPEC.md), validator, baker and player.

## Quick start

```bash
{cmds}
```

License: Apache-2.0 (see [LICENSE](LICENSE)).{samples}
"""


def scan(dest: Path, app: str = "") -> list[str]:
    found = []
    for p in sorted(dest.rglob("*")):
        if not p.is_file() or ".git" in p.parts or ".venv" in p.parts or p.suffix in {".svg", ".lock"}:
            continue
        try:
            text = p.read_text()
        except UnicodeDecodeError:
            continue
        rel = p.relative_to(dest)
        for patterns, kind in ((SECRET_PATTERNS, "SECRET?"), (PRIVATE_PATTERNS, "private")):
            for rx, what in patterns:
                for m in re.finditer(rx, text):
                    if kind == "private" and app and m.group(0).lower() == app.lower():
                        continue  # the exported app may name itself
                    line = text.count("\n", 0, m.start()) + 1
                    found.append(f"{kind:8} {rel}:{line}: {what}: {text.splitlines()[line - 1].strip()[:110]}")
    return list(dict.fromkeys(found))  # one report per line and kind


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("app")
    ap.add_argument("--dest", type=Path, help="default: ../<app>-public next to this repo")
    ap.add_argument("--title", help="root README title (default: the app README's first heading)")
    ap.add_argument("--no-verify", action="store_true", help="skip uv sync + pytest in the export")
    args = ap.parse_args()

    app_dir = ROOT / "apps" / args.app
    if not app_dir.is_dir():
        sys.exit(f"no such app: apps/{args.app}")
    dirty = run(["git", "status", "--porcelain", "--", f"apps/{args.app}", "packages/motionlang"], ROOT, quiet=True).stdout
    if dirty.strip():
        print(f"warning: uncommitted changes are NOT exported (only HEAD is):\n{dirty}")
    head = run(["git", "rev-parse", "--short", "HEAD"], ROOT, quiet=True).stdout.strip()

    dest = (args.dest or ROOT.parent / f"{args.app}-public").resolve()
    if dest.exists():
        if not (dest / MARKER).exists():
            sys.exit(f"{dest} exists and wasn't made by this script; refusing to overwrite it")
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    extras = [f for f in ("render.yaml",) if run(["git", "ls-files", f], ROOT, quiet=True).stdout.strip()]
    archive(["packages/motionlang", f"apps/{args.app}", "LICENSE", ".gitignore", *extras], dest)
    (dest / MARKER).write_text(f"exported from motion-dsl workspace @ {head}\n")

    samples = dest / "apps" / args.app / "src" / args.app / "static" / "samples"
    keep, dropped = licensed_samples(samples)
    for name in dropped:
        (samples / name).unlink()
    if dropped and (samples / "README.md").exists():  # no dead links in the public copy
        lines = (samples / "README.md").read_text().splitlines()
        (samples / "README.md").write_text("\n".join(l for l in lines if not any(f"({n})" in l for n in dropped)) + "\n")

    app_readme = (app_dir / "README.md").read_text()
    title = args.title or re.search(r"^#\s+(.+)$", app_readme, re.M).group(1).strip()
    blurb = next((p for p in app_readme.split("\n\n")[1:] if p.strip() and not p.startswith(("License", "#", "|", "```"))), "")
    (dest / "pyproject.toml").write_text(root_pyproject(args.app))
    (dest / "README.md").write_text(root_readme(args.app, title, blurb.strip()))
    gitignore = (dest / ".gitignore").read_text()
    if MARKER not in gitignore:
        (dest / ".gitignore").write_text(gitignore.rstrip() + f"\n{MARKER}\n")

    findings = scan(dest, args.app)

    if not args.no_verify:
        print("verifying: uv lock / sync / pytest in the export …")
        run(["uv", "lock", "-q"], dest, quiet=True)
        run(["uv", "sync", "-q"], dest, quiet=True)
        r = run(["uv", "run", "pytest", "-q"], dest, quiet=True)
        print("  " + r.stdout.strip().splitlines()[-1])

    run(["git", "init", "-q", "-b", "main"], dest, quiet=True)
    run(["git", "add", "-A"], dest, quiet=True)
    run(["git", "commit", "-q", "-m", f"{title}: submission snapshot (from {head})"], dest, quiet=True)

    print(f"\nexported apps/{args.app} @ {head} → {dest}")
    print(f"  samples kept: {len(keep)}; dropped (no license recorded): {', '.join(dropped) or 'none'}")
    print(f"  findings to review: {len(findings)}")
    for f in findings:
        print("   ", f)
    print(f"\nto publish (after `gh auth login`):\n  cd {dest} && gh repo create <owner>/{args.app} --public --source . --push")


if __name__ == "__main__":
    main()
