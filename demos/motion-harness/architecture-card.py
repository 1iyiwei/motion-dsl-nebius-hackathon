"""Draw MotionHarness' architecture card (3:2, for the Devpost gallery).

    uv run python demos/motion-harness/architecture-card.py      # → demos/motion-harness/architecture-card.png
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from card_style import BLUE, BLUE_BG, GREEN, INK, MUTED, Card  # noqa: E402

c = Card("MotionHarness", "A coding agent that writes motion verbs, tests them in sandboxes, watches them, and ships a plugin.",
         "github.com/1iyiwei/motion-dsl-nebius-hackathon · apps/motionharness")

# Row 1: you → sandboxes → critic → ship
c.box(70, 220, 380, 610, "You", ["dashboard, CLI, or", "a coding agent", "over MCP", "(Claude Code)", "",
                                 "“add a spring_follow", "verb: the actor chases", "a target with springs”"],
      title_size=34)

gx0, gy0, gx1, gy1 = 450, 200, 1170, 670
c.group(gx0, gy0, gx1, gy1, "Token Factory Sandboxes", "VM-isolated runs from immutable, branchable images",
        BLUE, BLUE_BG)
# the branch tree: environment checkpoint → three candidates → a repair branching from its parent
nodes = {"env": (gx0 + 40, gy0 + 120, gx0 + 230, gy0 + 186, "environment", INK),
         "c1": (gx0 + 300, gy0 + 104, gx0 + 440, gy0 + 160, "c1 · Super", INK),
         "c2": (gx0 + 300, gy0 + 176, gx0 + 440, gy0 + 232, "c2 · Super", INK),
         "c3": (gx0 + 300, gy0 + 248, gx0 + 440, gy0 + 304, "c3 · Ultra", INK),
         "r1": (gx0 + 510, gy0 + 104, gx1 - 40, gy0 + 160, "c4 · passes", GREEN),
         "r2": (gx0 + 510, gy0 + 248, gx1 - 40, gy0 + 304, "c5 · repair", INK)}
for x0, y0, x1, y1, label, color in nodes.values():
    c.d.rounded_rectangle((x0, y0, x1, y1), radius=14, fill="white", outline=color, width=3)
    c.text(((x0 + x1) / 2, y0 + (y1 - y0) / 2 - 13), label, 22, INK, center=True)
for a, b in (("env", "c1"), ("env", "c2"), ("env", "c3"), ("c1", "r1"), ("c3", "r2")):
    ax0, ay0, ax1, ay1 = nodes[a][:4]
    bx0, by0, bx1, by1 = nodes[b][:4]
    c.arrow(ax1, (ay0 + ay1) / 2, bx0, (by0 + by1) / 2, width=3)
for i, ln in enumerate(["each candidate: verb.py + tests + demo, written in parallel",
                        "pass = MotionLang’s fixed contract suite + its own tests",
                        "repairs branch from their parent’s saved image",
                        "model-written code never runs in the harness"]):
    c.bullet(gx0 + 40, gy0 + 330 + i * 32, ln, color=BLUE, size=22)

c.box(1250, 200, 1730, 420, "Critic", ["contact sheet + measurements", "→ Nemotron VL: “no visible", "overshoot” → polish"],
      title_size=32)
c.box(1250, 450, 1730, 670, "Ship", ["pip-installable plugin + diff", "report · preview · try it in", "a scene with/without it"],
      title_size=32)

c.arrow(380, 415, 450, 415, both=True)
c.arrow(1170, 310, 1250, 310, both=True)
c.arrow(1490, 420, 1490, 450)

# Row 2: the models, Tavily, MotionLang
tx0, ty0, tx1, ty1 = 450, 740, 1730, 1110
c.group(tx0, ty0, tx1, ty1, "Nebius Token Factory", "OpenAI-compatible API · candidates written concurrently",
        INK, "white")
cw = (tx1 - tx0 - 26 * 2 - 3 * 16) // 4
models = [("Nemotron 3 Super 120B", ["writes + repairs", "plugin code", "names the verb"]),
          ("Nemotron 3 Ultra 550B", ["an optional", "bigger coder", "per candidate"]),
          ("Nemotron 3.5 Super VL", ["critiques the", "rendered motion"]),
          ("Nemotron 3 Nano 30B", ["needs external", "docs? (~1 s)"])]
for i, (name, what) in enumerate(models):
    x = tx0 + 26 + i * (cw + 16)
    c.model(x, ty0 + 104, x + cw, ty1 - 26, name, what)

c.box(70, 740, 380, 960, "MotionLang", ["verbs are plugins", "a verb is valid when it", "passes the contract suite"],
      title_size=32)
c.box(70, 980, 380, 1110, "Tavily", ["specs for exporters"], title_size=28)

c.arrow(810, 670, 810, 740, color=GREEN)
c.text((824, 690), "every model call", 21, GREEN)
c.arrow(450, 850, 380, 850, width=4)

c.save(__file__)
