"""Rasterize baked tracks to frames and GIFs (optional extra: pip install motionlang[render], i.e. Pillow).

    from motionlang.render import to_gif, to_frames
    gif = to_gif(result.baked.to_json(), width=480, fps=20)     # bytes, e.g. for a chat message
    frames = to_frames(tracks, width=320, fps=4)                # PIL images, e.g. for a contact sheet

Matches the reference player (static/player.js) for fills (with holes), strokes, opacity, trim (draw_on),
clip (reveal) and transforms. Image actors are not drawn; text uses a system sans-serif font.
"""

from __future__ import annotations

import io
import math
from typing import Any

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFont

from .color import parse_color
from .paths import flatten_svg_path, format_path, parse_path

SUPERSAMPLE = 2
Pts = np.ndarray  # (N, 2)


def _at(v: Any, i: int) -> Any:
    return v[min(i, len(v) - 1)] if isinstance(v, list) else v


def _at_vec(v: Any, i: int) -> list[float]:
    return v[min(i, len(v) - 1)] if v and isinstance(v[0], list) else v


def _rgb(c: str | None) -> tuple[int, int, int] | None:
    if not c:
        return None
    try:
        return tuple(round(x * 255) for x in parse_color(c))  # type: ignore[return-value]
    except ValueError:
        return None


def _apply(m: list[float], pts: Pts) -> Pts:
    a, b, c, d, e, f = m
    return np.stack([a * pts[:, 0] + c * pts[:, 1] + e, b * pts[:, 0] + d * pts[:, 1] + f], axis=1)


_shape_cache: dict[int, tuple[list[Pts], bool]] = {}


def _local_shapes(g: dict) -> tuple[list[Pts], bool]:
    """Outline(s) in bbox-local coordinates and whether the shape is closed (fillable)."""
    key = id(g)
    if key in _shape_cache:
        return _shape_cache[key]
    tag = g.get("tag")
    if tag == "ellipse":
        th = np.linspace(0, 2 * math.pi, 97)
        out = ([np.stack([g["rx"] + g["rx"] * np.cos(th), g["ry"] + g["ry"] * np.sin(th)], axis=1)], True)
    elif tag in ("rect", "image"):
        w, h, r = float(g["w"]), float(g["h"]), min(float(g.get("r") or 0), g["w"] / 2, g["h"] / 2)
        if r <= 0:
            pts = np.array([[0, 0], [w, 0], [w, h], [0, h], [0, 0]], dtype=float)
        else:
            corners = [(w - r, r, -90), (w - r, h - r, 0), (r, h - r, 90), (r, r, 180)]
            pts = np.concatenate([
                np.stack([cx + r * np.cos(np.radians(a0 + np.linspace(0, 90, 9))), cy + r * np.sin(np.radians(a0 + np.linspace(0, 90, 9)))], axis=1)
                for cx, cy, a0 in corners
            ])
            pts = np.vstack([pts, pts[:1]])
        out = ([pts], True)
    elif tag == "path" and g.get("d"):
        chunks: list[list] = []
        for seg in parse_path(g["d"]):
            if seg[0] == "M" or not chunks:
                chunks.append([])
            chunks[-1].append(seg)
        polys = [flatten_svg_path(format_path(c, 3)) for c in chunks if any(cmd != "M" for cmd, _ in c)]
        out = ([p for p in polys if len(p) > 1], bool(g.get("closed", False)))
    else:
        out = ([], False)
    _shape_cache[key] = out
    return out


def _signed_area(p: Pts) -> float:
    x, y = p[:, 0], p[:, 1]
    return 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))


def _trim(polys: list[Pts], frac: float) -> list[Pts]:
    """The first `frac` of the total outline length, like the player's pathLength=1 dash."""
    if frac >= 0.999:
        return polys
    lens = [float(np.sum(np.linalg.norm(np.diff(p, axis=0), axis=1))) for p in polys]
    left = frac * sum(lens)
    out = []
    for p, L in zip(polys, lens):
        if left <= 0:
            break
        if L <= left:
            out.append(p)
            left -= L
            continue
        seg = np.linalg.norm(np.diff(p, axis=0), axis=1)
        cum = np.concatenate([[0], np.cumsum(seg)])
        k = int(np.searchsorted(cum, left)) - 1
        f = (left - cum[k]) / seg[k] if seg[k] > 0 else 0
        out.append(np.vstack([p[: k + 1], p[k] + (p[k + 1] - p[k]) * f]))
        break
    return out


