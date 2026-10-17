"""Id-addressed edit operations: the cheap path for small models ("slower", "more bounce").

Ops (applied in order, atomically; on any error the original document is untouched):
  {"op": "set", "id": ID, "field": "duration" | "init.x" | ..., "value": V}   block or actor field
  {"op": "set", "pointer": "/canvas/fps", "value": V}                       anything else (JSON pointer)
  {"op": "unset", "id": ID, "field": F}                                     remove an optional field
  {"op": "insert", "parent": ID, "block": B, "after"|"before": ID | "index": N}
  {"op": "remove", "id": ID}
  {"op": "replace", "id": ID, "block": B}
  {"op": "wrap", "ids": [ID, ...], "with": "seq"|"par"|"stagger", "id": NEW_ID}
"""

from __future__ import annotations

import copy
from typing import Any

from .issues import Issue

LIST_KINDS = ("seq", "par", "stagger")


class EditError(Exception):
    pass


def _find_block(node: Any, bid: str, parent: tuple[list, int] | None = None, holder: tuple[dict, str] | None = None):
    """Return (block, (list, index) | None, (dict, key) | None) for the block with id bid."""
    if not isinstance(node, dict):
        return None
    if node.get("id") == bid:
        return node, parent, holder
    for k in LIST_KINDS:
        if isinstance(node.get(k), list):
            for i, c in enumerate(node[k]):
                if (r := _find_block(c, bid, (node[k], i), None)) is not None:
                    return r
    if isinstance(node.get("repeat"), dict):
        if (r := _find_block(node["repeat"], bid, None, (node, "repeat"))) is not None:
            return r
    return None


def _children(block: dict) -> list:
    for k in LIST_KINDS:
        if isinstance(block.get(k), list):
            return block[k]
    raise EditError(f"block {block.get('id')!r} is not a seq/par/stagger container")


def _target(doc: dict, oid: str) -> dict:
    found = _find_block(doc.get("timeline"), oid)
    if found:
        return found[0]
    for a in doc.get("actors", []):
        if a.get("id") == oid:
            return a
    raise EditError(f"no block or actor with id {oid!r}")


def _walk_field(obj: dict, field: str, create: bool) -> tuple[dict, str]:
    parts = field.split(".")
    for p in parts[:-1]:
        if p not in obj or not isinstance(obj[p], dict):
            if not create:
                raise EditError(f"field {field!r} not found")
            obj[p] = {}
        obj = obj[p]
    return obj, parts[-1]


def _pointer_parent(doc: Any, pointer: str) -> tuple[Any, str | int]:
    if not pointer.startswith("/"):
        raise EditError("pointer must start with '/'")
    parts = [p.replace("~1", "/").replace("~0", "~") for p in pointer[1:].split("/")]
    cur = doc
    for p in parts[:-1]:
        cur = cur[int(p)] if isinstance(cur, list) else cur.setdefault(p, {})
    last: str | int = int(parts[-1]) if isinstance(cur, list) else parts[-1]
    return cur, last


def _apply_one(doc: dict, op: dict) -> None:
    kind = op.get("op")
    if kind == "set":
        if "pointer" in op:
            parent, key = _pointer_parent(doc, op["pointer"])
            parent[key] = op["value"]  # type: ignore[index]
            return
        if op.get("field") in ("id",):
            raise EditError("ids can't be changed with set; use replace")
        obj, key = _walk_field(_target(doc, op["id"]), op["field"], create=True)
        obj[key] = op["value"]
    elif kind == "unset":
        obj, key = _walk_field(_target(doc, op["id"]), op["field"], create=False)
        obj.pop(key, None)
    elif kind == "insert":
        found = _find_block(doc.get("timeline"), op["parent"])
        if not found:
            raise EditError(f"no container block {op['parent']!r}")
        kids = _children(found[0])
        if "after" in op or "before" in op:
            ref = op.get("after", op.get("before"))
            idx = next((i for i, c in enumerate(kids) if c.get("id") == ref), None)
            if idx is None:
                raise EditError(f"{ref!r} is not a child of {op['parent']!r}")
            kids.insert(idx + 1 if "after" in op else idx, op["block"])
        else:
            kids.insert(int(op.get("index", len(kids))), op["block"])
    elif kind in ("remove", "replace"):
        found = _find_block(doc.get("timeline"), op["id"])
        if not found:
            raise EditError(f"no block {op['id']!r}")
        _, at_list, at_key = found
        if at_list is None and at_key is None:
            if kind == "remove":
                raise EditError("can't remove the root block")
            doc["timeline"] = op["block"]
            return
        if kind == "remove":
            if at_key is not None:
                raise EditError("can't remove the only child of a repeat; remove the repeat")
            lst, i = at_list  # type: ignore[misc]
            del lst[i]
        elif at_list is not None:
            lst, i = at_list
            lst[i] = op["block"]
        else:
            holder, k = at_key  # type: ignore[misc]
            holder[k] = op["block"]
    elif kind == "wrap":
        ids = op["ids"]
        if op.get("with") not in LIST_KINDS:
            raise EditError("wrap `with` must be seq, par or stagger")
        first = _find_block(doc.get("timeline"), ids[0])
        if not first or first[1] is None:
            raise EditError(f"{ids[0]!r} is not inside a seq/par/stagger")
        lst, i = first[1]
        got = [c.get("id") for c in lst[i : i + len(ids)]]
        if got != list(ids):
            raise EditError(f"wrap ids must be adjacent siblings in order; found {got}")
        lst[i : i + len(ids)] = [{"id": op["id"], op["with"]: lst[i : i + len(ids)]}]
    else:
        raise EditError(f"unknown op {kind!r}; use set, unset, insert, remove, replace or wrap")


def apply_edits(doc: dict, ops: list[dict]) -> tuple[dict, list[Issue]]:
    """Apply ops to a copy of doc. Returns (new_doc, []) or (original doc, [E_EDIT issue])."""
    new = copy.deepcopy(doc)
    for i, op in enumerate(ops):
        try:
            _apply_one(new, op)
        except (EditError, KeyError, IndexError, TypeError, ValueError) as e:
            msg = str(e) if isinstance(e, EditError) else f"malformed op ({type(e).__name__}: {e})"
            return doc, [Issue("E_EDIT", f"edit #{i} ({op.get('op')}): {msg}", hint="no edits were applied; fix this op and resend all of them", pointer=f"/{i}")]
    return new, []
