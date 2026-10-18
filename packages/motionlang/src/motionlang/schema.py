"""JSON Schema and LLM prompt reference, both generated from the verb registry.

A newly registered verb shows up in both automatically; nothing here names a verb.
"""

from __future__ import annotations

import json
from typing import Any

from .ease import EASES
from .model import Document
from .verbs import Verb, load_plugins

COMMON_DOC = {
    "id": "unique block id (blocks and actors share one namespace)",
    "actor": "target actor id",
    "duration": "seconds; default depends on the verb",
    "ease": "easing name or {\"bezier\": [x1, y1, x2, y2]}",
    "delay": "seconds to wait before starting (within the parent container)",
    "label": "free-form note",
}


def _ease_schema() -> dict:
    return {
        "anyOf": [
            {"type": "string", "enum": sorted(EASES)},
            {
                "type": "object",
                "properties": {"bezier": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4}},
                "required": ["bezier"],
                "additionalProperties": False,
            },
        ]
    }


def _verb_schema(v: Verb, defs: dict) -> dict:
    ps = v.Params.model_json_schema(by_alias=True, ref_template="#/$defs/{model}")
    defs.update(ps.pop("$defs", {}))
    props: dict[str, Any] = {
        "id": {"type": "string", "description": COMMON_DOC["id"]},
        "verb": {"const": v.name},
    }
    required = ["id", "verb"]
    if v.needs_actor and not v.fixed_actor:
        props["actor"] = {"type": "string", "description": COMMON_DOC["actor"]}
        required.append("actor")
    props["duration"] = {"type": "number", "minimum": 0, "description": COMMON_DOC["duration"]}
    if v.uses_ease:
        props["ease"] = {"$ref": "#/$defs/Ease"}
    props["delay"] = {"type": "number", "minimum": 0, "description": COMMON_DOC["delay"]}
    props["label"] = {"type": "string"}
    props.update(ps.get("properties", {}))
    required += [r for r in ps.get("required", []) if r not in required]
    return {
        "type": "object",
        "title": v.name,
        "description": v.summary(),
        "properties": props,
        "required": required,
        "additionalProperties": False,
    }


def _container_schemas() -> dict[str, dict]:
    common = {
        "id": {"type": "string"},
        "delay": {"type": "number", "minimum": 0},
        "label": {"type": "string"},
    }
    block_list = {"type": "array", "items": {"$ref": "#/$defs/Block"}}
    mk = lambda key, extra, desc: {  # noqa: E731
        "type": "object",
        "description": desc,
        "properties": {**common, key: block_list if key != "repeat" else {"$ref": "#/$defs/Block"}, **extra},
        "required": ["id", key],
        "additionalProperties": False,
    }
    return {
        "Seq": mk("seq", {}, "children run one after another"),
        "Par": mk("par", {}, "children start together; lasts as long as the longest"),
        "Stagger": mk("stagger", {"offset": {"type": "number", "minimum": 0, "default": 0.1}}, "like par, child i starts i*offset later"),
        "Repeat": mk(
            "repeat",
            {"count": {"anyOf": [{"type": "integer", "minimum": 1}, {"const": "inf"}], "default": 2}},
            "plays its single child `count` times",
        ),
    }


def document_schema() -> dict:
    """Full JSON Schema (draft 2020-12) for a MotionLang document."""
    registry = load_plugins()
    schema = Document.model_json_schema(by_alias=True, ref_template="#/$defs/{model}")
    defs = schema.setdefault("$defs", {})
    defs["Ease"] = _ease_schema()
    containers = _container_schemas()
    defs.update(containers)
    verb_refs = []
    for name in sorted(registry):
        key = "Verb_" + name.replace(".", "_")
        defs[key] = _verb_schema(registry[name], defs)
        verb_refs.append({"$ref": f"#/$defs/{key}"})
    defs["Block"] = {"anyOf": [{"$ref": f"#/$defs/{k}"} for k in containers] + verb_refs}
    schema["properties"]["timeline"] = {"$ref": "#/$defs/Block", "description": "root block"}
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "MotionLang document"
    return schema


def _type_str(s: dict) -> str:
    if "anyOf" in s:
        return " | ".join(_type_str(x) for x in s["anyOf"] if x.get("type") != "null")
    if "enum" in s:
        return " | ".join(json.dumps(e) for e in s["enum"])
    if "const" in s:
        return json.dumps(s["const"])
    t = s.get("type")
    if t == "array":
        if "prefixItems" in s:
            return "[" + ", ".join(_type_str(x) for x in s["prefixItems"]) + "]"
        return f"{_type_str(s.get('items', {}))}[]"
    if t == "object" and "additionalProperties" in s and isinstance(s["additionalProperties"], dict):
        keys = s.get("propertyNames", {}).get("enum")
        return "{" + ("|".join(keys) if keys else "str") + ": " + _type_str(s["additionalProperties"]) + "}"
    return {"number": "num", "integer": "int", "string": "str", "boolean": "bool"}.get(t or "", t or "any")


def _model_fields(model: type) -> str:
    ps = model.model_json_schema(by_alias=True)  # type: ignore[attr-defined]
    req = set(ps.get("required", []))
    out = []
    for name, f in ps.get("properties", {}).items():
        if name == "kind":
            continue
        s = f"{name}{'' if name in req else '?'}: {_type_str(f)}"
        if f.get("default") not in (None, [], {}):
            s += f" = {json.dumps(f['default'])}"
        out.append(s)
    return ", ".join(out)


