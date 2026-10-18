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
TO_ONE = ', "to": 1'


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


def _active(sched: Schedule, actors: list[str], t: float) -> list[Leaf]:
    """Leaves on these actors active at t, else the most recently finished one."""
    mine = [lf for lf in sched.leaves if lf.block.actor in actors]
    now = [lf for lf in mine if lf.start - 1e-9 <= t <= lf.end + 1e-9]
    if now:
        return now
    done = [lf for lf in mine if lf.end <= t]
    return [max(done, key=lambda lf: lf.end)] if done else []


def _blame(sched: Schedule, actor: str, t: float, parents: dict[str, str | None] | None = None) -> tuple[str | None, str]:
    """The block to blame for where `actor` is at t: its own motion, or a parent group's."""
    chain, cur = [actor], actor
    while parents and parents.get(cur) and parents[cur] not in chain:
        cur = parents[cur]  # type: ignore[assignment]
        chain.append(cur)
    leaves = _active(sched, chain, t)
    if not leaves:
        return None, f"{actor}'s initial position" + (f" (or its parent {chain[1]!r}'s)" if len(chain) > 1 else "")
    return leaves[0].block.id, ", ".join(
        _leaf_desc(lf) + (f" on parent {lf.block.actor!r}" if lf.block.actor != actor else "") for lf in leaves
    )


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


def check_noops(baked: Baked) -> list[Issue]:
    """A verb that changes nothing over its whole run (e.g. `move` to where the actor already is) almost
    always means a missing starting pose: the actor was meant to come from somewhere else."""
    ev, out = baked.ev, []
    index = {a.id: i for i, a in enumerate(baked.parsed.doc.actors)}
    for lf in baked.sched.leaves:
        b = lf.block
        mode = b.verb.mode(b.params)
        if mode is None or b.actor is None or lf.duration <= 0:
            continue
        start = ev.from_state(lf)
        moved = False
        for u in (0.0, 0.25, 0.5, 0.75, 1.0):
            vals = ev.sample_at(lf, b.ease(u) if b.verb.uses_ease else u, u * lf.duration)
            for prop, v in vals.items():
                if prop in COLOR_PROPS:
                    moved |= v != start.get(prop)
                else:
                    ref = start[prop] if mode == "abs" else 0.0
                    moved |= abs(float(v) - float(ref)) > JUMP_TOL.get(prop, 0.01)
            if moved:
                break
        if moved:
            continue
        props = "/".join(b.verb.props(b.params))
        where = f"actors[{index[b.actor]}].init" if b.actor in index else "its init"
        now = ", ".join(f"{k} {v:g}" if isinstance(v, (int, float)) else f"{k} {v}" for k, v in start.items())
        out.append(Issue(
            "E_NOOP", f"{b.id} ({b.verb.name}) changes nothing: {b.actor}.{props} is already at its target ({now})",
            hint=(f"to animate {b.actor} into this state, set where it starts in {where} (e.g. a lower init y for a rise, "
                  f"init opacity 0 before a fade to 1, init sx/sy 0 before a scale to 1), or remove block {b.id!r}"),
            block=b.id, actor=b.actor, t=lf.start, pointer=b.pointer,
        ))
    return out


def check_visibility(baked: Baked) -> list[Issue]:
    """An actor with a shape that is never visible at any frame is almost always a mistake
    (e.g. init.opacity 0 and nothing fades it in), and the preview looks blank."""
    out = []
    for i, a in enumerate(baked.parsed.doc.actors):
        if a.id not in baked.aabb_world:
            continue  # groups have no geometry
        p = baked.props[a.id]
        opaque = baked.opacity[a.id] > VISIBLE
        det = np.array([abs(m[0] * m[3] - m[1] * m[2]) for m in baked.world[a.id]])
        sized = det > 1e-6
        open_ = (np.add(p["clip_l"], p["clip_r"]) < 0.999) & (np.add(p["clip_t"], p["clip_b"]) < 0.999)
        filled = np.array([f is not None for f in p["fill"]])
        traced = filled | (np.asarray(p["trim"]) > 1e-3)
        if (opaque & sized & open_ & traced).any():
            continue
        mine = [lf.block.id for lf in baked.sched.leaves if lf.block.actor == a.id]
        near = f" (e.g. in a par together with block {mine[0]!r})" if mine else ""

        def add(verb: str, extra: str = "") -> str:
            taken = set(baked.parsed.blocks) | set(baked.parsed.actors)
            new_id = next(c for c in (f"{a.id}_{verb}", *(f"{a.id}_{verb}{n}" for n in range(2, 99))) if c not in taken)
            return f'add the block {{"id": "{new_id}", "verb": "{verb}", "actor": "{a.id}"{extra}, "duration": 0.4}} where it should appear{near}'

        if not opaque.any():
            parent = baked.parsed.actors[a.parent] if a.parent else None
            if a.init.opacity <= VISIBLE or parent is None:
                why = f"its opacity stays at {float(baked.opacity[a.id].max()):.2f} (actors[{i}].init.opacity is {a.init.opacity:g})"
                hint = f'either remove "opacity" from actors[{i}].init, or {add("fade", TO_ONE)}'
            else:
                why = f"its parent {a.parent!r} is never visible"
                hint = f"make {a.parent!r} visible (see its own error)"
        elif not sized.any():
            why = f"its scale stays at 0 (actors[{i}].init.sx/sy)"
            hint = f'either remove "sx" and "sy" from actors[{i}].init, or {add("scale", TO_ONE)}'
        elif not open_.any():
            reveals = [lf.block.id for lf in baked.sched.leaves if lf.block.actor == a.id and lf.block.verb.name == "reveal"]
            why = "it stays fully clipped"
            hint = f'set "to": 1 on block {reveals[-1]!r}' if reveals else "end its reveal with \"to\": 1"
        else:
            why = f"its trim stays at 0 (actors[{i}].init.trim)"
            hint = f'either remove "trim" from actors[{i}].init, or {add("draw_on")}'
        out.append(Issue("E_INVISIBLE", f"{a.id} is never visible: {why}", hint=hint, actor=a.id, pointer=f"/actors/{i}"))
    return out


