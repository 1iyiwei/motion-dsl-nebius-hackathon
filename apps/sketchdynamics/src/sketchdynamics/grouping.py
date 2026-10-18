"""Semantic groups for an imported logo: parts that move together as one rigid component.

A grouping is a flat list `[{"id", "label", "parts": [asset ids], "pivot"?}]`; each part is in at most one
group. `pivot` is the group's rotation centre: one of its parts (whose centre is used, e.g. a rotor's hub)
or an [x, y] canvas point. Without it a group turns about the centre of its parts' bounding box.
`build_actors` turns it into a MotionLang actor tree:

    logo (group, at the centre of everything, canvas coordinates)
      ├─ sun (group, at the centre of its parts, relative to logo)
      │    ├─ disc   (asset actor, resting where it was drawn, relative to sun)
      │    └─ ray1 …
      └─ wave1 (an ungrouped part, directly under logo)

The planner then moves `sun` and every ray goes with it, instead of having to choreograph six actors
that must stay in formation. Positions follow motionlang's importer: a child's init x/y is its centre's
offset from the parent's centre, so the still logo looks exactly as drawn.
"""

from __future__ import annotations

import re
from typing import Any

from motionlang.paths import path_bbox

ROOT = "logo"


def _centre(boxes: list[list[float]]) -> tuple[float, float]:
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[0] + b[2] for b in boxes)
    y1 = max(b[1] + b[3] for b in boxes)
    return (x0 + x1) / 2, (y0 + y1) / 2


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:24] or "group"


def normalize_groups(groups: list[dict[str, Any]], assets: dict[str, Any]) -> list[dict[str, Any]]:
    """Drop unknown or repeated parts and groups of fewer than two; give each group a unique id that
    can't collide with a part id (actor ids share one namespace)."""
    taken = set(assets) | {ROOT}
    used: set[str] = set()
    out = []
    for g in groups:
        if not isinstance(g, dict):
            continue
        parts = [p for p in dict.fromkeys(g.get("parts") or []) if isinstance(p, str) and p in assets and p not in used]
        if len(parts) < 2:
            continue
        label = str(g.get("label") or g.get("id") or "group").strip()[:60]
        base = _slug(str(g.get("id") or label))
        gid, n = base, 2
        while gid in taken:
            gid, n = f"{base}_{n}", n + 1
        taken.add(gid)
        used.update(parts)
        group = {"id": gid, "label": label, "parts": parts}
        pivot = g.get("pivot")
        if isinstance(pivot, str) and pivot in parts:
            group["pivot"] = pivot
        elif isinstance(pivot, (list, tuple)) and len(pivot) == 2 and all(isinstance(v, (int, float)) for v in pivot):
            group["pivot"] = [float(pivot[0]), float(pivot[1])]
        out.append(group)
    return out


def groups_from_actors(actors: list[dict[str, Any]], assets: dict[str, Any]) -> list[dict[str, Any]]:
    """The file's own grouping (from the importer's actor tree), flattened to one level: each part goes
    to its nearest enclosing group, skipping a single all-enclosing root."""
    by_id = {a["id"]: a for a in actors}
    tops = [a for a in actors if not a.get("parent")]
    root = tops[0]["id"] if len(tops) == 1 and tops[0]["shape"]["kind"] == "group" else None
    groups: dict[str, dict[str, Any]] = {}
    for a in actors:
        if a["shape"]["kind"] != "asset":
            continue
        p = a.get("parent")
        if p and p != root:
            g = by_id[p]
            groups.setdefault(p, {"id": p, "label": g.get("label") or p, "parts": []})["parts"].append(a["shape"]["ref"])
    return normalize_groups(list(groups.values()), assets)


def build_actors(assets: dict[str, Any], groups: list[dict[str, Any]], opacity: dict[str, float] | None = None) -> list[dict[str, Any]]:
    """Actor tree for `groups` (already normalized). `opacity` keeps imported part opacities."""
    opacity = opacity or {}
    boxes = {k: a["bbox"] for k, a in assets.items()}
    cx, cy = _centre(list(boxes.values()))
    actors: list[dict[str, Any]] = [{"id": ROOT, "label": "the whole logo", "shape": {"kind": "group"}, "init": {"x": round(cx, 2), "y": round(cy, 2)}}]

    def part(pid: str, parent: str, origin: tuple[float, float]) -> None:
        x, y, w, h = boxes[pid]
        a: dict[str, Any] = {"id": pid, "shape": {"kind": "asset", "ref": pid}, "parent": parent,
                             "init": {"x": round(x + w / 2 - origin[0], 2), "y": round(y + h / 2 - origin[1], 2)}}
        if assets[pid].get("label") and assets[pid]["label"] != pid:
            a["label"] = assets[pid]["label"]
        if pid in opacity:
            a["init"]["opacity"] = opacity[pid]
        actors.append(a)

    grouped = set()
    for g in groups:
        # A group has no geometry, so it rotates and scales about its own position: put that at the pivot.
        pivot = g.get("pivot")
        if isinstance(pivot, str):
            gx, gy = _centre([boxes[pivot]])
        elif pivot:
            gx, gy = pivot
        else:
            gx, gy = _centre([boxes[p] for p in g["parts"]])
        label = g["label"] + (f' (turns about {pivot})' if isinstance(pivot, str) else "")
        actors.append({"id": g["id"], "label": label, "shape": {"kind": "group"}, "parent": ROOT,
                       "init": {"x": round(gx - cx, 2), "y": round(gy - cy, 2)}})
        for p in g["parts"]:
            part(p, g["id"], (gx, gy))
            grouped.add(p)
    for pid in assets:
        if pid not in grouped:
            part(pid, ROOT, (cx, cy))
    return actors


def part_opacity(actors: list[dict[str, Any]]) -> dict[str, float]:
    return {a["shape"]["ref"]: a["init"]["opacity"] for a in actors
            if a["shape"]["kind"] == "asset" and "opacity" in a.get("init", {})}


def split_part(assets: dict[str, Any], groups: list[dict[str, Any]], part: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """Split one part into its subpaths (e.g. a shape and a hole the importer kept together, or a glyph
    with two strokes). The pieces take the part's place, in its group; a pivot on it becomes a fixed point."""
    a = assets[part]
    subpaths = [d.strip() for d in re.split(r"(?=[Mm])", a.get("d", "")) if d.strip()]
    if len(subpaths) < 2:
        raise ValueError(f"{part} is a single shape; there is nothing to split")
    out: dict[str, Any] = {}
    pieces: list[str] = []
    for k, aid in enumerate(assets):
        if aid != part:
            out[aid] = assets[aid]
            continue
        for n, d in enumerate(subpaths, 1):
            pid, m = f"{part}_{n}", 2
            while pid in assets or pid in out:
                pid, m = f"{part}_{n}_{m}", m + 1
            x, y, w, h = path_bbox(d)
            piece = {**a, "d": d, "bbox": [round(x, 2), round(y, 2), round(w, 2), round(h, 2)]}
            if a.get("label") and a["label"] != part:
                piece["label"] = f"{a['label']} {n}"
            else:
                piece.pop("label", None)
            out[pid] = piece
            pieces.append(pid)
    new_groups = []
    for g in groups:
        if part not in g["parts"]:
            new_groups.append(g)
            continue
        i = g["parts"].index(part)
        g = {**g, "parts": g["parts"][:i] + pieces + g["parts"][i + 1:]}
        if g.get("pivot") == part:
            g["pivot"] = list(_centre([a["bbox"]]))
        new_groups.append(g)
    return out, new_groups, pieces
