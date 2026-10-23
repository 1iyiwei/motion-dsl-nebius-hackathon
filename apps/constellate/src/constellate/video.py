"""Night-sky MP4 of the *simulated* flight, seen from the ground.

A pinhole camera stands in front of the show at eye height, so depth drift from the wind shows as
parallax and dots shrink with distance. Glow is drawn at quarter resolution, blurred and added back.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

from .formations import parse_rgb
from .show import Limits

W, H = 1280, 720
BASE_ALTITUDE = 6.0  # m above ground of the airspace floor (where the launch grid hovers)
EYE = 1.7  # m


def ffmpeg_exe() -> str | None:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg")


class Camera:
    def __init__(self, limits: Limits, width: int = W, height: int = H):
        self.w, self.h = width, height
        self.cx = limits.width / 2
        self.dist = max(limits.width, limits.height * 16 / 9) * 1.15  # viewer distance in front of the plane
        self.look_z = BASE_ALTITUDE + limits.height * 0.45
        self.f = width * 0.5 / (limits.width * 0.62 / self.dist)  # the show spans ~80% of the frame
        self.pitch = np.arctan2(self.look_z - EYE, self.dist)

    def project(self, p: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(N, 3) metres [x, depth, z above the airspace floor] → screen (N, 2) px and distance (N,)."""
        rel_x = p[:, 0] - self.cx
        rel_y = p[:, 1] + self.dist  # forward
        rel_z = p[:, 2] + BASE_ALTITUDE - EYE
        c, s = np.cos(self.pitch), np.sin(self.pitch)
        fwd = rel_y * c + rel_z * s
        up = -rel_y * s + rel_z * c
        sx = self.w / 2 + self.f * rel_x / fwd
        sy = self.h / 2 - self.f * up / fwd
        return np.c_[sx, sy], np.sqrt(rel_x**2 + rel_y**2 + rel_z**2)


def _backdrop(width: int, height: int, seed: int = 7) -> Image.Image:
    y = np.linspace(0, 1, height)[:, None]
    top, bot = np.array([4, 6, 18]), np.array([18, 22, 44])
    sky = (top + (bot - top) * y**1.6)[:, None, :] * np.ones((1, width, 1))
    img = Image.fromarray(sky.astype(np.uint8).reshape(height, width, 3))
    d = ImageDraw.Draw(img)
    rng = np.random.default_rng(seed)
    for _ in range(260):
        x, yy, b = rng.uniform(0, width), rng.uniform(0, height * 0.8), rng.uniform(40, 150)
        d.point((x, yy), fill=(int(b), int(b), int(b * 1.1)))
    # Horizon: a low tree line and a few rooftops, in silhouette.
    ground = int(height * 0.9)
    xs = np.arange(0, width + 8, 8)
    ridge = ground - 10 - 8 * np.abs(np.sin(xs / 37.0)) - 5 * rng.random(len(xs))
    for x0, w0, h0 in ((90, 70, 34), (180, 40, 22), (980, 90, 40), (1100, 55, 26)):
        ridge[(xs >= x0 * width / W) & (xs <= (x0 + w0) * width / W)] = ground - h0 * height / H
    d.polygon([(0, height)] + list(zip(xs.tolist(), ridge.tolist())) + [(width, height)], fill=(2, 2, 6))
    return img


def frames(pos: np.ndarray, colors: list[list[str]], limits: Limits, width: int = W, height: int = H, every: int = 1):
    """Yield PIL frames. pos (F, N, 3) metres; colors[f][i] hex."""
    cam = Camera(limits, width, height)
    bg = _backdrop(width, height)
    q = 4
    rgb_cache: dict[str, tuple[int, int, int]] = {}
    for k in range(0, len(pos), every):
        scr, dist = cam.project(pos[k].astype(float))
        r = np.clip(2.6 * cam.dist / dist, 1.2, 4.0) * width / W
        glow = Image.new("RGB", (width // q, height // q))
        core = bg.copy()
        dg, dc = ImageDraw.Draw(glow), ImageDraw.Draw(core)
        for i in np.argsort(-dist):  # far first
            c = colors[k][i] if colors else "#ffffff"
            rgb = rgb_cache.get(c) or rgb_cache.setdefault(c, parse_rgb(c))
            x, y = scr[i]
            g = r[i] * 2.2 / q
            dg.ellipse([x / q - g, y / q - g, x / q + g, y / q + g], fill=rgb)
            hot = tuple(min(255, int(v * 0.55 + 140)) for v in rgb)
            dc.ellipse([x - r[i], y - r[i], x + r[i], y + r[i]], fill=hot)
        halo = glow.filter(ImageFilter.GaussianBlur(2.2)).resize((width, height), Image.BILINEAR)
        yield ImageChops.add(core, halo)


def write_mp4(path: Path, pos: np.ndarray, colors: list[list[str]], limits: Limits, fps: int = 30, width: int = W,
              height: int = H) -> Path:
    exe = ffmpeg_exe()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not exe:  # no encoder: fall back to an animated GIF next to it
        gif = path.with_suffix(".gif")
        fr = list(frames(pos, colors, limits, width // 2, height // 2, every=3))
        fr[0].save(gif, save_all=True, append_images=fr[1:], duration=int(1000 * 3 / fps), loop=0)
        return gif
    cmd = [exe, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", str(fps),
           "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-preset", "veryfast", "-movflags", "+faststart", str(path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for im in frames(pos, colors, limits, width, height):
            proc.stdin.write(im.tobytes())
    finally:
        proc.stdin.close()
        proc.wait()
    if proc.returncode:
        raise RuntimeError(f"ffmpeg failed with exit code {proc.returncode}")
    return path


def baked_colors(baked, n: int) -> list[list[str]]:
    cols = [baked.props[f"d{i}"]["fill"] for i in range(n)]
    return [[cols[i][k] for i in range(n)] for k in range(len(cols[0]))]
