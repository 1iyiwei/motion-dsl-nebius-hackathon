"""The 17 core verbs of MotionLang v0.1."""

from __future__ import annotations

import math
import random
import zlib
from typing import Any, Literal, Optional

from pydantic import Field, model_validator

from ..color import mix
from ..model import CAMERA_ID, CAMERA_PROPS, NUMERIC_PROPS, Actor, expand_prop
from .base import Mode, SampleContext, Verb, VerbParams, register

Vec2 = tuple[float, float]


def _lerp(a: float, b: float, u: float) -> float:
    return a + (b - a) * u


class _ToOrBy(VerbParams):
    @model_validator(mode="after")
    def _exactly_one(self):  # type: ignore[no-untyped-def]
        if (self.to is None) == (self.by is None):  # type: ignore[attr-defined]
            raise ValueError("give exactly one of `to` (absolute target) or `by` (relative change)")
        return self


# ---- tweens -------------------------------------------------------------------------------


class MoveParams(_ToOrBy):
    to: Optional[Vec2] = Field(None, description="absolute target [x, y]")
    by: Optional[Vec2] = Field(None, description="relative offset [dx, dy]")


@register
class Move(Verb):
    """Translate an actor to a point (`to`) or by an offset (`by`)."""

    name = "move"
    Params = MoveParams
    examples = [{"to": [640, 360], "duration": 1.0}, {"by": [200, -50], "ease": "outBack"}]

    def mode(self, p: MoveParams) -> Mode:
        return "abs" if p.to is not None else "rel"

    def props(self, p: MoveParams) -> tuple[str, ...]:
        return ("x", "y")

    def sample(self, p: MoveParams, u: float, t: float, ctx: SampleContext) -> dict[str, Any]:
        if p.to is not None:
            return {"x": _lerp(ctx.start["x"], p.to[0], u), "y": _lerp(ctx.start["y"], p.to[1], u)}
        return {"x": p.by[0] * u, "y": p.by[1] * u}  # type: ignore[index]


class RotateParams(_ToOrBy):
    to: Optional[float] = Field(None, description="absolute angle, degrees clockwise")
    by: Optional[float] = Field(None, description="relative turn, degrees clockwise")


@register
class Rotate(Verb):
    """Rotate around the actor's pivot, to an angle or by an angle (degrees, clockwise-positive)."""

    name = "rotate"
    Params = RotateParams
    examples = [{"by": 360, "duration": 1.2}, {"to": -15, "ease": "outElastic"}]

    def mode(self, p: RotateParams) -> Mode:
        return "abs" if p.to is not None else "rel"

    def props(self, p: RotateParams) -> tuple[str, ...]:
        return ("rotation",)

    def sample(self, p: RotateParams, u: float, t: float, ctx: SampleContext) -> dict[str, Any]:
        if p.to is not None:
            return {"rotation": _lerp(ctx.start["rotation"], p.to, u)}
        return {"rotation": p.by * u}  # type: ignore[operator]


class ScaleParams(_ToOrBy):
    to: Optional[float | Vec2] = Field(None, description="absolute scale, number or [sx, sy]")
    by: Optional[float | Vec2] = Field(None, description="relative scale change (added), number or [dsx, dsy]")


def _pair(v: float | Vec2) -> Vec2:
    return (float(v), float(v)) if isinstance(v, (int, float)) else (float(v[0]), float(v[1]))


@register
class Scale(Verb):
    """Scale around the actor's pivot, to a size (`to`, 1 = original) or by an additive amount (`by`)."""

    name = "scale"
    Params = ScaleParams
    examples = [{"to": 1.5, "ease": "outBack"}, {"by": [0.2, -0.1], "duration": 0.3}]

    def mode(self, p: ScaleParams) -> Mode:
        return "abs" if p.to is not None else "rel"

    def props(self, p: ScaleParams) -> tuple[str, ...]:
        return ("sx", "sy")

    def sample(self, p: ScaleParams, u: float, t: float, ctx: SampleContext) -> dict[str, Any]:
        if p.to is not None:
            tx, ty = _pair(p.to)
            return {"sx": _lerp(ctx.start["sx"], tx, u), "sy": _lerp(ctx.start["sy"], ty, u)}
        bx, by = _pair(p.by)  # type: ignore[arg-type]
        return {"sx": bx * u, "sy": by * u}


