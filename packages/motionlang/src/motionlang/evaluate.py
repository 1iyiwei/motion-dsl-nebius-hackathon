"""Lazy evaluation of any (actor, prop, t), implementing the composition rule.

For a property at time t:
  * the governing absolute leaf A is the latest abs leaf on that prop that has started;
    A interpolates from the composed value just before it started (its `from` state);
  * every relative leaf r adds δr(t) − δr(A.start): offsets already absorbed into A's
    `from` are not counted twice. With no governing A, the base is the actor's init value.
δr(τ) is 0 before r starts, r's sample during r, and r's final sample after it ends.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from .model import CAMERA_ID, COLOR_PROPS, Canvas
from .parse import Parsed
from .paths import Polyline, resolve_path
from .timing import Leaf, Schedule

DEFAULT_INK = "#333333"
CLOSED_KINDS = ("rect", "ellipse", "polygon", "text")


class DependencyCycle(Exception):
    pass


def initial_values(parsed: Parsed) -> dict[str, dict[str, Any]]:
    doc = parsed.doc
    cw, ch = doc.canvas.width, doc.canvas.height
    out: dict[str, dict[str, Any]] = {CAMERA_ID: {"x": cw / 2, "y": ch / 2, "zoom": 1.0, "rotation": 0.0}}
    for a in doc.actors:
        g = parsed.geometry[a.id]
        px, py = a.pivot
        if g.drawn:
            dx, dy = g.origin[0] + px * g.w, g.origin[1] + py * g.h
        else:
            dx, dy = cw / 2, ch / 2
        kind = a.shape.kind
        closed = kind in CLOSED_KINDS or (kind == "path" and a.shape.closed)
        fill = a.style.fill if a.style.fill is not None else (DEFAULT_INK if closed else None)
        stroke = a.style.stroke if a.style.stroke is not None else (DEFAULT_INK if kind in ("stroke", "path") and not closed else None)
        i = a.init
        out[a.id] = {
            "x": i.x if i.x is not None else dx,
            "y": i.y if i.y is not None else dy,
            "rotation": i.rotation, "sx": i.sx, "sy": i.sy, "opacity": i.opacity, "trim": i.trim,
            "fill": fill, "stroke": stroke,
        }
    return out


@dataclass
class _Ctx:
    actor: str
    t0: float
    duration: float
    start: dict[str, Any]
    canvas: Canvas
    ev: "Evaluator"

    def value(self, actor: str, prop: str, t: float) -> Any:
        return self.ev.value(actor, prop, t)

    def path(self, spec: dict) -> Polyline:
        return self.ev.path(spec)


class Evaluator:
    def __init__(self, parsed: Parsed, sched: Schedule):
        self.parsed = parsed
        self.sched = sched
        self.init = initial_values(parsed)
        self.abs: dict[tuple[str, str], list[Leaf]] = defaultdict(list)
        self.rel: dict[tuple[str, str], list[Leaf]] = defaultdict(list)
        for leaf in sched.leaves:
            b = leaf.block
            mode = b.verb.mode(b.params)
            if mode is None or b.actor is None:
                continue
            for prop in b.verb.props(b.params):
                (self.abs if mode == "abs" else self.rel)[(b.actor, prop)].append(leaf)
        for lst in self.abs.values():
            lst.sort(key=lambda lf: lf.key)
        self._strokes = {k: s.points for k, s in parsed.doc.strokes.items()}
        self._paths: dict[str, Polyline] = {}
        self._vals: dict[tuple, Any] = {}
        self._from: dict[int, dict[str, Any]] = {}
        self._samples: dict[tuple, dict[str, Any]] = {}
        self._active: set[tuple] = set()

    # ---- public ----

    def value(self, actor: str, prop: str, t: float, before: tuple[float, int] | None = None) -> Any:
        """Composed value at time t. `before` limits abs leaves to those ordered before a leaf."""
        key = (actor, prop, round(t, 9), before)
        if key in self._vals:
            return self._vals[key]
        if key in self._active:
            raise DependencyCycle(f"{actor}.{prop} depends on itself at t={t:.3f}s (circular follow/orbit?)")
        self._active.add(key)
        try:
            A = None
            for lf in self.abs.get((actor, prop), ()):
                if lf.start <= t + 1e-12 and (before is None or lf.key < before):
                    A = lf
                else:
                    break
            if A is not None:
                base = self.sample(A, t)[prop]
                ref = A.start
            else:
                base = self.init[actor][prop]
                ref = None
            if prop not in COLOR_PROPS:
                for r in self.rel.get((actor, prop), ()):
                    d = self.delta(r, prop, t) - (self.delta(r, prop, ref) if ref is not None else 0.0)
                    base = base + d
            self._vals[key] = base
            return base
        finally:
            self._active.discard(key)

    def from_state(self, leaf: Leaf) -> dict[str, Any]:
        """Composed values of the leaf's props just before it starts."""
        k = id(leaf)
        if k not in self._from:
            b = leaf.block
            self._from[k] = {p: self.value(b.actor, p, leaf.start, before=leaf.key) for p in b.verb.props(b.params)}  # type: ignore[arg-type]
        return self._from[k]

    def progress(self, leaf: Leaf, t: float) -> tuple[float, float]:
        """(u, local_t) with u eased when the verb uses easing."""
        d = leaf.duration
        tl = min(max(t - leaf.start, 0.0), d)
        u = 1.0 if d <= 0 else tl / d
        if leaf.block.verb.uses_ease:
            u = leaf.block.ease(u)
        return u, tl

    def sample(self, leaf: Leaf, t: float) -> dict[str, Any]:
        tl_key = round(min(max(t - leaf.start, 0.0), leaf.duration), 9)
        k = (id(leaf), tl_key)
        if k not in self._samples:
            u, tl = self.progress(leaf, t)
            self._samples[k] = self.sample_at(leaf, u, tl)
        return self._samples[k]

    def sample_at(self, leaf: Leaf, u: float, tl: float) -> dict[str, Any]:
        b = leaf.block
        ctx = _Ctx(b.actor or "", leaf.start, leaf.duration, self.from_state(leaf), self.parsed.doc.canvas, self)  # type: ignore[arg-type]
        return b.verb.sample(b.params, u, tl, ctx)

    def delta(self, leaf: Leaf, prop: str, t: float) -> float:
        if t <= leaf.start:
            return 0.0
        return float(self.sample(leaf, t).get(prop, 0.0))

    def path(self, spec: dict) -> Polyline:
        key = repr(sorted(spec.items()))
        if key not in self._paths:
            self._paths[key] = resolve_path(spec, self._strokes)
        return self._paths[key]

    def actor_props(self, actor: str, t: float) -> dict[str, Any]:
        return {p: self.value(actor, p, t) for p in self.init[actor]}
