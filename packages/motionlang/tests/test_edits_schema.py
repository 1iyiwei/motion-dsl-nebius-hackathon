import json

import jsonschema
import pytest

from motionlang import apply_edits, validate
from motionlang.schema import document_schema, prompt_reference
from motionlang.verbs import load_plugins


@pytest.fixture
def doc(doc_factory):
    return doc_factory(
        {"id": "m1", "verb": "move", "actor": "a", "by": [100, 0]},
        {"id": "m2", "verb": "fade", "actor": "a", "to": 0.5},
    )


def test_set_insert_remove_wrap(doc):
    new, issues = apply_edits(doc, [
        {"op": "set", "id": "m1", "field": "duration", "value": 2},
        {"op": "set", "id": "a", "field": "init.x", "value": 100},
        {"op": "set", "pointer": "/canvas/fps", "value": 30},
        {"op": "insert", "parent": "root", "after": "m1", "block": {"id": "h", "verb": "hold", "duration": 0.5}},
        {"op": "remove", "id": "m2"},
        {"op": "wrap", "ids": ["m1", "h"], "with": "par", "id": "both"},
    ])
    assert not issues
    assert new["timeline"]["seq"] == [{"id": "both", "par": [
        {"id": "m1", "verb": "move", "actor": "a", "by": [100, 0], "duration": 2},
        {"id": "h", "verb": "hold", "duration": 0.5},
    ]}]
    assert new["actors"][0]["init"]["x"] == 100 and new["canvas"]["fps"] == 30
    assert validate(new).ok
    assert doc["timeline"]["seq"][0].get("duration") is None  # original untouched


def test_edits_are_atomic(doc):
    new, issues = apply_edits(doc, [
        {"op": "set", "id": "m1", "field": "duration", "value": 2},
        {"op": "remove", "id": "nope"},
    ])
    assert new is doc and issues[0].code == "E_EDIT" and issues[0].pointer == "/1"


def test_schema_is_valid_and_accepts_examples(doc):
    schema = document_schema()
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(doc, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**doc, "timeline": {"id": "x", "verb": "teleport", "actor": "a"}}, schema)


def test_every_verb_example_matches_schema(doc_factory):
    schema = document_schema()
    for name, v in load_plugins().items():
        for ex in v.examples:
            block = {"id": "x", "verb": name, **({"actor": "a"} if v.needs_actor and not v.fixed_actor else {}), **ex}
            jsonschema.validate(doc_factory(block), schema)


def test_prompt_reference_lists_every_verb():
    ref = prompt_reference()
    for name in load_plugins():
        assert f"**{name}**" in ref
    json.dumps(ref)