class FadeParams(VerbParams):
    to: float = Field(ge=0, le=1, description="target opacity")


@register
class Fade(Verb):
    """Fade opacity to a target value (0 = invisible, 1 = opaque)."""

    name = "fade"
    Params = FadeParams
    examples = [{"to": 0.0, "duration": 0.5}, {"to": 0.4}]

    def mode(self, p: FadeParams) -> Mode:
        return "abs"

    def props(self, p: FadeParams) -> tuple[str, ...]:
        return ("opacity",)

    def sample(self, p: FadeParams, u: float, t: float, ctx: SampleContext) -> dict[str, Any]:
        return {"opacity": _lerp(ctx.start["opacity"], p.to, u)}


class RecolorParams(VerbParams):
    prop: Literal["fill", "stroke"] = "fill"
    to: str = Field(description="#rrggbb or CSS color name")


@register
class Recolor(Verb):
    """Change fill or stroke color, interpolated in OKLab so midpoints stay vivid."""

    name = "recolor"
    Params = RecolorParams
    examples = [{"to": "#2e86de"}, {"prop": "stroke", "to": "crimson", "duration": 0.4}]

    def mode(self, p: RecolorParams) -> Mode:
        return "abs"

    def props(self, p: RecolorParams) -> tuple[str, ...]:
        return (p.prop,)

    def sample(self, p: RecolorParams, u: float, t: float, ctx: SampleContext) -> dict[str, Any]:
        start = ctx.start[p.prop]
        return {p.prop: p.to if start is None else mix(start, p.to, u)}  # None = no paint yet

    def check_target(self, p: RecolorParams, actor: Actor | None) -> str | None:
        if actor is not None and actor.shape.kind in ("group", "image"):
            return f"recolor can't apply to a {actor.shape.kind} actor"
        return None


class DrawOnParams(VerbParams):
    from_: float = Field(0.0, alias="from", ge=0, le=1, description="visible fraction at start")
    to: float = Field(1.0, ge=0, le=1, description="visible fraction at end")


@register
class DrawOn(Verb):
    """Reveal a stroke/path/polygon/asset outline progressively, as if being drawn (animates `trim`)."""

    name = "draw_on"
    Params = DrawOnParams
    examples = [{"duration": 1.5, "ease": "inOutSine"}, {"from": 1.0, "to": 0.0}]

    def mode(self, p: DrawOnParams) -> Mode:
        return "abs"

    def props(self, p: DrawOnParams) -> tuple[str, ...]:
        return ("trim",)

    def sample(self, p: DrawOnParams, u: float, t: float, ctx: SampleContext) -> dict[str, Any]:
        return {"trim": _lerp(p.from_, p.to, u)}

    def jumps_at_start(self, p: DrawOnParams) -> bool:
        return True

    def holds_before(self, p: DrawOnParams) -> bool:
        return True

    def check_target(self, p: DrawOnParams, actor: Actor | None) -> str | None:
        if actor is not None and actor.shape.kind not in ("stroke", "path", "polygon", "asset"):
            return f"draw_on needs a stroke, path, polygon or asset actor, not {actor.shape.kind}"
        return None


REVEAL_SIDES: dict[str, tuple[str, ...]] = {
    "left_to_right": ("clip_r",), "right_to_left": ("clip_l",),
    "top_to_bottom": ("clip_b",), "bottom_to_top": ("clip_t",),
    "from_center": ("clip_l", "clip_t", "clip_r", "clip_b"),
}


class RevealParams(VerbParams):
    direction: Literal["left_to_right", "right_to_left", "top_to_bottom", "bottom_to_top", "from_center"] = Field(
        "left_to_right", description="the way the visible edge travels")
    from_: float = Field(0.0, alias="from", ge=0, le=1, description="visible fraction at start")
    to: float = Field(1.0, ge=0, le=1, description="visible fraction at end")


