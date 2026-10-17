"""Tier-1 validation: is this document well-formed and physically sane?"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from .bake import Baked, bake
from .evaluate import DependencyCycle
from .issues import Issue, Report
from .model import COLOR_PROPS, EndsAt, InBounds, MaxSpeed, NoOverlap, Smooth, TotalDuration
from .parse import Parsed, parse
from .timing import Leaf, Schedule, schedule

MAX_BAKE_SECONDS = 300.0
JUMP_TOL = {"x": 1.0, "y": 1.0, "rotation": 1.0}  # px / degrees; everything else uses 0.01
VISIBLE = 0.01


@dataclass
class Result:
    report: Report
    parsed: Parsed | None = None
    baked: Baked | None = None

    @property
    def ok(self) -> bool:
        return self.report.ok


def _leaf_desc(lf: Leaf) -> str:
    return f"{lf.block.id} ({lf.block.verb.name})"


def _active(sched: Schedule, actor: str, t: float) -> list[Leaf]:
    """Leaves on the actor active at t, else the most recently finished one."""
    mine = [lf for lf in sched.leaves if lf.block.actor == actor]
    now = [lf for lf in mine if lf.start - 1e-9 <= t <= lf.end + 1e-9]
    if now:
        return now
    done = [lf for lf in mine if lf.end <= t]
    return [max(done, key=lambda lf: lf.end)] if done else []


def _blame(sched: Schedule, actor: str, t: float) -> tuple[str | None, str]:
    leaves = _active(sched, actor, t)
    if not leaves:
        return None, f"{actor}'s initial position"
    return leaves[0].block.id, ", ".join(_leaf_desc(lf) for lf in leaves)


def check_conflicts(sched: Schedule) -> list[Issue]:
    by_prop: dict[tuple[str, str], list[Leaf]] = {}
    for lf in sched.leaves:
        b = lf.block
        if b.actor is None or b.verb.mode(b.params) != "abs":
            continue
        for p in b.verb.props(b.params):
            by_prop.setdefault((b.actor, p), []).append(lf)
    pairs: dict[tuple[int, int], tuple[Leaf, Leaf, list[str]]] = {}
    for (actor, prop), lst in by_prop.items():
        lst.sort(key=lambda lf: lf.key)
        for i, a in enumerate(lst):
            for b in lst[i + 1 :]:
                if b.start >= a.end - 1e-9:
                    break
                pairs.setdefault((id(a), id(b)), (a, b, []))[2].append(prop)
    out = []
    for a, b, props in pairs.values():
        actor = b.block.actor
        out.append(Issue(
            "E_CONFLICT",
            f"{_leaf_desc(a)} and {_leaf_desc(b)} both set {actor}.{'/'.join(props)} absolutely during "
            f"{max(a.start, b.start):.2f}–{min(a.end, b.end):.2f}s",
            hint="put them in sequence (seq), or make one relative (`by`, `relative: true`, or a procedural verb like wiggle/bounce)",
            block=b.block.id, actor=actor, t=b.start, pointer=b.block.pointer,
        ))
    return out


def check_discontinuities(baked: Baked) -> list[Issue]:
    ev, out = baked.ev, []
    for lf in baked.sched.leaves:
        b = lf.block
        mode = b.verb.mode(b.params)
        if mode is None or b.actor is None or (mode == "abs" and b.verb.jumps_at_start(b.params)):
            continue
        at0 = ev.sample_at(lf, b.ease(0.0) if b.verb.uses_ease else 0.0, 0.0)
        start = ev.from_state(lf)
        for prop, v in at0.items():
            if prop in COLOR_PROPS:
                continue
            ref = start[prop] if mode == "abs" else 0.0
            gap = abs(float(v) - float(ref))
            if gap > JUMP_TOL.get(prop, 0.01):
                if mode == "abs":
                    msg = f"{b.verb.name} makes {b.actor}.{prop} jump from {ref:.2f} to {float(v):.2f} when it starts"
                    hint = "start the verb where the actor already is"
                    if b.verb.name == "move_along":
                        hint = "add a move to the path start first, or set `relative: true` to carry the path to the actor"
                else:
                    msg = f"{b.verb.name} (relative) is not zero at its start ({prop} = {float(v):.3f}); this is a verb implementation bug"
                    hint = "relative verbs must return 0 at u=0"
                out.append(Issue("E_DISCONTINUITY", msg, hint=hint, block=b.id, actor=b.actor, t=lf.start, pointer=b.pointer))
    return out


def _sel(sel: Any, baked: Baked) -> list[str]:
    return list(baked.aabb_world) if sel == "*" else [a for a in sel if a in baked.aabb_world]


def check_constraints(baked: Baked) -> list[Issue]:
    doc, out = baked.parsed.doc, []
    W, H = doc.canvas.width, doc.canvas.height
    times, sched = baked.times, baked.sched
    known = set(baked.parsed.actors)
    for ci, c in enumerate(doc.constraints):
        ptr = f"/constraints/{ci}"
        refs = [r for r in (getattr(c, "actor", None), getattr(c, "a", None), getattr(c, "b", None)) if r]
        if isinstance(c, InBounds) and c.actors != "*":
            refs += c.actors
        missing = [r for r in refs if r not in known]
        if missing:
            out.append(Issue("E_REF", f"constraint {c.type} references unknown actor(s) {missing}", pointer=ptr))
            continue

        if isinstance(c, InBounds):
            unknown = [b for b in c.ignore if b not in baked.parsed.blocks]
            if unknown:
                out.append(Issue("E_REF", f"in_bounds.ignore names unknown block(s) {unknown}", pointer=f"{ptr}/ignore"))
            exempt = [lf for lf in sched.leaves if lf.block.id in c.ignore]
            for aid in _sel(c.actors, baked):
                # Stage (world) space: camera moves are deliberate framing and never count.
                box, vis = baked.aabb_world[aid], baked.opacity[aid] > VISIBLE
                for lf in exempt:
                    if lf.block.actor == aid:
                        vis = vis & ~((times >= lf.start - 1e-9) & (times <= lf.end + 1e-9))
                m = c.margin
                over = np.stack([m - box[:, 0], m - box[:, 1], box[:, 2] - (W - m), box[:, 3] - (H - m)], axis=1)
                over[~vis] = -np.inf
                worst = over.max(axis=1)
                if (worst > 0.5).any():
                    i = int(np.argmax(worst))
                    side = ("left", "top", "right", "bottom")[int(np.argmax(over[i]))]
                    bid, who = _blame(sched, aid, float(times[i]))
                    out.append(Issue(
                        "E_BOUNDS", f"{aid} goes {worst[i]:.0f}px past the {side} edge (margin {m:g}) at t={times[i]:.2f}s",
                        hint=f"active: {who}; move that motion inward or make it smaller. If it's a deliberate entrance/exit, add its block id to this constraint's `ignore` list",
                        block=bid, actor=aid, t=float(times[i]), pointer=ptr,
                    ))
        elif isinstance(c, MaxSpeed):
            pos = baked.pos[c.actor]
            if len(pos) > 1:
                speed = np.linalg.norm(np.diff(pos, axis=0), axis=1) * baked.fps
                i = int(np.argmax(speed))
                if speed[i] > c.px_per_s:
                    bid, who = _blame(sched, c.actor, float(times[i]))
                    out.append(Issue(
                        "E_SPEED", f"{c.actor} moves at {speed[i]:.0f}px/s at t={times[i]:.2f}s (max {c.px_per_s:g})",
                        hint=f"active: {who}; lengthen its duration or shorten the distance", block=bid, actor=c.actor, t=float(times[i]), pointer=ptr,
                    ))
        elif isinstance(c, Smooth):
            pos = baked.pos[c.actor]
            if len(pos) > 3:
                jerk = np.linalg.norm(np.diff(pos, n=3, axis=0), axis=1) * baked.fps**3
                i = int(np.argmax(jerk))
                if jerk[i] > c.max_jerk:
                    t = float(times[i + 1])
                    bid, who = _blame(sched, c.actor, t)
                    out.append(Issue(
                        "E_JERK", f"{c.actor} has a visible pop (jerk {jerk[i]:.3g}px/s³, max {c.max_jerk:.3g}) near t={t:.2f}s",
                        hint=f"active: {who}; use a gentler ease (inOutSine/inOutCubic) or overlap neighbouring moves", block=bid, actor=c.actor, t=t, pointer=ptr,
                    ))
        elif isinstance(c, NoOverlap):
            if c.a not in baked.aabb_world or c.b not in baked.aabb_world:
                continue
            A, B = baked.aabb_world[c.a], baked.aabb_world[c.b]
            vis = (baked.opacity[c.a] > VISIBLE) & (baked.opacity[c.b] > VISIBLE)
            if c.during is not None:
                vis &= (times >= c.during[0] - 1e-9) & (times <= c.during[1] + 1e-9)
            hit = vis & (A[:, 0] < B[:, 2]) & (B[:, 0] < A[:, 2]) & (A[:, 1] < B[:, 3]) & (B[:, 1] < A[:, 3])
            if hit.any():
                i = int(np.argmax(hit))
                bid, who = _blame(sched, c.a, float(times[i]))
                out.append(Issue(
                    "E_OVERLAP", f"{c.a} and {c.b} overlap at t={times[i]:.2f}s",
                    hint=f"active on {c.a}: {who}; separate their paths or offset their timing", block=bid, actor=c.a, t=float(times[i]), pointer=ptr,
                ))
        elif isinstance(c, EndsAt):
            t = baked.duration if c.t is None else c.t
            i = baked.frame_at(t)
            x, y = baked.pos[c.actor][i]
            d = math.hypot(x - c.pos[0], y - c.pos[1])
            if d > c.tol:
                bid, who = _blame(sched, c.actor, t)
                out.append(Issue(
                    "E_ENDS_AT", f"{c.actor} is at ({x:.0f}, {y:.0f}) at t={t:.2f}s, {d:.0f}px from target ({c.pos[0]:g}, {c.pos[1]:g})",
                    hint=f"last motion: {who}; adjust its target", block=bid, actor=c.actor, t=t, pointer=ptr,
                ))
        elif isinstance(c, TotalDuration):
            D = baked.duration
            if c.max is not None and D > c.max + c.tol:
                out.append(Issue("E_DURATION", f"timeline lasts {D:.2f}s, over the {c.max:g}s maximum",
                                 hint="shorten durations or run blocks in parallel (par/stagger)", pointer=ptr))
            if c.exact is not None and abs(D - c.exact) > c.tol:
                out.append(Issue("E_DURATION", f"timeline lasts {D:.2f}s, not {c.exact:g}s",
                                 hint="scale block durations or add/remove a hold", pointer=ptr))
    return out


def check(raw: dict) -> Result:
    """Parse, schedule, bake and check a document. Always returns a Report; `baked` is set
    whenever the document could be evaluated (even if constraints fail)."""
    report = Report()
    parsed, issues = parse(raw)
    report.extend(issues)
    if parsed is None:
        return Result(report)
    sched = schedule(parsed.root)
    report.stats = {
        "duration": round(sched.duration, 3),
        "actors": len(parsed.actors),
        "blocks": len(parsed.blocks),
        "loops": sched.loops,
    }
    if sched.duration > MAX_BAKE_SECONDS:
        report.add(Issue("E_DURATION", f"timeline lasts {sched.duration:.0f}s; the maximum is {MAX_BAKE_SECONDS:.0f}s",
                         hint="check for huge durations, counts or slow springs"))
        return Result(report, parsed)
    conflicts = check_conflicts(sched)
    report.extend(conflicts)
    if conflicts:
        return Result(report, parsed)
    try:
        baked = bake(parsed, sched)
    except DependencyCycle as e:
        report.add(Issue("E_REF", str(e), hint="break the cycle: one of the follow/orbit verbs must target an actor that doesn't depend back on it (or use lag > 0)"))
        return Result(report, parsed)
    report.extend(check_discontinuities(baked))
    report.extend(check_constraints(baked))
    return Result(report, parsed, baked)


def validate(raw: dict) -> Report:
    return check(raw).report
