"""Shared drawing helpers for the projects' architecture cards (3:2 PNGs for the Devpost galleries)."""

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1800, 1200
BG, INK, MUTED = "#fbfbf9", "#111111", "#5c5c5c"
GREEN, GREEN_BG = "#76b900", "#f1f8e3"  # NVIDIA parts
BLUE, BLUE_BG = "#2b5fd9", "#eaf0fd"  # Nebius platform / the user's side
FONTS = "/System/Library/Fonts/Supplemental/"


def font(size, bold=False):
    for name in (("Arial Bold.ttf", "Arial.ttf") if bold else ("Arial.ttf",)):
        try:
            return ImageFont.truetype(FONTS + name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


class Card:
    def __init__(self, title: str, subtitle: str, footer: str):
        self.img = Image.new("RGB", (W, H), BG)
        self.d = ImageDraw.Draw(self.img)
        self.d.text((70, 52), title, font=font(56, True), fill=INK)
        self.d.text((70, 122), subtitle, font=font(28), fill=MUTED)
        self.d.text((70, 1140), footer, font=font(22), fill=MUTED)

    def text(self, xy, s, size=22, color=MUTED, bold=False, center=False):
        f = font(size, bold)
        x, y = xy
        if center:
            x -= self.d.textlength(s, font=f) / 2
        self.d.text((x, y), s, font=f, fill=color)

    def box(self, x0, y0, x1, y1, title, lines=(), edge=INK, fill="white", tag=None, tag_color=GREEN, title_size=34,
            sub=None):
        d = self.d
        d.rounded_rectangle((x0, y0, x1, y1), radius=22, fill=fill, outline=edge, width=4)
        y = y0 + 22
        if tag:
            tw = d.textlength(tag, font=font(20, True))
            d.rounded_rectangle((x0 + 24, y, x0 + 24 + tw + 22, y + 32), radius=16, fill=tag_color)
            d.text((x0 + 35, y + 5), tag, font=font(20, True), fill="white")
            y += 44
        d.text((x0 + 24, y), title, font=font(title_size, True), fill=INK)
        y += title_size + 10
        if sub:
            d.text((x0 + 24, y), sub, font=font(22), fill=MUTED)
            y += 36
        else:
            y += 4
        for ln in lines:
            d.text((x0 + 24, y), ln, font=font(24), fill=MUTED)
            y += 34
        return y

    def group(self, x0, y0, x1, y1, title, sub, edge, fill):
        """A big container (a sandbox, a platform, the user's machine) with a heading."""
        self.d.rounded_rectangle((x0, y0, x1, y1), radius=26, fill=fill, outline=edge, width=5)
        self.d.text((x0 + 26, y0 + 20), title, font=font(30, True), fill=INK)
        if sub:
            self.d.text((x0 + 26, y0 + 58), sub, font=font(22), fill=MUTED)

    def bullet(self, x, y, s, color=GREEN, size=23):
        self.d.ellipse((x + 2, y + 7, x + 14, y + 19), fill=color)
        self.d.text((x + 26, y), s, font=font(size), fill=INK)

    def arrow(self, x0, y0, x1, y1, color=INK, width=5, both=False):
        d = self.d
        d.line((x0, y0, x1, y1), fill=color, width=width)

        def head(xa, ya, xb, yb):
            a = math.atan2(yb - ya, xb - xa)
            d.polygon([(xb, yb), (xb - 22 * math.cos(a - 0.45), yb - 22 * math.sin(a - 0.45)),
                       (xb - 22 * math.cos(a + 0.45), yb - 22 * math.sin(a + 0.45))], fill=color)

        head(x0, y0, x1, y1)
        if both:
            head(x1, y1, x0, y0)

    def model(self, x0, y0, x1, y1, name, what):
        """An NVIDIA model chip."""
        return self.box(x0, y0, x1, y1, name, what, edge=GREEN, fill=GREEN_BG, tag="NVIDIA", title_size=22)

    def save(self, script: str):
        out = Path(script).with_suffix(".png")
        self.img.save(out)
        print(out, self.img.size)
