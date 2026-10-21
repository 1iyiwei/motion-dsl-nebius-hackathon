"""The user's motion style, learned from 👍/👎 on animations CanvasMind made.

journal.jsonl is the evidence (one line per verdict); style.json is rebuilt from it, so the profile can always
say why it believes something. Counting verbs, easings and durations needs no model; Nano only turns the user's
free-text reasons ("too bouncy") into short notes.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path

from motionlang.agent.llm import LLM, Call, extract_json_text

NOTES_PROMPT = """You keep a motion designer's style notes: short, durable preferences about motion (pace, easing, bounce,
reveals, rhythm, length), each phrased as "prefers …" or "avoids …".
Current notes: {notes}
New feedback on an animation ({facts}): they {verdict} it{why}.
Update the notes: add what this feedback teaches, merge duplicates, drop notes it contradicts, keep at most 8.
Reply with JSON only: {{"notes": ["..."]}}"""


class Style:
    def __init__(self, home: Path):
        self.journal_path = Path(home) / "journal.jsonl"
        self.path = Path(home) / "style.json"

    def entries(self) -> list[dict]:
        if not self.journal_path.is_file():
            return []
        return [json.loads(line) for line in self.journal_path.read_text().splitlines() if line.strip()]

    def profile(self) -> dict:
        if self.path.is_file():
            return json.loads(self.path.read_text())
        return {"likes": 0, "dislikes": 0, "verbs": {}, "easings": {}, "seconds": {}, "notes": []}

    def record(self, item_id: str, verdict: str, why: str, facts: dict, llm: LLM | None = None) -> tuple[dict, list[Call]]:
        verdict = {"like": "liked", "liked": "liked", "up": "liked", "👍": "liked",
                   "dislike": "disliked", "disliked": "disliked", "down": "disliked", "👎": "disliked"}.get(verdict.strip().lower())
        if verdict is None:
            raise ValueError('verdict must be "like" or "dislike"')
        entry = {"t": round(time.time(), 1), "item": item_id, "verdict": verdict, "why": why.strip(),
                 "facts": {k: facts.get(k) for k in ("verbs", "easings", "seconds") if k in facts}}
        with open(self.journal_path, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        prof = self._rebuild()
        calls: list[Call] = []
        if entry["why"] and llm is not None:
            try:
                text, call = llm.json("editor", "style notes", [{"role": "user", "content": NOTES_PROMPT.format(
                    notes=json.dumps(prof["notes"]), facts=json.dumps(entry["facts"]), verdict=verdict,
                    why=f': "{entry["why"]}"')}], think=False, max_tokens=600, temperature=0.2)
                calls.append(call)
                notes = json.loads(extract_json_text(text)).get("notes", [])
                prof["notes"] = [str(n).strip()[:120] for n in notes if str(n).strip()][:8]
            except Exception:  # the counted part of the profile is still updated
                pass
        self.path.write_text(json.dumps(prof, indent=1, ensure_ascii=False))
        return prof, calls

    def _rebuild(self) -> dict:
        old = self.profile()
        verbs: dict[str, Counter] = {}
        easings: dict[str, Counter] = {}
        secs: dict[str, list[float]] = {"liked": [], "disliked": []}
        likes = dislikes = 0
        for e in self.entries():
            v = e["verdict"]
            likes += v == "liked"
            dislikes += v == "disliked"
            for name in e["facts"].get("verbs") or []:
                verbs.setdefault(name, Counter())[v] += 1
            for name in e["facts"].get("easings") or []:
                easings.setdefault(name, Counter())[v] += 1
            if e["facts"].get("seconds"):
                secs[v].append(float(e["facts"]["seconds"]))
        return {"updated": round(time.time(), 1), "likes": likes, "dislikes": dislikes,
                "verbs": {k: dict(c) for k, c in sorted(verbs.items())},
                "easings": {k: dict(c) for k, c in sorted(easings.items())},
                "seconds": {k: round(sum(v) / len(v), 1) for k, v in secs.items() if v},
                "notes": old.get("notes", [])}


def _leaning(counts: dict[str, dict]) -> tuple[list[str], list[str]]:
    liked = [k for k, c in counts.items() if c.get("liked", 0) > c.get("disliked", 0)]
    disliked = [k for k, c in counts.items() if c.get("disliked", 0) > c.get("liked", 0)]
    key = lambda k: -(counts[k].get("liked", 0) + counts[k].get("disliked", 0))  # noqa: E731
    return sorted(liked, key=key), sorted(disliked, key=key)


def summary(prof: dict) -> str:
    """The profile as one paragraph for the planner's prompt; "" until there is any feedback."""
    if not prof.get("likes") and not prof.get("dislikes"):
        return ""
    parts = []
    lv, dv = _leaning(prof.get("verbs", {}))
    le, de = _leaning(prof.get("easings", {}))
    if lv or le:
        parts.append("they liked animations using " + ", ".join(lv[:4] + [f"{e} easing" for e in le[:3]]))
    if dv or de:
        parts.append("they disliked " + ", ".join(dv[:3] + [f"{e} easing" for e in de[:3]]))
    if prof.get("seconds", {}).get("liked"):
        parts.append(f"liked animations last about {prof['seconds']['liked']} s")
    if prof.get("notes"):
        parts.append("notes: " + "; ".join(prof["notes"]))
    return "; ".join(parts) + "."


def explain(facts: dict, prof: dict) -> str:
    """Which of the user's liked traits a new animation uses, for the reply ("…since you liked those in 4 of 5")."""
    used = []
    for kind, names in (("verbs", facts.get("verbs") or []), ("easings", facts.get("easings") or [])):
        for n in names:
            c = prof.get(kind, {}).get(n, {})
            if c.get("liked", 0) > c.get("disliked", 0):
                used.append((n, c.get("liked", 0), c.get("liked", 0) + c.get("disliked", 0)))
    if not used:
        return ""
    used.sort(key=lambda u: -u[1])
    names = " + ".join(n for n, _, _ in used[:3])
    _, liked, total = used[0]
    return f"{names}, since you liked {'that' if len(used) == 1 else 'those'} in {liked} of {total} rated animations"
