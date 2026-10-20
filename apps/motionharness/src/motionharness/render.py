"""Draw baked tracks (plain data from the sandbox) into images a vision model can critique.

contact_sheet(): one 1280×720 image with an onion-skin of the whole motion, a strip of keyframes and
curves of position / scale / rotation over time for the actors the new verb moves. Motion is easier to
judge from trails and curves than from a single still ("does it overshoot?", "does it settle?").
"""

from __future__ import annotations

import io
import math
import re
from typing import Any  # noqa: F401

from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 720
INK, MUTED, ACCENT, GRID = (30, 32, 38), (120, 124, 135), (118, 185, 0), (228, 230, 235)  # accent: NVIDIA green
SERIES = [(228, 87, 46), (41, 115, 178), (118, 185, 0), (155, 89, 182), (243, 156, 18)]


def _font(size: int):
    for name in ("DejaVuSans.ttf", "Arial.ttf", "Helvetica.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _matrix(a: dict, i: int) -> list[float]:
    m = a["tracks"]["m"]
    if m and isinstance(m[0], list):
        return m[min(i, len(m) - 1)]
    return m


def _opacity(a: dict, i: int) -> float:
    v = a["tracks"].get("opacity", 1.0)
    return float(v[min(i, len(v) - 1)] if isinstance(v, list) else v)


def _color(a: dict, key: str, i: int) -> tuple[int, int, int] | None:
    v = a["tracks"].get(key)
    if isinstance(v, list):
        v = v[min(i, len(v) - 1)]
    if isinstance(v, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", v):
        return tuple(int(v[k : k + 2], 16) for k in (1, 3, 5))  # type: ignore[return-value]
    return None


_NUM = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
_CMD = re.compile(r"[MmLlHhVvCcSsQqTtZzAa]|[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")


def path_polylines(d: str, steps: int = 12) -> list[list[tuple[float, float]]]:
    """Flatten SVG path data (M L H V C S Q T Z; A as a straight line) into polylines."""
    toks = _CMD.findall(d)
    polys: list[list[tuple[float, float]]] = []
    cur: list[tuple[float, float]] = []
    x = y = sx = sy = 0.0
    last_c: tuple[float, float] | None = None
    cmd = ""
    i = 0

    def nums(n: int) -> list[float]:
        nonlocal i
        out = [float(t) for t in toks[i : i + n]]
        i += n
        return out

    while i < len(toks):
        if re.fullmatch(r"[A-Za-z]", toks[i]):
            cmd = toks[i]
            i += 1
            if cmd in "Zz":
                if cur:
                    cur.append((sx, sy))
                x, y = sx, sy
                continue
        if i >= len(toks) or re.fullmatch(r"[A-Za-z]", toks[i]):
            continue
        rel = cmd.islower()
        c = cmd.upper()
        ox, oy = (x, y) if rel else (0.0, 0.0)
        if c == "M":
            px, py = nums(2)
            x, y = ox + px, oy + py
            if cur:
                polys.append(cur)
            cur = [(x, y)]
            sx, sy = x, y
            cmd = "l" if rel else "L"
        elif c == "L":
            px, py = nums(2)
            x, y = ox + px, oy + py
            cur.append((x, y))
        elif c == "H":
            (px,) = nums(1)
            x = (x if rel else 0) + px
            cur.append((x, y))
        elif c == "V":
            (py,) = nums(1)
            y = (y if rel else 0) + py
            cur.append((x, y))
        elif c in "CSQT":
            n = {"C": 6, "S": 4, "Q": 4, "T": 2}[c]
            v = nums(n)
            pts = [(ox + v[k], oy + v[k + 1]) for k in range(0, n, 2)]
            if c in "ST":
                refl = (2 * x - last_c[0], 2 * y - last_c[1]) if last_c else (x, y)
                pts = [refl, *pts]
            p0 = (x, y)
            for s in range(1, steps + 1):
                u = s / steps
                if len(pts) == 3:  # cubic
                    a, b, e = pts
                    q = [(1 - u) ** 3 * p0[k] + 3 * (1 - u) ** 2 * u * a[k] + 3 * (1 - u) * u * u * b[k] + u ** 3 * e[k] for k in (0, 1)]
                else:  # quadratic
                    a, e = pts
                    q = [(1 - u) ** 2 * p0[k] + 2 * (1 - u) * u * a[k] + u * u * e[k] for k in (0, 1)]
                cur.append((q[0], q[1]))
            last_c = pts[-2]
            x, y = pts[-1]
            continue
        elif c == "A":
            v = nums(7)
            x, y = ox + v[5], oy + v[6]
            cur.append((x, y))
        else:
            i += 1
        last_c = None
    if cur:
        polys.append(cur)
    return [p for p in polys if len(p) > 1]


def _local_shapes(a: dict) -> tuple[list[list[tuple[float, float]]], bool]:
    """Shape outline(s) in bbox-local coordinates, and whether they're closed (fillable)."""
    g = a["geom"]
    if g.get("tag") == "ellipse":
        rx, ry = float(g["rx"]), float(g["ry"])
        return [[(rx + rx * math.cos(k / 32 * 2 * math.pi), ry + ry * math.sin(k / 32 * 2 * math.pi)) for k in range(33)]], True
    if g.get("tag") == "path" and g.get("d"):
        return path_polylines(g["d"]), bool(g.get("closed", True))
    w, h = float(g.get("w", g.get("bw", 20))), float(g.get("h", g.get("bh", 20)))
    return [[(0, 0), (w, 0), (w, h), (0, h), (0, 0)]], True


def _apply(m: list[float], p: tuple[float, float]) -> tuple[float, float]:
    a, b, c, d, e, f = m
    return a * p[0] + c * p[1] + e, b * p[0] + d * p[1] + f


def _center(a: dict, i: int) -> tuple[float, float]:
    g = a["geom"]
    return _apply(_matrix(a, i), (float(g.get("bw", 0)) / 2, float(g.get("bh", 0)) / 2))


def _blend(c: tuple[int, int, int], alpha: float, bg=(255, 255, 255)) -> tuple[int, int, int]:
    return tuple(int(bg[k] + (c[k] - bg[k]) * alpha) for k in range(3))  # type: ignore[return-value]


def _bg(tracks: dict) -> tuple[int, int, int]:
    v = (tracks.get("canvas") or {}).get("background") or "#ffffff"
    return tuple(int(v[k : k + 2], 16) for k in (1, 3, 5)) if re.fullmatch(r"#[0-9a-fA-F]{6}", v) else (255, 255, 255)  # type: ignore[return-value]


def draw_scene(draw: ImageDraw.ImageDraw, tracks: dict, frames: list[int], box: tuple[int, int, int, int], ghost: bool) -> None:
    """Draw the given frames into box (x0, y0, x1, y1), fitted to the canvas; earlier frames fainter when ghosting."""
    cw, ch = tracks["canvas"]["width"], tracks["canvas"]["height"]
    x0, y0, x1, y1 = box
    s = min((x1 - x0) / cw, (y1 - y0) / ch)
    ox, oy = x0 + ((x1 - x0) - cw * s) / 2, y0 + ((y1 - y0) - ch * s) / 2
    bg = _bg(tracks)
    draw.rectangle([ox, oy, ox + cw * s, oy + ch * s], fill=bg, outline=GRID)
    tf = lambda p: (ox + p[0] * s, oy + p[1] * s)  # noqa: E731
    for n, i in enumerate(frames):
        alpha = 0.15 + 0.85 * (n + 1) / len(frames) if ghost else 1.0
        for a in tracks["actors"]:
            op = _opacity(a, i) * alpha
            if op <= 0.01:
                continue
            polys, closed = _local_shapes(a)
            m = _matrix(a, i)
            fill, stroke = _color(a, "fill", i), _color(a, "stroke", i)
            for poly in polys:
                pts = [tf(_apply(m, p)) for p in poly]
                if closed and fill and len(pts) > 2:
                    draw.polygon(pts, fill=_blend(fill, op, bg))
                col = stroke or (fill if not closed else None)
                if col:
                    draw.line(pts, fill=_blend(col, op, bg), width=max(1, int(a["style"].get("stroke_width", 2) * s)))
                elif closed and not fill:
                    draw.line(pts, fill=_blend(INK, op, bg), width=1)


def _verb_actors(tracks: dict, verb: str | None) -> tuple[list[str], list[tuple[float, float]]]:
    tw = [t for t in tracks.get("tweens", []) if verb is None or t["verb"] == verb]
    ids = list(dict.fromkeys(t["actor"] for t in tw))
    return ids, [(t["start"], t["end"]) for t in tw]


def _curves(draw, tracks: dict, ids: list[str], spans, box, font, small) -> None:
    """Small multiples: x, y, scale, rotation over time for the actors the verb drives."""
    x0, y0, x1, y1 = box
    n = len(tracks["actors"][0]["tracks"]["m"]) if tracks["actors"] and isinstance(tracks["actors"][0]["tracks"]["m"][0], list) else tracks["frames"]
    n = max(tracks.get("frames", n), 1)
    dur = max(tracks.get("duration", 1.0), 1e-6)
    actors = {a["id"]: a for a in tracks["actors"]}
    series = {"x": [], "y": [], "scale": [], "rotation°": []}
    for aid in ids[:3]:
        a = actors.get(aid)
        if not a:
            continue
        xs, ys, ss, rs = [], [], [], []
        for i in range(n):
            m = _matrix(a, i)
            cx, cy = _center(a, i)
            xs.append(cx)
            ys.append(cy)
            ss.append(math.hypot(m[0], m[1]))
            rs.append(math.degrees(math.atan2(m[1], m[0])))
        for k, v in zip(series, (xs, ys, ss, rs)):
            series[k].append((aid, v))
    rows = [k for k in series if any(max(v) - min(v) > NOISE.get(k, 1e-3) for _, v in series[k])] or ["x", "y"]
    ph = (y1 - y0) / len(rows)
    for r, key in enumerate(rows):
        top, bot = y0 + r * ph + 18, y0 + (r + 1) * ph - 8
        draw.text((x0, top - 17), "y (screen, up is up)" if key == "y" else key, fill=MUTED, font=small)
        draw.rectangle([x0, top, x1, bot], outline=GRID)
        for t0, t1 in spans:
            draw.rectangle([x0 + (x1 - x0) * t0 / dur, top + 1, x0 + (x1 - x0) * t1 / dur, bot - 1], fill=(240, 247, 228))
        vals = [v for _, v in series[key]]
        if not vals:
            continue
        lo, hi = min(min(v) for v in vals), max(max(v) for v in vals)
        if hi - lo < 1e-6:
            lo, hi = lo - 1, hi + 1
        up = key != "y"  # screen y grows downward; draw it so "up" on the chart is up on screen
        draw.text((x1 - 70, top + 2), f"{hi if up else lo:.4g}", fill=MUTED, font=small)
        draw.text((x1 - 70, bot - 14), f"{lo if up else hi:.4g}", fill=MUTED, font=small)
        for k, (aid, v) in enumerate(series[key]):
            frac = lambda val: (val - lo) / (hi - lo) if up else (hi - val) / (hi - lo)  # noqa: E731
            pts = [(x0 + (x1 - x0) * i / max(n - 1, 1), bot - (bot - top) * frac(val)) for i, val in enumerate(v)]
            draw.line(pts, fill=SERIES[k % len(SERIES)], width=2)
    for k, aid in enumerate(ids[:3]):
        draw.text((x0 + 90 * k, y1 + 2), f"■ {aid}", fill=SERIES[k % len(SERIES)], font=small)
    draw.text((x0, y1 + 18), f"t = 0 … {dur:.2f}s (green: the verb is active)", fill=MUTED, font=small)


def contact_sheet(tracks: dict, title: str = "", verb: str | None = None) -> Image.Image:
    img = Image.new("RGB", (W, H), (250, 250, 252))
    draw = ImageDraw.Draw(img)
    font, small = _font(18), _font(13)
    n = max(int(tracks.get("frames", 1)), 1)
    draw.text((20, 12), title[:110], fill=INK, font=font)
    # onion skin + pivot trails
    box = (20, 44, 820, 494)
    ghosts = sorted({round(k * (n - 1) / 9) for k in range(10)})
    draw_scene(draw, tracks, ghosts, box, ghost=True)
    cw, ch = tracks["canvas"]["width"], tracks["canvas"]["height"]
    s = min((box[2] - box[0]) / cw, (box[3] - box[1]) / ch)
    ox, oy = box[0] + ((box[2] - box[0]) - cw * s) / 2, box[1] + ((box[3] - box[1]) - ch * s) / 2
    ids, spans = _verb_actors(tracks, verb)
    for k, aid in enumerate(ids[:3]):
        a = next((x for x in tracks["actors"] if x["id"] == aid), None)
        if a:
            trail = [(ox + c[0] * s, oy + c[1] * s) for c in (_center(a, i) for i in range(n))]
            draw.line(trail, fill=SERIES[k % len(SERIES)], width=2)
    draw.text((box[0], box[3] + 4), "onion skin: 10 poses, faint = early; lines = pivot paths", fill=MUTED, font=small)
    # keyframe strip
    strip = [round(k * (n - 1) / 5) for k in range(6)]
    fw = (W - 40 - 5 * 10) / 6
    for k, i in enumerate(strip):
        x = 20 + k * (fw + 10)
        draw_scene(draw, tracks, [i], (int(x), 540, int(x + fw), 690), ghost=False)
        draw.text((x, 694), f"t={i / max(tracks.get('fps', 60), 1):.2f}s", fill=MUTED, font=small)
    others = [a["id"] for a in tracks["actors"] if a["id"] not in ids and _moves(a)]
    _curves(draw, tracks, (ids + others)[:3] or [a["id"] for a in tracks["actors"][:2]], spans, (850, 64, 1260, 470), font, small)
    return img


# Below these spans a property is rounding noise in the baked matrices (3 decimals), not motion.
NOISE = {"x": 0.5, "y": 0.5, "scale": 0.01, "rotation": 0.5, "rotation°": 0.5}


def _moves(a: dict) -> bool:
    m = a["tracks"]["m"]
    return bool(m) and isinstance(m[0], list) and len({tuple(x) for x in m}) > 1


def _series(a: dict, n: int) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {"x": [], "y": [], "scale": [], "rotation": []}
    for i in range(n):
        m = _matrix(a, i)
        cx, cy = _center(a, i)
        out["x"].append(cx)
        out["y"].append(cy)
        out["scale"].append(math.hypot(m[0], m[1]))
        out["rotation"].append(math.degrees(math.atan2(m[1], m[0])))
    return out


def motion_metrics(tracks: dict, verb: str | None = None) -> list[str]:
    """Plain numbers about the demo for the critic and the polish step: travel, overshoot past the final value,
    and when each prop settles (within 2% of its travel) for every actor that moves."""
    n = max(int(tracks.get("frames", 1)), 1)
    fps = max(tracks.get("fps", 60), 1)
    ids, _ = _verb_actors(tracks, verb)
    lines = []
    for a in tracks["actors"]:
        if not _moves(a):
            continue
        role = "runs the verb" if a["id"] in ids else "other actor"
        for prop, v in _series(a, n).items():
            lo, hi, start, end = min(v), max(v), v[0], v[-1]
            travel = abs(end - start)
            span = hi - lo
            if span < NOISE.get(prop, 1e-3):
                continue
            tol = 0.02 * max(travel, span)
            settle = next((i for i in range(n) if all(abs(x - end) <= tol for x in v[i:])), n - 1) / fps
            head = f"{a['id']} ({role}) {prop}: {start:.4g} → {end:.4g}, range [{lo:.4g}, {hi:.4g}]"
            if travel < 0.25 * span:
                # It swings and comes back (a wobble, a pulse): "overshoot past the final value" would divide by ~0.
                peak = max(hi - start, start - lo)
                back = "returns to its start" if travel <= tol else f"ends {end - start:+.4g} from its start"
                lines.append(f"{head}, oscillates: peak deviation ±{peak:.4g} from the start, {back}, settles at t={settle:.2f}s")
            else:
                past = (hi - end) if end >= start else (end - lo)
                lines.append(f"{head}, overshoot past the final value {100 * past / travel:.1f}%, settles at t={settle:.2f}s")
    return lines[:12]


def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def jpeg_data_url(img: Image.Image, quality: int = 85) -> str:
    import base64

    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
