Design: a relative scale pulse, sin² so it starts and ends at zero change.

### verb.py
```python
"""mh.pulse: a heartbeat-style scale pulse."""

from __future__ import annotations

import math
from typing import Any

from pydantic import Field

from motionlang.verbs import Verb, VerbParams, register


class PulseParams(VerbParams):
    amount: float = Field(0.2, gt=0, le=2, description="peak scale change (added to sx and sy)")
    beats: int = Field(2, ge=1, le=20, description="number of pulses")


@register
class Pulse(Verb):
    """Pulse the actor's scale up and back down `beats` times, like a heartbeat."""

    name = "mh.pulse"
    Params = PulseParams
    examples = [{"amount": 0.2, "beats": 2}, {"amount": 0.5, "beats": 1, "duration": 0.6}]

    def mode(self, p: PulseParams) -> str:
        return "rel"

    def props(self, p: PulseParams) -> tuple[str, ...]:
        return ("sx", "sy")

    def default_duration(self, p: PulseParams) -> float:
        return 0.5 * p.beats

    def sample(self, p: PulseParams, u: float, t: float, ctx: Any) -> dict[str, Any]:
        d = p.amount * math.sin(math.pi * p.beats * u) ** 2
        return {"sx": d, "sy": d}
```

### test_verb.py
```python
import math

from motionlang import check, load_plugins


def doc(block):
    return {"motion": "0.1", "canvas": {"width": 800, "height": 600, "fps": 60},
            "actors": [{"id": "a", "shape": {"kind": "ellipse", "rx": 30, "ry": 30}, "init": {"x": 400, "y": 300}}],
            "timeline": {"id": "root", "seq": [block]}}


def test_registered():
    assert "mh.pulse" in load_plugins()


def test_peaks_at_amount():
    r = check(doc({"id": "p", "verb": "mh.pulse", "actor": "a", "amount": 0.3, "beats": 1}))
    assert r.ok, r.report.summary()
    m = r.baked.to_json()["actors"][0]["tracks"]["m"]
    peak = max(f[0] for f in m)
    assert math.isclose(peak, 1.3, abs_tol=0.01)
```

### demo.motion.json
```json
{"motion": "0.1", "canvas": {"width": 800, "height": 450, "fps": 30, "background": "#ffffff"},
 "actors": [{"id": "heart", "shape": {"kind": "ellipse", "rx": 50, "ry": 50}, "style": {"fill": "#e4572e"}, "init": {"x": 400, "y": 225}}],
 "timeline": {"id": "root", "seq": [{"id": "beat", "verb": "mh.pulse", "actor": "heart", "amount": 0.3, "beats": 3}]}}
```
