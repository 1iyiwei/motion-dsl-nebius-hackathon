"""Generate → validate → repair, and fast edits with escalation.

    generate("a ball bounces then a logo pops in")    planner writes a document; validator
                                                      errors go back to it until it's clean
    tweak(doc, "slower and bouncier")                 fast editor emits a few edit ops without
                                                      reasoning (~1 s); on failure it retries with
                                                      reasoning; then the planner regenerates
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any, Literal

from ..edits import apply_edits
from ..issues import Report
from ..validate import check
from . import prompts
from .llm import LLM, Call, extract_json_text

DEFAULT_CANVAS = {"width": 1280, "height": 720, "background": "#ffffff", "fps": 60}


@dataclass
class Attempt:
    kind: Literal["generate", "repair", "edit", "edit-repair", "regenerate"]
    call: Call
    doc: dict[str, Any] | None = None
    report: Report | None = None
    error: str | None = None  # unparseable reply
    raw: str = ""  # the model's reply, for debugging (-v)

    @property
    def ok(self) -> bool:
        return self.report is not None and self.report.ok

    def signature(self) -> frozenset:
        """What's wrong, ignoring wording and exact numbers: used to detect a stalled repair loop."""
        if self.error or not self.report:
            return frozenset({("UNPARSEABLE",)})
        return frozenset((e.code, e.block, e.actor) for e in self.report.errors)

    def problems(self) -> list[str]:
        if self.error:
            return [self.error]
        return [str(e) for e in self.report.errors] if self.report else []

    def __str__(self) -> str:
        status = "ok" if self.ok else f"{len(self.problems())} problem(s)"
        return f"{self.kind}: {status} ({self.call})"


@dataclass
class Outcome:
    attempts: list[Attempt] = field(default_factory=list)
    path: str = ""  # how the final doc was produced: generate | edit | regenerate
    note: str = ""  # why the loop stopped early, if it did

    @property
    def final(self) -> Attempt | None:
        return self.attempts[-1] if self.attempts else None

    @property
    def ok(self) -> bool:
        return bool(self.final and self.final.ok)

    @property
    def doc(self) -> dict[str, Any] | None:
        """Last valid document, else the last parseable one."""
        for a in reversed(self.attempts):
            if a.ok:
                return a.doc
        return next((a.doc for a in reversed(self.attempts) if a.doc is not None), None)

    @property
    def best(self) -> Attempt | None:
        """The attempt with the fewest problems among those that produced a document."""
        docs = [a for a in self.attempts if a.doc is not None]
        return min(docs, key=lambda a: (len(a.problems()), -self.attempts.index(a))) if docs else None

    @property
    def seconds(self) -> float:
        return sum(a.call.seconds for a in self.attempts)


def _normalize(doc: dict[str, Any], canvas: dict[str, Any], intent: str | None, strokes: dict | None) -> dict[str, Any]:
    """App policy the model shouldn't have to get right: version, canvas, strokes, on-screen check."""
    doc = copy.deepcopy(doc)
    doc["motion"] = "0.1"
    doc["canvas"] = {**canvas, **{k: v for k, v in doc.get("canvas", {}).items() if k == "background"}}
    if strokes:
        doc["strokes"] = {**doc.get("strokes", {}), **strokes}
    cons = doc.setdefault("constraints", [])
    if isinstance(cons, list) and not any(isinstance(c, dict) and c.get("type") == "in_bounds" for c in cons):
        cons.append({"type": "in_bounds", "margin": 8})
    if intent:
        meta = doc.setdefault("meta", {})
        if isinstance(meta, dict):
            meta.setdefault("intent", intent)
    return doc


def _parse_reply(text: str) -> tuple[dict | None, str | None]:
    try:
        doc = json.loads(extract_json_text(text))
    except (ValueError, json.JSONDecodeError) as e:
        return None, f"reply was not valid JSON ({e}); reply with a single JSON object"
    if not isinstance(doc, dict):
        return None, "reply must be a JSON object"
    return doc, None


def _document_rounds(
    llm: LLM, messages: list[dict], first_kind: str, canvas: dict, intent: str | None, strokes: dict | None,
    max_repairs: int, out: Outcome,
) -> None:
    kind = first_kind
    for _ in range(max_repairs + 1):
        text, call = llm.json("planner", kind, messages)
        doc, err = _parse_reply(text)
        att = Attempt(kind, call, error=err, raw=text)  # type: ignore[arg-type]
        if doc is not None:
            att.doc = _normalize(doc, canvas, intent, strokes)
            att.report = check(att.doc).report
        out.attempts.append(att)
        if att.ok:
            return
        prev = out.attempts[-2] if len(out.attempts) > 1 and out.attempts[-2].kind in ("generate", "repair", "regenerate") else None
        if prev is not None and prev.signature() == att.signature() and att.signature() != frozenset({("UNPARSEABLE",)}):
            out.note = (
                "stopped: the same problem survived a repair, so the request probably conflicts with a constraint "
                "or the scene layout; rephrase the request or relax the constraint"
            )
            return
        messages += [
            {"role": "assistant", "content": text if doc is None else json.dumps(att.doc, separators=(",", ":"))},
            {"role": "user", "content": prompts.repair_user(att.problems())},
        ]
        kind = "repair"


def generate(
    intent: str,
    *,
    canvas: dict[str, Any] | None = None,
    strokes: dict[str, Any] | None = None,
    llm: LLM | None = None,
    max_repairs: int = 3,
) -> Outcome:
    llm = llm or LLM()
    canvas = {**DEFAULT_CANVAS, **(canvas or {})}
    messages = [
        {"role": "system", "content": prompts.generation_system()},
        {"role": "user", "content": prompts.generation_user(intent, canvas, strokes)},
    ]
    out = Outcome(path="generate")
    _document_rounds(llm, messages, "generate", canvas, intent, strokes, max_repairs, out)
    return out


def tweak(doc: dict[str, Any], instruction: str, *, llm: LLM | None = None, max_repairs: int = 2) -> Outcome:
    """Small change via edit ops from the fast editor model; escalate to the planner if needed."""
    llm = llm or LLM()
    canvas = doc.get("canvas", DEFAULT_CANVAS)
    out = Outcome(path="edit")
    messages = [
        {"role": "system", "content": prompts.edit_system()},
        {"role": "user", "content": prompts.edit_user(doc, instruction)},
    ]
    kind = "edit"
    for think in (False, True):  # fast try without reasoning, then one reasoned repair
        text, call = llm.json("editor", kind, messages, think=think, max_tokens=4000, temperature=0.2)
        reply, err = _parse_reply(text)
        att = Attempt(kind, call, error=err, raw=text)  # type: ignore[arg-type]
        if reply is not None:
            ops = reply.get("ops")
            if not isinstance(ops, list):
                att.error = 'reply must be {"ops": [...]}'
            else:
                new, issues = apply_edits(doc, ops)
                if issues:
                    att.error = str(issues[0])
                else:
                    att.doc = new
                    att.report = check(new).report
        out.attempts.append(att)
        if att.ok:
            return out
        messages += [
            {"role": "assistant", "content": text},
            {"role": "user", "content": "That didn't work:\n" + "\n".join(f"- {p}" for p in att.problems())
             + '\nReply with corrected {"ops": [...]} for the ORIGINAL document.'},
        ]
        kind = "edit-repair"

    out.path = "regenerate"
    messages = [
        {"role": "system", "content": prompts.generation_system()},
        {"role": "user", "content": prompts.regenerate_user(doc, instruction)},
    ]
    _document_rounds(llm, messages, "regenerate", canvas, None, None, max_repairs, out)
    return out
