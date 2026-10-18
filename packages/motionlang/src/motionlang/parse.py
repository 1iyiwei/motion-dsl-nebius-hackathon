"""Parse a MotionLang JSON document into a typed model + block tree, collecting every error at once."""

from __future__ import annotations

import difflib

from dataclasses import dataclass, field
from typing import Any, Union

from pydantic import ValidationError

from .ease import EaseFn, resolve_ease
from .geometry import Geometry, actor_geometry
from .issues import Issue
from .model import ACTOR_PROPS, CAMERA_ID, CAMERA_PROPS, Actor, Document
from .paths import flatten_svg_path, resolve_path
from .verbs import Verb, load_plugins

CONTAINERS = ("seq", "par", "stagger", "repeat")
VERB_COMMON = {"id", "verb", "actor", "duration", "ease", "delay", "label"}
CONTAINER_COMMON = {"id", "delay", "label"}


@dataclass
class VerbBlock:
    id: str
    verb: Verb
    params: Any
    actor: str | None
    duration: float | None
    ease: EaseFn
    delay: float
    pointer: str
    label: str | None = None


@dataclass
class ContainerBlock:
    id: str
    kind: str  # seq | par | stagger | repeat
    children: list["Block"]
    delay: float
    pointer: str
    offset: float = 0.0  # stagger
    count: int | None = 1  # repeat; None = infinite
    label: str | None = None


Block = Union[VerbBlock, ContainerBlock]


@dataclass
class Parsed:
    doc: Document
    root: Block
    blocks: dict[str, Block]
    actors: dict[str, Actor]
    geometry: dict[str, Geometry] = field(default_factory=dict)

    def verb_blocks(self) -> list[VerbBlock]:
        return [b for b in self.blocks.values() if isinstance(b, VerbBlock)]


def _pointer(base: str, loc: tuple) -> str:
    return base + "".join(f"/{p}" for p in loc)


def _verb_fields(v: Verb) -> list[str]:
    return [f.alias or n for n, f in v.Params.model_fields.items()]


def _unknown_param_hint(name: str, verb: Verb, registry: dict[str, Verb]) -> str:
    accepts = _verb_fields(verb)
    hint = f"{verb.name} accepts: {', '.join(accepts) or '(no parameters)'}; plus common fields id, actor, duration{', ease' if verb.uses_ease else ''}, delay, label"
    close = difflib.get_close_matches(name, accepts + sorted(VERB_COMMON), n=1, cutoff=0.7)
    if close:
        return f"did you mean `{close[0]}`? {hint}"
    owners = sorted(n for n, v in registry.items() if n != verb.name and name in _verb_fields(v))
    if owners:
        hint += f". `{name}` belongs to {', '.join(owners)}"
    return hint


def _pyd_issues(err: ValidationError, base: str, block: str | None = None, verb: Verb | None = None) -> list[Issue]:
    out = []
    for e in err.errors():
        loc = tuple(p for p in e["loc"] if not (isinstance(p, str) and p in ("function-after", "tagged-union")))
        msg = e["msg"].removeprefix("Value error, ")
        field_ = ".".join(str(p) for p in loc) or "(document)"
        hint = _schema_hint(e)
        if verb is not None and e.get("type") == "extra_forbidden" and len(loc) == 1:
            msg = f"`{loc[0]}` is not a parameter of {verb.name}"
            hint = _unknown_param_hint(str(loc[0]), verb, load_plugins())
            out.append(Issue("E_SCHEMA", msg, hint=hint, block=block, pointer=_pointer(base, loc)))
            continue
        out.append(Issue("E_SCHEMA", f"{field_}: {msg}", hint=hint, block=block, pointer=_pointer(base, loc)))
    return out


def _schema_hint(e: dict) -> str:
    t = e.get("type", "")
    if t == "extra_forbidden":
        return "remove this field; it is not part of the verb or block (check spelling)"
    if t == "missing":
        return "add this required field"
    if t == "union_tag_invalid":
        return f"use one of: {e.get('ctx', {}).get('expected_tags', '')}"
    return ""