def _bounds_fix(baked: Baked, bid: str | None, aid: str, frame: int, push: np.ndarray) -> str:
    """A concrete number to change, so a repair doesn't have to work out coordinates: shift the blamed
    `move` (or the actor's init position) by `push` world px, expressed in the moved actor's parent frame."""
    block = baked.parsed.blocks.get(bid) if bid else None
    if block is not None:
        if getattr(block, "verb", None) is None or block.verb.name != "move" or block.actor is None:
            return ""
        target, p = block.actor, block.params
    else:
        target, p = aid, None
    parent = baked.parsed.actors[target].parent
    if parent:
        a, b, c, d, _, _ = baked.world[parent][frame]
        det = a * d - b * c
        if abs(det) < 1e-9:
            return ""
        local = np.array([d * push[0] - c * push[1], -b * push[0] + a * push[1]]) / det
    else:
        local = push
    frame_name = f"{parent}'s coordinates" if parent else "canvas coordinates"
    if p is None:
        x, y = baked.ev.init[target]["x"] + local[0], baked.ev.init[target]["y"] + local[1]
        return f"set {target}'s init x/y to about [{x:.0f}, {y:.0f}] ({frame_name})"
    key = "to" if p.to is not None else "by"
    x, y = (np.asarray(p.to if key == "to" else p.by, dtype=float) + local).tolist()
    return f'change {bid}\'s "{key}" to about [{x:.0f}, {y:.0f}] ({target} moves in {frame_name})'


def _sel(sel: Any, baked: Baked) -> list[str]:
    return list(baked.aabb_world) if sel == "*" else [a for a in sel if a in baked.aabb_world]


def check_constraints(baked: Baked) -> list[Issue]:
    doc, out = baked.parsed.doc, []
    W, H = doc.canvas.width, doc.canvas.height
    times, sched = baked.times, baked.sched
    known = set(baked.parsed.actors)
    parents = {k: a.parent for k, a in baked.parsed.actors.items()}
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
                lineage, cur = {aid}, aid
                while parents.get(cur) and parents[cur] not in lineage:
                    cur = parents[cur]  # type: ignore[assignment]
                    lineage.add(cur)
                for lf in exempt:
                    if lf.block.actor in lineage:  # its own entrance, or its group's
                        vis = vis & ~((times >= lf.start - 1e-9) & (times <= lf.end + 1e-9))
                m = c.margin
                over = np.stack([m - box[:, 0], m - box[:, 1], box[:, 2] - (W - m), box[:, 3] - (H - m)], axis=1)
                over[~vis] = -np.inf
                worst = over.max(axis=1)
                if (worst > 0.5).any():
                    i = int(np.argmax(worst))
                    side = ("left", "top", "right", "bottom")[int(np.argmax(over[i]))]
                    bid, who = _blame(sched, aid, float(times[i]), parents)
                    k = ("left", "top", "right", "bottom").index(side)
                    push = (worst[i] + 1) * np.array([(1, 0), (0, 1), (-1, 0), (0, -1)][k], dtype=float)  # world px back inside
                    fix = _bounds_fix(baked, bid, aid, i, push)
                    if bid:
                        hint = (f"active: {who}; {fix or 'move that motion inward or make it smaller'}. "
                                f'If it is a deliberate entrance or exit, add "{bid}" to constraints[{ci}].ignore instead')
                    else:
                        hint = f"active: {who}; {fix or f'move {aid} inward'}"
                    out.append(Issue(
                        "E_BOUNDS", f"{aid} goes {worst[i]:.0f}px past the {side} edge (margin {m:g}) at t={times[i]:.2f}s",
                        hint=hint, block=bid, actor=aid, t=float(times[i]), pointer=ptr,
                    ))
        elif isinstance(c, MaxSpeed):
            pos = baked.pos[c.actor]
            if len(pos) > 1:
                speed = np.linalg.norm(np.diff(pos, axis=0), axis=1) * baked.fps
                i = int(np.argmax(speed))
                if speed[i] > c.px_per_s:
                    bid, who = _blame(sched, c.actor, float(times[i]), parents)
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
                    bid, who = _blame(sched, c.actor, t, parents)
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
                bid, who = _blame(sched, c.a, float(times[i]), parents)
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
                bid, who = _blame(sched, c.actor, t, parents)
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
    report.extend(check_noops(baked))
    report.extend(check_visibility(baked))
    report.extend(check_constraints(baked))
    return Result(report, parsed, baked)


def validate(raw: dict) -> Report:
    return check(raw).report
