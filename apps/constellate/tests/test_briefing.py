import json

from motionlang.agent.llm import LLM

from constellate.briefing import brief
from constellate.show import Limits

RESULTS = {
    "altitude": [{"title": "Drone laws", "url": "https://example.org/laws", "text": "Altitude: 400 feet AGL. Higher requires a waiver."}],
    "setback": [{"title": "Show guide", "url": "https://example.org/shows", "text": "Keep spectators at least 50 meters from the drones."}],
    "wind": [{"title": "Weather", "url": "https://example.org/wx", "text": "The average wind speed in October is 7.3 miles per hour."}],
}


class Scripted:
    def __init__(self, replies):
        self.replies = list(replies)

    def complete(self, model, messages, **kw):
        return self.replies.pop(0), {}


def search(q):
    return next(v for k, v in RESULTS.items() if k in q)


def run(read_reply, base=None):
    llm = LLM(Scripted([json.dumps({"queries": ["altitude rules", "setback distance", "wind october"]}), json.dumps(read_reply)]))
    return brief("Test Park", "Oct 31", llm, search=search, base=base)


def test_verified_findings_set_limits_with_sources():
    b = run({"ceiling": {"value_m": 122, "source": 1, "quote": "400 feet AGL"},
             "crowd_setback": {"value_m": 50, "source": 2, "quote": "at least 50 meters"},
             "typical_wind": {"value_ms": 3.3, "source": 3, "quote": "is 7.3 miles per hour"},
             "airspace": [{"note": "check LAANC", "source": 1}], "summary": "ok"})
    assert b.limits == {"height": 112.0, "crowd_setback": 50.0, "design_wind": 6}
    assert all(f.verified and f.applied for f in b.findings)
    assert b.findings[0].source["url"] == "https://example.org/laws"
    assert Limits().model_copy(update=b.limits).height == 112.0


def test_unverified_numbers_only_tighten_limits():
    # Quotes that aren't in the source: a higher ceiling and a lower wind are ignored; a lower ceiling is kept.
    b = run({"ceiling": {"value_m": 300, "source": 1, "quote": "1000 feet is allowed"},
             "crowd_setback": {"value_m": 10, "source": 2, "quote": "10 m is fine"},
             "typical_wind": {"value_ms": 0.5, "source": 3, "quote": "wind is 1 mph"}})
    assert b.limits == {} and not any(f.applied for f in b.findings)
    b2 = run({"ceiling": {"value_m": 50, "source": 1, "quote": "made up 50 m"}})
    assert b2.limits == {"height": 40.0} and not b2.findings[0].verified


def test_missing_values_keep_defaults():
    b = run({"ceiling": {"value_m": None}, "summary": ""})
    assert b.limits == {} and b.findings[0].note.startswith("default kept")
