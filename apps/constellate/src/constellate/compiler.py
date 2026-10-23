"""Show script → MotionLang document, one actor per drone.

Transitions use CAPT (Turpin, Michael & Kumar, 2014): drones are assigned to the next formation by
minimizing the sum of squared distances (Hungarian algorithm), then all fly straight lines that start and
end together with the same easing. If start and goal points are at least √2 × the minimum separation
apart, no two drones come closer than that separation. `stagger` and `expand` break the synchronization
on purpose (for the look); the flight checker then decides whether the result is still safe.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import linear_sum_assignment
from scipy.spatial.distance import cdist

from .formations import Sampled, launch_grid, sample
from .show import ColorStep, FormStep, HoldStep, Show, SpinStep

PPM = 10.0  # canvas pixels per metre
FPS = 30
EASE = "inOutSine"  # peak speed π/2·D/T and peak acceleration π²/2·D/T²: gentler than cubic easing
EXPAND = 1.5  # `expand` spreads the old formation by this factor about its centre
STAGGER = 0.5  # `stagger` spreads departures over this fraction of the transition
DRONE_RADIUS_PX = 5
BACKGROUND = "#05060f"
LAND_ID = "land"


@dataclass
class Span:
    step: str
    kind: str
    index: int | None  # position in show.steps (None for the automatic landing)
    t0: float
    t1: float
    moving_until: float  # end of the transition/spin part; after it the formation holds
    formation: str | None = None
    style: str | None = None


@dataclass
class Compiled:
    doc: dict
    spans: list[Span]
    samples: dict[str, Sampled] = field(default_factory=dict)
    final: np.ndarray | None = None

    @property
    def duration(self) -> float:
        return self.spans[-1].t1 if self.spans else 0.0


def to_px(p: np.ndarray, show: Show) -> np.ndarray:
    return np.c_[p[:, 0] * PPM, (show.limits.height - p[:, 1]) * PPM]


def assign(cur: np.ndarray, goal: np.ndarray) -> np.ndarray:
    """goal row for each drone, minimizing the sum of squared distances."""
    _, cols = linear_sum_assignment(cdist(cur, goal, "sqeuclidean"))
    return cols


def min_transition(dist: float, limits) -> float:
    """Shortest straight inOutSine flight of `dist` metres within the speed and acceleration limits."""
    if dist <= 0:
        return 0.0
    return max(math.pi / 2 * dist / limits.max_speed, math.sqrt(math.pi**2 / 2 * dist / limits.max_accel))


class _Builder:
    def __init__(self, show: Show):
        self.show = show
        self.n = show.drones
        self.seqs: list[list[dict]] = [[] for _ in range(self.n)]
        self.t = 0.0

    def _r(self, v: float) -> float:
        return round(float(v), 4)

    def hold_all(self, bid: str, dur: float) -> None:
        if dur <= 1e-6:
            return
        for i in range(self.n):
            self.seqs[i].append({"id": f"{bid}_d{i}", "verb": "hold", "duration": self._r(dur)})

    def move(self, bid: str, i: int, a: np.ndarray, b: np.ndarray, dur: float, delay: float = 0.0, color: str | None = None,
             cur_color: str | None = None) -> None:
        """One drone flies a → b (metres) in `dur` seconds after `delay`, recolouring on the way."""
        seq = self.seqs[i]
        if delay > 1e-6:
            seq.append({"id": f"{bid}_d{i}_wait", "verb": "hold", "duration": self._r(delay)})
        parts = []
        if np.linalg.norm(b - a) > 0.01:
            pb = to_px(b[None], self.show)[0]
            parts.append({"id": f"{bid}_d{i}_mv", "verb": "move", "actor": f"d{i}", "to": [self._r(pb[0]), self._r(pb[1])],
                          "duration": self._r(dur), "ease": EASE})
        if color and color != cur_color:
            parts.append({"id": f"{bid}_d{i}_led", "verb": "recolor", "actor": f"d{i}", "prop": "fill", "to": color,
                          "duration": self._r(dur), "ease": "linear"})
        if not parts:
            seq.append({"id": f"{bid}_d{i}_still", "verb": "hold", "duration": self._r(dur)})
        elif len(parts) == 1:
            seq.append(parts[0])
        else:
            seq.append({"id": f"{bid}_d{i}", "par": parts})


def compile_show(show: Show, seed: int = 0) -> Compiled:
    lim = show.limits
    n = show.drones
    b = _Builder(show)
    samples: dict[str, Sampled] = {}
    spans: list[Span] = []
    pos = launch_grid(n, lim)
    start = pos.copy()
    colors = [show.color] * n

    def get(fid: str) -> Sampled:
        if fid not in samples:
            samples[fid] = sample(show.formations[fid], n, lim, show.sources, seed=seed)
        return samples[fid]

    def fly(sid: str, goal: np.ndarray, goal_colors: list[str], T: float, style: str) -> None:
        nonlocal pos, colors
        if style == "expand":
            c = pos.mean(axis=0)
            spread = c + (pos - c) * EXPAND
            t1 = T * 0.35
            for i in range(n):
                b.move(f"{sid}_out", i, pos[i], spread[i], t1)
            cols = assign(spread, goal)
            g = goal[cols]
            gc = [goal_colors[k] for k in cols]
            for i in range(n):
                b.move(f"{sid}_in", i, spread[i], g[i], T - t1, color=gc[i], cur_color=colors[i])
        else:
            cols = assign(pos, goal)
            g = goal[cols]
            gc = [goal_colors[k] for k in cols]
            if style == "stagger":
                order = np.argsort(np.argsort(g[:, 0] + 1e-3 * g[:, 1]))  # left-to-right ripple
                fly_t = T * (1 - STAGGER)
                for i in range(n):
                    delay = T * STAGGER * order[i] / max(n - 1, 1)
                    b.move(sid, i, pos[i], g[i], fly_t, delay=delay, color=gc[i], cur_color=colors[i])
                    rest = T - delay - fly_t
                    if rest > 1e-6:
                        b.seqs[i].append({"id": f"{sid}_d{i}_after", "verb": "hold", "duration": b._r(rest)})
            else:
                for i in range(n):
                    b.move(sid, i, pos[i], g[i], T, color=gc[i], cur_color=colors[i])
        pos, colors = g, gc

    current_formation: str | None = None
    for idx, st in enumerate(show.steps):
        t0 = b.t
        if isinstance(st, FormStep):
            s = get(st.formation)
            if st.color:
                goal_colors = [st.color] * n
            elif s.colors:
                goal_colors = s.colors
            else:
                goal_colors = None  # keep each drone's colour (assignment-independent)
            if goal_colors is None:
                cols = assign(pos, s.points)
                inv = np.empty(n, int)
                inv[cols] = np.arange(n)
                goal_colors = [colors[inv[k]] for k in range(n)]
            fly(st.id, s.points, goal_colors, st.transition, st.style)
            b.t += st.transition
            moving_until = b.t
            b.hold_all(f"{st.id}_hold", st.hold)
            b.t += st.hold
            current_formation = st.formation
            spans.append(Span(st.id, "form", idx, t0, b.t, moving_until, st.formation, st.style))
        elif isinstance(st, SpinStep):
            c = pos.mean(axis=0)
            cpx = to_px(c[None], show)[0]
            ppx = to_px(pos, show)
            ang = math.radians(360 * st.turns)
            rel = ppx - cpx
            rot = np.c_[rel[:, 0] * math.cos(ang) - rel[:, 1] * math.sin(ang), rel[:, 0] * math.sin(ang) + rel[:, 1] * math.cos(ang)]
            new_px = cpx + rot
            for i in range(n):
                if np.linalg.norm(rel[i]) < 0.3 * PPM:
                    b.seqs[i].append({"id": f"{st.id}_d{i}_still", "verb": "hold", "duration": b._r(st.duration)})
                else:
                    b.seqs[i].append({"id": f"{st.id}_d{i}", "verb": "orbit", "actor": f"d{i}",
                                      "center": [b._r(cpx[0]), b._r(cpx[1])], "turns": st.turns,
                                      "duration": b._r(st.duration), "ease": EASE})
            pos = np.c_[new_px[:, 0] / PPM, show.limits.height - new_px[:, 1] / PPM]
            b.t += st.duration
            spans.append(Span(st.id, "spin", idx, t0, b.t, b.t, current_formation))
        elif isinstance(st, ColorStep):
            for i in range(n):
                b.move(st.id, i, pos[i], pos[i], st.duration, color=st.color, cur_color=colors[i])
            colors = [st.color] * n
            b.t += st.duration
            spans.append(Span(st.id, "color", idx, t0, b.t, t0, current_formation))
        elif isinstance(st, HoldStep):
            b.hold_all(st.id, st.duration)
            b.t += st.duration
            spans.append(Span(st.id, "hold", idx, t0, b.t, t0, current_formation))
    if show.land:
        t0 = b.t
        cols = assign(pos, start)
        dist = float(np.linalg.norm(start[cols] - pos, axis=1).max())
        T = max(3.0, math.ceil(min_transition(dist, lim) * 1.25 * 2) / 2)
        fly(LAND_ID, start, colors, T, "direct")
        b.t += T
        spans.append(Span(LAND_ID, "land", None, t0, b.t, b.t))

    sp = to_px(start, show)
    actors = [{"id": f"d{i}", "label": f"drone {i}", "shape": {"kind": "ellipse", "rx": DRONE_RADIUS_PX, "ry": DRONE_RADIUS_PX},
               "style": {"fill": show.color}, "init": {"x": round(float(sp[i, 0]), 3), "y": round(float(sp[i, 1]), 3)}}
              for i in range(n)]
    timeline = {"id": "show", "par": [{"id": f"drone{i}", "seq": b.seqs[i]} for i in range(n) if b.seqs[i]]}
    doc = {
        "motion": "0.1",
        "canvas": {"width": round(lim.width * PPM), "height": round(lim.height * PPM), "background": BACKGROUND, "fps": FPS},
        "actors": actors,
        "timeline": timeline,
        "meta": {"title": show.title, "generator": "constellate", "pixels_per_metre": PPM},
    }
    return Compiled(doc, spans, samples, pos)
