"""Draw SketchDynamics' architecture card (3:2, for the Devpost gallery).

    uv run python demos/sketch-dynamics/architecture-card.py      # → demos/sketch-dynamics/architecture-card.png
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from card_style import BLUE, BLUE_BG, GREEN, INK, MUTED, Card  # noqa: E402

c = Card("SketchDynamics", "Sketch it, say it, or drop in a logo: Nemotron writes the motion, a validator checks it.",
         "github.com/1iyiwei/motion-dsl-nebius-hackathon · apps/sketchdynamics")

# Row 1: you → the app → what you get
c.box(70, 220, 400, 610, "You", ["in the browser", "", "type or say what", "should happen", "draw strokes",
                                 "drop an SVG logo", "“slower”, “more bounce”"])

gx0, gy0, gx1, gy1 = 480, 200, 1330, 670
c.group(gx0, gy0, gx1, gy1, "SketchDynamics", "FastAPI + vanilla JS · every model call shown live in a side panel",
        BLUE, BLUE_BG)
c.box(gx0 + 30, gy0 + 96, gx0 + 410, gy0 + 278, "Logo rig", ["SVG → parts (holes kept)", "VL names + groups them",
                                                               "proposes 3 animations"], title_size=28)
c.box(gx0 + 440, gy0 + 96, gx1 - 30, gy0 + 278, "Shape finder", ["library → Tavily (icon sites)", "VL picks the best",
                                                                  "or Super draws one"], title_size=28)
c.box(gx0 + 30, gy0 + 296, gx1 - 30, gy1 - 22, "Plan → validate → repair",
      ["Super writes MotionLang · the validator returns errors with literal fixes", "→ edit-op repairs until valid · tweaks via Nano edit ops"],
      title_size=28)

c.box(1410, 220, 1730, 610, "You get", ["a live SVG player", "versions to step", "back through", "",
                                        "export: HTML player", "or MotionLang JSON"])

c.arrow(400, 415, 480, 415, both=True)
c.arrow(1330, 415, 1410, 415)

# Row 2: the models, Tavily, MotionLang
tx0, ty0, tx1, ty1 = 470, 720, 1730, 1110
c.group(tx0, ty0, tx1, ty1, "Nebius Token Factory", "OpenAI-compatible API · structured output · every model call",
        INK, "white")
cw = (tx1 - tx0 - 26 * 2 - 3 * 18) // 4
models = [("Nemotron 3 Super 120B", ["plans (structured)", "repairs", "draws missing shapes"]),
          ("Nemotron 3 Ultra 550B", ["planner on request", "“Retry with Ultra”"]),
          ("Nemotron 3.5 Super VL", ["names logo parts", "groups rigid pieces", "compares shapes"]),
          ("Nemotron 3 Nano 30B", ["“slower” → edit ops", "which objects need", "a shape (~1 s)"])]
for i, (name, what) in enumerate(models):
    x = tx0 + 26 + i * (cw + 18)
    c.model(x, ty0 + 104, x + cw, ty1 - 26, name, what)

c.box(70, 720, 400, 1000, "MotionLang", ["declarative motion DSL", "validator + baker", "shared by all three apps"],
      title_size=32)

c.arrow(900, 670, 900, 720, color=GREEN)
c.text((914, 680), "every model call", 21, GREEN)
c.arrow(470, 860, 400, 860, width=4)
c.text((404, 878), "plans", 20, MUTED)

c.save(__file__)