@register
class Reveal(Verb):
    """Wipe a shape into view behind a moving straight edge (a directional mask; works on filled shapes,
    unlike draw_on). `from: 1, to: 0` wipes it out again."""

    name = "reveal"
    Params = RevealParams
    examples = [{"direction": "left_to_right", "duration": 0.8}, {"direction": "from_center", "from": 1.0, "to": 0.0}]

    def mode(self, p: RevealParams) -> Mode:
        return "abs"

    def props(self, p: RevealParams) -> tuple[str, ...]:
        return REVEAL_SIDES[p.direction]

    def sample(self, p: RevealParams, u: float, t: float, ctx: SampleContext) -> dict[str, Any]:
        hidden = 1.0 - _lerp(p.from_, p.to, u)
        sides = self.props(p)
        share = hidden / 2 if len(sides) == 4 else hidden
        return {side: share for side in sides}

    def jumps_at_start(self, p: RevealParams) -> bool:
        return True

    def holds_before(self, p: RevealParams) -> bool:
        return True

    def check_target(self, p: RevealParams, actor: Actor | None) -> str | None:
        if actor is not None and actor.shape.kind == "group":
            return "reveal needs an actor with a shape, not a group; reveal each child (a stagger works well)"
        return None


Side = Literal["left", "right", "top", "bottom", "scale", "fade"]
_SIDE_PROPS: dict[str, tuple[str, ...]] = {
    "left": ("x",), "right": ("x",), "top": ("y",), "bottom": ("y",), "scale": ("sx", "sy"), "fade": ("opacity",),
}


def _offstage_value(side: str, prop: str, rest: float, distance: float | None, ctx: SampleContext) -> float:
    """The pose just off the canvas (or at scale 0 / transparent) on `side`, for one prop."""
    if side in ("scale", "fade"):
        return 0.0
    horizontal = side in ("left", "right")
    sx, sy = ctx.parent_scale()
    # Shifting by 1.1x the canvas size takes anything that rests on the canvas fully off it.
    d = distance if distance is not None else 1.1 * (ctx.canvas.width if horizontal else ctx.canvas.height)
    d /= sx if horizontal else sy
    return rest - d if side in ("left", "top") else rest + d


class EnterParams(VerbParams):
    from_: Side = Field("left", alias="from", description="where it comes from: a canvas edge, or scale/fade in place")
    distance: Optional[float] = Field(None, gt=0, le=100000, description="px to travel (default: just off-canvas)")


@register
class Enter(Verb):
    """Bring an actor in from off-canvas (or from scale 0 / transparent) to where it rests: its init pose.
    It is hidden until this block starts, and no in_bounds ignore entry is needed. Works on groups."""

    name = "enter"
    Params = EnterParams
    default_ease = "outCubic"
    examples = [{"from": "left", "duration": 0.8}, {"from": "scale", "duration": 0.5, "ease": "outBack"}]

    def mode(self, p: EnterParams) -> Mode:
        return "abs"

    def props(self, p: EnterParams) -> tuple[str, ...]:
        return _SIDE_PROPS[p.from_]

    def sample(self, p: EnterParams, u: float, t: float, ctx: SampleContext) -> dict[str, Any]:
        return {k: _lerp(_offstage_value(p.from_, k, ctx.start[k], p.distance, ctx), ctx.start[k], u) for k in self.props(p)}

    def jumps_at_start(self, p: EnterParams) -> bool:
        return True

    def holds_before(self, p: EnterParams) -> bool:
        return True

    def offstage(self, p: EnterParams) -> Literal["before", "after"] | None:
        return "before" if p.from_ in ("left", "right", "top", "bottom") else None


class ExitParams(VerbParams):
    to: Side = Field("right", description="where it goes: a canvas edge, or scale/fade out in place")
    distance: Optional[float] = Field(None, gt=0, le=100000, description="px to travel (default: just off-canvas)")


