"""Flight checker: is the planned show flyable? Runs on MotionLang's baked tracks, in metres.

Issues use MotionLang's `Issue` shape, but `block` names a show-script step and `pointer` points into the
show script, because repairs edit the script (a handful of numbers), not the per-drone document. Hints
carry computed numbers: required acceleration scales with 1/T², so the duration that fits is T·√(a/a_max).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.spatial import cKDTree

from motionlang import Baked, Issue

from .compiler import PPM, Compiled, Span
from .show import Show

SAFETY = 1.05  # hints aim a little past the limit, so one repair round is enough


@dataclass
class FlightReport:
    issues: list[Issue] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "issues": [i.to_dict() for i in self.issues], "stats": self.stats}


def tracks_m(baked: Baked, show: Show) -> tuple[np.ndarray, np.ndarray]:
    """(times (F,), positions (F, N, 2) in metres, z up)."""
    p = np.stack([baked.pos[f"d{i}"] for i in range(show.drones)], axis=1) / PPM
    p[..., 1] = show.limits.height - p[..., 1]
    return baked.times, p


def _ptr(span: Span) -> str | None:
    return f"/steps/{span.index}" if span.index is not None else None


def _dur_field(span: Span) -> str:
    return "transition" if span.kind == "form" else "duration"


def _span_at(spans: list[Span], t: float) -> Span:
    for s in spans:
        if s.t0 - 1e-9 <= t < s.t1 - 1e-9:
            return s
    return spans[-1]


def _nice(v: float) -> float:
    return math.ceil(v * 2) / 2


def check_flight(show: Show, comp: Compiled, baked: Baked) -> FlightReport:
    lim = show.limits
    times, P = tracks_m(baked, show)
    dt = float(times[1] - times[0]) if len(times) > 1 else 1.0
    V = np.gradient(P, dt, axis=0)
    A = np.gradient(V, dt, axis=0)
    speed = np.linalg.norm(V, axis=2)
    accel = np.linalg.norm(A, axis=2)
    rep = FlightReport()
    out = rep.issues
    spans = comp.spans

    # Formation density: CAPT is only guaranteed when targets are √2 × min separation apart.
    need = math.sqrt(2) * lim.min_separation
    for fid, s in comp.samples.items():
        if s.min_spacing >= need:
            continue
        f = show.formations[fid]
        size = f.size * need / s.min_spacing * SAFETY
        fewer = int(show.drones * (s.min_spacing / need) ** (2 if f.fill == "fill" else 1))
        sev = "error" if s.min_spacing < lim.min_separation else "warning"
        # The largest size that still fits inside the airspace margins, so this hint can't fight E_GEOFENCE.
        span = np.ptp(s.points, axis=0)
        cx, cz = f.center[0] * lim.width, f.center[1] * lim.height
        room = 2 * np.array([min(cx - lim.margin, lim.width - lim.margin - cx), min(cz - lim.margin, lim.height - lim.margin - cz)])
        fits = f.size * float(np.min(np.where(span > 1e-6, room / np.maximum(span, 1e-6), np.inf)))
        cap = math.floor(min(1.0, fits) * 100) / 100
        if size <= cap or size / SAFETY <= cap:
            fix = f"set formations.{fid}.size to {min(size, cap):.2f} (spacing grows with size)"
        elif f.kind == "preset" and f.shape == "ring":
            fix = f"set formations.{fid}.shape to `circle` and fill to `fill` (a ring this size can't space {show.drones} drones)"
        elif f.fill != "fill":
            fix = f"set formations.{fid}.fill to `fill` (drones spread over the shape, or a band along a drawn line, instead of one outline)"
        else:
            fix = f"the airspace can't hold {show.drones} drones in this shape; ask the user for about {fewer} drones or another shape"
        out.append(Issue("E_DENSITY", f"formation {fid!r}: closest drones are {s.min_spacing:.2f} m apart; "
                         f"{'minimum separation' if sev == 'error' else 'collision-free transitions need'} "
                         f"{lim.min_separation if sev == 'error' else need:.2f} m",
                         hint=fix, severity=sev,
                         pointer=f"/formations/{fid}"))

    # Speed and acceleration, per moving step: one issue each, with the duration that fits.
    for sp in spans:
        m = (times >= sp.t0 - 1e-9) & (times <= sp.moving_until + 1e-9)
        if sp.moving_until - sp.t0 < 1e-6 or not m.any():
            continue
        T = sp.moving_until - sp.t0
        idx = np.nonzero(m)[0]
        checks = (("E_SPEED", speed, lim.max_speed, "m/s", 1.0), ("E_ACCEL", accel, lim.max_accel, "m/s²", 0.5))
        # One duration that satisfies both limits, so a single repair fixes the step.
        new_t = _nice(max(T * max(float(arr[idx].max()) / cap, 1.0) ** power for _, arr, cap, _, power in checks) * SAFETY)
        for code, arr, cap, unit, power in checks:
            sub = arr[idx]
            peak = float(sub.max())
            if peak <= cap:
                continue
            f, d = np.unravel_index(int(sub.argmax()), sub.shape)
            if sp.kind == "land":
                hint = "the landing is automatic; this is a compiler bug"
            else:
                hint = f"set steps[{sp.index}].{_dur_field(sp)} ({sp.step}) from {T:g} to {new_t:g} s"
                if sp.style == "stagger":
                    hint += ", or use style `direct` (stagger leaves each drone half the time)"
            out.append(Issue(code, f"d{d} reaches {peak:.1f} {unit} during {sp.step!r}; limit {cap:g} {unit}",
                             hint=hint, block=sp.step, actor=f"d{d}", t=float(times[idx[f]]), pointer=_ptr(sp)))

    # Separation while moving (holds are covered by E_DENSITY). Spatial index per frame: only nearby pairs.
    worst: dict[str, tuple[float, int, int, float]] = {}
    pairs: dict[str, set] = {}
    for k in range(len(times)):
        sp = _span_at(spans, float(times[k]))
        if times[k] > sp.moving_until + 1e-9:
            continue
        close = cKDTree(P[k]).query_pairs(lim.min_separation, output_type="ndarray")
        if not len(close):
            continue
        d = np.linalg.norm(P[k, close[:, 0]] - P[k, close[:, 1]], axis=1)
        # A pair that is just as close where it starts or ends is a formation-density problem (E_DENSITY).
        k0, k1 = int(np.searchsorted(times, sp.t0 - 1e-9)), min(int(np.searchsorted(times, sp.moving_until - 1e-9)), len(times) - 1)
        rest = np.minimum(np.linalg.norm(P[k0, close[:, 0]] - P[k0, close[:, 1]], axis=1),
                          np.linalg.norm(P[k1, close[:, 0]] - P[k1, close[:, 1]], axis=1))
        keep = d < rest - 0.05
        close, d = close[keep], d[keep]
        if not len(close):
            continue
        j = int(d.argmin())
        pairs.setdefault(sp.step, set()).update(map(tuple, close.tolist()))
        if sp.step not in worst or d[j] < worst[sp.step][0]:
            worst[sp.step] = (float(d[j]), int(close[j, 0]), int(close[j, 1]), float(times[k]))
    for sp in spans:
        if sp.step not in worst:
            continue
        d, a, b, t = worst[sp.step]
        npairs = len(pairs[sp.step])
        if sp.kind == "form" and sp.style == "stagger":
            hint = f"set steps[{sp.index}].style to `direct` (all drones arrive together, collision-free) or `expand`"
        elif sp.kind == "form" and sp.style == "expand":
            hint = f"set steps[{sp.index}].style to `direct`"
        elif sp.kind == "form":
            hint = f"enlarge formation {sp.formation!r} (see E_DENSITY) or set steps[{sp.index}].style to `expand`"
        elif sp.kind == "spin":
            hint = f"enlarge formation {sp.formation!r}: a spin keeps distances, so this pair is already too close"
        else:
            hint = "the landing is automatic; enlarge the last formation"
        out.append(Issue("E_SEPARATION", f"d{a} and d{b} come within {d:.2f} m at t={t:.2f}s during {sp.step!r} "
                         f"({npairs} pair{'s' if npairs != 1 else ''} closer than {lim.min_separation:g} m)",
                         hint=hint, block=sp.step, actor=f"d{a}", t=t, pointer=_ptr(sp)))

    # Geofence: stay inside the airspace minus the margin.
    lo = np.array([lim.margin, lim.margin - 1e-6])
    hi = np.array([lim.width - lim.margin, lim.height - lim.margin])
    for sp in spans:
        last = sp is spans[-1]
        m = (times >= sp.t0 - 1e-9) & ((times < sp.t1 - 1e-9) | last)
        sub = P[m]
        over = np.maximum(lo - sub, 0) + np.maximum(sub - hi, 0)
        if not over.any() or (sp.kind == "land" and any(i.code == "E_GEOFENCE" for i in out)):
            continue  # a landing that starts outside is the previous step's fault, already reported
        mag = over.max(axis=2)
        f, d = np.unravel_index(int(mag.argmax()), mag.shape)
        x, z = sub[f, d]
        side = "left" if x < lo[0] else "right" if x > hi[0] else "bottom" if z < lo[1] else "top"
        fid = sp.formation
        if fid and sp.kind in ("form", "spin", "hold", "color"):
            f_ = show.formations[fid]
            c = np.array([f_.center[0] * lim.width, f_.center[1] * lim.height])
            ext = np.abs(sub - c).max(axis=(0, 1))
            room = np.minimum(hi - c, c - lo)
            factor = float(np.min(np.where(ext > 1e-9, room / np.maximum(ext, 1e-9), np.inf)))
            hint = f"set formations.{fid}.size from {f_.size:g} to {math.floor(f_.size * factor / SAFETY * 100) / 100:.2f}"
            if sp.kind == "spin":
                hint += " (a spin sweeps the formation's widest radius in every direction)"
            elif sp.style == "expand":
                hint += f", or set steps[{sp.index}].style to `direct` (expand spreads the previous formation)"
            if not np.all(room > 0):
                hint = f"move formations.{fid}.center inside the airspace"
        else:
            hint = "keep formations away from the edges"
        out.append(Issue("E_GEOFENCE", f"d{d} is {float(mag[f, d]):.1f} m past the {side} edge of the airspace during {sp.step!r} "
                         f"(margin {lim.margin:g} m)", hint=hint, block=sp.step, actor=f"d{d}",
                         t=float(times[m][f]), pointer=_ptr(sp)))

    # Battery: total flight time against endurance minus reserve.
    if comp.duration > lim.show_time_budget:
        cut = comp.duration - lim.show_time_budget
        out.append(Issue("E_FLIGHT_TIME", f"the show flies {comp.duration:.0f} s; batteries allow {lim.show_time_budget:.0f} s "
                         f"with a {lim.battery_reserve:.0%} reserve", hint=f"shorten holds or steps by {math.ceil(cut)} s in total"))

    minsep = float("inf")
    for k in range(0, len(times), 3):
        dd = cKDTree(P[k]).query(P[k], k=2)[0][:, 1]
        minsep = min(minsep, float(dd.min()))
    rep.stats = {"duration": round(comp.duration, 2), "drones": show.drones, "peak_speed": round(float(speed.max()), 2),
                 "peak_accel": round(float(accel.max()), 2), "min_separation": round(minsep, 2),
                 "battery_used": round(comp.duration / lim.max_flight_time, 3)}
    return rep
