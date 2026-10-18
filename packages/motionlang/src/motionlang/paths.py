"""Path geometry: polylines with arc-length parameterization, SVG path flattening, splines."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

import numpy as np

Point = tuple[float, float]

CURVE_SEGMENTS = 24  # samples per curve segment when flattening


@dataclass
class Polyline:
    """A flattened path, parameterized by arc length for constant-speed travel."""

    pts: np.ndarray  # (N, 2)

    def __post_init__(self) -> None:
        self.pts = np.asarray(self.pts, dtype=float).reshape(-1, 2)
        if len(self.pts) == 0:
            raise ValueError("path has no points")
        d = np.diff(self.pts, axis=0)
        seg = np.linalg.norm(d, axis=1)
        self.cum = np.concatenate([[0.0], np.cumsum(seg)])
        # Unwrapped heading per segment, so turning through ±180° stays continuous.
        keep = seg > 1e-9
        heads = np.unwrap(np.arctan2(d[keep, 1], d[keep, 0])) if keep.any() else np.zeros(0)
        self._heads = np.degrees(heads)
        self._head_s = (self.cum[:-1][keep] + self.cum[1:][keep]) / 2  # heading sampled at segment midpoints

    @property
    def length(self) -> float:
        return float(self.cum[-1])

    @property
    def start(self) -> Point:
        return float(self.pts[0, 0]), float(self.pts[0, 1])

    def _locate(self, u: float) -> tuple[int, float]:
        s = min(max(u, 0.0), 1.0) * self.length
        i = int(np.searchsorted(self.cum, s, side="right")) - 1
        i = min(max(i, 0), len(self.pts) - 2) if len(self.pts) > 1 else 0
        seg = self.cum[i + 1] - self.cum[i] if len(self.pts) > 1 else 0.0
        return i, (s - self.cum[i]) / seg if seg > 0 else 0.0

    def point_at(self, u: float) -> Point:
        """Point at fraction u of total arc length."""
        if len(self.pts) == 1 or self.length == 0:
            return self.start
        i, f = self._locate(u)
        p = self.pts[i] + (self.pts[i + 1] - self.pts[i]) * f
        return float(p[0]), float(p[1])

    def angle_at(self, u: float) -> float:
        """Unwrapped tangent direction in degrees (clockwise-positive in y-down space).

        Unwrapped means a spiral keeps increasing past 180° instead of jumping to -180°.
        """
        if len(self._heads) == 0:
            return 0.0
        s = min(max(u, 0.0), 1.0) * self.length
        return float(np.interp(s, self._head_s, self._heads))

    def translated(self, dx: float, dy: float) -> "Polyline":
        return Polyline(self.pts + np.array([dx, dy]))

    def reversed(self) -> "Polyline":
        return Polyline(self.pts[::-1].copy())

    def bbox(self) -> tuple[float, float, float, float]:
        mn, mx = self.pts.min(axis=0), self.pts.max(axis=0)
        return float(mn[0]), float(mn[1]), float(mx[0]), float(mx[1])


def catmull_rom(points: list[Point], samples: int = 12) -> np.ndarray:
    """Centripetal Catmull-Rom through all points (keeps sketch strokes smooth without overshoot)."""
    p = np.asarray(points, dtype=float).reshape(-1, 2)
    if len(p) < 3:
        return p
    ext = np.vstack([2 * p[0] - p[1], p, 2 * p[-1] - p[-2]])
    out = [p[0]]
    for i in range(1, len(ext) - 2):
        p0, p1, p2, p3 = ext[i - 1], ext[i], ext[i + 1], ext[i + 2]

        def tj(ti: float, a: np.ndarray, b: np.ndarray) -> float:
            return ti + max(float(np.linalg.norm(b - a)) ** 0.5, 1e-6)

        t0 = 0.0
        t1 = tj(t0, p0, p1)
        t2 = tj(t1, p1, p2)
        t3 = tj(t2, p2, p3)
        for t in np.linspace(t1, t2, samples + 1)[1:]:
            a1 = (t1 - t) / (t1 - t0) * p0 + (t - t0) / (t1 - t0) * p1
            a2 = (t2 - t) / (t2 - t1) * p1 + (t - t1) / (t2 - t1) * p2
            a3 = (t3 - t) / (t3 - t2) * p2 + (t - t2) / (t3 - t2) * p3
            b1 = (t2 - t) / (t2 - t0) * a1 + (t - t0) / (t2 - t0) * a2
            b2 = (t3 - t) / (t3 - t1) * a2 + (t - t1) / (t3 - t1) * a3
            out.append((t2 - t) / (t2 - t1) * b1 + (t - t1) / (t2 - t1) * b2)
    return np.asarray(out)


def arc_points(center: Point, radius: float, start_deg: float, end_deg: float) -> np.ndarray:
    n = max(8, int(abs(end_deg - start_deg) / 360 * 96))
    th = np.radians(np.linspace(start_deg, end_deg, n + 1))
    return np.stack([center[0] + radius * np.cos(th), center[1] + radius * np.sin(th)], axis=1)


_TOKEN = re.compile(r"[MmLlHhVvCcSsQqTtZzAa]|[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
_ARITY = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7, "Z": 0}

Segment = tuple[str, list[Point]]  # ("M"|"L"|"C"|"Q"|"Z", absolute points)


def _arc_to_cubics(p0: Point, rx: float, ry: float, phi_deg: float, large: bool, sweep: bool, p1: Point) -> list[list[Point]]:
    """SVG elliptical arc (endpoint form) → cubic Béziers, one per ≤90° (SVG spec F.6.5)."""
    if p0 == p1:
        return []
    rx, ry = abs(rx), abs(ry)
    if rx < 1e-12 or ry < 1e-12:
        return [[p0, p1, p1]]  # degenerate: straight line, kept as a cubic so callers stay uniform
    phi = math.radians(phi_deg % 360)
    cp, sp = math.cos(phi), math.sin(phi)
    dx, dy = (p0[0] - p1[0]) / 2, (p0[1] - p1[1]) / 2
    x1, y1 = cp * dx + sp * dy, -sp * dx + cp * dy
    lam = (x1 / rx) ** 2 + (y1 / ry) ** 2
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num = rx * rx * ry * ry - rx * rx * y1 * y1 - ry * ry * x1 * x1
    den = rx * rx * y1 * y1 + ry * ry * x1 * x1
    k = math.sqrt(max(num, 0.0) / den) if den else 0.0
    if large == sweep:
        k = -k
    cx1, cy1 = k * rx * y1 / ry, -k * ry * x1 / rx
    cx = cp * cx1 - sp * cy1 + (p0[0] + p1[0]) / 2
    cy = sp * cx1 + cp * cy1 + (p0[1] + p1[1]) / 2

    def ang(ux: float, uy: float, vx: float, vy: float) -> float:
        return math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)

    th1 = ang(1, 0, (x1 - cx1) / rx, (y1 - cy1) / ry)
    dth = ang((x1 - cx1) / rx, (y1 - cy1) / ry, (-x1 - cx1) / rx, (-y1 - cy1) / ry)
    if not sweep and dth > 0:
        dth -= 2 * math.pi
    elif sweep and dth < 0:
        dth += 2 * math.pi
    n = max(1, math.ceil(abs(dth) / (math.pi / 2) - 1e-9))
    step = dth / n
    t = 4 / 3 * math.tan(step / 4)

    def pt(th: float) -> Point:
        x, y = rx * math.cos(th), ry * math.sin(th)
        return (cp * x - sp * y + cx, sp * x + cp * y + cy)

    def deriv(th: float) -> Point:
        x, y = -rx * math.sin(th), ry * math.cos(th)
        return (cp * x - sp * y, sp * x + cp * y)

    out = []
    a = th1
    start = p0
    for i in range(n):
        b = a + step
        end = p1 if i == n - 1 else pt(b)
        da, db = deriv(a), deriv(b)
        out.append([(start[0] + t * da[0], start[1] + t * da[1]), (end[0] - t * db[0], end[1] - t * db[1]), end])
        a, start = b, end
    return out


def parse_path(d: str) -> list[Segment]:
    """Parse SVG path data (all commands, absolute and relative) into absolute M/L/C/Q/Z segments.

    H/V become L, S/T become C/Q with explicit control points, and arcs (A) become cubics.
    """
    tokens = _TOKEN.findall(d)
    if not tokens:
        raise ValueError("empty SVG path")
    segs: list[Segment] = []
    cur = (0.0, 0.0)
    start = (0.0, 0.0)
    last_ctrl: Point | None = None
    last_cmd = ""
    i = 0
    cmd = ""
    while i < len(tokens):
        tok = tokens[i]
        if tok.isalpha():
            cmd = tok
            i += 1
            if cmd in "Zz":
                segs.append(("Z", []))
                cur = start
                last_cmd, last_ctrl = "Z", None
                continue
        elif not cmd:
            raise ValueError("SVG path must start with a command")
        elif cmd in "Zz":
            raise ValueError("SVG path has numbers after Z without a command")
        up = cmd.upper()
        n = _ARITY[up]
        arg_tokens = tokens[i : i + n]
        if len(arg_tokens) < n or any(t.isalpha() for t in arg_tokens):
            raise ValueError(f"SVG path command {cmd} needs {n} numbers")
        args = [float(v) for v in arg_tokens]
        i += n
        rel = cmd.islower()
        ox, oy = cur if rel else (0.0, 0.0)

        if up == "M":
            cur = (args[0] + ox, args[1] + oy)
            start = cur
            segs.append(("M", [cur]))
            cmd = "l" if rel else "L"  # implicit lineto after moveto
            last_ctrl = None
        elif up in "LHV":
            if up == "L":
                cur = (args[0] + ox, args[1] + oy)
            elif up == "H":
                cur = (args[0] + ox, cur[1])
            else:
                cur = (cur[0], args[0] + (cur[1] if rel else 0.0))
            segs.append(("L", [cur]))
            last_ctrl = None
        elif up in "CS":
            if up == "C":
                c1 = (args[0] + ox, args[1] + oy)
                c2, end = (args[2] + ox, args[3] + oy), (args[4] + ox, args[5] + oy)
            else:
                c1 = (2 * cur[0] - last_ctrl[0], 2 * cur[1] - last_ctrl[1]) if last_cmd in "CS" and last_ctrl else cur
                c2, end = (args[0] + ox, args[1] + oy), (args[2] + ox, args[3] + oy)
            segs.append(("C", [c1, c2, end]))
            cur, last_ctrl = end, c2
        elif up in "QT":
            if up == "Q":
                c1, end = (args[0] + ox, args[1] + oy), (args[2] + ox, args[3] + oy)
            else:
                c1 = (2 * cur[0] - last_ctrl[0], 2 * cur[1] - last_ctrl[1]) if last_cmd in "QT" and last_ctrl else cur
                end = (args[0] + ox, args[1] + oy)
            segs.append(("Q", [c1, end]))
            cur, last_ctrl = end, c1
        elif up == "A":
            end = (args[5] + ox, args[6] + oy)
            for c in _arc_to_cubics(cur, args[0], args[1], args[2], bool(args[3]), bool(args[4]), end):
                segs.append(("C", c))
            cur, last_ctrl = end, None
        last_cmd = up
    return segs


def flatten_svg_path(d: str) -> np.ndarray:
    """Flatten an SVG path `d` string (any command, absolute or relative) to points.

    Subpaths are concatenated in order.
    """
    out: list[Point] = []
    cur = (0.0, 0.0)
    start = (0.0, 0.0)
    for cmd, pts in parse_path(d):
        if cmd == "M":
            cur = start = pts[0]
            out.append(cur)
        elif cmd == "L":
            cur = pts[0]
            out.append(cur)
        elif cmd == "Z":
            if out:
                out.append(start)
            cur = start
        else:
            if not out:
                out.append(cur)
            ctrl = [cur, *pts]
            for t in np.linspace(0, 1, CURVE_SEGMENTS + 1)[1:]:
                if len(ctrl) == 3:
                    (x0, y0), (x1, y1), (x2, y2) = ctrl
                    x = (1 - t) ** 2 * x0 + 2 * (1 - t) * t * x1 + t * t * x2
                    y = (1 - t) ** 2 * y0 + 2 * (1 - t) * t * y1 + t * t * y2
                else:
                    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = ctrl
                    x = (1 - t) ** 3 * x0 + 3 * (1 - t) ** 2 * t * x1 + 3 * (1 - t) * t * t * x2 + t**3 * x3
                    y = (1 - t) ** 3 * y0 + 3 * (1 - t) ** 2 * t * y1 + 3 * (1 - t) * t * t * y2 + t**3 * y3
                out.append((float(x), float(y)))
            cur = pts[-1]
    if not out:
        raise ValueError("SVG path has no points")
    return np.asarray(out, dtype=float)


def _num(v: float, precision: int) -> str:
    s = f"{v:.{precision}f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def format_path(segs: list[Segment], precision: int = 2) -> str:
    return " ".join(cmd + " ".join(f"{_num(x, precision)},{_num(y, precision)}" for x, y in pts) for cmd, pts in segs)


def transform_path(d: str, m: tuple[float, float, float, float, float, float], precision: int = 2) -> str:
    """Apply an affine matrix (SVG order a b c d e f) to path data; returns absolute M/L/C/Q/Z data.

    Exact for every command, because arcs are converted to cubics before transforming.
    """
    a, b, c, dd, e, f = m
    segs = [(cmd, [(a * x + c * y + e, b * x + dd * y + f) for x, y in pts]) for cmd, pts in parse_path(d)]
    return format_path(segs, precision)


def path_bbox(d: str) -> tuple[float, float, float, float]:
    """(x, y, w, h) of the flattened path."""
    pts = flatten_svg_path(d)
    mn, mx = pts.min(axis=0), pts.max(axis=0)
    return float(mn[0]), float(mn[1]), float(mx[0] - mn[0]), float(mx[1] - mn[1])


def resolve_path(spec: dict, strokes: dict[str, "list[Point]"]) -> Polyline:
    """Resolve a path spec ({stroke}|{points}|{svg}|{arc}) to a Polyline. Raises ValueError/KeyError."""
    if not isinstance(spec, dict) or len(spec.keys() - {"smooth"}) != 1:
        raise ValueError('path must be exactly one of {"stroke"}, {"points"}, {"svg"}, {"arc"}')
    if "stroke" in spec:
        ref = spec["stroke"]
        if ref not in strokes:
            raise KeyError(f"stroke {ref!r} is not defined in document.strokes")
        pts = strokes[ref]
        return Polyline(catmull_rom(pts) if spec.get("smooth", True) else np.asarray(pts, dtype=float))
    if "points" in spec:
        pts = spec["points"]
        if len(pts) < 1:
            raise ValueError("points path needs at least one point")
        return Polyline(catmull_rom(pts) if spec.get("smooth", False) else np.asarray(pts, dtype=float))
    if "svg" in spec:
        return Polyline(flatten_svg_path(spec["svg"]))
    if "arc" in spec:
        a = spec["arc"]
        return Polyline(arc_points(tuple(a["center"]), float(a["radius"]), float(a.get("from", 0)), float(a.get("to", 360))))
    raise ValueError('path must be exactly one of {"stroke"}, {"points"}, {"svg"}, {"arc"}')
