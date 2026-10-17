"""Generic verb contract (spec §11). Runs over every registered verb, core or plugin.

A verb that passes this suite is a valid Motion verb, whoever (or whatever agent) wrote it.
"""

from __future__ import annotations

import math
from typing import Any

import annotated_types as at
import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from conftest import make_doc
from motionlang import check
from motionlang.parse import VERB_COMMON, parse
from motionlang.timing import schedule
from motionlang.verbs import Verb, load_plugins

CASES = [(name, i) for name, v in sorted(load_plugins().items()) for i in range(len(v.examples))]


def _block(name: str, verb: Verb, ex: dict[str, Any]) -> dict:
    block = {"id": "x", "verb": name, **ex}
    if verb.needs_actor and not verb.fixed_actor:
        for actor in ("a", "line"):
            block["actor"] = actor
            if not check(make_doc(block)).report.errors:
                break
    return block


def _baked(name: str, i: int, ex: dict | None = None):
    v = load_plugins()[name]
    r = check(make_doc(_block(name, v, ex if ex is not None else v.examples[i])))
    assert r.ok, r.report.summary()
    return v, r


@pytest.mark.parametrize("name,i", CASES)
def test_example_is_valid_and_deterministic(name, i):
    _, r1 = _baked(name, i)
    _, r2 = _baked(name, i)
    assert r1.baked.to_json() == r2.baked.to_json()


@pytest.mark.parametrize("name,i", CASES)
def test_identity_at_start_and_continuity(name, i):
    v, r = _baked(name, i)
    (leaf,) = r.baked.sched.leaves
    ev = r.baked.ev
    mode = v.mode(leaf.block.params)
    if mode is None:
        return
    start = ev.from_state(leaf)
    at0 = ev.sample_at(leaf, 0.0, 0.0)
    if mode == "rel":
        assert all(abs(float(x)) < 1e-6 for x in at0.values()), at0
    elif not v.jumps_at_start(leaf.block.params):
        for p, x in at0.items():
            if isinstance(x, (int, float)):
                assert x == pytest.approx(start[p], abs=1e-6), p
    # Continuity: the largest step between samples must shrink as sampling gets finer.
    # (A real jump stays the same size no matter how finely you sample.)
    def max_steps(n: int) -> dict[str, float]:
        rows = [ev.sample_at(leaf, k / n, leaf.duration * k / n) for k in range(n + 1)]
        out = {}
        for p in rows[0]:
            if isinstance(rows[0][p], (int, float)):
                vals = [float(row[p]) for row in rows]
                out[p] = max(abs(b - a) for a, b in zip(vals, vals[1:]))
        return out

    coarse, fine = max_steps(500), max_steps(4000)
    for p in coarse:
        assert fine[p] <= 0.5 * coarse[p] + 1e-6, f"{p}: step {coarse[p]:.4g} -> {fine[p]:.4g} under 8x refinement (discontinuity)"


def _bounds(field) -> tuple[float, float]:
    lo, hi = -500.0, 500.0
    for m in field.metadata:
        if isinstance(m, at.Ge):
            lo = float(m.ge)
        elif isinstance(m, at.Gt):
            lo = float(m.gt) + 1e-3
        elif isinstance(m, at.Le):
            hi = float(m.le)
        elif isinstance(m, at.Lt):
            hi = float(m.lt) - 1e-3
    return lo, min(hi, lo + 1000.0)


@pytest.mark.parametrize("name", sorted(load_plugins()))
@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(data=st.data())
def test_bounded_for_any_valid_params(name, data):
    """Perturb numeric params within their declared bounds; output must stay finite."""
    v = load_plugins()[name]
    ex = dict(data.draw(st.sampled_from(v.examples)))
    for fname, field in v.Params.model_fields.items():
        key = field.alias or fname
        if field.annotation in (float, int) and key not in VERB_COMMON:
            lo, hi = _bounds(field)
            if field.annotation is int:
                ex[key] = data.draw(st.integers(int(math.ceil(lo)), int(min(hi, lo + 20))))
            else:
                ex[key] = data.draw(st.floats(lo, hi, allow_nan=False))
    try:
        v.Params.model_validate({k: x for k, x in ex.items() if k not in VERB_COMMON})
    except ValidationError:
        assume(False)
    block = _block(name, v, ex)
    parsed, _ = parse(make_doc(block))
    assume(parsed is not None and schedule(parsed.root).duration <= 20)
    r = check(make_doc(block))
    assume(r.baked is not None)
    for actor, props in r.baked.props.items():
        for p, vals in props.items():
            if vals and isinstance(vals[0], float):
                assert all(math.isfinite(x) for x in vals), (actor, p)


@pytest.mark.parametrize("name", sorted(load_plugins()))
def test_params_round_trip(name):
    v = load_plugins()[name]
    for ex in v.examples:
        p = v.Params.model_validate({k: x for k, x in ex.items() if k not in VERB_COMMON})
        again = v.Params.model_validate(p.model_dump(by_alias=True))
        assert again == p
    assert v.summary(), "verbs need a docstring: its first paragraph becomes the LLM-facing summary"
