"""Formation sampling: a shape (preset, stroke, SVG, text) → N drone positions in metres.

Every shape is rasterized to a mask, so one sampler serves all of them: `fill` spreads drones over the
mask, `outline` over its boundary (or over the line itself for open strokes). Points are spread with
k-means and then pushed apart, so spacing is close to even and the separation check rarely trips on a
well-sized formation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont
from scipy.cluster.vq import kmeans2
from scipy.spatial import cKDTree

from motionlang.color import parse_color, to_hex
from motionlang.importers.svg import import_svg
from motionlang.paths import flatten_svg_path

from .show import Formation, Limits, Sources

RES = 480  # raster size of the longer side
MAX_CANDIDATES = 24000

Contour = tuple[np.ndarray, bool]  # (points (K, 2) in shape units, y down; closed?)


@dataclass
class Sampled:
    points: np.ndarray  # (N, 2) metres [x, z]
    colors: list[str] | None  # per-drone LED colour from the artwork, if any
    min_spacing: float  # smallest nearest-neighbour distance, m


# ---------- shapes as contours (y down, any scale) ----------

def _preset(shape: str) -> tuple[list[Contour], bool]:
    """Returns contours and whether the shape is naturally an open line."""
    t = np.linspace(0, 2 * math.pi, 400, endpoint=False)
    if shape == "heart":
        x = 16 * np.sin(t) ** 3
        y = -(13 * np.cos(t) - 5 * np.cos(2 * t) - 2 * np.cos(3 * t) - np.cos(4 * t))
        return [(np.c_[x, y], True)], False
    if shape == "star":
        a = np.arange(10) * math.pi / 5 - math.pi / 2
        r = np.where(np.arange(10) % 2 == 0, 1.0, 0.45)
        return [(np.c_[r * np.cos(a), r * np.sin(a)], True)], False
    if shape in ("ring", "circle"):
        return [(np.c_[np.cos(t), np.sin(t)], True)], False
    if shape == "grid":
        return [(np.array([[0, 0], [1.6, 0], [1.6, 1], [0, 1]], float), True)], False
    if shape == "spiral":
        s = np.linspace(0, 3 * 2 * math.pi, 600)
        return [(np.c_[s * np.cos(s), s * np.sin(s)], False)], True
    if shape == "wave":
        s = np.linspace(0, 4 * math.pi, 400)
        return [(np.c_[s, -np.sin(s) * 2.5], False)], True
    raise ValueError(f"unknown preset {shape!r}")


def _svg_layers(text: str) -> list[tuple[list[Contour], str | None, bool]]:
    """One layer per imported asset: (contours, fill colour or None, filled?)."""
    imp = import_svg(text)
    layers = []
    for a in imp["assets"].values():
        subs = [s for s in a["d"].split("M") if s.strip()]
        contours = []
        for s in subs:
            try:
                pts = flatten_svg_path("M" + s)
            except ValueError:
                continue
            if len(pts) >= 2:
                contours.append((pts, s.rstrip().upper().endswith("Z")))
        fill = a.get("fill")
        filled = bool(fill) and fill != "none"
        color = fill if filled else a.get("stroke")
        try:
            color = to_hex(parse_color(color)) if color and color != "none" else None
        except ValueError:
            color = None
        if contours:
            layers.append((contours, color, filled))
    if not layers:
        raise ValueError("the SVG has no drawable shapes")
    return layers


# ---------- rasterize ----------

def _bounds(contours: list[Contour]) -> tuple[float, float, float, float]:
    allp = np.vstack([c for c, _ in contours])
    return float(allp[:, 0].min()), float(allp[:, 1].min()), float(allp[:, 0].max()), float(allp[:, 1].max())


def _raster(layers: list[tuple[list[Contour], str | None, bool]], line_frac: float = 0.012):
    """Mask (filled area), line mask (open lines/strokes) and a colour image, all RES-sized."""
    x0, y0, x1, y1 = _bounds([c for cs, _, _ in layers for c in cs])
    w, h = max(x1 - x0, 1e-9), max(y1 - y0, 1e-9)
    k = (RES - 8) / max(w, h)
    W, H = int(w * k) + 8, int(h * k) + 8
    to_px = lambda p: [(float((x - x0) * k + 4), float((y - y0) * k + 4)) for x, y in p]  # noqa: E731
    fill = Image.new("1", (W, H), 0)
    line = Image.new("1", (W, H), 0)
    color = Image.new("RGB", (W, H), (255, 255, 255))
    lw = max(2, int(RES * line_frac))
    for contours, col, filled in layers:
        if filled:
            layer = Image.new("1", (W, H), 0)
            for pts, _ in contours:
                if len(pts) < 3:
                    continue
                one = Image.new("1", (W, H), 0)
                ImageDraw.Draw(one).polygon(to_px(pts), fill=1)
                layer = ImageChops.logical_xor(layer, one)  # even-odd: holes stay holes
            fill = ImageChops.logical_or(fill, layer)
            if col:
                color.paste(parse_rgb(col), mask=layer)
        else:
            d = ImageDraw.Draw(line)
            for pts, closed in contours:
                p = to_px(pts)
                d.line(p + ([p[0]] if closed else []), fill=1, width=lw, joint="curve")
            if col:
                cl = Image.new("1", (W, H), 0)
                dd = ImageDraw.Draw(cl)
                for pts, closed in contours:
                    p = to_px(pts)
                    dd.line(p + ([p[0]] if closed else []), fill=1, width=lw)
                color.paste(parse_rgb(col), mask=cl)
    return fill, line, color


def parse_rgb(hex_color: str) -> tuple[int, int, int]:
    r, g, b = parse_color(hex_color)
    return int(round(r * 255)), int(round(g * 255)), int(round(b * 255))


def _text_raster(text: str):
    font = ImageFont.load_default(RES // 3)
    probe = ImageDraw.Draw(Image.new("1", (1, 1)))
    x0, y0, x1, y1 = probe.textbbox((0, 0), text, font=font)
    W, H = x1 - x0 + 16, y1 - y0 + 16
    mask = Image.new("1", (W, H), 0)
    ImageDraw.Draw(mask).text((8 - x0, 8 - y0), text, fill=1, font=font)
    return mask, Image.new("1", (W, H), 0), None


def _boundary(mask: Image.Image) -> Image.Image:
    eroded = mask.convert("L").filter(ImageFilter.MinFilter(3)).point(lambda v: 255 if v > 127 else 0).convert("1")
    return ImageChops.logical_and(mask, ImageChops.invert(eroded))


# ---------- spread N points ----------

def _spread(cand: np.ndarray, n: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    if len(cand) > MAX_CANDIDATES:
        cand = cand[rng.choice(len(cand), MAX_CANDIDATES, replace=False)]
    if len(cand) < n:
        raise ValueError(f"shape too small to place {n} drones")
    cent, _ = kmeans2(cand, _kmeanspp(cand, n, rng), iter=30, minit="matrix")
    return _relax(cand[cKDTree(cand).query(cent)[1]], cand)


def _kmeanspp(cand: np.ndarray, n: int, rng: np.random.Generator) -> np.ndarray:
    """k-means++ seeding in O(len(cand)) memory (SciPy's minit="++" peaks near 1 GB on 24k points)."""
    centers = [cand[rng.integers(len(cand))]]
    d2 = ((cand - centers[0]) ** 2).sum(axis=1)
    for _ in range(1, n):
        c = cand[rng.choice(len(cand), p=d2 / d2.sum())] if d2.sum() > 0 else cand[rng.integers(len(cand))]
        centers.append(c)
        d2 = np.minimum(d2, ((cand - c) ** 2).sum(axis=1))
    return np.array(centers)


def _relax(pts: np.ndarray, cand: np.ndarray) -> np.ndarray:
    n = len(pts)
    tree = cKDTree(cand)
    # Push apart: k-means leaves some pairs close; nudge each toward even spacing, staying on the shape.
    target = float(np.median(cKDTree(pts).query(pts, k=2)[0][:, 1]))
    for _ in range(25):
        dist, idx = cKDTree(pts).query(pts, k=2)
        d, j = dist[:, 1], idx[:, 1]
        close = d < target * 0.9
        if not close.any():
            break
        away = pts - pts[j]
        norm = np.maximum(np.linalg.norm(away, axis=1, keepdims=True), 1e-9)
        step = ((target - d) * 0.5)[:, None] * away / norm
        moved = pts + np.where(close[:, None], step, 0)
        pts = cand[tree.query(moved)[1]]
        if len(np.unique(pts, axis=0)) < n:  # snapping merged two; keep the previous layout
            pts = cand[tree.query(moved - np.where(close[:, None], step, 0))[1]]
            break
    return pts


def _along(contours: list[Contour], n: int) -> np.ndarray:
    """n points evenly spaced by arc length over all contours (closed ones wrap around)."""
    polys = [np.vstack([c, c[:1]]) if closed else c for c, closed in contours]
    lens = [float(np.linalg.norm(np.diff(p, axis=0), axis=1).sum()) for p in polys]
    total = sum(lens)
    counts = [int(n * L / total) for L in lens]
    for k in np.argsort([-(n * L / total - int(n * L / total)) for L in lens])[: n - sum(counts)]:
        counts[k] += 1
    out = []
    for p, L, k, (_, closed) in zip(polys, lens, counts, contours):
        if k == 0:
            continue
        cum = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(p, axis=0), axis=1))])
        s = (np.arange(k) + 0.5) * L / k if closed else np.linspace(0, L, k)
        out.append(np.c_[np.interp(s, cum, p[:, 0]), np.interp(s, cum, p[:, 1])])
    return np.vstack(out)


