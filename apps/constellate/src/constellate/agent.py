"""Nemotron in the loop: write the show script, then make it flyable from the checker's and sim's issues.

    plan(intent)          planner (Ultra) writes a show script: artistic choices only
    make_flyable(show)    check → issues → editor (Nano) turns hints into edit ops → recheck, then the
                          wind sweep → sim issues → more ops, until certified or out of rounds
    tweak(show, "...")    editor applies a user's change as edit ops

Edits are JSON-pointer ops on the show script. `limits` are off-limits to the model: the site briefing or
the user sets them, so the agent can't make a show "pass" by loosening its own safety envelope.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import ValidationError

from motionlang import Issue
from motionlang.agent.llm import LLM, extract_json_text

from .pipeline import Checked, run
from .show import Show
from .sweep import Sweep, sim_issues, sweep

Emit = Callable[[dict], None]

SCRIPT_GUIDE = """A show script is JSON:
{"title": str, "color": "#rrggbb" (starting LED colour),
 "formations": {id: formation},
 "steps": [step, ...]}

formation (every kind also takes "center": [fx, fz] as fractions of the airspace, default [0.5, 0.5], and
"size": fraction of the airspace height, default 0.6; text is wide, so keep it about 0.15-0.3):
  {"kind": "preset", "shape": "heart"|"star"|"ring"|"circle"|"grid"|"spiral"|"wave", "fill": "outline"|"fill"}
  {"kind": "stroke", "stroke": <stroke id>, "fill": "outline"|"fill"}   (a line the user drew)
  {"kind": "svg", "svg": <svg id>, "fill": "fill"|"outline", "keep_colors": true}   (the user's artwork)
  {"kind": "text", "text": "UP TO 24 CHARS", "fill": "fill"|"outline"}

step (each has a unique snake_case "id"; the first step must be "form": drones take off into it):
  {"kind": "form", "id", "formation": <formation id>, "transition": seconds, "style": "direct"|"stagger"|"expand",
   "hold": seconds, "color": "#rrggbb" (optional; svg formations keep artwork colours)}
  {"kind": "spin", "id", "turns": revolutions (negative = counter-clockwise), "duration": seconds}
  {"kind": "color", "id", "color": "#rrggbb", "duration": seconds}
  {"kind": "hold", "id", "duration": seconds}

Styles: "direct" = every drone flies straight and all arrive together (provably collision-free; the default);
"stagger" = a left-to-right ripple (looks lively, can cause near misses); "expand" = spread out first, then fly in.
Drones land automatically at the end. Physics is checked afterwards and durations get corrected, so choose
what looks good; typical transitions are 6-12 s, holds 2-5 s, spins of large formations need 20 s or more."""

PLAN_SYSTEM = f"""You are the choreographer of a drone light show. Turn the user's request into a show script.
Make artistic choices only (shapes, order, colours, pacing); a compiler computes every drone's path and a flight
checker and wind simulation verify safety afterwards.

{SCRIPT_GUIDE}

Use only the stroke and svg ids listed in the request. Reply with the show script JSON object only."""

REPAIR_SYSTEM = f"""You fix drone show scripts so they are safe to fly. You get the script and a list of problems found by
the flight checker and the wind simulation. Each problem has a hint naming the exact change; apply the hints, keeping
the show's look. Reply with JSON: {{"ops": [op, ...], "note": "one short sentence on what you changed"}}.

op: {{"op": "set", "path": "/steps/1/duration", "value": 34.5}} (JSON pointer into the script; also
"/formations/heart/size", "/steps/2/style"), {{"op": "add", "path": "/steps/3", "value": {{...step}}}} (inserts
before index 3; "/steps/-" appends), {{"op": "remove", "path": "/steps/2"}}.
Never touch "/limits", "/sources" or "/drones": those are the site's and the user's decisions. When a hint offers a
choice that changes limits (a lower design wind), take the other option.

{SCRIPT_GUIDE}"""

TWEAK_SYSTEM = f"""You edit drone show scripts. Apply the user's change with as few ops as possible.
Reply with JSON: {{"ops": [op, ...], "note": "one short sentence"}}. Op format: {{"op": "set"|"add"|"remove", "path": <JSON pointer>,
"value": ...}}. Never touch "/limits" or "/sources". Set "/drones" (10-500) only when the user asks for a different number of drones.

{SCRIPT_GUIDE}"""

PROTECTED = ("/limits", "/sources", "/drones")


@dataclass
class Round:
    stage: str  # "flight" | "sim"
    issues: list[dict]
    ops: list[dict] = field(default_factory=list)
    note: str = ""


@dataclass
class Outcome:
    show: Show
    checked: Checked
    sweep: Sweep | None
    rounds: list[Round]
    ok: bool

    @property
    def certificate(self) -> dict | None:
        return self.sweep.certificate() if self.sweep else None


def _noop(_: dict) -> None:
    pass


def call_event(call) -> dict:
    return {"type": "call", "msg": str(call), "purpose": call.purpose, "model": call.model.split("/")[-1],
            "seconds": round(call.seconds, 2), "tokens": [call.prompt_tokens, call.completion_tokens], "think": call.think}


def sources_brief(show_or_sources) -> str:
    src = getattr(show_or_sources, "sources", show_or_sources)
    parts = []
    for sid, pts in src.strokes.items():
        parts.append(f"stroke {sid!r}: a drawn line of {len(pts)} points")
    for vid in src.svgs:
        parts.append(f"svg {vid!r}: the user's artwork")
    return "\n".join(parts) or "(no strokes or artwork: use presets and text)"


# ---------- edit ops ----------

def _walk(doc: Any, path: str) -> tuple[Any, str]:
    if not path.startswith("/"):
        raise ValueError(f"path must start with '/': {path!r}")
    parts = [p.replace("~1", "/").replace("~0", "~") for p in path[1:].split("/")]
    cur = doc
    for p in parts[:-1]:
        cur = cur[int(p)] if isinstance(cur, list) else cur[p]
    return cur, parts[-1]


def apply_ops(raw: dict, ops: list[dict], protected: tuple[str, ...] = PROTECTED) -> dict:
    """Apply JSON-pointer ops atomically; raises ValueError naming the failing op."""
    doc = copy.deepcopy(raw)
    for n, op in enumerate(ops):
        try:
            path = op["path"]
            if path.startswith(protected):
                raise ValueError(f"{path} is not editable")
            parent, key = _walk(doc, path)
            kind = op.get("op", "set")
            if isinstance(parent, list):
                if kind == "add":
                    parent.insert(len(parent) if key == "-" else int(key), op["value"])
                elif kind == "remove":
                    parent.pop(int(key))
                else:
                    parent[int(key)] = op["value"]
            else:
                if kind == "remove":
                    parent.pop(key)
                else:
                    parent[key] = op["value"]
        except (KeyError, IndexError, ValueError, TypeError) as e:
            raise ValueError(f"op {n} ({json.dumps(op)[:120]}) failed: {e}") from e
    return doc


def _script(show: Show) -> dict:
    """What the model sees: the script without sources (artwork can be large) or limits (not its call)."""
    d = show.model_dump(mode="json", exclude={"sources", "limits"})
    return d


def _merge(raw: dict, base: Show, allow_drones: bool = False) -> Show:
    raw = dict(raw)
    drones = raw.pop("drones", base.drones) if allow_drones else base.drones
    for k in ("sources", "limits", "drones"):
        raw.pop(k, None)
    return Show.model_validate({**raw, "sources": base.sources.model_dump(), "limits": base.limits.model_dump(), "drones": drones})


def _issue_lines(issues: list[Issue]) -> str:
    return "\n".join(f"- {i}" for i in issues)


# ---------- the three entry points ----------

def plan(intent: str, base: Show, llm: LLM, emit: Emit = _noop, retries: int = 2) -> Show:
    """Planner writes a script for `intent`; `base` carries sources, limits and drone count."""
    msgs = [{"role": "system", "content": PLAN_SYSTEM},
            {"role": "user", "content": f"Request: {intent}\n\nDrones: {base.drones}\nAvailable:\n{sources_brief(base)}"}]
    for attempt in range(retries + 1):
        emit({"type": "call_start", "msg": "planner writes the show script"})
        text, call = llm.json("planner", "show script", msgs)
        emit(call_event(call))
        try:
            raw = json.loads(extract_json_text(text))
            show = _merge(raw, base)
            emit({"type": "plan", "msg": f"{len(show.formations)} formations, {len(show.steps)} steps", "script": _script(show)})
            return show
        except (ValueError, ValidationError) as e:
            err = str(e)[:1500]
            emit({"type": "error", "msg": f"script rejected: {err.splitlines()[0]}"})
            msgs += [{"role": "assistant", "content": text[-6000:]},
                     {"role": "user", "content": f"That script is invalid:\n{err}\nReply with the corrected full script JSON."}]
    raise RuntimeError("the planner did not produce a valid show script")


def _edit(show: Show, system: str, user: str, llm: LLM, emit: Emit, purpose: str, role: str = "editor",
          think: bool = False, protected: tuple[str, ...] = PROTECTED) -> tuple[Show, list[dict], str]:
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    last = ""
    for _ in range(3):
        emit({"type": "call_start", "msg": purpose})
        text, call = llm.json(role, purpose, msgs, think=think, max_tokens=4000, temperature=0.1)
        emit(call_event(call))
        try:
            reply = json.loads(extract_json_text(text))
            ops = reply.get("ops") or []
            new = _merge(apply_ops(show.model_dump(mode="json"), ops, protected), show, allow_drones="/drones" not in protected)
            return new, ops, str(reply.get("note", ""))
        except (ValueError, ValidationError) as e:
            last = str(e)[:1200]
            emit({"type": "error", "msg": f"edit rejected: {last.splitlines()[0]}"})
            msgs += [{"role": "assistant", "content": text[-4000:]},
                     {"role": "user", "content": f"Those ops failed:\n{last}\nReply with corrected ops JSON."}]
    raise RuntimeError(f"the editor could not produce valid ops: {last}")


def tweak(show: Show, request: str, llm: LLM, emit: Emit = _noop) -> tuple[Show, list[dict], str]:
    user = f"Script:\n{json.dumps(_script(show))}\n\nChange: {request}"
    # The user may ask for a different fleet size; the repair loop may not change it.
    return _edit(show, TWEAK_SYSTEM, user, llm, emit, "editor applies the change", protected=("/limits", "/sources"))


def _signature(issues: list[Issue]) -> frozenset:
    return frozenset((i.code, i.block, i.pointer) for i in issues)


def make_flyable(show: Show, llm: LLM, emit: Emit = _noop, max_rounds: int = 6, simulate: bool = True,
                 winds=None, seeds: int | None = None) -> Outcome:
    rounds: list[Round] = []
    seen: set[frozenset] = set()
    sw: Sweep | None = None
    checked = run(show)
    for _ in range(max_rounds + 1):
        if not checked.motion.ok:  # the compiler's own output failed MotionLang validation: a bug, not the model's
            raise RuntimeError("compiled document failed MotionLang validation: " + "; ".join(map(str, checked.motion.errors[:3])))
        issues = [i for i in checked.flight.issues if i.severity == "error"]
        stage = "flight"
        emit({"type": "flight", "msg": "flight check: " + ("clean" if not issues else f"{len(issues)} problems"),
              "issues": [i.to_dict() for i in checked.flight.issues], "stats": checked.flight.stats})
        if not issues and simulate:
            emit({"type": "sim_start", "msg": "wind sweep"})
            kw = {k: v for k, v in (("winds", winds), ("seeds", seeds)) if v is not None}
            sw = sweep(show, checked.compiled, checked.baked, **kw)
            issues = sim_issues(show, checked.compiled, checked.baked, sw)
            stage = "sim"
            emit({"type": "sim", "msg": "wind sweep: " + ("certified" if not issues else f"{len(issues)} problems"),
                  "certificate": sw.certificate(), "by_wind": {str(k): v for k, v in sw.by_wind().items()},
                  "issues": [i.to_dict() for i in issues]})
        if not issues:
            return Outcome(show, checked, sw, rounds, True)
        if len(rounds) >= max_rounds:
            break
        sig = _signature(issues)
        stalled = sig in seen
        seen.add(sig)
        user = (f"Script:\n{json.dumps(_script(show))}\n\nProblems ({stage} check):\n{_issue_lines(issues)}"
                + ("\n\nThe last fix didn't remove these problems; make a bigger change (e.g. a larger formation, a "
                   "longer step, or style direct)." if stalled else ""))
        role, think = ("planner", True) if stalled else ("editor", False)
        show, ops, note = _edit(show, REPAIR_SYSTEM, user, llm, emit, "planner repairs (stalled)" if stalled else "editor applies the hints",
                                role=role, think=think)
        rounds.append(Round(stage, [i.to_dict() for i in issues], ops, note))
        emit({"type": "repair", "msg": note or f"{len(ops)} edits", "ops": ops})
        checked = run(show)
        sw = None
    return Outcome(show, checked, sw, rounds, False)
