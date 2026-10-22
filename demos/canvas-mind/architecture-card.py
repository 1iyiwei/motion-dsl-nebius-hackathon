"""Draw CanvasMind's architecture card (3:2, for the Devpost gallery).

    uv run python demos/canvas-mind/architecture-card.py      # → demos/canvas-mind/architecture-card.png
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1800, 1200
BG, INK, MUTED, LINE = "#fbfbf9", "#111111", "#5c5c5c", "#d9d9d4"
GREEN, GREEN_BG = "#76b900", "#f1f8e3"  # NVIDIA parts
BLUE, BLUE_BG = "#2b5fd9", "#eaf0fd"  # Nebius / the user's own machine
FONTS = "/System/Library/Fonts/Supplemental/"


def font(size, bold=False):
    for name in (("Arial Bold.ttf", "Arial.ttf") if bold else ("Arial.ttf",)):
        try:
            return ImageFont.truetype(FONTS + name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img)


def box(x0, y0, x1, y1, title, lines=(), edge=INK, fill="white", tag=None, tag_color=GREEN, title_size=34):
    d.rounded_rectangle((x0, y0, x1, y1), radius=22, fill=fill, outline=edge, width=4)
    y = y0 + 24
    if tag:
        tw = d.textlength(tag, font=font(20, True))
        d.rounded_rectangle((x0 + 24, y, x0 + 24 + tw + 22, y + 32), radius=16, fill=tag_color)
        d.text((x0 + 35, y + 5), tag, font=font(20, True), fill="white")
        y += 46
    d.text((x0 + 24, y), title, font=font(title_size, True), fill=INK)
    y += title_size + 14
    for ln in lines:
        d.text((x0 + 24, y), ln, font=font(24), fill=MUTED)
        y += 34
    return y


def arrow(x0, y0, x1, y1, label="", color=INK, width=5, label_dy=-38, both=False):
    d.line((x0, y0, x1, y1), fill=color, width=width)

    def head(xa, ya, xb, yb):
        import math

        a = math.atan2(yb - ya, xb - xa)
        p = [(xb, yb), (xb - 22 * math.cos(a - 0.45), yb - 22 * math.sin(a - 0.45)),
             (xb - 22 * math.cos(a + 0.45), yb - 22 * math.sin(a + 0.45))]
        d.polygon(p, fill=color)

    head(x0, y0, x1, y1)
    if both:
        head(x1, y1, x0, y0)
    if label:
        f = font(22)
        lines = label.split("\n")
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2 + label_dy
        for i, ln in enumerate(lines):
            tw = d.textlength(ln, font=f)
            d.text((mx - tw / 2, my + i * 28), ln, font=f, fill=color if color != INK else MUTED)


# Title
d.text((70, 52), "CanvasMind", font=font(56, True), fill=INK)
d.text((70, 122), "A private, always-on studio assistant: your files stay on your machine; the agent runs sandboxed.",
       font=font(28), fill=MUTED)

# Row 1: phone → sandbox → your machine
box(70, 230, 420, 560, "You", ["Telegram on your phone", "", "send a logo or sketch", "“animate it in my style”",
                                 "thumbs up / down + why", "“what’s new this week?”"])

sx0, sy0, sx1, sy1 = 520, 200, 1070, 600
d.rounded_rectangle((sx0, sy0, sx1, sy1), radius=26, fill=GREEN_BG, outline=GREEN, width=5)
d.text((sx0 + 26, sy0 + 20), "NVIDIA OpenShell sandbox", font=font(30, True), fill=INK)
d.text((sx0 + 26, sy0 + 58), "installed and managed by NVIDIA NemoClaw", font=font(22), fill=MUTED)
box(sx0 + 30, sy0 + 104, sx1 - 30, sy0 + 270, "Hermes Agent",
    ["chat · memory · skills · cron jobs", "CanvasMind persona + skill"], edge=INK, title_size=32)
for i, ln in enumerate(["no API keys (OpenShell adds them at egress)", "no access to your files",
                        "egress only to allow-listed hosts"]):
    y = sy0 + 292 + i * 34
    d.ellipse((sx0 + 32, y + 7, sx0 + 44, y + 19), fill=GREEN)
    d.text((sx0 + 56, y), ln, font=font(23), fill=INK)

mx0, my0, mx1, my1 = 1250, 200, 1730, 600
d.rounded_rectangle((mx0, my0, mx1, my1), radius=26, fill=BLUE_BG, outline=BLUE, width=5)
d.text((mx0 + 26, my0 + 20), "Your machine", font=font(30, True), fill=INK)
d.text((mx0 + 26, my0 + 58), "MacBook or any Linux box", font=font(22), fill=MUTED)
box(mx0 + 30, my0 + 100, mx1 - 30, my0 + 236, "canvasmind MCP server", ["file · recall · animate · tweak",
                                                                       "feedback · style · digest"], title_size=28)
box(mx0 + 30, my0 + 248, mx1 - 30, my0 + 382, "~/CanvasMind", ["items/ · library.sqlite",
                                                                "style.json · journal.jsonl"], title_size=28)

arrow(420, 395, 520, 395, color=INK, both=True)
d.text((470 - d.textlength("Telegram", font=font(20)) / 2, 412), "Telegram", font=font(20), fill=MUTED)
arrow(1070, 330, 1250, 330, "MCP\nover HTTPS", color=INK, both=True, label_dy=-66)
for i, ln in enumerate(["via Tailscale", "(private tailnet)"]):
    tw = d.textlength(ln, font=font(20))
    d.text((1160 - tw / 2, 348 + i * 26), ln, font=font(20), fill=MUTED)

# Row 2: Nebius Token Factory with the models, MotionLang, Tavily
tx0, ty0, tx1, ty1 = 470, 720, 1500, 1110
d.rounded_rectangle((tx0, ty0, tx1, ty1), radius=26, fill="white", outline=INK, width=4)
d.text((tx0 + 26, ty0 + 20), "Nebius Token Factory", font=font(30, True), fill=INK)
d.text((tx0 + 26, ty0 + 58), "OpenAI-compatible API · every model call · nothing to deploy", font=font(22), fill=MUTED)
cw = (tx1 - tx0 - 26 * 2 - 2 * 20) // 3
models = [("Nemotron 3 Super 120B", ["the agent", "animation planner", "weekly digest"]),
          ("Nemotron 3.5 Super VL", ["captions what you file", "names a logo’s parts"]),
          ("Nemotron 3 Nano 30B", ["recall · style notes", "quick edits · checks"])]
for i, (name, what) in enumerate(models):
    x = tx0 + 26 + i * (cw + 20)
    box(x, ty0 + 110, x + cw, ty1 - 26, name, what, edge=GREEN, fill=GREEN_BG, tag="NVIDIA", title_size=23)

box(70, 720, 400, 1000, "MotionLang", ["validates every plan", "before it reaches you", "→ GIF back to the chat"],
    title_size=32)
box(1570, 720, 1730, 960, "Tavily", ["search", "queries", "only"], title_size=30)

# Model routes
arrow(815, 600, 815, 720, color=GREEN, width=5)
d.text((828, 630), "Super via OpenShell’s", font=font(21), fill=GREEN)
d.text((828, 656), "inference route", font=font(21), fill=GREEN)
arrow(1420, 600, 1300, 720, color=BLUE, width=5)
d.text((1160, 630), "VL · Nano · Super", font=font(21), fill=BLUE)
arrow(1650, 600, 1650, 720, color=BLUE, width=4)
arrow(470, 900, 400, 900, color=INK, width=4)
d.text((404, 860), "plans", font=font(20), fill=MUTED)

d.text((70, 1140), "github.com/1iyiwei/motion-dsl-nebius-hackathon · apps/canvasmind · t.me/CanvasMindBot",
       font=font(22), fill=MUTED)

out = Path(__file__).with_suffix(".png")
img.save(out)
print(out, img.size)