def _num(raw: dict, key: str, default: float, issues: list[Issue], ptr: str, bid: str | None, minimum: float = 0.0) -> float:
    v = raw.get(key, default)
    if not isinstance(v, (int, float)) or isinstance(v, bool) or v < minimum:
        issues.append(Issue("E_SCHEMA", f"{key} must be a number ≥ {minimum:g}, got {v!r}", block=bid, pointer=f"{ptr}/{key}"))
        return default
    return float(v)


def _parse_block(raw: Any, ptr: str, issues: list[Issue], registry: dict[str, Verb]) -> Block | None:
    if not isinstance(raw, dict):
        issues.append(Issue("E_SCHEMA", "block must be an object", pointer=ptr))
        return None
    bid = raw.get("id")
    if not isinstance(bid, str) or not bid:
        issues.append(Issue("E_SCHEMA", "block needs a non-empty string `id`", hint="give every block a short unique id like \"b1\"", pointer=ptr))
        bid = None
    label = raw.get("label")
    kinds = [k for k in CONTAINERS if k in raw]
    if "verb" in raw and kinds:
        issues.append(Issue("E_SCHEMA", f"block has both `verb` and `{kinds[0]}`", hint="a block is either a verb or a container", block=bid, pointer=ptr))
        return None

    if kinds:
        if len(kinds) > 1:
            issues.append(Issue("E_SCHEMA", f"block has several container keys: {kinds}", block=bid, pointer=ptr))
            return None
        kind = kinds[0]
        allowed = CONTAINER_COMMON | {kind} | ({"offset"} if kind == "stagger" else set()) | ({"count"} if kind == "repeat" else set())
        for extra in sorted(set(raw) - allowed):
            issues.append(Issue("E_SCHEMA", f"unknown field `{extra}` on {kind} block", hint="remove it", block=bid, pointer=f"{ptr}/{extra}"))
        delay = _num(raw, "delay", 0.0, issues, ptr, bid)
        if kind == "repeat":
            child_raw = raw["repeat"]
            child = _parse_block(child_raw, f"{ptr}/repeat", issues, registry)
            children = [child] if child else []
            c = raw.get("count", 2)
            if c == "inf":
                count = None
            elif isinstance(c, int) and not isinstance(c, bool) and c >= 1:
                count = c
            else:
                issues.append(Issue("E_SCHEMA", f"repeat count must be an integer ≥ 1 or \"inf\", got {c!r}", block=bid, pointer=f"{ptr}/count"))
                count = 1
            return ContainerBlock(bid or "?", kind, children, delay, ptr, count=count, label=label)
        items = raw[kind]
        if not isinstance(items, list):
            issues.append(Issue("E_SCHEMA", f"`{kind}` must be a list of blocks", block=bid, pointer=f"{ptr}/{kind}"))
            items = []
        children = [c for i, item in enumerate(items) if (c := _parse_block(item, f"{ptr}/{kind}/{i}", issues, registry))]
        offset = _num(raw, "offset", 0.1, issues, ptr, bid) if kind == "stagger" else 0.0
        return ContainerBlock(bid or "?", kind, children, delay, ptr, offset=offset, label=label)

    name = raw.get("verb")
    if not isinstance(name, str):
        issues.append(Issue("E_SCHEMA", "block needs `verb` or one of seq/par/stagger/repeat", block=bid, pointer=ptr))
        return None
    verb = registry.get(name)
    if verb is None:
        issues.append(Issue("E_REF", f"unknown verb {name!r}", hint=f"use one of: {', '.join(sorted(registry))}", block=bid, pointer=f"{ptr}/verb"))
        return None
    params_raw = {k: v for k, v in raw.items() if k not in VERB_COMMON}
    try:
        params = verb.Params.model_validate(params_raw)
    except ValidationError as e:
        issues.extend(_pyd_issues(e, ptr, bid, verb))
        return None
    actor = raw.get("actor")
    if verb.fixed_actor:
        if actor not in (None, verb.fixed_actor):
            issues.append(Issue("E_SCHEMA", f"{name} always targets `{verb.fixed_actor}`; omit `actor`", block=bid, pointer=f"{ptr}/actor"))
        actor = verb.fixed_actor
    elif verb.needs_actor and not isinstance(actor, str):
        issues.append(Issue("E_SCHEMA", f"{name} needs `actor` (an actor id)", hint="add \"actor\": \"<id>\"", block=bid, pointer=ptr))
        return None
    duration = raw.get("duration")
    if duration is not None:
        duration = _num(raw, "duration", 1.0, issues, ptr, bid)
    try:
        ease = resolve_ease(raw.get("ease", verb.default_ease))
    except ValueError as e:
        issues.append(Issue("E_SCHEMA", str(e), block=bid, pointer=f"{ptr}/ease"))
        ease = resolve_ease(verb.default_ease)
    if "ease" in raw and not verb.uses_ease:
        issues.append(Issue("E_SCHEMA", f"{name} ignores `ease` (its timing is physical)", hint="remove `ease`", severity="warning", block=bid, pointer=f"{ptr}/ease"))
    delay = _num(raw, "delay", 0.0, issues, ptr, bid)
    return VerbBlock(bid or "?", verb, params, actor, duration, ease, delay, ptr, label=label)