def _font(px: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ("DejaVuSans-Bold.ttf", "Arial Bold.ttf", "Arial.ttf", "Helvetica.ttc", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, max(px, 1))
        except OSError:
            continue
    return ImageFont.load_default(max(px, 1))


class _Renderer:
    def __init__(self, tracks: dict, width: int | None):
        self.T = tracks
        cw, ch = tracks["canvas"]["width"], tracks["canvas"]["height"]
        self.scale = (width / cw) if width else 1.0
        self.size = (max(1, round(cw * self.scale)), max(1, round(ch * self.scale)))
        self.ss = SUPERSAMPLE
        self.big = (self.size[0] * self.ss, self.size[1] * self.ss)
        self.bg = _rgb(tracks["canvas"].get("background")) or (255, 255, 255)

    def frame(self, i: int) -> Image.Image:
        k = self.scale * self.ss
        img = Image.new("RGB", self.big, self.bg)
        for a in self.T["actors"]:
            tr, g = a["tracks"], a["geom"]
            op = float(_at(tr.get("opacity", 1.0), i))
            if op <= 0.004 or g.get("tag") in (None, "group", "image"):
                continue
            m = [v * k for v in _at_vec(tr["m"], i)]
            if abs(m[0] * m[3] - m[1] * m[2]) < 1e-12:
                continue
            fill, stroke = _rgb(_at(tr.get("fill"), i)), _rgb(_at(tr.get("stroke"), i))
            if g.get("tag") == "text":
                if fill:
                    self._text(img, g, m, fill, op)
                continue
            polys, closed = _local_shapes(g)
            if not polys:
                continue
            screen = [_apply(m, p) for p in polys]
            sw = float(g.get("width") or a.get("style", {}).get("stroke_width", 4)) * math.sqrt(abs(m[0] * m[3] - m[1] * m[2]))
            pad = sw + 2
            allpts = np.vstack(screen)
            x0, y0 = np.floor(allpts.min(axis=0) - pad).astype(int)
            x1, y1 = np.ceil(allpts.max(axis=0) + pad).astype(int)
            x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, self.big[0]), min(y1, self.big[1])
            if x1 <= x0 or y1 <= y0:
                continue
            region = (x1 - x0, y1 - y0)
            off = np.array([x0, y0], dtype=float)
            clip = self._clip_mask(g, tr, i, m, region, off)
            fill_closed = closed and g.get("tag") == "path" or g.get("tag") in ("rect", "ellipse")
            if fill and fill_closed:
                mask = self._fill_mask(screen, g, region, off)
                self._paint(img, fill, mask, clip, op, (x0, y0))
            if stroke and sw > 0:
                trim = float(_at(tr.get("trim", 1.0), i)) if g.get("tag") == "path" else 1.0
                lines = _trim([s - off for s in screen], trim) if trim > 0.001 else []
                if lines:
                    mask = Image.new("L", region, 0)
                    d = ImageDraw.Draw(mask)
                    w = max(1, round(sw))
                    for p in lines:
                        pts = [tuple(q) for q in p]
                        d.line(pts, fill=255, width=w, joint="curve")
                        if g.get("tag") == "path":  # round caps, like the player
                            for q in (pts[0], pts[-1]):
                                d.ellipse([q[0] - w / 2, q[1] - w / 2, q[0] + w / 2, q[1] + w / 2], fill=255)
                    self._paint(img, stroke, mask, clip, op, (x0, y0))
        return img.resize(self.size, Image.Resampling.LANCZOS)

    def _fill_mask(self, screen: list[Pts], g: dict, region: tuple[int, int], off: Pts) -> Image.Image:
        if len(screen) == 1:
            mask = Image.new("L", region, 0)
            ImageDraw.Draw(mask).polygon([tuple(q) for q in screen[0] - off], fill=255)
            return mask
        # Holes: evenodd XORs every subpath; nonzero adds subpaths that wind like the largest and cuts the others.
        evenodd = g.get("fill_rule") == "evenodd"
        areas = [_signed_area(p) for p in screen]
        main = areas[int(np.argmax(np.abs(areas)))] > 0
        mask = Image.new("L", region, 0)
        for p, area in zip(screen, areas):
            sub = Image.new("L", region, 0)
            ImageDraw.Draw(sub).polygon([tuple(q) for q in p - off], fill=255)
            if evenodd:
                mask = ImageChops.logical_xor(mask.convert("1"), sub.convert("1")).convert("L")
            elif (area > 0) == main:
                mask = ImageChops.lighter(mask, sub)
            else:
                mask = ImageChops.subtract(mask, sub)
        return mask

    def _clip_mask(self, g: dict, tr: dict, i: int, m: list[float], region: tuple[int, int], off: Pts) -> Image.Image | None:
        c = tr.get("clip")
        if not c:
            return None
        l, t, r, b = _at_vec(c, i)
        if max(l, t, r, b) <= 0:
            return None
        W, H = float(g.get("bw", 0)), float(g.get("bh", 0))
        pad = max(W, H)
        x0, y0 = (l * W if l > 0 else -pad), (t * H if t > 0 else -pad)
        x1, y1 = ((1 - r) * W if r > 0 else W + pad), ((1 - b) * H if b > 0 else H + pad)
        mask = Image.new("L", region, 0)
        if x1 > x0 and y1 > y0:
            rect = _apply(m, np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=float)) - off
            ImageDraw.Draw(mask).polygon([tuple(q) for q in rect], fill=255)
        return mask

    def _paint(self, img: Image.Image, color: tuple[int, int, int], mask: Image.Image, clip: Image.Image | None, op: float, at: tuple[int, int]) -> None:
        if clip is not None:
            mask = ImageChops.multiply(mask, clip)
        if op < 0.999:
            mask = mask.point(lambda v: round(v * op))
        img.paste(Image.new("RGB", mask.size, color), at, mask)

    def _text(self, img: Image.Image, g: dict, m: list[float], color: tuple[int, int, int], op: float) -> None:
        # Draw the text upright at its on-screen resolution, then warp it with the actor's matrix.
        k = max(math.sqrt(abs(m[0] * m[3] - m[1] * m[2])), 1e-6)
        w, h = max(1, math.ceil(float(g.get("bw", 1)) * k * 1.3)), max(1, math.ceil(float(g.get("bh", g["size"])) * k * 1.3))
        local = Image.new("L", (w, h), 0)
        ImageDraw.Draw(local).text((0, g["size"] * 0.8 * k), g["text"], fill=255, font=_font(round(g["size"] * k)), anchor="ls")
        a, b, c, d, e, f = (m[0] / k, m[1] / k, m[2] / k, m[3] / k, m[4], m[5])
        det = a * d - b * c
        if abs(det) < 1e-12:
            return
        inv = (d / det, -c / det, (c * f - d * e) / det, -b / det, a / det, (b * e - a * f) / det)
        warped = local.transform(img.size, Image.Transform.AFFINE, inv, resample=Image.Resampling.BILINEAR)
        if op < 0.999:
            warped = warped.point(lambda v: round(v * op))
        img.paste(Image.new("RGB", img.size, color), (0, 0), warped)


