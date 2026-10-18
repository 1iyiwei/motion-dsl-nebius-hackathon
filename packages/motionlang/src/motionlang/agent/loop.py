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
from typing import Any, Callable, Literal

from ..edits import apply_edits
from ..issues import Report
from ..schema import decoding_schema
from ..validate import check
from . import prompts
from .llm import _FENCE, _THINK, LLM, Call, extract_json_text, prefers_structured

DEFAULT_CANVAS = {"width": 1280, "height": 720, "background": "#ffffff", "fps": 60}


@dataclass
class Attempt:
    kind: Literal["generate", "repair", "repair-edit", "edit", "edit-repair", "regenerate"]
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
        return frozenset((e.code, e.block, e.actor, e.pointer) for e in self.report.errors)

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


OnAttempt = Callable[[Attempt], None]


def _normalize(
    doc: dict[str, Any], canvas: dict[str, Any], intent: str | None, strokes: dict | None, assets: dict | None = None,
    suggested: list[dict] | None = None, restore_missing: bool = True,
) -> dict[str, Any]:
    """App policy the model shouldn't have to get right: version, canvas, strokes, assets, on-screen check,
    and the suggested actors' layout."""
    doc = copy.deepcopy(doc)
    doc["motion"] = "0.1"
    if suggested and isinstance(doc.get("actors"), list):
        doc["actors"] = _with_suggested(doc["actors"], suggested, restore_missing)
    if "timeline" not in doc and isinstance(doc.get("animations"), (list, dict)):
        doc["timeline"] = doc.pop("animations")  # a common slip; the content is usually right
    if isinstance(doc.get("timeline"), list):
        doc["timeline"] = {"id": "root" if not _has_id(doc["timeline"], "root") else "timeline_root", "seq": doc["timeline"]}
    doc["canvas"] = {**canvas, **{k: v for k, v in doc.get("canvas", {}).items() if k == "background"}}
    if strokes:
        doc["strokes"] = {**doc.get("strokes", {}), **strokes}
    if assets:
        doc["assets"] = copy.deepcopy(assets)  # the user's assets win over anything the model wrote
    cons = doc.setdefault("constraints", [])
    if isinstance(cons, list) and not any(isinstance(c, dict) and c.get("type") == "in_bounds" for c in cons):
        cons.append({"type": "in_bounds", "margin": 8})
    if intent:
        meta = doc.setdefault("meta", {})
        if isinstance(meta, dict):
            meta.setdefault("intent", intent)
    return doc


def _with_suggested(actors: list, suggested: list[dict], restore_missing: bool = True) -> list:
    """Fill what the model left out from the suggested actors, by id, so it only states what it changes.

    Constrained decoding (Super-120B) tends to emit `null` or drop optional fields like `init`, which loses
    the drawn layout (group offsets). Null fields are dropped; missing shape/parent/pivot and missing init
    keys come from the suggestion. With restore_missing, a suggested actor the model left out entirely is
    added back (a logo part that silently disappears is never what the user wants); a regenerate passes
    False, since the edit may have removed it on purpose."""
    by_id = {a["id"]: a for a in suggested if isinstance(a, dict) and "id" in a}
    out, seen = [], set()
    for a in actors:
        if not isinstance(a, dict):
            out.append(a)
            continue
        a = {k: v for k, v in a.items() if v is not None}
        sug = by_id.get(a.get("id"))
        if sug is not None:
            seen.add(a["id"])
            for k, v in sug.items():
                if k == "init":
                    init = a.get("init") if isinstance(a.get("init"), dict) else {}
                    a["init"] = {**v, **{ik: iv for ik, iv in init.items() if iv is not None}}
                elif k not in a:
                    a[k] = copy.deepcopy(v)
        out.append(a)
    if restore_missing:
        out += [copy.deepcopy(a) for aid, a in by_id.items() if aid not in seen]
    return out


def _has_id(node: Any, bid: str) -> bool:
    if isinstance(node, dict):
        return node.get("id") == bid or any(_has_id(v, bid) for v in node.values())
    return isinstance(node, list) and any(_has_id(v, bid) for v in node)


