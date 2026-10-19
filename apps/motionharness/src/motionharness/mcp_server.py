"""MotionHarness as an MCP server (stdio), so coding agents (Claude Code, Cursor, …) can ask it for a tested verb.

    claude mcp add motionharness -- uv run --directory /path/to/repo motionharness mcp

Tools: new_verb (write, test, critique, ship; returns the report), list_runs, get_run, read_artifact.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from motionlang.agent.llm import load_env

from .harness import Config, Harness, tavily_search
from .plugin import Task
from .sandbox import make_sandbox

ARTIFACTS = {"report.md", "plugin.diff", "plugin/pyproject.toml"}


def create_server(root: Path, sandbox_kind: str = "auto", harness: Harness | None = None):
    from mcp.server.fastmcp import FastMCP  # optional dependency: pip install motionharness[mcp]

    root = Path(root)
    mcp = FastMCP("motionharness", instructions=(
        "MotionHarness writes MotionLang verb plugins with NVIDIA Nemotron, tests them in a sandbox against the "
        "MotionLang contract suite, has a vision model critique the motion, and returns an installable package. "
        "A run takes 1–5 minutes."))
    state = {"harness": harness}

    def get_harness() -> Harness:
        if state["harness"] is None:
            state["harness"] = Harness(make_sandbox(sandbox_kind, root / ".sandbox"), root=root, tavily=tavily_search())
        return state["harness"]

    def summary(run_json: dict) -> dict:
        win = next((c for c in run_json["candidates"] if c["id"] == run_json.get("winner")), None)
        return {
            "id": run_json["id"], "status": run_json["status"], "verb": run_json["task"]["verb"], "winner": run_json.get("winner"),
            "candidates": [{"id": c["id"], "parent": c["parent"], "kind": c["kind"], "status": c["status"],
                            "contract": (c["report"] or {}).get("contract"), "own_tests": (c["report"] or {}).get("task"),
                            "critique_score": (c["critique"] or {}).get("score")} for c in run_json["candidates"]],
            "critique": win and win.get("critique"), "seconds": run_json.get("seconds"), "tokens": run_json.get("tokens"),
            "plugin_dir": str((root / run_json["id"] / "plugin").resolve()) if win else None,
        }

    @mcp.tool()
    async def new_verb(task: str, name: str = "", namespace: str = "mh", candidates: int = 3, rounds: int = 3, critique: bool = True) -> dict:
        """Write, test and critique a new MotionLang verb plugin.

        task: what the verb should do, e.g. "add a `spring_follow` verb: the actor chases a target with spring physics".
        Returns the run summary (per-candidate test results, the vision critique) and the path of the installable
        plugin package (install it into an environment that has motionlang: `uv pip install <plugin_dir>` in the
        MotionHarness workspace)."""
        import anyio

        h = get_harness()
        t = Task(task, name=name, namespace=namespace or "mh")
        cfg = Config(candidates=max(1, min(candidates, 5)), max_rounds=max(0, min(rounds, 5)), critique=critique)
        run = await anyio.to_thread.run_sync(h.run, t, cfg)  # the sandbox backend runs its own event loop
        return summary(run.to_dict())

    @mcp.tool()
    def list_runs(limit: int = 10) -> list[dict]:
        """Recent MotionHarness runs (newest first)."""
        out = []
        for p in sorted(root.glob("*/run.json"), reverse=True)[: max(1, limit)]:
            r = json.loads(p.read_text())
            out.append({"id": r["id"], "status": r["status"], "verb": r["task"]["verb"], "winner": r.get("winner")})
        return out

    @mcp.tool()
    def get_run(run_id: str) -> dict:
        """One run's summary: candidates, test results, critique, plugin path."""
        return summary(json.loads(_run_dir(root, run_id).joinpath("run.json").read_text()))

    @mcp.tool()
    def read_artifact(run_id: str, path: str = "report.md") -> str:
        """Read a run artifact: report.md, plugin.diff, plugin/pyproject.toml, or plugin/src/<module>/verb.py."""
        d = _run_dir(root, run_id)
        p = (d / path).resolve()
        if not p.is_relative_to(d) or not p.is_file() or not (path in ARTIFACTS or (path.startswith("plugin/") and p.suffix in {".py", ".md", ".json"})):
            raise ValueError(f"not a readable artifact: {path}")
        return p.read_text()[:100_000]

    return mcp


def _run_dir(root: Path, run_id: str) -> Path:
    d = (root / run_id).resolve()
    if d.parent != root.resolve() or not (d / "run.json").exists():
        raise ValueError(f"no such run: {run_id}")
    return d


def main(root: Path, sandbox_kind: str = "auto") -> None:
    load_env()
    create_server(Path(os.environ.get("MOTIONHARNESS_RUNS", root)), sandbox_kind).run()  # stdio
