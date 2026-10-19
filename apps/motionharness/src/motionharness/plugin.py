"""A task, the files a model writes for it, and the installable plugin package they become."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

NAME = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
WORDS = re.compile(r"`([a-z][a-z0-9_]*\.)?([a-z][a-z0-9_]{1,40})`")


@dataclass
class Task:
    """`add a spring_follow verb: the actor chases a target with spring physics`."""

    description: str
    name: str = ""  # bare verb name; inferred from the description when empty
    namespace: str = "mh"

    def __post_init__(self) -> None:
        if not self.name:
            m = WORDS.search(self.description) or re.search(r"\b([a-z]+_[a-z0-9_]+)\b", self.description)
            self.name = (m.group(m.lastindex or 1) if m else "custom_verb").lower()
        if "." in self.name:
            self.namespace, self.name = self.name.split(".", 1)
        if not NAME.match(self.name) or not NAME.match(self.namespace):
            raise ValueError(f"verb and namespace must be snake_case identifiers, got {self.namespace}.{self.name}")

    @property
    def verb(self) -> str:
        return f"{self.namespace}.{self.name}"

    @property
    def module(self) -> str:
        return f"{self.namespace}_{self.name}"

    @property
    def dist(self) -> str:
        return f"motionlang-{self.namespace}-{self.name}".replace("_", "-")

    @property
    def test_file(self) -> str:
        return f"test_{self.name}.py"

    def to_dict(self) -> dict:
        return {"description": self.description, "verb": self.verb, "module": self.module, "dist": self.dist}


FILES = ("verb.py", "test_verb.py", "demo.motion.json")
_BLOCK = re.compile(r"^#{2,4}\s*`?([\w.]+)`?\s*\n+```[\w+-]*\n(.*?)\n```", re.M | re.S)
_UNCHANGED = re.compile(r"^#{2,4}\s*`?([\w.]+)`?\s*\n+\(?unchanged\)?", re.M | re.I)
_THINK = re.compile(r"<think>.*?</think>", re.S)


@dataclass
class Source:
    """The three files a model writes. `notes` is its short design note."""

    verb: str = ""
    tests: str = ""
    demo: str = ""
    notes: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"verb.py": self.verb, "test_verb.py": self.tests, "demo.motion.json": self.demo}

    def missing(self) -> list[str]:
        return [k for k, v in self.as_dict().items() if not v.strip()]


def parse_reply(text: str, previous: Source | None = None) -> Source:
    """Pull `### verb.py` / `### test_verb.py` / `### demo.motion.json` fenced blocks out of a reply.

    A file marked `(unchanged)` keeps the previous version (repairs needn't resend everything)."""
    text = _THINK.sub("", text)
    found = {name.lower(): body for name, body in _BLOCK.findall(text)}
    prev = previous.as_dict() if previous else {}
    out = {}
    for f in FILES:
        stem = f.split(".")[0]
        body = found.get(f) or next((b for n, b in found.items() if n.startswith(stem)), None)
        out[f] = body if body is not None else prev.get(f, "")
    unchanged = {n.lower() for n in _UNCHANGED.findall(text)}
    for f in FILES:
        marked = f in unchanged or re.fullmatch(r"\(?\s*unchanged\s*\)?\.?", out[f].strip(), re.I)
        if marked and prev.get(f):
            out[f] = prev[f]
    notes = _BLOCK.sub("", _UNCHANGED.sub("", text)).strip()
    return Source(out["verb.py"], out["test_verb.py"], out["demo.motion.json"], notes[:1500])


def pyproject(task: Task) -> str:
    return f'''[project]
name = "{task.dist}"
version = "0.1.0"
description = "MotionLang verb plugin: {task.verb} (written and tested by MotionHarness)"
license = "Apache-2.0"
requires-python = ">=3.11"
dependencies = ["motionlang>=0.1"]

[project.optional-dependencies]
test = ["motionlang[testing]>=0.1"]

[project.entry-points."motionlang.verbs"]
{task.name} = "{task.module}.verb"

[build-system]
requires = ["hatchling>=1.26"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/{task.module}"]
'''


def readme(task: Task, src: Source, report: dict | None = None) -> str:
    lines = [f"# {task.dist}", "", f"MotionLang verb plugin adding **`{task.verb}`**.", "", f"> {task.description}", ""]
    if src.notes:
        lines += ["## Design notes", "", src.notes, ""]
    lines += ["## Install", "", "Into an environment that already has `motionlang` (it isn't on PyPI yet):", "",
              "```bash", "uv pip install .          # in the MotionLang workspace", "pip install <repo>/packages/motionlang .   # anywhere else", "```", "",
              f"The verb registers through the `motionlang.verbs` entry point, so `motionlang schema` and `motionlang prompt` "
              f"(and every planner built on them) see `{task.verb}` right away. Documents using it should declare "
              f'`"requires": ["{task.verb}>=1"]`.', ""]
    if src.demo.strip():
        lines += ["## Example", "", "```bash", "motionlang preview examples/demo.motion.json -o demo.html", "```", ""]
    if report:
        lines += ["## Test report", "", report_markdown(report), ""]
    return "\n".join(lines)


def report_markdown(report: dict) -> str:
    rows = ["| Suite | Passed | Failed |", "| --- | --- | --- |"]
    for suite in ("contract", "task"):
        s = report.get(suite) or {}
        rows.append(f"| {suite} | {s.get('passed', 0)} | {s.get('failed', 0)} |")
    return "\n".join(rows)


def package_files(task: Task, src: Source, report: dict | None = None) -> dict[str, str]:
    """The plugin package, relative to its root."""
    files = {
        "pyproject.toml": pyproject(task),
        "README.md": readme(task, src, report),
        f"src/{task.module}/__init__.py": f'"""MotionLang plugin: {task.verb}. The verb registers when `verb` is imported."""\n',
        f"src/{task.module}/verb.py": src.verb.rstrip() + "\n",
        f"tests/{task.test_file}": src.tests.rstrip() + "\n",
        "tests/test_contract.py": f"# The MotionLang verb contract suite (SPEC §11): run with MOTIONLANG_CONTRACT_VERB={task.verb} pytest\n"
                                  "from motionlang.testing.contract import *  # noqa: F401,F403\n",
        "LICENSE.txt": "Apache-2.0\n",
    }
    if src.demo.strip():
        files["examples/demo.motion.json"] = src.demo.rstrip() + "\n"
    return files


def unified_diff(old: dict[str, str], new: dict[str, str], prefix: str = "plugin") -> str:
    """A git-style diff of two file maps (old empty = the whole package as additions)."""
    out = []
    for path in sorted(set(old) | set(new)):
        a, b = old.get(path), new.get(path)
        if a == b:
            continue
        out.extend(difflib.unified_diff(
            (a or "").splitlines(keepends=True), (b or "").splitlines(keepends=True),
            fromfile=f"a/{prefix}/{path}" if a is not None else "/dev/null",
            tofile=f"b/{prefix}/{path}" if b is not None else "/dev/null",
        ))
    return "".join(l if l.endswith("\n") else l + "\n" for l in out)


def excerpt(message: str, limit: int = 1200) -> str:
    """The useful part of a pytest failure: its summary line, the failing source line(s) and the `E` lines."""
    lines = message.splitlines()
    head = lines[0] if lines else ""
    keep = [l for i, l in enumerate(lines) if l.startswith("E ") or l.startswith(">") or (i + 1 < len(lines) and lines[i + 1].startswith("E "))]
    loc = [l for l in lines if l.rstrip().endswith(("Error", "Exception")) or (":" in l and l.split(":")[0].endswith(".py"))][-2:]
    text = "\n".join(dict.fromkeys([head, *keep[:30], *loc]))
    if len(text) < 80:
        text = message[-limit:]
    return text[:limit]


@dataclass
class TestCase:
    suite: str
    name: str
    outcome: str  # passed | failed | error | skipped
    message: str = ""


@dataclass
class Report:
    """What came back from one sandbox run."""

    cases: list[TestCase] = field(default_factory=list)
    install_ok: bool = False
    install_log: str = ""
    logs: dict[str, str] = field(default_factory=dict)
    probe: dict = field(default_factory=dict)
    platform_error: str | None = None
    timed_out: bool = False

    def suite(self, name: str) -> dict:
        cs = [c for c in self.cases if c.suite == name]
        return {"passed": sum(c.outcome == "passed" for c in cs), "failed": sum(c.outcome in ("failed", "error") for c in cs),
                "skipped": sum(c.outcome == "skipped" for c in cs), "total": len(cs)}

    @property
    def demo_ok(self) -> bool:
        return bool((self.probe.get("demo") or {}).get("ok"))

    @property
    def passed(self) -> bool:
        """The acceptance gate: installs, registers, the contract suite and its own tests pass, the demo validates."""
        c, t = self.suite("contract"), self.suite("task")
        return (self.install_ok and self.probe.get("registered", False) and c["total"] > 0 and c["failed"] == 0
                and t["total"] > 0 and t["failed"] == 0 and self.demo_ok)

    def score(self) -> tuple:
        c, t = self.suite("contract"), self.suite("task")
        return (self.passed, self.install_ok, bool(self.probe.get("registered")), -c["failed"], c["passed"], self.demo_ok, -t["failed"], t["passed"])

    def problems(self, limit: int = 12) -> list[str]:
        """Failures written for the repair prompt: what failed and the assertion text."""
        out = []
        if self.platform_error:
            out.append(f"sandbox error: {self.platform_error}")
        if self.timed_out:
            out.append("the sandbox run timed out: baking is far too slow (sample() is called for every frame of hundreds of "
                       "test documents). Use a closed form, or integrate with a coarse fixed step and cache nothing between calls.")
        if not self.install_ok:
            out.append(f"plugin install failed:\n{self.install_log[-1500:]}")
        if self.probe.get("error"):
            out.append(f"loading the plugin failed:\n{self.probe['error'][-1500:]}")
        elif self.install_ok and not self.probe.get("registered"):
            out.append(f"the entry point loaded but no verb named {self.probe.get('verb') or 'as required'} was registered "
                       f"(registered plugin verbs: {self.probe.get('plugin_verbs')})")
        if self.probe.get("registered") and self.probe.get("n_examples") == 0:
            out.append("the verb's `examples` list is empty: the contract suite needs at least one example (give two)")
        for c in self.cases:
            if c.outcome in ("failed", "error"):
                out.append(f"[{c.suite}] {c.name}: {excerpt(c.message)}")
        demo = self.probe.get("demo")
        if demo and not demo.get("ok"):
            out.append("demo.motion.json does not validate: " + "; ".join(demo.get("issues", [])[:5]))
        for suite in ("contract", "task"):
            if self.suite(suite)["total"] == 0 and self.install_ok:
                log = self.logs.get(suite, "")
                out.append(f"[{suite}] no tests ran (collection error?):\n{log[-1500:]}")
        return out[:limit]

    def to_dict(self) -> dict:
        return {
            "passed": self.passed, "install_ok": self.install_ok, "contract": self.suite("contract"), "task": self.suite("task"),
            "demo_ok": self.demo_ok, "cases": [c.__dict__ for c in self.cases], "problems": self.problems(),
            "probe": {k: v for k, v in self.probe.items() if k not in ("demo",)} | {"demo": {k: v for k, v in (self.probe.get("demo") or {}).items() if k != "tracks"}},
            "platform_error": self.platform_error,
        }
