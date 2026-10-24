"""Draw Constellate's architecture card (3:2, for the Devpost gallery).

    uv run python demos/constellate/architecture-card.py      # → demos/constellate/architecture-card.png
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from card_style import BLUE, BLUE_BG, GREEN, INK, Card  # noqa: E402

c = Card("Constellate", "Sketch or say a drone light show. Nemotron choreographs it; code and physics make it flyable.",
         "github.com/1iyiwei/motion-dsl-nebius-hackathon · apps/constellate")

# Row 1: you → the deterministic pipeline → what you get
c.box(70, 200, 380, 670, "You", ["web app or CLI", "", "sketch a shape, drop", "an SVG logo, or say", "“a red heart that spins,", "then our logo”", "",
                                 "your phone is the wind"], title_size=34)

gx0, gy0, gx1, gy1 = 450, 200, 1230, 670
c.group(gx0, gy0, gx1, gy1, "Flight safety (deterministic)", "one actor per drone · checked in metres", BLUE, BLUE_BG)
stages = [("Compiler", "Hungarian assignment + CAPT paths → MotionLang"),
          ("Flight checker", "separation · speed · accel · airspace · battery"),
          ("Wind sweep", "84 quadrotor flights: 7 winds × 12 gust seeds")]
for i, (title, line) in enumerate(stages):
    y0 = gy0 + 100 + i * 122
    c.box(gx0 + 36, y0, gx1 - 36, y0 + 100, title, [line], title_size=26)
    if i:
        c.arrow((gx0 + gx1) / 2, y0 - 22, (gx0 + gx1) / 2, y0, width=4)

outs = [("Certificate", ["flyable up to 8 m/s", "wind × gust heatmap"]),
        ("Night-sky video", ["the simulated flight,", "seen from the ground"]),
        ("Live wind", ["tilt / shake the phone;", "the swarm reacts at 30 fps"])]
for i, (title, lines) in enumerate(outs):
    y0 = 200 + i * 165
    c.box(1300, y0, 1730, y0 + 140, title, lines, title_size=28)

c.arrow(380, 435, 450, 435, both=True)
c.arrow(1230, 435, 1300, 435)

# Row 2: the models on Token Factory, Tavily, MotionLang
tx0, ty0, tx1, ty1 = 450, 740, 1730, 1110
c.group(tx0, ty0, tx1, ty1, "Nebius Token Factory", "OpenAI-compatible API · each step uses the model size it needs",
        INK, "white")
cw = (tx1 - tx0 - 26 * 2 - 2 * 16) // 3
models = [("Nemotron 3 Ultra 550B", ["writes the show script", "(artistic choices only);", "takes stalled repairs"]),
          ("Nemotron 3 Nano 30B", ["problems + hints →", "edit ops in ~1.5 s;", "tweaks; search queries"]),
          ("Nemotron 3 Super 120B", ["reads search results", "into cited limits"])]
for i, (name, what) in enumerate(models):
    x = tx0 + 26 + i * (cw + 16)
    c.model(x, ty0 + 104, x + cw, ty1 - 26, name, what)

c.box(70, 740, 380, 960, "Tavily", ["venue rules + wind →", "ceiling, crowd setback,", "design wind (quoted)"], title_size=30)
c.box(70, 980, 380, 1110, "MotionLang", ["validator · baker · player"], title_size=28)

c.arrow(700, 740, 700, 670, color=GREEN)
c.text((714, 690), "show script, edit ops", 21, GREEN)
c.arrow(1000, 670, 1000, 740, color=INK)
c.text((1014, 690), "problems + computed hints", 21, INK)
c.arrow(380, 850, 450, 850, width=4)

c.save(__file__)
