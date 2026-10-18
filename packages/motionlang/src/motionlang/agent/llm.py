"""Thin OpenAI-compatible client for Nebius Token Factory, with per-call telemetry."""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

DEFAULT_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"

# Model roles. Override with MOTION_PLANNER_MODEL / MOTION_EDITOR_MODEL / MOTION_VISION_MODEL.
PLANNER_MODEL = "nvidia/Nemotron-3-Ultra-550b-a55b"  # writes and repairs whole documents
EDITOR_MODEL = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"  # fast id-addressed edits
VISION_MODEL = "nvidia/Nemotron-3.5-Super-VL"  # sketches and rendered frames (later)


def prefers_structured(model: str) -> bool:
    """Whether to constrain this model's documents to the JSON Schema by default: on for the Super family
    (most reliable that way), off for Ultra (degenerates under constrained decoding) and others."""
    return "super" in model.lower() and "-vl" not in model.lower()


def model_for(role: str) -> str:
    default = {"planner": PLANNER_MODEL, "editor": EDITOR_MODEL, "vision": VISION_MODEL}[role]
    return os.environ.get(f"MOTION_{role.upper()}_MODEL", default)


def load_env(start: Path | None = None) -> Path | None:
    """Load KEY=VALUE lines from the nearest .env upward from `start` (cwd by default).

    Existing environment variables win. Values are never printed.
    """
    here = (start or Path.cwd()).resolve()
    for d in (here, *here.parents):
        f = d / ".env"
        if f.is_file():
            for line in f.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.removeprefix("export ").strip()
                os.environ.setdefault(k, v.strip().strip("'\""))
            return f
    return None


@dataclass
class Call:
    purpose: str
    model: str
    seconds: float
    prompt_tokens: int = 0
    completion_tokens: int = 0
    think: bool = True

    def __str__(self) -> str:
        short = self.model.split("/")[-1] + ("" if self.think else ", no reasoning")
        return f"{self.purpose} via {short}: {self.seconds:.1f}s, {self.prompt_tokens}→{self.completion_tokens} tokens"


class ChatBackend(Protocol):
    def complete(self, model: str, messages: list[dict[str, Any]], **kw: Any) -> tuple[str, dict[str, int]]:
        """Return (content, usage)."""


class TokenFactory:
    """ChatBackend over the OpenAI SDK pointed at Token Factory."""

    def __init__(self, api_key: str | None = None, base_url: str | None = None, timeout: float = 300):
        from openai import OpenAI  # optional dependency: pip install motionlang[agent]

        load_env()
        key = api_key or os.environ.get("NEBIUS_API_KEY")
        if not key:
            raise RuntimeError("NEBIUS_API_KEY is not set (put it in .env or the environment)")
        self._client = OpenAI(api_key=key, base_url=base_url or os.environ.get("NEBIUS_BASE_URL", DEFAULT_BASE_URL), timeout=timeout, max_retries=2)

    def complete(self, model: str, messages: list[dict[str, Any]], **kw: Any) -> tuple[str, dict[str, int]]:
        r = self._client.chat.completions.create(model=model, messages=messages, **kw)
        u = r.usage
        usage = {"prompt_tokens": u.prompt_tokens, "completion_tokens": u.completion_tokens} if u else {}
        return r.choices[0].message.content or "", usage


class LLM:
    """Records a Call for every request so apps can show latency/cost per step."""

    def __init__(self, backend: ChatBackend | None = None, models: dict[str, str] | None = None):
        """models: per-instance overrides by role ({"planner": "nvidia/nemotron-3-super-120b-a12b"}), so a
        server can pick models per request without touching the process-wide MOTION_*_MODEL env vars."""
        self.backend = backend or TokenFactory()
        self.models = dict(models or {})
        self.calls: list[Call] = []

    def model_for(self, role: str) -> str:
        """The model this instance uses for a role: its override, else the env/default."""
        return getattr(self, "models", {}).get(role) or model_for(role)  # subclasses may skip __init__

    def json(
        self, role: str, purpose: str, messages: list[dict[str, Any]], *,
        think: bool = True, max_tokens: int = 16000, temperature: float = 0.3, schema: dict | None = None,
    ) -> tuple[str, Call]:
        """JSON-mode completion. think=False turns off Nemotron's reasoning phase (≈3x faster for small edits).

        schema: constrain decoding to this JSON Schema (Token Factory structured output). Works well with
        Super-120B; Ultra tends to degenerate under it, so callers opt in per model."""
        model = self.model_for(role)
        fmt = {"type": "json_schema", "json_schema": {"name": "motion_document", "schema": schema, "strict": True}} if schema else {"type": "json_object"}
        kw: dict[str, Any] = {"response_format": fmt, "max_tokens": max_tokens, "temperature": temperature}
        if not think:
            kw["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
        t = time.time()
        text, usage = self.backend.complete(model, messages, **kw)
        call = Call(purpose, model, time.time() - t, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), think)
        self.calls.append(call)
        return text, call


_THINK = re.compile(r"<think>.*?</think>", re.S)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.M)


def extract_json_text(text: str) -> str:
    """Strip reasoning tags and code fences; return the outermost {...} span."""
    t = _FENCE.sub("", _THINK.sub("", text)).strip()
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        raise ValueError("no JSON object in the model's reply")
    return t[i : j + 1]
