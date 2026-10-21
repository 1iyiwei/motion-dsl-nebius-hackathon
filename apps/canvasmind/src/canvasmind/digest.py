"""The weekly motion digest: what's new in motion design that fits this user's style, plus one draft animation.

Tavily only ever sees search queries built from the style profile (never files or captions). Nano writes the
queries, Super reads the results and writes the digest, and one library SVG is animated after a trend it picked.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

from motionlang.agent.llm import LLM, Call, extract_json_text

from . import style

QUERIES_PROMPT = """Today is {today}. Write 3 web search queries to find what is new and notable in motion design this
month that this motion designer would care about. Their style: {style}. Their recent work: {recent}.
Mix one broad query (motion design / logo animation trends), one about techniques close to their style, and one about
a fresh direction they haven't tried. Reply with JSON only: {{"queries": ["...", "...", "..."]}}"""

DIGEST_PROMPT = """You write a short weekly motion-design digest for one motion designer, read on a phone.
Their style: {style}
Their library has these SVG logos you may draft an animation for: {svgs}
Search results (title, url, snippet):
{results}

Pick the 3 most useful findings for them: motion-design craft, techniques, notable work or tools a motion designer
would use. Skip AI-model and AI-product announcements and generic web-design listicles unless nothing else is left. For each: a one-line headline, one sentence on why it fits (or stretches)
their style, and the source URL. Then pick ONE finding to try now: choose one of their SVGs and write a 15-30 word
animation brief for it in the spirit of that finding (2D motion only: move, rotate, scale, fade, bounce, wiggle, draw
a line on, wipe a shape in).
Reply with JSON only: {{"items": [{{"headline": "...", "why": "...", "url": "..."}}], "draft": {{"item_id": "<svg id or empty>", "brief": "...", "inspired_by": "<headline>"}}}}"""


@dataclass
class Digest:
    text: str
    items: list[dict]
    draft: object | None = None  # animation.Animated
    calls: list[Call] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)


def tavily_search(query: str, max_results: int = 5) -> list[dict]:
    import httpx

    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        raise RuntimeError("TAVILY_API_KEY is not set")
    r = httpx.post("https://api.tavily.com/search", timeout=30, headers={"Authorization": f"Bearer {key}"},
                   json={"query": query, "max_results": max_results, "search_depth": "basic", "time_range": "month"})
    r.raise_for_status()
    return [{"title": x.get("title", ""), "url": x.get("url", ""), "snippet": (x.get("content") or "")[:300]}
            for x in r.json().get("results", [])]


def make(studio, *, draft: bool = True, search=tavily_search) -> Digest:
    llm: LLM = studio.make_llm()
    prof = studio.style.profile()
    style_text = style.summary(prof) or "no feedback yet; assume clean, modern logo motion"
    recent = [i.title for i in studio.lib.recent(8)]
    text, call = llm.json("editor", "digest queries", [{"role": "user", "content": QUERIES_PROMPT.format(
        today=time.strftime("%B %Y"), style=style_text, recent=", ".join(recent) or "none yet")}],
        think=False, max_tokens=300, temperature=0.4)
    calls = [call]
    queries = [str(q) for q in json.loads(extract_json_text(text)).get("queries", [])][:3]
    results, seen = [], set()
    for q in queries:
        for r in search(q):
            if r["url"] and r["url"] not in seen:
                seen.add(r["url"])
                results.append(r)
    if not results:
        return Digest("No new motion-design finds this week.", [], calls=calls, queries=queries)
    svgs = [i for i in studio.lib.recent(200) if i.kind == "svg"][:15]
    text, call = llm.json("planner", "digest", [{"role": "user", "content": DIGEST_PROMPT.format(
        style=style_text, svgs=", ".join(f"{i.id} ({i.title})" for i in svgs) or "none",
        results="\n".join(f"- {r['title']} | {r['url']} | {r['snippet']}" for r in results[:15]))}],
        think=False, max_tokens=1500, temperature=0.4)
    calls.append(call)
    d = json.loads(extract_json_text(text))
    items = [i for i in d.get("items", []) if isinstance(i, dict) and i.get("headline")][:3]
    known = {r["url"] for r in results}
    for i in items:  # only cite what the search actually returned
        if i.get("url") not in known:
            i["url"] = ""
    lines = [f"🎬 Motion digest · {time.strftime('%b %d')}"]
    for n, i in enumerate(items, 1):
        lines.append(f"{n}. {i['headline']} — {i.get('why', '')}" + (f"\n   {i['url']}" if i.get("url") else ""))
    out = Digest("\n".join(lines), items, calls=calls, queries=queries)
    plan = d.get("draft") or {}
    if draft and plan.get("item_id") in {i.id for i in svgs} and plan.get("brief"):
        try:
            out.draft = studio.animate(plan["item_id"], plan["brief"])
            out.calls += out.draft.calls
            out.text += f"\n\nI tried one: {plan['brief']} (after “{plan.get('inspired_by', '')}”)."
        except Exception as e:  # the digest is still worth sending
            out.text += f"\n\n(The draft animation didn't work out: {e})"
    folder = studio.lib.home / "digests"
    folder.mkdir(exist_ok=True)
    (folder / time.strftime("%Y-%m-%d.md")).write_text(out.text + "\n\nQueries: " + "; ".join(queries) + "\n")
    return out
