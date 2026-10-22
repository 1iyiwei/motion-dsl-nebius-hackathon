"""The two model calls behind filing and recall: Nemotron VL describes an item, Nano re-ranks search hits."""

from __future__ import annotations

import json
import re
import time
from typing import Any

from motionlang.agent.llm import LLM, Call, extract_json_text

CAPTION_PROMPT = """You are filing an item into a motion designer's private studio library so it can be found later by a vague description.
Describe what you see. {hint}
Reply with JSON only:
{{"title": "3-6 words naming the item", "caption": "1-2 sentences: what it is, its style, composition and colours",
 "kind": "sketch | logo | illustration | photo | animation | ui | typography | reference | other",
 "tags": ["8-14 lowercase search words: subject, objects, style, mood, medium, colours"],
 "palette": ["up to 5 dominant colours as #rrggbb"]}}"""

RERANK_PROMPT = """Today is {today}. The user is looking for something in their studio library: "{query}"
Candidates (id | filed date | title | caption | tags):
{items}
Score how well each candidate matches the request: 3 = clearly what they mean, 2 = plausibly, 1 = only loosely related,
0 = no. Judge the subject, not shared colours or style. Use the dates for time words like "last week" or "today".
Reply with JSON only: {{"scores": {{"<id>": <0-3>, ...}}}}"""


def _parse(text: str) -> dict[str, Any]:
    return json.loads(extract_json_text(text))


def caption(llm: LLM, preview_url: str, *, filename: str, note: str = "", facts: dict | None = None) -> tuple[dict, Call]:
    hint = f"The file is called {filename!r}."
    if note:
        hint += f" The user said about it: {note!r}."
    if facts:
        hint += f" It is an animation strip (frames left to right); its program uses {json.dumps(facts)}."
    msgs = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": preview_url}},
                                         {"type": "text", "text": CAPTION_PROMPT.format(hint=hint)}]}]
    text, call = llm.json("vision", "describe item", msgs, think=False, max_tokens=800, temperature=0.2)
    d = _parse(text)
    tags = [str(t).strip().lower() for t in d.get("tags", []) if str(t).strip()]
    kind = str(d.get("kind", "")).strip().lower()
    if kind and kind not in tags:
        tags.insert(0, kind)
    palette = [c for c in d.get("palette", []) if isinstance(c, str) and c.startswith("#")][:5]
    return {"title": str(d.get("title", "")).strip()[:80], "caption": str(d.get("caption", "")).strip()[:500],
            "tags": tags[:16], "palette": palette}, call


def rerank(llm: LLM, query: str, items: list[dict], limit: int) -> tuple[list[str], Call]:
    lines = "\n".join(f"- {i['id']} | {i['filed']} | {i['title']} | {i['caption']} | {', '.join(i['tags'])}" for i in items)
    prompt = RERANK_PROMPT.format(today=time.strftime("%Y-%m-%d (%A)"), query=query, items=lines, limit=limit)
    text, call = llm.json("editor", "recall", [{"role": "user", "content": prompt}], think=False,
                          max_tokens=200 + 20 * len(items), temperature=0)
    scores = _parse(text).get("scores") or {}
    order = {i["id"]: n for n, i in enumerate(items)}  # search order breaks ties
    keep = [k for k, v in scores.items() if k in order and isinstance(v, (int, float)) and v >= 2]
    keep.sort(key=lambda k: (-scores[k], order[k]))
    return keep[:limit], call


MISSING_PROMPT = """An animation was planned from this request: "{brief}"
The animation's actors (id | shape | fill | text):
{actors}
Which objects named in the request does no actor depict? Count only things to be drawn (shapes, characters, words to
show), not motions, styles, timings or the scene as a whole. An object drawn roughly counts as present. Use the
request's own words for each missing object; reply {{"missing": []}} when everything is there.
Reply with JSON only: {{"missing": ["..."]}}"""


def missing_objects(llm: LLM, brief: str, doc: dict) -> tuple[list[str], Call]:
    """Objects the request names that the planned document never draws (the planner sometimes drops half a scene)."""
    assets = doc.get("assets") or {}
    lines = []
    for a in doc.get("actors") or []:
        shape = a.get("shape") or {}
        kind = shape.get("kind", "")
        if kind == "group":
            continue
        label = (assets.get(shape.get("ref"), {}) or {}).get("label", "") if kind == "asset" else ""
        fill = (a.get("style") or {}).get("fill") or (a.get("style") or {}).get("stroke") or ""
        lines.append(f"- {a.get('id')} | {kind}{f' ({label})' if label else ''} | {fill} | {shape.get('text', '')}")
    text, call = llm.json("editor", "missing objects", [{"role": "user", "content": MISSING_PROMPT.format(
        brief=brief, actors="\n".join(lines) or "(none)")}], think=False, max_tokens=300, temperature=0)
    words = set(re.findall(r"[a-z0-9]+", brief.lower()))
    missing = [str(m).strip() for m in _parse(text).get("missing", [])
               if set(re.findall(r"[a-z0-9]+", str(m).lower())) & words - _STOP][:5]  # only what the request named
    return missing, call


_STOP = {"a", "an", "the", "of", "and", "in", "on", "to", "with", "word", "three", "two", "one"}
