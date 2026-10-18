"""Sample a parsed document into per-frame tracks: the single source of truth for players,
exporters and constraint checks."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .evaluate import Evaluator
from .geometry import IDENTITY, Mat, apply, camera_matrix, local_matrix, mul
from .model import CAMERA_ID, CLIP_PROPS, COLOR_PROPS
from .parse import Parsed
from .timing import Schedule, schedule


@dataclass
class Baked:
    parsed: Parsed
    sched: Schedule
    ev: Evaluator
    times: np.ndarray
    props: dict[str, dict[str, list[Any]]]  # actor -> prop -> per-frame values (camera included)
    world: dict[str, list[Mat]]  # bbox-local -> world
    screen: dict[str, list[Mat]]  # bbox-local -> screen (camera applied)
    opacity: dict[str, np.ndarray]  # effective opacity (parents multiplied in)
    pos: dict[str, np.ndarray]  # (N, 2) pivot position in world space
    aabb_world: dict[str, np.ndarray] = field(default_factory=dict)  # (N, 4) x0 y0 x1 y1; absent for groups
    aabb_screen: dict[str, np.ndarray] = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return self.sched.duration

    @property
    def fps(self) -> int:
        return self.parsed.doc.canvas.fps

    def frame_at(self, t: float) -> int:
        return int(min(max(round(t * self.fps), 0), len(self.times) - 1))

    def to_json(self, precision: int = 3) -> dict[str, Any]:
        """Compact player format. Constant tracks collapse to a single value."""

        def pack(vals: list[Any]) -> Any:
            if vals and all(v == vals[0] for v in vals):
                vals = [vals[0]]
            if vals and isinstance(vals[0], float):
                vals = [round(v, precision) for v in vals]
            elif vals and isinstance(vals[0], tuple):
                vals = [[round(x, precision) for x in v] for v in vals]
            return vals[0] if len(vals) == 1 else vals

        doc = self.parsed.doc
        order = sorted(range(len(doc.actors)), key=lambda i: (doc.actors[i].z, i))
        actors = []
        for i in order:
            a = doc.actors[i]
            g = self.parsed.geometry[a.id]
            p = self.props[a.id]
            actors.append({
                "id": a.id,
                "geom": {**g.svg, "bw": round(g.w, precision), "bh": round(g.h, precision)},
                "style": {"stroke_width": a.style.stroke_width},
                "tracks": {
                    "m": pack(self.screen[a.id]),
                    "opacity": pack([float(v) for v in self.opacity[a.id]]),
                    "fill": pack(p["fill"]),
                    "stroke": pack(p["stroke"]),
                    "trim": pack([float(v) for v in p["trim"]]),
                    "clip": pack([tuple(float(p[c][k]) for c in CLIP_PROPS) for k in range(len(self.times))]),
                },
            })
        return {
            "motion": doc.motion,
            "fps": self.fps,
            "duration": round(self.duration, 6),
            "frames": len(self.times),
            "loop": self.sched.loops,
            "canvas": {"width": doc.canvas.width, "height": doc.canvas.height, "background": doc.canvas.background},
            "actors": actors,
            "tweens": [
                {
                    "block": lf.block.id, "verb": lf.block.verb.name, "actor": lf.block.actor,
                    "start": round(lf.start, 6), "end": round(lf.end, 6),
                    "props": list(lf.block.verb.props(lf.block.params)),
                }
                for lf in self.sched.leaves
                if lf.block.actor is not None
            ],
        }


def frame_times(duration: float, fps: int) -> np.ndarray:
    n = max(1, math.ceil(duration * fps - 1e-9) + 1)
    return np.minimum(np.arange(n) / fps, duration)


def bake(parsed: Parsed, sched: Schedule | None = None) -> Baked:
    """Raises evaluate.DependencyCycle for circular follow/orbit dependencies."""
    sched = sched or schedule(parsed.root)
    ev = Evaluator(parsed, sched)
    doc = parsed.doc
    times = frame_times(sched.duration, doc.canvas.fps)
    ids = [CAMERA_ID] + [a.id for a in doc.actors]
    props = {aid: {p: [ev.value(aid, p, float(t)) for t in times] for p in ev.init[aid]} for aid in ids}
    for aid in ids:
        for p, vals in props[aid].items():
            if p not in COLOR_PROPS:
                props[aid][p] = [float(v) for v in vals]

    cam = props[CAMERA_ID]
    cams = [
        camera_matrix(doc.canvas.width, doc.canvas.height, cam["x"][i], cam["y"][i], cam["zoom"][i], cam["rotation"][i])
        for i in range(len(times))
    ]

    world: dict[str, list[Mat]] = {}
    opacity: dict[str, np.ndarray] = {}

    def resolve(aid: str) -> None:
        if aid in world:
            return
        a = parsed.actors[aid]
        g = parsed.geometry[aid]
        p = props[aid]
        px, py = a.pivot[0] * g.w, a.pivot[1] * g.h
        locs = [local_matrix(p["x"][i], p["y"][i], p["rotation"][i], p["sx"][i], p["sy"][i], px, py) for i in range(len(times))]
        op = np.asarray(p["opacity"], dtype=float)
        if a.parent:
            resolve(a.parent)
            world[aid] = [mul(pm, lm) for pm, lm in zip(world[a.parent], locs)]
            opacity[aid] = op * opacity[a.parent]
        else:
            world[aid] = locs
            opacity[aid] = op

    for a in doc.actors:
        resolve(a.id)

    screen = {aid: [mul(c, m) for c, m in zip(cams, ms)] for aid, ms in world.items()}
    baked = Baked(parsed, sched, ev, times, props, world, screen, opacity, {})
    for a in doc.actors:
        g = parsed.geometry[a.id]
        pv = np.array([[a.pivot[0] * g.w, a.pivot[1] * g.h]])
        baked.pos[a.id] = np.array([apply(m, pv)[0] for m in world[a.id]])
        if len(g.outline):
            for store, mats in ((baked.aabb_world, world[a.id]), (baked.aabb_screen, screen[a.id])):
                boxes = np.empty((len(times), 4))
                for i, m in enumerate(mats):
                    q = apply(m, g.outline)
                    boxes[i] = (q[:, 0].min(), q[:, 1].min(), q[:, 0].max(), q[:, 1].max())
                store[a.id] = boxes
    return baked


__all__ = ["Baked", "bake", "frame_times", "IDENTITY"]