@register
class Exit(Verb):
    """Take an actor off-canvas (or to scale 0 / transparent) from where it is. No in_bounds ignore entry
    is needed."""

    name = "exit"
    Params = ExitParams
    default_ease = "inCubic"
    examples = [{"to": "right", "duration": 0.8}, {"to": "fade", "duration": 0.4}]

    def mode(self, p: ExitParams) -> Mode:
        return "abs"

    def props(self, p: ExitParams) -> tuple[str, ...]:
        return _SIDE_PROPS[p.to]

    def sample(self, p: ExitParams, u: float, t: float, ctx: SampleContext) -> dict[str, Any]:
        return {k: _lerp(ctx.start[k], _offstage_value(p.to, k, ctx.start[k], p.distance, ctx), u) for k in self.props(p)}

    def offstage(self, p: ExitParams) -> Literal["before", "after"] | None:
        return "after" if p.to in ("left", "right", "top", "bottom") else None


class CameraParams(_ToOrBy):
    to: Optional[dict[Literal["x", "y", "zoom", "rotation"], float]] = Field(None, description="absolute camera values")
    by: Optional[dict[Literal["x", "y", "zoom", "rotation"], float]] = Field(None, description="relative camera change")

    @model_validator(mode="after")
    def _non_empty(self):  # type: ignore[no-untyped-def]
        if not (self.to or self.by):
            raise ValueError("camera needs at least one of x, y, zoom, rotation")
        return self


@register
class Camera(Verb):
    """Pan (x, y = point shown at canvas center), zoom, or rotate the view. Omit `actor`."""

    name = "camera"
    Params = CameraParams
    fixed_actor = CAMERA_ID
    examples = [{"to": {"zoom": 1.6, "x": 400, "y": 300}, "duration": 1.5}, {"by": {"x": 300}}]

    def mode(self, p: CameraParams) -> Mode:
        return "abs" if p.to is not None else "rel"

    def props(self, p: CameraParams) -> tuple[str, ...]:
        keys = (p.to or p.by or {}).keys()
        return tuple(k for k in CAMERA_PROPS if k in keys)

    def sample(self, p: CameraParams, u: float, t: float, ctx: SampleContext) -> dict[str, Any]:
        if p.to is not None:
            return {k: _lerp(ctx.start[k], v, u) for k, v in p.to.items()}
        return {k: v * u for k, v in p.by.items()}  # type: ignore[union-attr]


# ---- paths and procedural motion ----------------------------------------------------------


class MoveAlongParams(VerbParams):
    path: dict[str, Any] = Field(description='{"stroke": id} | {"points": [[x,y],...], "smooth": bool} | {"svg": d} | {"arc": {center, radius, from, to}}; '
                                             'optional "space": "canvas" (default for strokes: follows the line where it was drawn, '
                                             'even for a part inside a group) or "parent" (default for the others: the actor\'s own x/y space)')
    orient: bool = Field(False, description="turn with the path's direction")
    reverse: bool = False
    relative: bool = Field(False, description="translate the path so it starts at the actor")


@register
class MoveAlong(Verb):
    """Travel along a path at constant speed (shaped by `ease`).

    The actor must be at the path start unless `relative` is true. With `orient`, the actor
    turns as the path turns, keeping its starting heading relative to the path.
    """

    name = "move_along"
    Params = MoveAlongParams
    examples = [
        {"path": {"stroke": "s1"}, "orient": True, "duration": 1.6},
        {"path": {"points": [[0, 0], [120, -80], [240, 0]], "smooth": True}, "relative": True},
        {"path": {"svg": "M 0 0 C 50 -100 150 -100 200 0"}, "relative": True, "reverse": True},
    ]

    def mode(self, p: MoveAlongParams) -> Mode:
        return "rel" if p.relative else "abs"

    def props(self, p: MoveAlongParams) -> tuple[str, ...]:
        return ("x", "y", "rotation") if p.orient else ("x", "y")

    def path_specs(self, p: MoveAlongParams) -> list[dict]:
        return [p.path]

    def sample(self, p: MoveAlongParams, u: float, t: float, ctx: SampleContext) -> dict[str, Any]:
        poly = ctx.path(p.path)
        if p.reverse:
            poly = poly.reversed()
        x, y = poly.point_at(u)
        turn = poly.angle_at(u) - poly.angle_at(0)
        if p.relative:
            sx, sy = poly.start
            out = {"x": x - sx, "y": y - sy}
            if p.orient:
                out["rotation"] = turn
            return out
        out = {"x": x, "y": y}
        if p.orient:
            out["rotation"] = ctx.start["rotation"] + turn
        return out