def _asset_hint(ref: str, doc: Document) -> str:
    if not doc.assets:
        return "this document has no assets; use another shape kind, or import the SVG first (motionlang import-svg) and pass its assets"
    listed = ", ".join(f"{k} ({v.label})" if v.label and v.label != k else k for k, v in doc.assets.items())
    close = difflib.get_close_matches(ref, list(doc.assets), n=1, cutoff=0.6)
    return (f"did you mean `{close[0]}`? " if close else "") + f"use one of the given asset ids exactly: {listed}"


def _walk(b: Block):
    yield b
    if isinstance(b, ContainerBlock):
        for c in b.children:
            yield from _walk(c)


def parse(raw: dict) -> tuple[Parsed | None, list[Issue]]:
    """Parse and statically check a document. Returns (None, issues) if it can't be evaluated."""
    registry = load_plugins()
    issues: list[Issue] = []
    if not isinstance(raw, dict):
        return None, [Issue("E_SCHEMA", "document must be a JSON object")]
    try:
        doc = Document.model_validate(raw)
    except ValidationError as e:
        issues.extend(i for i in _pyd_issues(e, "") if not (i.pointer or "").startswith("/timeline/"))
        doc = None
    root = _parse_block(raw.get("timeline"), "/timeline", issues, registry) if "timeline" in raw else None
    if root is None and not issues:
        issues.append(Issue("E_SCHEMA", "document needs a `timeline` root block", pointer="/timeline"))
    if doc is None or root is None or any(i.severity == "error" for i in issues):
        return None, issues

    # ---- ids and references ----
    actors: dict[str, Actor] = {}
    for i, a in enumerate(doc.actors):
        if a.id in actors or a.id == CAMERA_ID:
            why = "is reserved for the built-in camera" if a.id == CAMERA_ID else "is used twice"
            issues.append(Issue("E_DUP_ID", f"actor id {a.id!r} {why}", hint="rename one of them", actor=a.id, pointer=f"/actors/{i}/id"))
        actors[a.id] = a
    blocks: dict[str, Block] = {}
    for b in _walk(root):
        if b.id in blocks or b.id in actors or b.id == CAMERA_ID:
            issues.append(Issue("E_DUP_ID", f"block id {b.id!r} is not unique (block and actor ids share one namespace)", hint="rename the block", block=b.id, pointer=b.pointer))
        blocks[b.id] = b

    strokes = {k: s.points for k, s in doc.strokes.items()}
    for i, a in enumerate(doc.actors):
        if a.parent is not None and a.parent not in actors:
            issues.append(Issue("E_REF", f"actor {a.id!r} has unknown parent {a.parent!r}", actor=a.id, pointer=f"/actors/{i}/parent"))
        if a.shape.kind == "stroke" and a.shape.ref not in strokes:
            issues.append(Issue("E_REF", f"actor {a.id!r} references unknown stroke {a.shape.ref!r}", hint=f"defined strokes: {sorted(strokes) or 'none'}", actor=a.id, pointer=f"/actors/{i}/shape/ref"))
        if a.shape.kind == "asset" and a.shape.ref not in doc.assets:
            issues.append(Issue("E_REF", f"actor {a.id!r} references unknown asset {a.shape.ref!r}", hint=_asset_hint(a.shape.ref, doc), actor=a.id, pointer=f"/actors/{i}/shape/ref"))
    # parent cycles
    for a in doc.actors:
        seen, cur = set(), a
        while cur is not None and cur.parent in actors:
            if cur.id in seen:
                issues.append(Issue("E_REF", f"actor {a.id!r} is in a parent cycle", actor=a.id))
                break
            seen.add(cur.id)
            cur = actors[cur.parent]

    known = set(actors) | {CAMERA_ID}
    for b in blocks.values():
        if not isinstance(b, VerbBlock):
            continue
        v, p = b.verb, b.params
        if b.actor is not None and b.actor not in known:
            issues.append(Issue("E_REF", f"{v.name} targets unknown actor {b.actor!r}", hint=f"actors: {sorted(actors)}", block=b.id, pointer=f"{b.pointer}/actor"))
            continue
        for ref in v.actor_refs(p):
            if ref not in known:
                issues.append(Issue("E_REF", f"{v.name} references unknown actor {ref!r}", hint=f"actors: {sorted(actors)}", block=b.id, pointer=b.pointer))
            elif ref == b.actor:
                issues.append(Issue("E_REF", f"{v.name} on {b.actor!r} can't reference itself", block=b.id, pointer=b.pointer))
        for spec in v.path_specs(p):
            try:
                resolve_path(spec, strokes)
            except (ValueError, KeyError, TypeError) as e:
                code = "E_REF" if isinstance(e, KeyError) else "E_SCHEMA"
                issues.append(Issue(code, f"bad path: {str(e).strip(chr(39))}", hint=f"defined strokes: {sorted(strokes) or 'none'}", block=b.id, pointer=f"{b.pointer}/path"))
        if b.actor is not None:
            target = None if b.actor == CAMERA_ID else actors[b.actor]
            allowed = CAMERA_PROPS if target is None else ACTOR_PROPS
            bad = [x for x in v.props(p) if x not in allowed]
            msg = v.check_target(p, target)
            if bad and not msg:
                msg = f"{v.name} can't animate {', '.join(bad)} on {'the camera' if target is None else 'an actor'}"
            if msg:
                issues.append(Issue("E_SCHEMA", msg, block=b.id, pointer=b.pointer))
        if "." in v.name and not any(r.split(">")[0].split("=")[0].strip() == v.name for r in doc.requires):
            issues.append(Issue("E_REF", f"plugin verb {v.name!r} is not listed in `requires`", hint=f"add \"{v.name}\" to requires", severity="warning", block=b.id))
    if any(i.severity == "error" for i in issues):
        return None, issues

    for k, asset in doc.assets.items():
        try:
            flatten_svg_path(asset.d)
        except ValueError as e:
            issues.append(Issue("E_SCHEMA", f"asset {k!r}: bad path data: {e}", hint="assets come from the SVG importer; re-import instead of editing `d`", pointer=f"/assets/{k}/d"))
    if any(i.severity == "error" for i in issues):
        return None, issues

    geometry = {}
    for a in doc.actors:
        try:
            geometry[a.id] = actor_geometry(a, doc)
        except ValueError as e:
            issues.append(Issue("E_SCHEMA", f"actor {a.id!r} shape: {e}", actor=a.id))
    if any(i.severity == "error" for i in issues):
        return None, issues
    return Parsed(doc, root, blocks, actors, geometry), issues
