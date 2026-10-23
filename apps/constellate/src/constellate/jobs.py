"""Nebius Serverless Jobs through the `nebius` CLI.

Every job sets a CPU platform and preset explicitly: the CLI's default is an H100.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PLATFORM = "cpu-d3"
DEFAULT_PRESET = "4vcpu-16gb"


def cli_path() -> str | None:
    p = os.environ.get("NEBIUS_CLI")
    if p:
        return str(Path(p).expanduser())
    return shutil.which("nebius") or (str(Path.home() / ".nebius/bin/nebius") if (Path.home() / ".nebius/bin/nebius").exists() else None)


def project_id() -> str | None:
    return os.environ.get("NEBIUS_CLOUD_PROJECT_ID") or None


@dataclass
class JobSpec:
    name: str
    image: str
    command: str | None = None
    args: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    volumes: list[str] = field(default_factory=list)
    platform: str = field(default_factory=lambda: os.environ.get("CONSTELLATE_JOB_PLATFORM", DEFAULT_PLATFORM))
    preset: str = field(default_factory=lambda: os.environ.get("CONSTELLATE_JOB_PRESET", DEFAULT_PRESET))
    disk: str = "32Gi"
    timeout: str = "1h"


def create_args(spec: JobSpec, project: str, dry_run: bool = False) -> list[str]:
    a = ["--no-browser", "ai", "job", "create", "--parent-id", project, "--name", spec.name, "--image", spec.image,
         "--platform", spec.platform, "--preset", spec.preset, "--disk-size", spec.disk, "--timeout", spec.timeout,
         "--on-demand", "--format", "json"]
    if spec.command:
        a += ["--container-command", spec.command]
    if spec.args:
        a += ["--args", spec.args]
    for k, v in spec.env.items():
        a += ["--env", f"{k}={v}"]
    for v in spec.volumes:
        a += ["--volume", v]
    if dry_run:
        a.append("--dry-run")
    else:
        a.append("--async")
    return a


def run_cli(args: list[str], timeout: float = 120) -> subprocess.CompletedProcess:
    cli = cli_path()
    if not cli:
        raise RuntimeError("nebius CLI not found; install it (https://docs.nebius.com/cli) or set NEBIUS_CLI")
    return subprocess.run([cli, *args], capture_output=True, text=True, timeout=timeout)


def hello_spec() -> JobSpec:
    return JobSpec(name="constellate-hello", image="python:3.12-slim", command="python",
                   args="-c 'import os,platform;print(\"hello from\",platform.node(),os.cpu_count(),\"cpus\")'")
