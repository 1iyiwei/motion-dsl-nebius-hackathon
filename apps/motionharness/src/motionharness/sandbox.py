"""Where generated code runs. Two backends behind one small interface:

    ContreeSandbox   Nebius Token Factory Sandboxes (Beta): every run starts from an immutable image
                     (a checkpoint) and saves its result as a new image, so candidates branch from one
                     prepared environment and repairs branch from the candidate they fix.
    LocalSandbox     the same semantics with directories and subprocesses, for when Sandboxes access
                     isn't set up. It does NOT isolate untrusted code; it only strips secrets from the env.

Generated code never runs in the harness process: the host only reads back files (test reports, tracks).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

SANDBOX_FILES = ("probe.py", "shim_install.py")
# The contract suite ships in motionlang (motionlang.testing.contract); MOTIONLANG_CONTRACT_VERB limits it to one verb.
CONTRACT_TEST = b"from motionlang.testing.contract import *  # noqa: F401,F403  (SPEC \xc2\xa711)\n"

TEST_DEPS = ("pydantic>=2.7", "numpy>=1.26", "pytest>=8", "hypothesis>=6.100", "hatchling>=1.26")
OCI_BASE = "docker.io/library/python:3.12-slim"
SANDBOXES_URL = "https://api.tokenfactory.nebius.com/sandboxes"
WORK = "/work"


def harness_files() -> dict[str, bytes]:
    pkg = resources.files("motionharness") / "sandbox_files"
    files = {name: (pkg / name).read_bytes() for name in SANDBOX_FILES}
    files["test_contract.py"] = CONTRACT_TEST
    return files


@dataclass
class Checkpoint:
    id: str  # Sandboxes image UUID, or a local directory
    backend: str
    label: str = ""


@dataclass
class Job:
    base: Checkpoint
    files: dict[str, bytes]  # relative to the work dir
    shell: str
    collect: list[str] = field(default_factory=list)  # files to read back after the run
    timeout: float = 600
    label: str = ""


@dataclass
class RunResult:
    exit_code: int
    stdout: str
    stderr: str
    seconds: float
    checkpoint: Checkpoint | None  # the saved state after the run (branch point for repairs)
    files: dict[str, bytes] = field(default_factory=dict)
    error: str | None = None  # the platform failed (not the command)


class Sandbox(ABC):
    name: str
    python: str  # interpreter command inside the sandbox

    @abstractmethod
    def environment(self) -> Checkpoint:
        """A checkpoint with motionlang, the test tools and the harness files installed (cached)."""

    @abstractmethod
    def run_many(self, jobs: list[Job]) -> list[RunResult]:
        """Run jobs concurrently; each starts from its own base checkpoint."""

    def run(self, job: Job) -> RunResult:
        return self.run_many([job])[0]

    @abstractmethod
    def install_cmd(self, plugin_dir: str) -> str:
        """Shell command that installs a plugin package from the work dir."""

    def describe(self) -> dict:
        return {"backend": self.name}


# ---- local -------------------------------------------------------------------------------------------


class LocalSandbox(Sandbox):
    name = "local"

    def __init__(self, root: Path, python: str | None = None, max_workers: int = 4):
        self.root = Path(root).resolve()  # the run's cwd is the image dir; paths must be absolute
        self.python = python or sys.executable
        self.max_workers = max_workers
        self._env: Checkpoint | None = None
        self.fallback_reason: str | None = None  # why Sandboxes wasn't used, when it was configured

    def environment(self) -> Checkpoint:
        if self._env is None:
            d = self.root / "env"
            d.mkdir(parents=True, exist_ok=True)
            for name, data in harness_files().items():
                (d / name).write_bytes(data)
            self._env = Checkpoint(str(d), self.name, "environment")
        return self._env

    def install_cmd(self, plugin_dir: str) -> str:
        return f"{_q(self.python)} shim_install.py {plugin_dir} site"

    def _one(self, job: Job) -> RunResult:
        t = time.time()
        d = self.root / f"img-{uuid.uuid4().hex[:10]}"
        shutil.copytree(job.base.id, d)
        for rel, data in job.files.items():
            p = d / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(d), "LANG": "C.UTF-8",
            "PYTHONPATH": str(d / "site"), "PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1",
        }  # no API keys: generated code must not see them
        try:
            proc = subprocess.Popen(["/bin/sh", "-c", job.shell], cwd=d, env=env, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, start_new_session=True)
            try:
                out, err = proc.communicate(timeout=job.timeout)
                code = proc.returncode
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                out, err = proc.communicate()
                code, err = 124, (err or "") + f"\n[timed out after {job.timeout:.0f}s]"
        except OSError as e:
            return RunResult(-1, "", "", time.time() - t, None, error=str(e))
        files = {rel: (d / rel).read_bytes() for rel in job.collect if (d / rel).is_file()}
        return RunResult(code, out[-1_000_000:], err[-1_000_000:], time.time() - t, Checkpoint(str(d), self.name, job.label), files)

    def run_many(self, jobs: list[Job]) -> list[RunResult]:
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            return list(pool.map(self._one, jobs))

    def describe(self) -> dict:
        d = {"backend": self.name, "isolation": "subprocess (no isolation; secrets stripped)", "root": str(self.root)}
        if self.fallback_reason:
            d["fallback_reason"] = self.fallback_reason
        return d


def _q(s: str) -> str:
    return "'" + s.replace("'", "'\"'\"'") + "'"


# ---- Token Factory Sandboxes -------------------------------------------------------------------------


def _motionlang_tree() -> dict[str, bytes]:
    """The installed motionlang package as a buildable source tree (works for a workspace or a PyPI install)."""
    import motionlang

    pkg = Path(motionlang.__file__).parent
    files: dict[str, bytes] = {}
    for p in sorted(pkg.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and p.suffix not in {".pyc"}:
            files[f"src/motionlang/{p.relative_to(pkg)}"] = p.read_bytes()
    files["pyproject.toml"] = (
        '[project]\nname = "motionlang"\nversion = "%s"\nrequires-python = ">=3.11"\n'
        'dependencies = ["pydantic>=2.7", "numpy>=1.26"]\n'
        '[project.entry-points."motionlang.verbs"]\ncore = "motionlang.verbs.core"\n'
        '[build-system]\nrequires = ["hatchling>=1.26"]\nbuild-backend = "hatchling.build"\n' % motionlang.__version__
    ).encode()
    return files


class ContreeSandbox(Sandbox):
    name = "contree"
    python = "python"

    def __init__(self, cache: Path | None = None, base_image: str = OCI_BASE):
        from contree_sdk import Contree  # optional dependency: pip install motionharness[sandboxes]

        self._Contree = Contree
        self.cache = cache or Path.home() / ".cache" / "motionharness" / "contree.json"
        self.base_image = base_image
        self._env: Checkpoint | None = None
        self.env_seconds: float | None = None  # how long preparing the environment took (None: cached)

    @staticmethod
    def available() -> str | None:
        """None if usable, else why not."""
        try:
            import contree_sdk  # noqa: F401
        except ImportError:
            return "contree-sdk is not installed (reinstall motionharness: uv sync)"
        missing = [k for k in ("NEBIUS_API_KEY", "NEBIUS_PROJECT_ID") if not os.environ.get(k)]
        return f"{' and '.join(missing)} not set" if missing else None

    NEEDED = ("import", "spawn")  # import the base image once; spawn every candidate run

    @staticmethod
    def whoami(timeout: float = 10) -> tuple[int, dict]:
        """(HTTP status, body) of the Sandboxes whoami probe: permissions and limits for this key + project."""
        import httpx

        r = httpx.get(f"{SANDBOXES_URL}/v1/whoami", timeout=timeout,
                      headers={"Authorization": f"Bearer {os.environ['NEBIUS_API_KEY']}", "Project": os.environ["NEBIUS_PROJECT_ID"]})
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, {"error": r.text[:300]}

    @classmethod
    def access(cls, timeout: float = 10) -> str | None:
        """None if this project may run Sandboxes, else why not (configured but inactive, bad key, unreachable)."""
        why = cls.available()
        if why:
            return why
        try:
            status, info = cls.whoami(timeout)
        except Exception as e:
            return f"Sandboxes API unreachable ({type(e).__name__})"
        if status != 200:
            return f"Sandboxes API refused the key/project (HTTP {status}: {str(info.get('error', ''))[:120]})"
        perms = info.get("permissions") or {}
        missing = [p for p in cls.NEEDED if not perms.get(p)]
        if missing:
            return (f"the project is not activated for Sandboxes yet (permissions {', '.join(missing)} are false; "
                    "email contree@nebius.com with the project ID to activate it)")
        return None

    def install_cmd(self, plugin_dir: str) -> str:
        return f"python -m pip install -q --no-deps --no-build-isolation --no-index ./{plugin_dir}"

    def _client(self):
        return self._Contree()

    def _key(self, files: dict[str, bytes]) -> str:
        h = hashlib.sha256((self.base_image + "\n".join(TEST_DEPS)).encode())
        for k in sorted(files):
            h.update(k.encode() + b"\0" + files[k])
        return h.hexdigest()[:16]

    def environment(self) -> Checkpoint:
        if self._env is not None:
            return self._env
        src = _motionlang_tree()
        files = {f"/opt/motionlang/{k}": v for k, v in src.items()}
        files.update({f"{WORK}/{k}": v for k, v in harness_files().items()})
        key = self._key(files)
        cached = json.loads(self.cache.read_text()) if self.cache.exists() else {}
        if key in cached:
            self._env = Checkpoint(cached[key], self.name, "environment (cached)")
            return self._env
        deps = " ".join(f"'{d}'" for d in TEST_DEPS)
        shell = f"python -m pip install -q {deps} && python -m pip install -q /opt/motionlang && python -c 'import motionlang, hypothesis, pytest'"
        t = time.time()

        async def go():
            client = self._client()
            base = await client.images.oci(self.base_image)
            return await base.run(shell=shell, files=files, cwd="/", timeout=900, disposable=False)

        r = asyncio.run(go())
        self.env_seconds = time.time() - t
        if r.exit_code != 0 or r.uuid is None:
            raise RuntimeError(f"preparing the Sandboxes environment failed (exit {r.exit_code}): {(r.stderr or '')[-2000:]}")
        cached[key] = r.uuid
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        self.cache.write_text(json.dumps(cached, indent=1))
        self._env = Checkpoint(r.uuid, self.name, "environment")
        return self._env

    async def _one(self, client, job: Job) -> RunResult:
        t = time.time()
        try:
            image = await client.images.use(job.base.id, strict=True)
            r = await image.run(
                shell=job.shell, cwd=WORK, files={f"{WORK}/{k}": v for k, v in job.files.items()},
                timeout=job.timeout, disposable=False, truncate_output_at=1_000_000,
            )
            files = {}
            if r.uuid is not None:
                for rel in job.collect:
                    try:
                        files[rel] = await r.read(f"{WORK}/{rel}")
                    except Exception:  # a missing output file is the command's failure, reported by the caller
                        pass
            cp = Checkpoint(r.uuid, self.name, job.label) if r.uuid else None
            return RunResult(r.exit_code if r.exit_code is not None else -1, r.stdout or "", r.stderr or "", time.time() - t, cp, files)
        except Exception as e:  # platform failure, not a test failure
            return RunResult(-1, "", "", time.time() - t, None, error=f"{type(e).__name__}: {e}")

    def run_many(self, jobs: list[Job]) -> list[RunResult]:
        async def go():
            client = self._client()
            return await asyncio.gather(*(self._one(client, j) for j in jobs))

        return list(asyncio.run(go()))

    def describe(self) -> dict:
        return {"backend": self.name, "isolation": "Token Factory Sandboxes microVM", "base_image": self.base_image}


def make_sandbox(kind: str, root: Path) -> Sandbox:
    """kind: auto (Sandboxes when this project may use them, else local) | local | contree.

    auto probes access once (whoami, a few seconds at most), so a configured-but-inactive project falls back to local
    with a note instead of failing every run."""
    kind = kind or os.environ.get("MOTIONHARNESS_SANDBOX", "auto")
    if kind == "local":
        return LocalSandbox(root)
    if kind == "contree":
        why = ContreeSandbox.access()
        if why:
            raise RuntimeError(f"Sandboxes backend unavailable: {why}")
        return ContreeSandbox()
    if ContreeSandbox.available():  # not configured at all: local, quietly
        return LocalSandbox(root)
    why = ContreeSandbox.access(timeout=5)
    if why:
        sb = LocalSandbox(root)
        sb.fallback_reason = why
        return sb
    return ContreeSandbox()