def _parse_reply(text: str) -> tuple[dict | None, str | None]:
    try:
        doc = json.loads(extract_json_text(text))
    except (ValueError, json.JSONDecodeError) as e:
        stripped = _FENCE.sub("", _THINK.sub("", text)).strip()
        if stripped.startswith("["):  # a bare list of edit ops
            try:
                doc = json.loads(stripped)
            except json.JSONDecodeError:
                return None, f"reply was not valid JSON ({e}); reply with a single JSON object"
        else:
            return None, f"reply was not valid JSON ({e}); reply with a single JSON object"
    # Edit ops sent bare (one op, or a list) instead of {"ops": [...]}: a common slip in patch rounds.
    if isinstance(doc, list) and all(isinstance(o, dict) and "op" in o for o in doc):
        return {"ops": doc}, None
    if isinstance(doc, dict) and "op" in doc and "timeline" not in doc:
        return {"ops": [doc]}, None
    if not isinstance(doc, dict):
        return None, "reply must be a JSON object"
    if len(doc) == 1:  # a slip seen from Super-120B: the whole document escaped into one key
        (key,) = doc
        if '":' in key:
            for tail in ("", "}", '"}', "]}"):
                try:
                    inner, _ = json.JSONDecoder().raw_decode('{"' + key + tail)
                except json.JSONDecodeError:
                    continue
                if isinstance(inner, dict):
                    return inner, None
    return doc, None


OPS_REPAIR_MAX = 12  # a parseable document with at most this many problems is patched with edit ops
DOC_KINDS = ("generate", "repair", "repair-edit", "regenerate")


def _document_rounds(
    llm: LLM, messages: list[dict], first_kind: str, canvas: dict, intent: str | None, strokes: dict | None,
    max_repairs: int, out: Outcome, assets: dict | None = None, on_attempt: "OnAttempt | None" = None,
    structured: bool = False, suggested: list[dict] | None = None, restore_missing: bool = True,
) -> None:
    """Planner writes a document, then repairs it. A near-valid document is repaired with edit ops on
    that document (whole-document rewrites drift); otherwise, or if patching stalls, it is rewritten."""
    kind = first_kind
    base: dict | None = None  # the document that ops apply to, in "repair-edit" rounds
    rewrote_after_stall = False
    for _ in range(max_repairs + 1):
        schema = decoding_schema() if structured and kind != "repair-edit" else None
        text, call = llm.json("planner", kind, messages, schema=schema)
        reply, err = _parse_reply(text)
        att = Attempt(kind, call, error=err, raw=text)  # type: ignore[arg-type]
        applied = False
        if reply is not None and kind == "repair" and "ops" in reply and "timeline" not in reply and base is not None:
            att.kind = kind = att.call.purpose = "repair-edit"  # the model patched instead of rewriting; apply the patch
        if reply is not None and kind == "repair-edit" and "ops" not in reply:
            att.kind = kind = att.call.purpose = "repair"  # the model rewrote the document anyway; take it as a rewrite
        if reply is not None and kind == "repair-edit":
            ops = reply.get("ops")
            if not isinstance(ops, list):
                att.error = 'reply must be {"ops": [...]}'
            else:
                new, issues = apply_edits(base, ops)  # type: ignore[arg-type]
                if issues:
                    att.error = str(issues[0])
                else:
                    applied = True
                    att.doc = _normalize(new, canvas, intent, strokes, assets, suggested, restore_missing)
        elif reply is not None:
            att.doc = _normalize(reply, canvas, intent, strokes, assets, suggested, restore_missing)
        if att.doc is not None:
            att.report = check(att.doc).report
        out.attempts.append(att)
        if on_attempt:
            on_attempt(att)
        if att.ok:
            return

        prev = next((a for a in reversed(out.attempts[:-1]) if a.kind in DOC_KINDS and a.report is not None), None)
        stalled = att.report is not None and prev is not None and prev.signature() == att.signature()
        if stalled and (kind != "repair-edit" or rewrote_after_stall):
            out.note = (
                "stopped: the same problems survived a repair, so the request probably conflicts with a constraint "
                "or the scene layout; rephrase the request or relax the constraint"
            )
            return

        # Patch only a document with the right overall shape and few problems; a wrong top level needs a rewrite.
        if att.doc is not None and not stalled and _patchable(att.report):  # type: ignore[arg-type]
            base = att.doc
            if kind == "repair-edit":
                messages += [{"role": "assistant", "content": text},
                             {"role": "user", "content": prompts.repair_ops_user(att.problems(), att.doc)}]
            else:
                messages += [{"role": "assistant", "content": _shown(att.doc)},
                             {"role": "user", "content": prompts.repair_ops_user(att.problems())}]
            kind = "repair-edit"
        elif kind == "repair-edit" and not applied and att.doc is None and base is not None:
            messages += [{"role": "assistant", "content": text}, {"role": "user", "content": prompts.edit_failed_user(att.error or "")}]
        else:  # far from valid, unparseable, or patching stalled: rewrite the whole document
            rewrote_after_stall = rewrote_after_stall or stalled
            base = att.doc if att.doc is not None else base
            last = att.doc if att.doc is not None else base
            if last is not None and kind == "repair-edit":
                messages += [{"role": "assistant", "content": text},
                             {"role": "user", "content": f"Current document:\n{_shown(last)}\n\n" + prompts.repair_user(att.problems())}]
            else:
                messages += [{"role": "assistant", "content": text if att.doc is None else _shown(att.doc)},
                             {"role": "user", "content": prompts.repair_user(att.problems())}]
            kind = "repair"


