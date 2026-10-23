"""Monte Carlo wind sweep → flight certificate and sim issues for the repair loop.

Runs locally in a process pool. The same `run_one` entry point is what a Nebius Serverless Job would run
per shard (see `jobs.py`); we cut that path because AI Cloud billing is separate from Token Factory credits.
"""

from __future__ import annotations

import math
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass

import numpy as np

from motionlang import Issue

from .compiler import Compiled, Span
from .flight import _span_at, tracks_m
from .show import Show
from .sim import G, Airframe, Wind, simulate

WINDS = (0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0)
SEEDS = 12


@dataclass
class Run:
    wind: float
    seed: int
    direction: float
    max_err: float
    err_drone: int
    err_t: float
    saturated: float  # fraction of drone-frames at a thrust/tilt limit
    min_sep: float
    near_pairs: int
    worst: tuple[float, int, int, float] | None  # (t, i, j, d) closest near miss
    battery_left: float  # worst drone, fraction of endurance
    ok: bool
    toward_crowd: float = 0.0  # largest drift off the show plane toward the spectators (−y), m


def _direction(seed: int) -> float:
    return float(np.random.default_rng(1000 + seed).uniform(0, 360))


def run_one(args: tuple) -> Run:
    times, plan, limits, wind, seed = args
    d = _direction(seed)
    r = simulate(times, plan, limits["max_flight_time"], Wind(speed=wind, direction_deg=d), seed=seed, alert=limits["near_miss"])
    k = int(r.err.argmax())
    f, i = divmod(k, r.err.shape[1])
    pairs = {(a, b) for _, a, b, _ in r.close}
    worst = min(r.close, key=lambda c: c[3]) if r.close else None
    left = float(1 - r.battery_used.max())
    ok = bool(r.err.max() <= limits["max_tracking_error"] and not pairs and left >= limits["battery_reserve"])
    return Run(wind, seed, d, float(r.err.max()), i, float(times[f]), float(r.saturated.mean()), float(r.min_dist.min()),
               len(pairs), worst, left, ok, float(max(0.0, -r.pos[..., 1].min())))


@dataclass
class Sweep:
    runs: list[Run]
    design_wind: float
    certified_wind: float | None  # highest tested wind with every seed passing at it and below
    crowd_setback: float = 30.0

    def by_wind(self) -> dict[float, dict]:
        out = {}
        for w in sorted({r.wind for r in self.runs}):
            rs = [r for r in self.runs if r.wind == w]
            out[w] = {"runs": len(rs), "passed": int(sum(r.ok for r in rs)), "max_err": round(max(r.max_err for r in rs), 2),
                      "min_sep": round(min(r.min_sep for r in rs), 2), "near_pairs": sum(r.near_pairs for r in rs),
                      "battery_left": round(min(r.battery_left for r in rs), 3)}
        return out

    def certificate(self) -> dict:
        ok_runs = [r for r in self.runs if r.wind <= self.design_wind]
        cw = self.certified_wind
        return {
            "flyable": cw is not None and cw >= self.design_wind,
            "certified_wind": cw, "design_wind": self.design_wind, "runs": len(self.runs),
            "near_misses_at_design_wind": sum(r.near_pairs for r in ok_runs),
            "worst_separation": round(min(r.min_sep for r in ok_runs), 2) if ok_runs else None,
            "max_tracking_error": round(max(r.max_err for r in ok_runs), 2) if ok_runs else None,
            "battery_left": round(min(r.battery_left for r in ok_runs), 3) if ok_runs else None,
            "closest_to_crowd": round(self.crowd_setback - max(r.toward_crowd for r in ok_runs), 2) if ok_runs else None,
            "note": "simulation-based planning aid, not a regulatory approval",
        }

    def to_dict(self) -> dict:
        return {"certificate": self.certificate(), "by_wind": {str(k): v for k, v in self.by_wind().items()},
                "runs": [asdict(r) for r in self.runs]}


def sweep(show: Show, comp: Compiled, baked, winds=WINDS, seeds: int = SEEDS, workers: int | None = None) -> Sweep:
    times, plan = tracks_m(baked, show)
    lim = show.limits.model_dump()
    jobs = [(times, plan, lim, w, s) for w in winds for s in range(seeds)]
    # CONSTELLATE_SWEEP_WORKERS caps the pool on small hosts, where os.cpu_count() reports the machine, not the container.
    workers = workers or int(os.environ.get("CONSTELLATE_SWEEP_WORKERS") or 0) or min(len(jobs), max(1, (os.cpu_count() or 2) - 1))
    if workers == 1:
        runs = [run_one(j) for j in jobs]
    else:
        with ProcessPoolExecutor(workers) as ex:
            runs = list(ex.map(run_one, jobs, chunksize=2))
    cert = None
    for w in sorted(winds):
        if all(r.ok for r in runs if r.wind == w):
            cert = w
        else:
            break
    return Sweep(runs, show.limits.design_wind, cert, show.limits.crowd_setback)


