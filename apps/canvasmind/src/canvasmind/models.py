"""The two model calls behind filing and recall: Nemotron VL describes an item, Nano re-ranks search hits."""

from __future__ import annotations

import json
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