class OrbitParams(VerbParams):
    center: Optional[Vec2] = Field(None, description="fixed center [x, y]")
    around: Optional[str] = Field(None, description="actor id to orbit (center follows it)")
    radius: Optional[float] = Field(None, ge=0, description="default: current distance to the center")
    turns: float = Field(1.0, description="revolutions; negative = counter-clockwise")
    start_angle: Optional[float] = Field(None, description="degrees; default: current angle to the center")

    @model_validator(mode="after")
    def _one_center(self):  # type: ignore[no-untyped-def]
        if (self.center is None) == (self.around is None):
            raise ValueError("give exactly one of `center` [x, y] or `around` (actor id)")
        return self


@register
class Orbit(Verb):
    """Circle around a point or another actor. Starts from the actor's current position by default."""

    name = "orbit"
    Params = OrbitParams
    default_ease = "linear"
    examples = [{"center": [640, 360], "turns": 1, "duration": 3}, {"around": "b", "turns": 2, "duration": 4}]

    def mode(self, p: OrbitParams) -> Mode:
        return "abs"

    def props(self, p: OrbitParams) -> tuple[str, ...]:
        return ("x", "y")

    def actor_refs(self, p: OrbitParams) -> list[str]:
        return [p.around] if p.around else []

    def _center(self, p: OrbitParams, ctx: SampleContext, t_abs: float) -> Vec2:
        if p.center is not None:
            return p.center
        return ctx.value(p.around, "x", t_abs), ctx.value(p.around, "y", t_abs)  # type: ignore[arg-type]

    def sample(self, p: OrbitParams, u: float, t: float, ctx: SampleContext) -> dict[str, Any]:
        c0 = self._center(p, ctx, ctx.t0)
        dx, dy = ctx.start["x"] - c0[0], ctx.start["y"] - c0[1]
        r = p.radius if p.radius is not None else math.hypot(dx, dy)
        a0 = p.start_angle if p.start_angle is not None else math.degrees(math.atan2(dy, dx))
        c = self._center(p, ctx, ctx.t0 + t)
        th = math.radians(a0 + 360.0 * p.turns * u)
        return {"x": c[0] + r * math.cos(th), "y": c[1] + r * math.sin(th)}


GRAVITY = 2500.0  # px/s²: an 80 px hop takes ~0.5 s, which reads as a light ball


class BounceParams(VerbParams):
    height: float = Field(80, gt=0, description="first hop height, px")
    count: int = Field(3, ge=1, le=20)
    decay: float = Field(0.6, gt=0, le=1, description="each hop is this fraction of the previous height")
    squash: float = Field(0.15, ge=0, le=0.6, description="squash-and-stretch amount at each landing")


@register
class Bounce(Verb):
    """Hop in place with gravity-shaped arcs and squash-and-stretch on landing. Ends at rest.

    Duration defaults to the physical time; giving `duration` speeds it up or slows it down.
    """

    name = "bounce"
    Params = BounceParams
    uses_ease = False
    examples = [{"height": 80, "count": 3}, {"height": 140, "count": 5, "decay": 0.5, "squash": 0.25, "duration": 2}]

    def mode(self, p: BounceParams) -> Mode:
        return "rel"

    def props(self, p: BounceParams) -> tuple[str, ...]:
        return ("y", "sx", "sy")

    def _hops(self, p: BounceParams) -> list[tuple[float, float]]:
        hs = [p.height * p.decay**i for i in range(p.count)]
        return [(h, 2 * math.sqrt(2 * h / GRAVITY)) for h in hs]

    def default_duration(self, p: BounceParams) -> float:
        return sum(T for _, T in self._hops(p))

    def sample(self, p: BounceParams, u: float, t: float, ctx: SampleContext) -> dict[str, Any]:
        hops = self._hops(p)
        total = sum(T for _, T in hops)
        tau = u * total  # physical time
        y = 0.0
        start = 0.0
        for h, T in hops:
            if tau <= start + T:
                s = tau - start
                v = math.sqrt(2 * GRAVITY * h)
                y = -(v * s - GRAVITY * s * s / 2)
                break
            start += T
        # Squash pulses at the landings between hops (not at the very start or end, so the
        # verb leaves the actor exactly as it found it).
        sq = 0.0
        land = 0.0
        for j, (_, T) in enumerate(hops[:-1]):
            land += T
            w = min(0.06, T / 2, hops[j + 1][1] / 2)
            dist = abs(tau - land)
            if dist < w:
                sq = max(sq, p.squash * (p.decay**j) * math.cos(math.pi * dist / (2 * w)) ** 2)
        return {"y": min(y, 0.0), "sx": sq, "sy": -sq}


