"""Show script → compiled MotionLang → MotionLang validation + flight check."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from motionlang import Report, check

from .compiler import Compiled, compile_show
from .flight import FlightReport, check_flight
from .show import Show

# Drones per MotionLang bake. Baking holds every actor's per-frame values at once (~9 MB per drone-minute), so a
# 100-drone show baked whole peaks near 1 GB; drones are independent, so baking them in groups gives the same
# tracks and the same validation at a fraction of the memory (small hosts have 512 MB).
CHUNK = int(os.environ.get("CONSTELLATE_BAKE_CHUNK") or 10)


@dataclass
class Tracks:
    """The baked show, merged from per-chunk bakes: what the checker, sim, video and player need."""

    times: np.ndarray
    fps: int
    duration: float
    pos: dict[str, np.ndarray] = field(default_factory=dict)  # actor -> (F, 2) pivot in canvas px
    props: dict[str, dict[str, list]] = field(default_factory=dict)  # actor -> {"fill": per-frame colours}
    player_json: str = ""  # MotionLang player tracks (precision 2), kept serialized: as Python lists they'd be ~3 MB per drone

    def to_json(self, precision: int = 2) -> dict:
        return json.loads(self.player_json)


@dataclass
class Checked:
    show: Show
    compiled: Compiled
    motion: Report  # MotionLang's own report (structure, conflicts, jumps): non-empty means a compiler bug
    flight: FlightReport
    baked: Tracks | None

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


def check_chunked(doc: dict, chunk: int = CHUNK) -> tuple[Report, Tracks | None]:
    """MotionLang-check a one-actor-per-drone document in groups of `chunk` drones and merge the results."""
    actors = doc["actors"]
    seq_of = {s["id"]: s for s in doc["timeline"]["par"]}  # compiler.py names drone dN's sequence "droneN"
    report = Report()
    merged: Tracks | None = None
    header, actor_js, tween_js = {}, [], []
    for i in range(0, len(actors), chunk):
        part = actors[i:i + chunk]
        sub_seqs = [seq_of[k] for a in part if (k := "drone" + a["id"].removeprefix("d")) in seq_of]
        sub = {**doc, "actors": part, "timeline": {"id": doc["timeline"]["id"], "par": sub_seqs}}
        res = check(sub)
        report.errors += res.report.errors
        report.warnings += res.report.warnings
        if res.baked is None:
            return report, None
        b = res.baked
        js = b.to_json(precision=2)
        if merged is None:
            merged = Tracks(b.times, b.fps, b.duration)
            report.stats = dict(res.report.stats)
            header = {k: v for k, v in js.items() if k not in ("actors", "tweens")}
        actor_js += [json.dumps(a, separators=(",", ":")) for a in js["actors"]]
        tween_js += [json.dumps(t, separators=(",", ":")) for t in js["tweens"]]
        for a in part:
            merged.pos[a["id"]] = b.pos[a["id"]]
            merged.props[a["id"]] = {"fill": b.props[a["id"]]["fill"]}
        del res, b, js  # free this chunk's bake before the next one
    if merged is not None:
        report.stats["actors"] = len(actors)
        head = json.dumps(header, separators=(",", ":"))[:-1]
        merged.player_json = f'{head},"actors":[{",".join(actor_js)}],"tweens":[{",".join(tween_js)}]}}'
    return report, merged


def run(show: Show, seed: int = 0) -> Checked:
    comp = compile_show(show, seed=seed)
    report, tracks = check_chunked(comp.doc)
    flight = check_flight(show, comp, tracks) if tracks is not None else FlightReport()
    return Checked(show, comp, report, flight, tracks)
