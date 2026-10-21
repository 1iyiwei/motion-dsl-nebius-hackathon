"""Settings from the environment (.env is loaded by the CLI)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _paths(value: str) -> list[Path]:
    return [Path(p).expanduser() for p in value.split(":") if p.strip()]


@dataclass
class Config:
    home: Path
    token: str = ""
    public_host: str = ""
    port: int = 8770
    watch: list[Path] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            home=Path(os.environ.get("CANVASMIND_HOME", "~/CanvasMind")).expanduser(),
            token=os.environ.get("CANVASMIND_MCP_TOKEN", ""),
            public_host=os.environ.get("CANVASMIND_PUBLIC_HOST", ""),
            port=int(os.environ.get("CANVASMIND_PORT", "8770")),
            watch=_paths(os.environ.get("CANVASMIND_WATCH", "")),
        )