class WiggleParams(VerbParams):
    amplitude: float = Field(10, gt=0, description="px for x/y, degrees for rotation")
    frequency: float = Field(4, gt=0, description="wiggles per second")
    props_: list[Literal["x", "y", "rotation", "sx", "sy", "opacity"]] = Field(["x", "y"], alias="props")
    seed: int = 0


@register
class Wiggle(Verb):
    """Smooth random jitter that eases in and out, returning exactly to rest. Deterministic per `seed`."""

    name = "wiggle"
    Params = WiggleParams
    uses_ease = False
    examples = [{"amplitude": 6, "frequency": 5, "duration": 1}, {"amplitude": 8, "props": ["rotation"], "seed": 7}]

    def mode(self, p: WiggleParams) -> Mode:
        return "rel"

    def props(self, p: WiggleParams) -> tuple[str, ...]:
        return tuple(dict.fromkeys(p.props_))

    def sample(self, p: WiggleParams, u: float, t: float, ctx: SampleContext) -> dict[str, Any]:
        env = math.sin(math.pi * min(max(u, 0.0), 1.0))
        out = {}
        for prop in self.props(p):
            rng = random.Random(zlib.crc32(f"{p.seed}:{prop}".encode()))
            total = 0.0
            for k in range(3):  # three incommensurate sines ≈ smooth noise
                f = p.frequency * (1.0, 1.73, 2.61)[k]
                total += (0.6, 0.3, 0.15)[k] * math.sin(2 * math.pi * f * t + rng.uniform(0, 2 * math.pi))
            scale = p.amplitude * (0.01 if prop in ("sx", "sy", "opacity") else 1.0)
            out[prop] = scale * env * total / 1.05
        return out


class SpringToParams(VerbParams):
    prop: Literal["x", "y", "rotation", "sx", "sy", "opacity", "position", "scale", "zoom"] = "scale"
    to: float | list[float] = Field(description="target value; a list for multi-value props (position, scale)")
    stiffness: float = Field(170, gt=0, le=5000)
    damping: float = Field(26, gt=0)

    @model_validator(mode="after")
    def _settles(self):  # type: ignore[no-untyped-def]
        ratio = self.damping / (2 * math.sqrt(self.stiffness))
        if ratio < 0.05:
            raise ValueError(
                f"damping {self.damping:g} is too low for stiffness {self.stiffness:g} (damping ratio {ratio:.3f} < 0.05); "
                f"it would ring for ~{_settle_time(self.stiffness, self.damping):.0f}s. Use damping ≥ {0.1 * math.sqrt(self.stiffness):.1f}"
            )
        return self


def _spring_response(k: float, c: float, t: float) -> float:
    """Normalized displacement of a unit-mass damped spring released from 1 at rest."""
    w0 = math.sqrt(k)
    z = c / (2 * w0)
    if z < 1:
        wd = w0 * math.sqrt(1 - z * z)
        return math.exp(-z * w0 * t) * (math.cos(wd * t) + (z * w0 / wd) * math.sin(wd * t))
    if z == 1:
        return math.exp(-w0 * t) * (1 + w0 * t)
    s = math.sqrt(z * z - 1)
    r1, r2 = -w0 * (z - s), -w0 * (z + s)
    return (r2 * math.exp(r1 * t) - r1 * math.exp(r2 * t)) / (r2 - r1)