def _slowdown(sp: Span, times, plan, wind: float, gust: float, af: Airframe) -> float | None:
    """Factor k so that, with the step k× longer, drag at (planned speed + gusty wind) plus the planned
    acceleration fits in the tilt-limited horizontal thrust. None if even hovering can't hold the wind."""
    m = (times >= sp.t0) & (times <= sp.moving_until)
    if not m.any():
        return None
    dt = float(times[1] - times[0])
    v = np.gradient(plan, dt, axis=0)[m]
    a = np.gradient(np.gradient(plan, dt, axis=0), dt, axis=0)[m]
    vp, ap = float(np.linalg.norm(v, axis=2).max()), float(np.linalg.norm(a, axis=2).max())
    avail = G * math.tan(math.radians(af.max_tilt_deg)) * 0.8  # keep 20% for the position loop
    w = wind * (1 + 2 * gust)
    if af.drag * w >= avail:
        return None
    for k in np.arange(1.0, 4.01, 0.05):
        if af.drag * (vp / k + w) + ap / k**2 <= avail:
            return float(k)
    return None


def sim_issues(show: Show, comp: Compiled, baked, sw: Sweep) -> list[Issue]:
    """Issues for the lowest failing wind at or below the design wind, with hints for the script."""
    lim = show.limits
    fails = [r for r in sw.runs if not r.ok and r.wind <= lim.design_wind]
    if not fails:
        return []
    w = min(r.wind for r in fails)
    at = [r for r in fails if r.wind == w]
    out: list[Issue] = []
    lower = (f"lower limits.design_wind to {sw.certified_wind:g} m/s (what the show survives now)"
             if sw.certified_wind is not None else "fly only in calm air")
    times, plan = tracks_m(baked, show)
    near = [r for r in at if r.near_pairs]
    if near:
        r = min(near, key=lambda r: r.worst[3])
        t, i, j, d = r.worst
        sp = _span_at(comp.spans, t)
        fi = int(np.searchsorted(times, t))
        planned = float(np.linalg.norm(plan[fi, i] - plan[fi, j]))
        fid = sp.formation
        if fid:
            f = show.formations[fid]
            factor = (planned + (lim.near_miss - d) + 0.3) / max(planned, 1e-6)
            hint = f"set formations.{fid}.size from {f.size:g} to {min(1.0, math.ceil(f.size * factor * 100) / 100):.2f} (more room for gusts), or {lower}"
        else:
            hint = lower
        out.append(Issue("E_SIM_NEAR_MISS", f"in {w:g} m/s wind (gusts from {r.direction:.0f}°, seed {r.seed}) d{i} and d{j} come within "
                         f"{d:.2f} m at t={t:.2f}s during {sp.step!r}; planned {planned:.2f} m apart ({sum(x.near_pairs for x in at)} near-miss pairs in "
                         f"{len(at)} of {sum(1 for x in sw.runs if x.wind == w)} runs)", hint=hint, block=sp.step, actor=f"d{i}", t=t,
                         pointer=f"/steps/{sp.index}" if sp.index is not None else None))
    drift = [r for r in at if r.max_err > lim.max_tracking_error]
    if drift:
        r = max(drift, key=lambda r: r.max_err)
        sp = _span_at(comp.spans, r.err_t)
        k = _slowdown(sp, times, plan, w, Wind().gust, Airframe()) if sp.kind in ("form", "spin") else None
        if k and k > 1.0 and sp.index is not None:
            field = "transition" if sp.kind == "form" else "duration"
            T = sp.moving_until - sp.t0
            hint = f"set steps[{sp.index}].{field} ({sp.step}) from {T:g} to {math.ceil(T * k * 2) / 2:g} s (less speed into the wind), or {lower}"
        else:
            hint = lower
        out.append(Issue("E_SIM_TRACKING", f"in {w:g} m/s wind (gusts from {r.direction:.0f}°, seed {r.seed}) d{r.err_drone} drifts "
                         f"{r.max_err:.2f} m from its path at t={r.err_t:.2f}s during {sp.step!r} (limit {lim.max_tracking_error:g} m; "
                         f"thrust at its limit {r.saturated:.1%} of the time)", hint=hint, block=sp.step, actor=f"d{r.err_drone}",
                         t=r.err_t, pointer=f"/steps/{sp.index}" if sp.index is not None else None))
    low = [r for r in at if r.battery_left < lim.battery_reserve]
    if low:
        r = min(low, key=lambda r: r.battery_left)
        short = (lim.battery_reserve - r.battery_left) * lim.max_flight_time * 1.1
        out.append(Issue("E_SIM_BATTERY", f"in {w:g} m/s wind a drone lands with {r.battery_left:.0%} battery; the reserve is "
                         f"{lim.battery_reserve:.0%}", hint=f"shorten holds or steps by {math.ceil(short)} s in total, or {lower}"))
    return out
