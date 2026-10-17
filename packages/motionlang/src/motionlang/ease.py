"""Easing functions: map linear progress u ∈ [0, 1] to eased progress."""

from __future__ import annotations

import math
from typing import Any, Callable

EaseFn = Callable[[float], float]

_C1 = 1.70158
_C2 = _C1 * 1.525
_C3 = _C1 + 1
_C4 = (2 * math.pi) / 3
_C5 = (2 * math.pi) / 4.5


def _out_bounce(u: float) -> float:
    n1, d1 = 7.5625, 2.75
    if u < 1 / d1:
        return n1 * u * u
    if u < 2 / d1:
        u -= 1.5 / d1
        return n1 * u * u + 0.75
    if u < 2.5 / d1:
        u -= 2.25 / d1
        return n1 * u * u + 0.9375
    u -= 2.625 / d1
    return n1 * u * u + 0.984375


# "in" variants of each family; "out" and "inOut" are derived below.
_IN: dict[str, EaseFn] = {
    "Quad": lambda u: u * u,
    "Cubic": lambda u: u**3,
    "Quart": lambda u: u**4,
    "Sine": lambda u: 1 - math.cos(u * math.pi / 2),
    "Expo": lambda u: 0.0 if u == 0 else 2 ** (10 * u - 10),
    "Back": lambda u: _C3 * u**3 - _C1 * u * u,
    "Elastic": lambda u: (
        u if u in (0.0, 1.0) else -(2 ** (10 * u - 10)) * math.sin((u * 10 - 10.75) * _C4)
    ),
    "Bounce": lambda u: 1 - _out_bounce(1 - u),
}


def _out(f: EaseFn) -> EaseFn:
    return lambda u: 1 - f(1 - u)


def _in_out(f: EaseFn) -> EaseFn:
    return lambda u: f(2 * u) / 2 if u < 0.5 else 1 - f(2 - 2 * u) / 2


EASES: dict[str, EaseFn] = {"linear": lambda u: u}
for _family, _fn in _IN.items():
    EASES[f"in{_family}"] = _fn
    EASES[f"out{_family}"] = _out(_fn)
    EASES[f"inOut{_family}"] = _in_out(_fn)
# Back/Elastic in-out use their own canonical forms (overshoot scaled differently).
EASES["inOutBack"] = lambda u: (
    ((2 * u) ** 2 * ((_C2 + 1) * 2 * u - _C2)) / 2
    if u < 0.5
    else ((2 * u - 2) ** 2 * ((_C2 + 1) * (u * 2 - 2) + _C2) + 2) / 2
)
EASES["inOutElastic"] = lambda u: (
    u
    if u in (0.0, 1.0)
    else -(2 ** (20 * u - 10) * math.sin((20 * u - 11.125) * _C5)) / 2
    if u < 0.5
    else (2 ** (-20 * u + 10) * math.sin((20 * u - 11.125) * _C5)) / 2 + 1
)

DEFAULT_EASE = "inOutQuad"


def cubic_bezier(x1: float, y1: float, x2: float, y2: float) -> EaseFn:
    """CSS-style cubic-bezier timing function (endpoints fixed at 0,0 and 1,1)."""

    def bez(t: float, a: float, b: float) -> float:
        return 3 * a * (1 - t) ** 2 * t + 3 * b * (1 - t) * t * t + t**3

    def dbez(t: float, a: float, b: float) -> float:
        return 3 * a * (1 - t) ** 2 + 6 * (b - a) * (1 - t) * t + 3 * (1 - b) * t * t

    def f(u: float) -> float:
        if u <= 0:
            return 0.0
        if u >= 1:
            return 1.0
        t = u
        for _ in range(8):  # Newton, then fall back to bisection if it stalls
            d = dbez(t, x1, x2)
            if abs(d) < 1e-6:
                break
            t -= (bez(t, x1, x2) - u) / d
        if not 0 <= t <= 1 or abs(bez(t, x1, x2) - u) > 1e-5:
            lo, hi = 0.0, 1.0
            for _ in range(40):
                t = (lo + hi) / 2
                if bez(t, x1, x2) < u:
                    lo = t
                else:
                    hi = t
        return bez(t, y1, y2)

    return f


def resolve_ease(spec: Any) -> EaseFn:
    """Turn an ease spec (name or {"bezier": [x1, y1, x2, y2]}) into a function.

    Raises ValueError with a message suitable for an LLM repair loop.
    """
    if spec is None:
        return EASES[DEFAULT_EASE]
    if isinstance(spec, str):
        if spec in EASES:
            return EASES[spec]
        raise ValueError(f"unknown ease {spec!r}; use one of: {', '.join(sorted(EASES))}")
    if isinstance(spec, dict) and set(spec) == {"bezier"}:
        pts = spec["bezier"]
        if (
            isinstance(pts, list)
            and len(pts) == 4
            and all(isinstance(v, (int, float)) for v in pts)
            and 0 <= pts[0] <= 1
            and 0 <= pts[2] <= 1
        ):
            return cubic_bezier(*map(float, pts))
        raise ValueError("bezier ease needs [x1, y1, x2, y2] with x1, x2 in [0, 1]")
    raise ValueError('ease must be a name like "inOutCubic" or {"bezier": [x1, y1, x2, y2]}')