def _settle_time(k: float, c: float, eps: float = 1e-3) -> float:
    w0 = math.sqrt(k)
    z = c / (2 * w0)
    if z < 1:
        wd = w0 * math.sqrt(1 - z * z)
        amp = math.sqrt(1 + (z * w0 / wd) ** 2)
        return math.log(amp / eps) / (z * w0)
    t, dt = 0.0, 1 / 240
    while abs(_spring_response(k, c, t)) > eps and t < 30:
        t += dt
    return t


@register
class SpringTo(Verb):
    """Spring a numeric property to a target with physical overshoot. Duration is derived unless given."""

    name = "spring_to"
    Params = SpringToParams
    uses_ease = False
    examples = [{"prop": "scale", "to": 1.2}, {"prop": "position", "to": [500, 300], "stiffness": 120, "damping": 12}]

    def mode(self, p: SpringToParams) -> Mode:
        return "abs"

    def props(self, p: SpringToParams) -> tuple[str, ...]:
        return expand_prop(p.prop)

    def default_duration(self, p: SpringToParams) -> float:
        return _settle_time(p.stiffness, p.damping)

    def _targets(self, p: SpringToParams) -> list[float]:
        n = len(self.props(p))
        if isinstance(p.to, list):
            return [float(v) for v in p.to] if len(p.to) == n else [float(p.to[0])] * n
        return [float(p.to)] * n

    def sample(self, p: SpringToParams, u: float, t: float, ctx: SampleContext) -> dict[str, Any]:
        tau = u * _settle_time(p.stiffness, p.damping)  # time-scaled when duration is given
        r = 0.0 if u >= 1 else _spring_response(p.stiffness, p.damping, tau)
        return {prop: to + (ctx.start[prop] - to) * r for prop, to in zip(self.props(p), self._targets(p))}

    def check_target(self, p: SpringToParams, actor: Actor | None) -> str | None:
        props = self.props(p)
        allowed = CAMERA_PROPS if actor is None else NUMERIC_PROPS
        bad = [x for x in props if x not in allowed]
        if bad:
            return f"spring_to can't animate {', '.join(bad)} on this actor"
        if isinstance(p.to, list) and len(p.to) not in (1, len(props)):
            return f"spring_to `to` has {len(p.to)} values but {p.prop} has {len(props)}"
        return None


class FollowParams(VerbParams):
    target: str = Field(description="actor id to follow")
    lag: float = Field(0.2, ge=0, le=5, description="seconds behind the target")
    offset: Vec2 = (0.0, 0.0)


@register
class Follow(Verb):
    """Trail another actor's position with a time lag. Blends in smoothly from the current position."""

    name = "follow"
    Params = FollowParams
    uses_ease = False
    examples = [{"target": "b", "lag": 0.3, "duration": 2}, {"target": "b", "lag": 0.1, "offset": [0, -60]}]

    def mode(self, p: FollowParams) -> Mode:
        return "abs"

    def props(self, p: FollowParams) -> tuple[str, ...]:
        return ("x", "y")

    def actor_refs(self, p: FollowParams) -> list[str]:
        return [p.target]

    def sample(self, p: FollowParams, u: float, t: float, ctx: SampleContext) -> dict[str, Any]:
        tt = ctx.t0 + t - p.lag
        tx = ctx.value(p.target, "x", tt) + p.offset[0]
        ty = ctx.value(p.target, "y", tt) + p.offset[1]
        blend = max(p.lag, 0.25)
        w = min(t / blend, 1.0)
        w = w * w * (3 - 2 * w)
        return {"x": _lerp(ctx.start["x"], tx, w), "y": _lerp(ctx.start["y"], ty, w)}


class HoldParams(VerbParams):
    pass


@register
class Hold(Verb):
    """Wait without changing anything. Omit `actor`."""

    name = "hold"
    Params = HoldParams
    needs_actor = False
    examples = [{"duration": 0.5}]

    def mode(self, p: HoldParams) -> None:
        return None

    def props(self, p: HoldParams) -> tuple[str, ...]:
        return ()

    def sample(self, p: HoldParams, u: float, t: float, ctx: SampleContext) -> dict[str, Any]:
        return {}