def _actors_section() -> list[str]:
    from typing import get_args

    from .model import Actor, Init, Shape, Style

    union = get_args(get_args(Shape)[0])
    lines = ["## Actors", f"actor: {{{_model_fields(Actor).replace(': any', '')}}}"]
    for m in union:
        kind = m.model_fields["kind"].annotation.__args__[0]
        fields = _model_fields(m)
        lines.append(f'- shape {{"kind": "{kind}"{", " + fields if fields else ""}}}')
    lines.append(f"- style: {{{_model_fields(Style)}}}")
    lines.append(f"- init: {{{_model_fields(Init)}}} (omit x/y only for drawn shapes (stroke, polygon, path, asset): they stay where drawn, even inside a group; others default to canvas center)")
    lines.append('Strokes: document.strokes = {"id": {"points": [[x, y], ...], "width"?: num}}.')
    lines.append(
        'Assets: document.assets holds imported vector parts (e.g. pieces of a logo). They are given to you: '
        'show one with shape {"kind": "asset", "ref": "<asset id>"} with init x/y at the part\'s resting position (omitting x/y gives the same). '
        "Never write or change asset path data."
    )
    return lines


def decoding_schema() -> dict[str, Any]:
    """document_schema() for constrained decoding: keywords the Token Factory grammar engine rejects
    (propertyNames) and prose (title/description/default/examples) removed."""
    drop = {"propertyNames", "title", "description", "default", "examples"}

    def clean(n: Any, names: bool = False) -> Any:
        """names=True: n maps field names to schemas (properties/$defs), so its keys are kept as they are."""
        if isinstance(n, dict):
            return {k: clean(v, k in ("properties", "$defs") and not names) for k, v in n.items() if names or k not in drop}
        if isinstance(n, list):
            return [clean(v) for v in n]
        return n

    out = clean(document_schema())
    out.pop("$schema", None)
    return out


def prompt_reference() -> str:
    """Compact Markdown reference of the language for an LLM system prompt."""
    registry = load_plugins()
    lines = [
        "# MotionLang DSL v0.1 reference",
        "",
        "Coordinates: canvas px, origin top-left, +y down. Angles: degrees, clockwise-positive. Time: seconds.",
        "Actor props: x, y (pivot position), rotation, sx, sy (scale), opacity, trim (0–1 visible part of a stroke), fill, stroke, "
        "clip_l/clip_t/clip_r/clip_b (0–1 hidden part from each side; animate them with reveal).",
        "Built-in actor `camera` (x, y = point shown at canvas center; zoom; rotation).",
        "",
        *_actors_section(),
        "",
        "## Containers",
        '- {"id", "seq": [blocks]}: one after another',
        '- {"id", "par": [blocks]}: together',
        '- {"id", "stagger": [blocks], "offset": 0.1}: child i starts i*offset later',
        '- {"id", "repeat": block, "count": 2 | "inf"}',
        "All blocks accept `delay`. Verb blocks: {\"id\", \"verb\", \"actor\", \"duration\"?, \"ease\"?, ...params}.",
        "",
        "## Composition rule",
        "- abs verbs set a value; rel verbs add on top. Two abs verbs on the same actor+prop at overlapping times = error E_CONFLICT.",
        "- abs verbs start from the current value, so they never jump (except draw_on). move_along needs the actor at the path start unless `relative: true`.",
        "",
        "## Verbs",
    ]
    for name in sorted(registry):
        v = registry[name]
        ps = v.Params.model_json_schema(by_alias=True)
        req = set(ps.get("required", []))
        params = []
        for pname, pschema in ps.get("properties", {}).items():
            if "$ref" in pschema:
                pschema = ps["$defs"][pschema["$ref"].split("/")[-1]]
            s = f"{pname}{'' if pname in req else '?'}: {_type_str(pschema)}"
            if "default" in pschema and pschema["default"] is not None:
                s += f" = {json.dumps(pschema['default'])}"
            if pschema.get("description"):
                s += f" ({pschema['description']})"
            params.append(s)
        mode = "/".join(sorted({(v.mode(v.Params.model_validate({k: x for k, x in ex.items() if k not in COMMON_DOC}))) for ex in v.examples} - {None})) or "none"
        flags = []
        if not v.uses_ease:
            flags.append("no ease")
        if v.fixed_actor or not v.needs_actor:
            flags.append("no actor")
        lines.append(f"- **{name}** [{mode}{', ' + ', '.join(flags) if flags else ''}]: {v.summary()}")
        if params:
            lines.append("  - params: " + "; ".join(params))
        if v.examples:
            ex = {"id": "b1", "verb": name, **({"actor": "a"} if v.needs_actor and not v.fixed_actor else {}), **v.examples[0]}
            lines.append(f"  - e.g. `{json.dumps(ex)}`")
    lines += [
        "",
        f"## Eases\n{', '.join(sorted(EASES))}, or {{\"bezier\": [x1, y1, x2, y2]}}. Default inOutQuad.",
        "",
        "## Constraints (document.constraints)",
        "in_bounds {actors: \"*\"|[ids], margin}; max_speed {actor, px_per_s}; smooth {actor, max_jerk}; "
        "no_overlap {a, b, during?: [t0, t1]}; ends_at {actor, pos: [x, y], t?, tol}; total_duration {max? | exact?, tol}.",
    ]
    return "\n".join(lines)
