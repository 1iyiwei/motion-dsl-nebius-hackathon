"""Verb plugin interface and registry.

A verb turns parameters + progress into property values. The core never special-cases
verbs: timing, composition, continuity checks, JSON Schema and the LLM prompt reference
are all derived from what each Verb declares here.
"""

from __future__ import annotations

import inspect
from importlib.metadata import entry_points
from typing import Any, ClassVar, Literal, Protocol

from pydantic import BaseModel, ConfigDict

from ..model import Actor, Canvas
from ..paths import Polyline

Mode = Literal["abs", "rel"]


class VerbParams(BaseModel):
    """Base for verb parameter models. Unknown fields are errors so LLM typos surface."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class SampleContext(Protocol):
    """What a verb can see while sampling."""

    actor: str
    t0: float  # absolute start time of this block (s)
    duration: float  # resolved duration (s)
    start: dict[str, Any]  # composed value of each written prop just before this block starts
    canvas: Canvas

    def value(self, actor: str, prop: str, t: float) -> Any:
        """Composed value of another actor's prop at absolute time t (for follow/orbit-around)."""

    def path(self, spec: dict) -> Polyline:
        """Resolve a path spec against the document's strokes (cached)."""


class Verb:
    """Subclass, set the ClassVars, implement `sample`, and decorate with @register.

    sample(p, u, t, ctx) returns {prop: value}:
      - mode "abs": absolute values. At u=0 they should equal ctx.start unless the verb
        declares an intentional jump; the validator reports E_DISCONTINUITY otherwise.
      - mode "rel": deltas added on top of the absolute layer. Must be 0 at u=0.
    u is progress in [0, 1], already eased when `uses_ease` is True. t is seconds since start.
    """

    name: ClassVar[str]
    Params: ClassVar[type[VerbParams]]
    since: ClassVar[str] = "0.1"
    uses_ease: ClassVar[bool] = True
    default_ease: ClassVar[str] = "inOutQuad"
    fixed_actor: ClassVar[str | None] = None  # e.g. "camera"; the block then omits `actor`
    needs_actor: ClassVar[bool] = True
    # Example blocks (without id/actor) used by docs, prompts and the contract test suite.
    examples: ClassVar[list[dict[str, Any]]] = []

    def mode(self, p: Any) -> Mode | None:
        raise NotImplementedError

    def props(self, p: Any) -> tuple[str, ...]:
        raise NotImplementedError

    def default_duration(self, p: Any) -> float:
        return 1.0

    def sample(self, p: Any, u: float, t: float, ctx: SampleContext) -> dict[str, Any]:
        raise NotImplementedError

    def jumps_at_start(self, p: Any) -> bool:
        """True if starting away from the current value is the point (e.g. draw_on from 0)."""
        return False

    def actor_refs(self, p: Any) -> list[str]:
        """Other actors this verb reads (validated as references; used for dependency checks)."""
        return []

    def path_specs(self, p: Any) -> list[dict]:
        return []

    def check_target(self, p: Any, actor: Actor | None) -> str | None:
        """Return an error message if this verb can't apply to the target actor."""
        return None

    @classmethod
    def summary(cls) -> str:
        return inspect.cleandoc(cls.__doc__ or "").split("\n\n")[0].replace("\n", " ")


REGISTRY: dict[str, Verb] = {}
_plugins_loaded = False


def register(cls: type[Verb]) -> type[Verb]:
    name = cls.name
    if name in REGISTRY and type(REGISTRY[name]) is not cls:
        raise ValueError(f"verb {name!r} is already registered by {type(REGISTRY[name]).__module__}")
    if "." not in name and not cls.__module__.startswith("motionlang."):
        raise ValueError(f"third-party verb {name!r} must be namespaced, e.g. 'acme.{name}'")
    REGISTRY[name] = cls()
    return cls


def load_plugins() -> dict[str, Verb]:
    """Import core verbs and every module exposed under the `motionlang.verbs` entry point."""
    global _plugins_loaded
    if not _plugins_loaded:
        from . import core  # noqa: F401  (registers on import)

        for ep in entry_points(group="motionlang.verbs"):
            ep.load()
        _plugins_loaded = True
    return REGISTRY


def get_verb(name: str) -> Verb | None:
    return load_plugins().get(name)
