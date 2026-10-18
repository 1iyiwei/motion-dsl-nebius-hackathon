"""SVG → MotionLang assets: one asset per drawn element, plus suggested actors that mirror the `<g>` tree.

    result = import_svg(open("logo.svg").read(), fit=(1280, 720))
    result["assets"]   # {"swoosh": {"kind": "path", "d": "M...", "fill": "#e4572e", "bbox": [x, y, w, h], "label": "Swoosh"}}
    result["actors"]   # [{"id": "logo", "shape": {"kind": "group"}, "init": {"x": .., "y": ..}},
                       #  {"id": "swoosh", "shape": {"kind": "asset", "ref": "swoosh"}, "parent": "logo"}, ...]
    result["dropped"]  # what couldn't be kept (gradients → solid fill, filters, text, ...)

Transforms are flattened into the path data, and every shape (rect, circle, ellipse, line, polyline,
polygon, arcs) becomes absolute M/L/C/Q/Z data. Standard library XML only.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

from ..color import mix, parse_color, to_hex
from ..geometry import IDENTITY, Mat, mul
from ..model import CAMERA_ID
from ..paths import path_bbox, transform_path

INKSCAPE_LABEL = "{http://www.inkscape.org/namespaces/inkscape}label"
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
SHAPES = ("path", "rect", "circle", "ellipse", "line", "polyline", "polygon")
CONTAINERS = ("g", "a", "switch", "svg")
SKIP = ("defs", "symbol", "clipPath", "mask", "pattern", "marker", "linearGradient", "radialGradient", "style",
        "title", "desc", "metadata", "filter", "script", "namedview", "sodipodi:namedview")
INHERITED = ("fill", "stroke", "stroke-width", "fill-rule", "fill-opacity", "stroke-opacity", "visibility", "color")
STYLE_PROPS = INHERITED + ("opacity", "display", "filter", "clip-path", "mask", "stop-color")
DEFAULT_STYLE = {"fill": "#000000", "stroke": "none", "stroke-width": "1", "fill-rule": "nonzero", "color": "#000000"}
_NUM = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
_TRANSFORM = re.compile(r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)")
WORK_PRECISION = 4  # intermediate path data; the final pass rounds to `precision`


_AUTO_CLASS = re.compile(r"(cls|st|fil|str|fnt)-?\d+")  # Illustrator/CorelDRAW style classes say nothing


def _class_name(el: ET.Element) -> str | None:
    return next((c for c in (el.get("class") or "").split() if not _AUTO_CLASS.fullmatch(c)), None)


def _tag(el: ET.Element) -> str:
    return el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else ""


def _num(v: str | None, default: float = 0.0) -> float:
    if v is None:
        return default
    m = _NUM.match(v.strip())
    return float(m.group()) if m else default


def parse_transform(text: str | None) -> Mat:
    m = IDENTITY
    for name, args in _TRANSFORM.findall(text or ""):
        a = [float(x) for x in _NUM.findall(args)]
        if name == "matrix" and len(a) == 6:
            t = tuple(a)
        elif name == "translate" and a:
            t = (1, 0, 0, 1, a[0], a[1] if len(a) > 1 else 0)
        elif name == "scale" and a:
            t = (a[0], 0, 0, a[1] if len(a) > 1 else a[0], 0, 0)
        elif name == "rotate" and a:
            r = math.radians(a[0])
            cs, sn = math.cos(r), math.sin(r)
            t = (cs, sn, -sn, cs, 0, 0)
            if len(a) == 3:
                t = mul(mul((1, 0, 0, 1, a[1], a[2]), t), (1, 0, 0, 1, -a[1], -a[2]))
        elif name == "skewX" and a:
            t = (1, 0, math.tan(math.radians(a[0])), 1, 0, 0)
        elif name == "skewY" and a:
            t = (1, math.tan(math.radians(a[0])), 0, 1, 0, 0)
        else:
            continue
        m = mul(m, t)  # type: ignore[arg-type]
    return m


def _parse_decls(text: str) -> dict[str, str]:
    out = {}
    for part in text.split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip()] = v.strip().removesuffix("!important").strip()
    return out


def _parse_css(text: str) -> list[tuple[tuple[int, int], str, dict[str, str]]]:
    """Simple selectors only (tag, .class, #id, tag.class), which is what design tools export."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    rules = []
    for order, (sels, body) in enumerate(re.findall(r"([^{}]+)\{([^}]*)\}", text)):
        decls = _parse_decls(body)
        for sel in sels.split(","):
            sel = sel.strip()
            if re.fullmatch(r"[A-Za-z][\w-]*|[A-Za-z][\w-]*?\.[\w-]+|\.[\w-]+|#[\w-]+", sel):
                spec = 100 if sel.startswith("#") else 10 if "." in sel else 1
                rules.append(((spec, order), sel, decls))
    return sorted(rules, key=lambda r: r[0])


def _matches(sel: str, el: ET.Element) -> bool:
    if sel.startswith("#"):
        return el.get("id") == sel[1:]
    tag, _, cls = sel.partition(".")
    if tag and _tag(el) != tag:
        return False
    return not cls or cls in (el.get("class") or "").split()


@dataclass
class _Group:
    id: str | None
    label: str | None
    children: list["_Group | str"] = field(default_factory=list)  # str = asset id


class _Importer:
    def __init__(self, root: ET.Element):
        self.root = root
        self.by_id = {el.get("id"): el for el in root.iter() if el.get("id")}
        self.css = _parse_css("\n".join(el.text or "" for el in root.iter() if _tag(el) == "style"))
        self.assets: dict[str, dict[str, Any]] = {}
        self.opacity: dict[str, float] = {}
        self.dropped: list[str] = []
        self.used_ids: set[str] = {CAMERA_ID}
        self.counts: dict[str, int] = {}

    # ---- naming / reporting ----

    def drop(self, msg: str) -> None:
        if msg not in self.dropped:
            self.dropped.append(msg)

    def unique_id(self, raw: str | None, fallback: str) -> str:
        base = re.sub(r"[^A-Za-z0-9_-]+", "_", raw or "").strip("_")
        if not base:
            self.counts[fallback] = self.counts.get(fallback, 0) + 1
            base = f"{fallback}{self.counts[fallback]}"
        if base[0].isdigit():
            base = f"{fallback}_{base}"
        uid, n = base, 2
        while uid in self.used_ids:
            uid, n = f"{base}_{n}", n + 1
        self.used_ids.add(uid)
        return uid

    def label_of(self, el: ET.Element) -> str | None:
        if el.get(INKSCAPE_LABEL):
            return el.get(INKSCAPE_LABEL)
        title = next((c for c in el if _tag(c) == "title" and (c.text or "").strip()), None)
        if title is not None:
            return title.text.strip()  # type: ignore[union-attr]
        return el.get("id") or _class_name(el)

    def name_of(self, el: ET.Element) -> str:
        return el.get("id") or el.get(INKSCAPE_LABEL) or _class_name(el) or f"unnamed <{_tag(el)}>"

    # ---- style ----

    def style(self, el: ET.Element, parent: dict[str, str]) -> dict[str, str]:
        s = {k: v for k, v in parent.items() if k in INHERITED}
        for k in STYLE_PROPS:
            if el.get(k) is not None:
                s[k] = el.get(k)  # type: ignore[assignment]
        for _, sel, decls in self.css:
            if _matches(sel, el):
                s.update({k: v for k, v in decls.items() if k in STYLE_PROPS})
        s.update({k: v for k, v in _parse_decls(el.get("style") or "").items() if k in STYLE_PROPS})
        for k, v in list(s.items()):
            if v == "inherit":
                s[k] = parent.get(k, DEFAULT_STYLE.get(k, ""))
        return s

    def paint(self, value: str | None, st: dict[str, str], what: str, name: str) -> str | None:
        v = (value or "").strip()
        if not v or v == "none" or v == "transparent":
            return None
        if v == "currentColor":
            v = st.get("color", "#000000")
        m = re.match(r"url\(\s*['\"]?#([^'\")\s]+)['\"]?\s*\)\s*(.*)", v)
        if m:
            ref, fallback = self.by_id.get(m.group(1)), m.group(2).strip()
            solid = self.gradient_color(ref) if ref is not None else None
            if solid is None and fallback and fallback != "none":
                solid = self.color(fallback)
            solid = solid or "#808080"
            kind = _tag(ref) if ref is not None else "missing paint server"
            self.drop(f"{kind} {what} on {name!r} → solid {solid}")
            return solid
        c = self.color(v)
        if c is None:
            self.drop(f"unknown {what} colour {v!r} on {name!r} → #000000")
            return "#000000"
        return c

    def color(self, v: str) -> str | None:
        v = v.strip()
        m = re.fullmatch(r"rgba?\(\s*([\d.]+)(%?)[\s,]+([\d.]+)(%?)[\s,]+([\d.]+)(%?)(?:[\s,/]+[\d.]+%?)?\s*\)", v)
        if m:
            vals = [float(m.group(i)) * (2.55 if m.group(i + 1) else 1) for i in (1, 3, 5)]
            return "#" + "".join(f"{max(0, min(255, round(x))):02x}" for x in vals)
        try:
            return to_hex(parse_color(v))
        except (ValueError, KeyError):
            return None

    def gradient_color(self, el: ET.Element, depth: int = 0) -> str | None:
        """A gradient's middle colour, as the solid fallback."""
        stops = [c for c in el if _tag(c) == "stop"]
        href = el.get("href") or el.get(XLINK_HREF)
        if not stops and href and href.startswith("#") and depth < 5 and href[1:] in self.by_id:
            return self.gradient_color(self.by_id[href[1:]], depth + 1)
        colors = [self.color(self.style(s, {}).get("stop-color", "#000000")) or "#000000" for s in stops]
        if not colors:
            return None
        return colors[0] if len(colors) == 1 else mix(colors[0], colors[-1], 0.5)

    # ---- geometry ----

    def shape_d(self, el: ET.Element) -> str | None:
        t = _tag(el)
        g = lambda k, d=0.0: _num(el.get(k), d)  # noqa: E731
        if t == "path":
            return el.get("d") or None
        if t == "rect":
            x, y, w, h = g("x"), g("y"), g("width"), g("height")
            if w <= 0 or h <= 0:
                return None
            rx, ry = el.get("rx"), el.get("ry")
            rx_ = _num(rx if rx is not None else ry)
            ry_ = _num(ry if ry is not None else rx)
            rx_, ry_ = min(rx_, w / 2), min(ry_, h / 2)
            if rx_ <= 0 or ry_ <= 0:
                return f"M{x},{y} H{x + w} V{y + h} H{x} Z"
            return (f"M{x + rx_},{y} H{x + w - rx_} A{rx_},{ry_} 0 0 1 {x + w},{y + ry_} V{y + h - ry_} "
                    f"A{rx_},{ry_} 0 0 1 {x + w - rx_},{y + h} H{x + rx_} A{rx_},{ry_} 0 0 1 {x},{y + h - ry_} "
                    f"V{y + ry_} A{rx_},{ry_} 0 0 1 {x + rx_},{y} Z")
        if t in ("circle", "ellipse"):
            cx, cy = g("cx"), g("cy")
            rx = g("r") if t == "circle" else g("rx")
            ry = g("r") if t == "circle" else g("ry")
            if rx <= 0 or ry <= 0:
                return None
            return f"M{cx - rx},{cy} A{rx},{ry} 0 1 1 {cx + rx},{cy} A{rx},{ry} 0 1 1 {cx - rx},{cy} Z"
        if t == "line":
            return f"M{g('x1')},{g('y1')} L{g('x2')},{g('y2')}"
        if t in ("polyline", "polygon"):
            nums = [float(v) for v in _NUM.findall(el.get("points") or "")]
            pts = list(zip(nums[0::2], nums[1::2]))
            if len(pts) < 2:
                return None
            return "M" + " L".join(f"{x},{y}" for x, y in pts) + (" Z" if t == "polygon" else "")
        return None

    def add_shape(self, el: ET.Element, ctm: Mat, st: dict[str, str], opacity: float, id_hint: str | None = None) -> str | None:
        name = self.name_of(el)
        d = self.shape_d(el)
        if not d:
            return None
        fill = None if _tag(el) == "line" else self.paint(st.get("fill", "#000000"), st, "fill", name)  # a line has no area
        stroke = self.paint(st.get("stroke"), st, "stroke", name)
        if fill is None and stroke is None:
            self.drop(f"{name!r} has neither fill nor stroke (skipped)")
            return None
        try:
            td = transform_path(d, ctm, WORK_PRECISION)
            path_bbox(td)
        except ValueError as e:
            self.drop(f"{name!r}: unreadable path data ({e})")
            return None
        aid = self.unique_id(id_hint or el.get("id") or el.get(INKSCAPE_LABEL) or _class_name(el), _tag(el))
        asset: dict[str, Any] = {"kind": "path", "d": td}
        if fill is not None:
            asset["fill"] = fill
        if stroke is not None:
            asset["stroke"] = stroke
            asset["stroke_width"] = _num(st.get("stroke-width"), 1.0) * math.sqrt(abs(ctm[0] * ctm[3] - ctm[1] * ctm[2]))
        if st.get("fill-rule") == "evenodd" and fill is not None:
            asset["fill_rule"] = "evenodd"
        label = self.label_of(el)
        if label:
            asset["label"] = label
        alpha = opacity * _num(st.get("fill-opacity" if fill is not None else "stroke-opacity"), 1.0)
        if alpha < 0.999:
            self.opacity[aid] = round(max(alpha, 0.0), 3)
        self.assets[aid] = asset
        return aid

    # ---- tree walk ----

    def walk(self, el: ET.Element, ctm: Mat, parent_style: dict[str, str], opacity: float, depth: int = 0) -> list[_Group | str]:
        t = _tag(el)
        if t in SKIP or depth > 64:
            return []
        st = self.style(el, parent_style)
        if st.get("display") == "none" or st.get("visibility") in ("hidden", "collapse"):
            return []
        name = self.name_of(el)
        for prop in ("filter", "clip-path", "mask"):
            if st.get(prop) and st[prop] != "none":
                self.drop(f"{prop} on {name!r} (ignored)")
        opacity *= _num(st.get("opacity"), 1.0)
        ctm = mul(ctm, parse_transform(el.get("transform")))
        if t in SHAPES:
            aid = self.add_shape(el, ctm, st, opacity)
            return [aid] if aid else []
        if t == "use":
            href = el.get("href") or el.get(XLINK_HREF) or ""
            ref = self.by_id.get(href[1:]) if href.startswith("#") else None
            if ref is None:
                self.drop(f"<use> {name!r} points at {href!r}, which isn't in the file (skipped)")
                return []
            ctm = mul(ctm, (1, 0, 0, 1, _num(el.get("x")), _num(el.get("y"))))
            if _tag(ref) in SHAPES:
                ref_st = self.style(ref, st)
                aid = self.add_shape(ref, mul(ctm, parse_transform(ref.get("transform"))), ref_st, opacity * _num(ref_st.get("opacity"), 1.0), el.get("id") or ref.get("id"))
                return [aid] if aid else []
            if _tag(ref) in ("symbol", "g"):
                return self._group(el if el.get("id") else ref, list(ref), ctm, st, opacity, depth)
            return []
        if t in CONTAINERS:
            if t == "svg" and el is not self.root:
                ctm = mul(ctm, (1, 0, 0, 1, _num(el.get("x")), _num(el.get("y"))))
            return self._group(el, list(el), ctm, st, opacity, depth)
        if t in ("text", "image", "foreignObject"):
            self.drop(f"<{t}> element {name!r} (skipped; {'convert text to outlines' if t == 'text' else 'only vector shapes are imported'})")
        return []

    def _group(self, el: ET.Element, kids: list[ET.Element], ctm: Mat, st: dict[str, str], opacity: float, depth: int) -> list[_Group | str]:
        children: list[_Group | str] = []
        for c in kids:
            children += self.walk(c, ctm, st, opacity, depth + 1)
        if not children:
            return []
        named = el is not self.root and _tag(el) != "svg" and (el.get("id") or el.get(INKSCAPE_LABEL))
        if not named and (len(children) == 1 or el is self.root):
            return children  # anonymous wrappers add nothing to animate
        return [_Group(el.get("id") or el.get(INKSCAPE_LABEL), self.label_of(el), children)]


def _bbox_union(boxes: list[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[0] + b[2] for b in boxes)
    y1 = max(b[1] + b[3] for b in boxes)
    return x0, y0, x1 - x0, y1 - y0


def import_svg(
    text: str,
    *,
    fit: tuple[float, float] | None = None,
    margin: float = 0.1,
    precision: int = 2,
    root_id: str = "logo",
) -> dict[str, Any]:
    """Import an SVG document. Returns {"canvas", "assets", "actors", "dropped"}.

    fit: (width, height) of the target canvas; the artwork is scaled uniformly and centred to fill it
    with `margin` (a fraction of the canvas) on each side. Without it, coordinates stay in viewBox units.
    """
    if "<!ENTITY" in text:
        raise ValueError("SVG files with XML entity declarations are not accepted")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise ValueError(f"not a valid SVG/XML document: {e}") from e
    if _tag(root) != "svg":
        raise ValueError(f"root element is <{_tag(root)}>, expected <svg>")

    vb = [float(v) for v in _NUM.findall(root.get("viewBox") or "")]
    if len(vb) == 4 and vb[2] > 0 and vb[3] > 0:
        base: Mat = (1, 0, 0, 1, -vb[0], -vb[1])
        size: tuple[float, float] | None = (vb[2], vb[3])
    else:
        base = IDENTITY
        w, h = _num(root.get("width")), _num(root.get("height"))
        size = (w, h) if w > 0 and h > 0 else None

    imp = _Importer(root)
    top = imp.walk(root, base, dict(DEFAULT_STYLE), 1.0)
    if not imp.assets:
        raise ValueError("no drawable shapes found in the SVG" + (f" (dropped: {'; '.join(imp.dropped)})" if imp.dropped else ""))

    boxes = {k: path_bbox(a["d"]) for k, a in imp.assets.items()}
    if fit is not None:
        fw, fh = fit
        x, y, w, h = _bbox_union(list(boxes.values()))
        s = min(fw * (1 - 2 * margin) / max(w, 1e-9), fh * (1 - 2 * margin) / max(h, 1e-9))
        m: Mat = (s, 0, 0, s, fw / 2 - s * (x + w / 2), fh / 2 - s * (y + h / 2))
        for a in imp.assets.values():
            a["d"] = transform_path(a["d"], m, precision)
            if "stroke_width" in a:
                a["stroke_width"] *= s
        canvas = {"width": fw, "height": fh}
    else:
        for a in imp.assets.values():
            a["d"] = transform_path(a["d"], IDENTITY, precision)
        if size is None:
            x, y, w, h = _bbox_union(list(boxes.values()))
            size = (x + w, y + h)
        canvas = {"width": size[0], "height": size[1]}
    for k, a in imp.assets.items():
        boxes[k] = path_bbox(a["d"])
        a["bbox"] = [round(v, precision) for v in boxes[k]]
        if "stroke_width" in a:
            a["stroke_width"] = round(a["stroke_width"], precision)

    if len(top) > 1 or isinstance(top[0], str):
        top = [_Group(root_id, None, top)] if len(top) > 1 else top

    actors: list[dict[str, Any]] = []

    def leaves(g: _Group | str) -> list[str]:
        return [g] if isinstance(g, str) else [x for c in g.children for x in leaves(c)]

    def emit(node: _Group | str, parent: str | None, parent_center: tuple[float, float]) -> None:
        if isinstance(node, str):
            a: dict[str, Any] = {"id": node, "shape": {"kind": "asset", "ref": node}}
            if parent:
                a["parent"] = parent
            if node in imp.opacity:
                a["init"] = {"opacity": imp.opacity[node]}
            actors.append(a)
            return
        gid = imp.unique_id(node.id, "group")
        x, y, w, h = _bbox_union([boxes[k] for k in leaves(node)])
        cx, cy = x + w / 2, y + h / 2
        g: dict[str, Any] = {"id": gid, "shape": {"kind": "group"},
                             "init": {"x": round(cx - parent_center[0], precision), "y": round(cy - parent_center[1], precision)}}
        if parent:
            g["parent"] = parent
        if node.label and node.label != gid:
            g["label"] = node.label
        actors.append(g)
        for c in node.children:
            emit(c, gid, (cx, cy))

    for node in top:
        emit(node, None, (0.0, 0.0))
    return {"canvas": canvas, "assets": imp.assets, "actors": actors, "dropped": imp.dropped}
