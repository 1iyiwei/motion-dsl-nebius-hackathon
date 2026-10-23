"""Site briefing: venue + date → searched rules and weather → cited show limits.

Nano writes the searches, Tavily runs them, and Super reads the results into numbers, each with the source
it came from and a verbatim quote. A quote that isn't in the source text is marked unverified, and an
unverified number is only used when it is more conservative than the default, so a misread page can
tighten the limits but never loosen them.
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Callable

from motionlang.agent.llm import LLM, extract_json_text

from .agent import call_event
from .show import Limits

BRIEFER_MODEL = "nvidia/nemotron-3-super-120b-a12b"
FLOOR = 10.0  # m above ground of the airspace floor; the ceiling minus this is the airspace height
GUST_ALLOWANCE = 1.3  # design for typical wind × this, rounded up to the sweep's 2 m/s steps

QUERIES_PROMPT = """You prepare a drone light show at: {venue}, on {date}.
Write 4 web search queries (short, specific, include the place) to find:
1. the legal maximum altitude for drones or drone shows there (country and city rules),
2. required distance between a drone show and spectators (crowd setback),
3. airports, restricted airspace or no-fly zones near the venue,
4. typical wind speed at that place in that month, in the evening.
Reply with JSON: {{"queries": ["...", "...", "...", "..."]}}"""

READ_PROMPT = """You extract safety limits for a drone light show at {venue} on {date} from search results.
Results (id, title, url, text):
{results}

Reply with JSON:
{{"ceiling": {{"value_m": number|null, "source": id|null, "quote": "exact words copied from that result's text"}},
 "crowd_setback": {{"value_m": number|null, "source": id|null, "quote": "..."}},
 "typical_wind": {{"value_ms": number|null, "source": id|null, "quote": "..."}},
 "airspace": [{{"note": "one sentence", "source": id}}],
 "summary": "two sentences for the show designer"}}
Rules: convert to metres and m/s (400 ft = 122 m; 1 mph = 0.447 m/s; 1 km/h = 0.278 m/s; 1 knot = 0.514 m/s).
The quote must be copied character for character from the cited result's text and must contain the number.
Use null when the results don't say; never guess."""


@dataclass
class Finding:
    topic: str
    value: float | None
    unit: str
    source: dict | None  # {title, url}
    quote: str
    verified: bool
    applied: bool = False
    note: str = ""


@dataclass
class Briefing:
    venue: str
    date: str
    queries: list[str]
    results: list[dict]
    findings: list[Finding]
    airspace: list[dict]
    summary: str
    limits: dict  # the Limits fields this briefing sets
    calls: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["results"] = [{k: r[k] for k in ("id", "title", "url")} for r in self.results]
        return d

    def apply(self, limits: Limits) -> Limits:
        return limits.model_copy(update=self.limits)


def tavily_search(query: str, max_results: int = 4) -> list[dict]:
    # Copied from another app in this repo (apps never import each other); moves into motionlang later.
    import httpx

    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        raise RuntimeError("TAVILY_API_KEY is not set")
    r = httpx.post("https://api.tavily.com/search", timeout=30, headers={"Authorization": f"Bearer {key}"},
                   json={"query": query, "max_results": max_results, "search_depth": "basic"})
    r.raise_for_status()
    return [{"title": x.get("title", ""), "url": x.get("url", ""), "text": (x.get("content") or "")[:900]}
            for x in r.json().get("results", [])]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("’", "'").replace("“", '"').replace("”", '"')).strip().lower()


def _verified(quote: str, result: dict | None, value: float | None) -> bool:
    if not quote or result is None:
        return False
    q = _norm(quote)
    return len(q) >= 8 and q in _norm(result["text"] + " " + result["title"]) and bool(re.search(r"\d", q))


