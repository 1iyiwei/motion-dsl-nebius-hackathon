"""Show script → compiled MotionLang → MotionLang validation + flight check."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from motionlang import Report, check
from motionlang.bake import Baked

from .compiler import Compiled, compile_show
from .flight import FlightReport, check_flight
from .show import Show


@dataclass
class Checked:
    show: Show
    compiled: Compiled
    motion: Report  # MotionLang's own report (structure, conflicts, jumps): non-empty means a compiler bug
    flight: FlightReport
    baked: Baked | None

    @property
    def ok(self) -> bool:
        return self.motion.ok and self.flight.ok

    def summary(self) -> dict:
        return {"ok": self.ok, "motion": self.motion.to_dict(), "flight": self.flight.to_dict(),
                "steps": [{"id": s.step, "kind": s.kind, "t0": round(s.t0, 2), "t1": round(s.t1, 2)} for s in self.compiled.spans]}


def load_show(path: Path) -> Show:
    """Load a show file; SVG sources given as paths resolve relative to the file."""
    raw = json.loads(Path(path).read_text())
    svgs = raw.get("sources", {}).get("svgs", {})
    for k, v in svgs.items():
        if isinstance(v, str) and not v.lstrip().startswith("<"):
            svgs[k] = (Path(path).parent / v).read_text() if not Path(v).is_absolute() else Path(v).read_text()
    return Show.model_validate(raw)


def run(show: Show, seed: int = 0) -> Checked:
    comp = compile_show(show, seed=seed)
    res = check(comp.doc)
    flight = check_flight(show, comp, res.baked) if res.baked is not None else FlightReport()
    return Checked(show, comp, res.report, flight, res.baked)