def _patchable(report: Report) -> bool:
    top_level = any(e.code == "E_SCHEMA" and (e.pointer or "").count("/") <= 1 for e in report.errors)
    return not top_level and len(report.errors) <= OPS_REPAIR_MAX


def _shown(doc: dict) -> str:
    return json.dumps(prompts.without_asset_data(doc), separators=(",", ":"))


def generate(
    intent: str,
    *,
    canvas: dict[str, Any] | None = None,
    strokes: dict[str, Any] | None = None,
    assets: dict[str, Any] | None = None,
    suggested_actors: list[dict] | None = None,
    llm: LLM | None = None,
    max_repairs: int = 3,
    on_attempt: OnAttempt | None = None,
    structured: bool | None = None,
) -> Outcome:
    """`on_attempt` is called right after each attempt is checked (for live progress UIs).

    `structured=True` constrains the planner's documents to the MotionLang JSON Schema (Token Factory
    structured output), which removes format slips. None (default) turns it on exactly when the planner
    is a Super model (llm.prefers_structured); Ultra degenerates under constrained decoding.

    `assets` / `suggested_actors` are what importers.svg.import_svg returns; the model sees only a
    summary (ids, labels, bboxes, colours) and the assets are merged into every document it writes."""
    llm = llm or LLM()
    if structured is None:
        structured = prefers_structured(llm.model_for("planner"))
    canvas = {**DEFAULT_CANVAS, **(canvas or {})}
    messages = [
        {"role": "system", "content": prompts.generation_system()},
        {"role": "user", "content": prompts.generation_user(intent, canvas, strokes, assets, suggested_actors)},
    ]
    out = Outcome(path="generate")
    _document_rounds(llm, messages, "generate", canvas, intent, strokes, max_repairs, out, assets, on_attempt, structured, suggested_actors)
    return out


def tweak(
    doc: dict[str, Any], instruction: str, *, llm: LLM | None = None, max_repairs: int = 2, on_attempt: OnAttempt | None = None,
) -> Outcome:
    """Small change via edit ops from the fast editor model; escalate to the planner if needed.

    `on_attempt` is called right after each attempt is checked (for live progress UIs)."""
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
        if on_attempt:
            on_attempt(att)
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
    _document_rounds(llm, messages, "regenerate", canvas, None, None, max_repairs, out, doc.get("assets"), on_attempt,
                     prefers_structured(llm.model_for("planner")), doc.get("actors"), restore_missing=False)
    return out