def _grid_points(n: int, aspect: float = 1.6) -> np.ndarray:
    rows = max(1, round(math.sqrt(n / aspect)))
    cols = math.ceil(n / rows)
    pts = [(c, r) for r in range(rows) for c in range(cols)][:n]
    return np.array(pts, float)


# ---------- public ----------

def sample(f: Formation, n: int, limits: Limits, sources: Sources, seed: int = 0) -> Sampled:
    """Sample formation `f` with `n` drones, placed and scaled in the airspace (metres, z up)."""
    colors_img = None
    if f.kind == "preset" and f.shape == "grid":
        px = _grid_points(n)
        px[:, 1] = px[:, 1].max() - px[:, 1]  # rows count downward, like pixels
    else:
        px = None
        band = False
        if f.kind == "preset":
            contours, open_line = _preset(f.shape)
            layers = [(contours, None, not open_line)]
            fill_mode = "outline" if f.shape == "ring" else f.fill
            if f.shape == "circle":
                fill_mode = "fill" if f.fill == "fill" else "outline"
        elif f.kind == "stroke":
            pts = np.asarray(sources.strokes[f.stroke], float) * [1, -1]  # metres z-up → y-down
            closed = len(pts) > 2 and np.linalg.norm(pts[0] - pts[-1]) < 0.08 * np.ptp(pts, axis=0).max()
            layers = [([(pts, bool(closed))], None, f.fill == "fill" and bool(closed))]
            fill_mode = f.fill if closed else "outline"
            band = f.fill == "fill" and not closed  # an open line "filled" is a band of drones along it
        elif f.kind == "svg":
            layers = _svg_layers(_svg_text(sources.svgs[f.svg]))
            fill_mode = f.fill
        else:
            layers = None
            fill_mode = f.fill
        if layers is not None and fill_mode == "outline" and f.kind in ("preset", "stroke") and not band:
            k = RES / max(np.ptp(np.vstack([c for c, _ in layers[0][0]]), axis=0).max(), 1e-9)
            px = _relax(_along(layers[0][0], n) * k, _along(layers[0][0], 8000) * k)
            layers = None
            cols = None
        elif layers is None:
            mask, line, colors_img = _text_raster(f.text)
        else:
            mask, line, colors_img = _raster(layers, line_frac=0.07 if f.kind == "stroke" and band else 0.012)
        if px is None:
            area = mask if fill_mode == "fill" else _boundary(mask)
            area = ImageChops.logical_or(area, line)
            ys, xs = np.nonzero(np.asarray(area, dtype=bool))
            cand = np.c_[xs, ys].astype(float)
            px = _spread(cand, n, seed)
            if f.kind == "svg" and getattr(f, "keep_colors", True) and colors_img is not None:
                arr = np.asarray(colors_img)
                cols = [to_hex(tuple(c / 255 for c in arr[int(y), int(x)])) for x, y in px]
            else:
                cols = None
    # Place: pixel space (y down) → metres (z up), fitted to the formation box.
    x0, y0 = px.min(axis=0)
    span = np.ptp(px, axis=0)
    sh = max(span[1], 1e-9) if span[1] > 1e-9 else max(span[0], 1e-9)
    scale = f.size * limits.height / sh
    if span[1] <= 1e-9:  # a flat line: size sets its width instead
        scale = f.size * limits.width / max(span[0], 1e-9)
    cx, cz = f.center[0] * limits.width, f.center[1] * limits.height
    x = (px[:, 0] - x0 - span[0] / 2) * scale + cx
    z = -(px[:, 1] - y0 - span[1] / 2) * scale + cz
    pts = np.c_[x, z]
    nn = cKDTree(pts).query(pts, k=2)[0][:, 1]
    return Sampled(pts, cols if f.kind == "svg" else None, float(nn.min()))


def _svg_text(src: str) -> str:
    if src.lstrip().startswith("<"):
        return src
    from pathlib import Path

    return Path(src).read_text()


def launch_grid(n: int, limits: Limits, spacing: float | None = None) -> np.ndarray:
    """Where drones start and land: rows just above the lowest altitude, centred."""
    s = spacing or limits.min_separation * 1.6
    usable = limits.width - 2 * limits.margin
    cols = max(1, min(n, int(usable // s) + 1))
    pts = []
    for i in range(n):
        r, c = divmod(i, cols)
        in_row = min(cols, n - r * cols)
        x = limits.width / 2 + (c - (in_row - 1) / 2) * s
        pts.append((x, limits.margin + r * s))
    return np.array(pts, float)