def _num(v) -> float | None:
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def brief(venue: str, date: str, llm: LLM, emit: Callable[[dict], None] = lambda e: None,
          search: Callable[[str], list[dict]] = tavily_search, base: Limits | None = None) -> Briefing:
    base = base or Limits()
    llm.models.setdefault("briefer", os.environ.get("CONSTELLATE_BRIEFER_MODEL", BRIEFER_MODEL))
    calls = []
    emit({"type": "call_start", "msg": "editor writes the searches"})
    text, call = llm.json("editor", "briefing queries", [{"role": "user", "content": QUERIES_PROMPT.format(venue=venue, date=date)}],
                          think=False, max_tokens=400, temperature=0.2)
    calls.append(str(call))
    emit(call_event(call))
    queries = [str(q) for q in json.loads(extract_json_text(text)).get("queries", [])][:4]
    results, seen = [], set()
    for q in queries:
        emit({"type": "search", "msg": f"Tavily: {q}"})
        for r in search(q):
            if r["url"] and r["url"] not in seen:
                seen.add(r["url"])
                results.append({**r, "id": len(results) + 1})
    if not results:
        raise RuntimeError("the searches returned nothing")
    listing = "\n\n".join(f"[{r['id']}] {r['title']}\n{r['url']}\n{r['text']}" for r in results)
    emit({"type": "call_start", "msg": "briefer reads the results"})
    text, call = llm.json("briefer", "briefing limits", [{"role": "user", "content": READ_PROMPT.format(venue=venue, date=date, results=listing)}],
                          think=False, max_tokens=1500, temperature=0.1)
    calls.append(str(call))
    emit(call_event(call))
    raw = json.loads(extract_json_text(text))
    by_id = {r["id"]: r for r in results}

    findings, limits = [], {}
    specs = (("ceiling", "value_m", "m"), ("crowd_setback", "value_m", "m"), ("typical_wind", "value_ms", "m/s"))
    for topic, key, unit in specs:
        item = raw.get(topic) or {}
        value = _num(item.get(key))
        src = by_id.get(_num(item.get("source")) and int(item["source"]))
        quote = str(item.get("quote") or "")
        ok = value is not None and _verified(quote, src, value)
        f = Finding(topic, value, unit, {"title": src["title"], "url": src["url"]} if src else None, quote, ok)
        findings.append(f)
        if value is None:
            default = {"ceiling": f"{base.height:g} m airspace height", "crowd_setback": f"{base.crowd_setback:g} m",
                       "typical_wind": f"design wind {base.design_wind:g} m/s"}[topic]
            f.note = f"default kept ({default})"
            continue
        if topic == "ceiling":
            height = max(20.0, min(150.0, value - FLOOR))
            safer = height < base.height
            if ok or safer:
                limits["height"] = round(height, 1)
                f.applied = True
                f.note = f"airspace height {height:.0f} m above a {FLOOR:.0f} m floor"
        elif topic == "crowd_setback":
            setback = max(5.0, min(500.0, value))
            if ok or setback > base.crowd_setback:
                limits["crowd_setback"] = round(setback, 1)
                f.applied = True
                f.note = "show plane at least this far from spectators"
        else:
            design = max(2.0, min(12.0, math.ceil(value * GUST_ALLOWANCE / 2) * 2))
            if ok or design > base.design_wind:
                limits["design_wind"] = design
                f.applied = True
                f.note = f"design wind {design:g} m/s (typical × {GUST_ALLOWANCE:g} for gusts)"
        if not f.applied:
            f.note = "quote not found in the source; kept the safer default"
    airspace = []
    for a in raw.get("airspace") or []:
        src = by_id.get(_num(a.get("source")) and int(a["source"])) if isinstance(a, dict) else None
        if isinstance(a, dict) and a.get("note"):
            airspace.append({"note": str(a["note"]), "source": {"title": src["title"], "url": src["url"]} if src else None})
    b = Briefing(venue, date, queries, results, findings, airspace, str(raw.get("summary") or ""), limits, calls)
    emit({"type": "briefing", "msg": f"{sum(f.applied for f in findings)} limits set from {len(results)} sources", "briefing": b.to_dict()})
    return b