def _frame_indices(tracks: dict, fps: float | None) -> tuple[list[int], float]:
    src = float(tracks["fps"])
    out_fps = min(float(fps), src) if fps else src
    n = int(math.floor(tracks["duration"] * out_fps + 1e-9)) + 1
    return [min(round(j / out_fps * src), tracks["frames"] - 1) for j in range(n)], out_fps


def to_frames(tracks: dict, *, fps: float | None = None, width: int | None = None) -> list[Image.Image]:
    """RGB frames of baked tracks (Baked.to_json()), resampled to `fps` (default: the tracks' fps) and
    scaled to `width` px (default: the canvas width)."""
    idx, _ = _frame_indices(tracks, fps)
    r = _Renderer(tracks, width)
    try:
        return [r.frame(i) for i in idx]
    finally:
        _shape_cache.clear()


def to_gif(tracks: dict, *, fps: float | None = 20, width: int | None = 480, loop: bool = True, colors: int = 128) -> bytes:
    """An animated GIF of baked tracks: chat-sized by default (480 px wide, 20 fps)."""
    idx, out_fps = _frame_indices(tracks, fps)
    frames = to_frames(tracks, fps=out_fps, width=width)
    pal = [f.quantize(colors=colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE) for f in frames]
    buf = io.BytesIO()
    kw: dict[str, Any] = {"loop": 0} if loop else {}
    duration = max(20, round(1000 / out_fps / 10) * 10)  # GIF delays are in 10 ms steps; browsers clamp below 20 ms
    pal[0].save(buf, format="GIF", save_all=True, append_images=pal[1:], duration=duration, optimize=True, disposal=1, **kw)
    return buf.getvalue()


__all__ = ["to_frames", "to_gif"]
