"""Shapes for things the planner can only approximate: "a crystal falls from the sky" shouldn't play as a ball.

For each object a request names that has no shape yet, the app looks in three places, cheapest first:

1. the shape library: the SVGs in `static/samples/`, listed with their source and license in its README;
2. the web, via Tavily search restricted to sites with clean, reusable icons (SVG Repo, Wikimedia Commons);
3. Nemotron itself, which writes a simple flat SVG when nothing found looks right.

Nemotron VL compares the candidates in one call, the user picks one, and it joins the scene as imported
parts. Every SVG, found or drawn, goes through motionlang's importer and is written back out as plain
paths, so nothing from the web (scripts, links, external images) reaches the page or the library.
"""

from __future__ import annotations

import html
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import httpx

from motionlang.importers import import_svg
from motionlang.paths import transform_path

THUMB = 200  # cleaned SVGs use a 0..THUMB viewBox
MAX_SVG_BYTES = 400_000
MAX_PARTS = 80  # beyond this an icon is an illustration: slow to plan, hard to animate
FETCH_HOSTS = {"www.svgrepo.com", "svgrepo.com", "upload.wikimedia.org", "commons.wikimedia.org"}
SEARCH_DOMAINS = ["svgrepo.com", "commons.wikimedia.org"]
USER_AGENT = "SketchDynamics/0.1 (open-source hackathon demo; icon lookup)"
LIBRARY_SECTION = "## Found or drawn in SketchDynamics"


@dataclass
class Candidate:
    id: str
    name: str  # the object it is for, e.g. "crystal"
    title: str  # what the source calls it
    source: str  # library | svgrepo | commons | nemotron
    svg: str  # cleaned: plain paths in a 0..THUMB box
    parts: int
    page_url: str = ""
    license: str = ""  # empty = not known
    license_verified: bool = False
    credit: str = ""

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


# ---------- cleaning ----------

def _attr(v: Any) -> str:
    return html.escape(str(v), quote=True)


def assets_svg(assets: dict[str, dict[str, Any]], opacity: dict[str, float], box: tuple[float, float]) -> str:
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {box[0]:g} {box[1]:g}">']
    for aid, a in assets.items():
        attrs = [f'd="{_attr(a["d"])}"', f'fill="{_attr(a.get("fill") or "none")}"']
        if a.get("stroke"):
            attrs.append(f'stroke="{_attr(a["stroke"])}" stroke-width="{_attr(a.get("stroke_width") or 1)}" stroke-linecap="round" stroke-linejoin="round"')
        if a.get("fill_rule") == "evenodd":
            attrs.append('fill-rule="evenodd"')
        if aid in opacity:
            attrs.append(f'opacity="{_attr(opacity[aid])}"')
        out.append(f"<path {' '.join(attrs)}/>")
    out.append("</svg>")
    return "\n".join(out)


def clean_svg(text: str) -> tuple[str, int]:
    """Any SVG → (plain-path SVG in a THUMB box, number of parts). Raises ValueError if unusable."""
    imported = import_svg(text, fit=(THUMB, THUMB), margin=0.04)
    assets = imported["assets"]
    if not assets:
        raise ValueError("no drawable shapes")
    if len(assets) > MAX_PARTS:
        raise ValueError(f"{len(assets)} parts is too detailed to animate (max {MAX_PARTS})")
    opacity = {a["shape"]["ref"]: a["init"]["opacity"] for a in imported["actors"]
               if a["shape"]["kind"] == "asset" and "opacity" in a.get("init", {})}
    return assets_svg(assets, opacity, (THUMB, THUMB)), len(assets)


def slug(text: str, sep: str = "_") -> str:
    return re.sub(r"[^a-z0-9]+", sep, text.lower()).strip(sep)[:32] or "shape"


def _words(text: str) -> set[str]:
    return {w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w
            for w in re.findall(r"[a-z]+", text.lower())}


# ---------- placing a shape in the scene ----------

def _union(boxes: list[list[float]]) -> tuple[float, float, float, float]:
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    return x0, y0, max(b[0] + b[2] for b in boxes) - x0, max(b[1] + b[3] for b in boxes) - y0


def placed_parts(svg: str, name: str, existing: dict[str, Any], canvas: tuple[float, float]) -> tuple[dict[str, Any], dict[str, float], dict[str, Any] | None]:
    """The shape's parts in canvas coordinates, ids prefixed with its name, set beside what's already
    on the canvas (the planner moves it from there). Returns (assets, opacities, group for >1 part)."""
    W, H = canvas
    size = H * (0.3 if existing else 0.45)
    imported = import_svg(svg, fit=(size, size), margin=0)
    parts = imported["assets"]
    x, y, w, h = _union([a["bbox"] for a in parts.values()])
    # Free spot: the canvas centre when empty, else the first of these that clears the existing art.
    spots = [(W / 2, H / 2)]
    if existing:
        ex, ey, ew, eh = _union([a["bbox"] for a in existing.values() if a.get("bbox")])
        spots = [(W * 0.82, H / 2), (W * 0.18, H / 2), (W / 2, H * 0.2), (W / 2, H * 0.8), (W * 0.82, H * 0.2), (W / 2, H / 2)]
        clear = lambda cx, cy: cx + w / 2 < ex or cx - w / 2 > ex + ew or cy + h / 2 < ey or cy - h / 2 > ey + eh  # noqa: E731
        spots = [s for s in spots if clear(*s)] or spots[-1:]
    cx, cy = spots[0]
    m = (1, 0, 0, 1, cx - (x + w / 2), cy - (y + h / 2))
    base = slug(name)
    taken = set(existing)
    single = len(parts) == 1
    out: dict[str, Any] = {}
    renamed: dict[str, str] = {}
    for n, (old, a) in enumerate(parts.items(), 1):
        pid, k = (base if single else f"{base}_{n}"), 2
        while pid in taken or pid in out:
            pid, k = f"{base}_{n}_{k}" if not single else f"{base}_{k}", k + 1
        d = transform_path(a["d"], m)
        bx, by, bw, bh = a["bbox"]
        out[pid] = {**a, "d": d, "bbox": [round(bx + m[4], 2), round(by + m[5], 2), bw, bh],
                    "label": name if single else f"{name} part {n}", "meta": {"source": f"{name} shape"}}
        renamed[old] = pid
    opacity = {renamed[a["shape"]["ref"]]: a["init"]["opacity"] for a in imported["actors"]
               if a["shape"]["kind"] == "asset" and "opacity" in a.get("init", {})}
    group = None if single else {"id": base, "label": name, "parts": list(out)}
    return out, opacity, group


# ---------- the library ----------

class Library:
    """SVG files in a folder with a README listing each as `* [name](file.svg): …` (source and license)."""

    def __init__(self, folder: Path, writable: bool = True):
        self.folder = Path(folder)
        self.writable = writable
        self._lock = threading.Lock()

    def entries(self) -> list[tuple[str, str]]:
        readme = self.folder / "README.md"
        if not readme.exists():
            return []
        found = re.findall(r"^\s*[*-]\s*\[([^\]]+)\]\(([^)]+\.svg)\)", readme.read_text(), re.M)
        return [(name, file) for name, file in found if (self.folder / file).is_file()]

    def find(self, name: str) -> list[Candidate]:
        want = _words(name)
        out = []
        for title, file in self.entries():
            if want and want <= _words(f"{title} {file}"):
                try:
                    svg, parts = clean_svg((self.folder / file).read_text())
                except ValueError:
                    continue
                out.append(Candidate(f"lib:{file}", name, title, "library", svg, parts, page_url=f"/static/samples/{file}"))
        return out

    def save(self, name: str, svg: str, *, license: str, source: str, page_url: str = "", credit: str = "") -> str:
        if not self.writable:
            raise PermissionError("saving shapes is turned off on this server")
        if not license.strip():
            raise ValueError("a shape needs a license to go in the library (see its source page)")
        clean, _ = clean_svg(svg)
        with self._lock:
            base = slug(name, "-")
            file, n = f"{base}.svg", 2
            while (self.folder / file).exists():
                file, n = f"{base}-{n}.svg", n + 1
            (self.folder / file).write_text(clean + "\n")
            origin = {"nemotron": "drawn by Nemotron in SketchDynamics", "svgrepo": "from SVG Repo",
                      "commons": "from Wikimedia Commons"}.get(source, f"from {source}")
            line = f"* [{name.strip()}]({file}): {origin}" + (f" ({page_url})" if page_url.startswith("http") else "") \
                + (f", by {credit}" if credit else "") + f"; license: {license.strip()}"
            readme = self.folder / "README.md"
            text = readme.read_text() if readme.exists() else "# Sources\n"
            if LIBRARY_SECTION not in text:
                text = text.rstrip() + f"\n\n{LIBRARY_SECTION}\n"
            readme.write_text(text.rstrip() + "\n" + line + "\n")
        return file


# ---------- the web, via Tavily ----------

class WebSearch:
    """Tavily search restricted to icon sites, then the SVG files themselves (from allowed hosts only)."""

    def __init__(self, api_key: str, http: httpx.Client | None = None):
        self.api_key = api_key
        self.http = http or httpx.Client(timeout=20, headers={"User-Agent": USER_AGENT})

    @classmethod
    def from_env(cls) -> "WebSearch | None":
        key = os.environ.get("TAVILY_API_KEY", "").strip()
        return cls(key) if key else None

    def search(self, name: str, limit: int = 8) -> tuple[list[Candidate], dict[str, Any]]:
        r = self.http.post("https://api.tavily.com/search", headers={"Authorization": f"Bearer {self.api_key}"},
                           json={"query": f"{name} svg icon", "include_domains": SEARCH_DOMAINS, "max_results": 10})
        r.raise_for_status()
        results = r.json().get("results", [])
        found: list[dict[str, Any]] = []
        commons: list[dict[str, Any]] = []
        for res in results:
            url = res.get("url", "")
            title = re.sub(r"(?i)\s*(svg vector|vector svg icon|svg icon|- svg repo|\| svg repo|free download).*$", "", res.get("title", "")).strip()
            if m := re.search(r"svgrepo\.com/svg/(\d+)/([a-z0-9-]+)", url):
                sid, s = m.groups()
                found.append({"title": title or s.replace("-", " "), "source": "svgrepo", "page_url": f"https://www.svgrepo.com/svg/{sid}/{s}",
                              "svg_url": f"https://www.svgrepo.com/show/{sid}/{s}.svg"})
            elif m := re.search(r"commons\.wikimedia\.org/wiki/(File:[^?#]+\.svg)", url, re.I):
                commons.append({"file": unquote(m.group(1)), "title": title, "page_url": url.split("?")[0]})
        found += self._commons(commons)
        seen, unique = set(), []
        for f in found:
            if f["svg_url"] not in seen:
                seen.add(f["svg_url"])
                unique.append(f)
        unique = unique[:limit]
        with ThreadPoolExecutor(max_workers=6) as pool:
            svgs = list(pool.map(lambda f: self._fetch(f["svg_url"]), unique))
        out, skipped = [], 0
        for n, (f, text) in enumerate(zip(unique, svgs), 1):
            try:
                svg, parts = clean_svg(text) if text else ("", 0)
            except ValueError:
                svg = ""
            if not svg:
                skipped += 1
                continue
            out.append(Candidate(f"web:{n}", name, f["title"] or name, f["source"], svg, parts, page_url=f["page_url"],
                                 license=f.get("license", ""), license_verified=bool(f.get("license")), credit=f.get("credit", "")))
        return out, {"results": len(results), "svgs": len(unique), "usable": len(out), "skipped": skipped}

    def _commons(self, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Commons answers with the file URL and its license in one API call."""
        if not files:
            return []
        try:
            r = self.http.get("https://commons.wikimedia.org/w/api.php", params={
                "action": "query", "format": "json", "prop": "imageinfo", "iiprop": "url|extmetadata",
                "iiextmetadatafilter": "LicenseShortName|Artist", "titles": "|".join(f["file"] for f in files[:10])})
            pages = r.json().get("query", {}).get("pages", {}).values()
        except (httpx.HTTPError, ValueError):
            return []
        info = {p.get("title", "").replace(" ", "_"): p.get("imageinfo", [{}])[0] for p in pages}
        out = []
        for f in files:
            ii = info.get(f["file"].replace(" ", "_"))
            if not ii or not ii.get("url"):
                continue
            meta = ii.get("extmetadata", {})
            artist = re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", "")).strip()[:80]
            title = f["title"] or f["file"][5:-4].replace("_", " ")
            out.append({"title": re.sub(r"(?i)^file:|\.svg.*$", "", title).strip(), "source": "commons", "page_url": f["page_url"],
                        "svg_url": ii["url"].split("?")[0], "license": meta.get("LicenseShortName", {}).get("value", ""), "credit": artist})
        return out

    def _fetch(self, url: str) -> str | None:
        u = urlparse(url)
        if u.scheme != "https" or u.hostname not in FETCH_HOSTS:
            return None
        try:
            with self.http.stream("GET", url, follow_redirects=False) as r:
                if r.status_code != 200:
                    return None
                data = b""
                for chunk in r.iter_bytes():
                    data += chunk
                    if len(data) > MAX_SVG_BYTES:
                        return None
            return data.decode("utf-8", errors="replace")
        except httpx.HTTPError:
            return None


# ---------- prompts ----------

def needed_messages(intent: str, have: list[str]) -> list[dict[str, Any]]:
    return [{"role": "user", "content": (
        f'An animation request: "{intent}"\n'
        + (f"Already on the canvas: {', '.join(have)}.\n" if have else "")
        + "List the concrete objects (things, as nouns) the request names and wants to SHOW that need a recognizable drawing and are "
        "not already on the canvas. Never list actions or motions (bounce, fall, spin, fade), colours or sizes. Leave out what simple geometry draws well (ball, dot, circle, square, box, rectangle, triangle, star, "
        "line, arrow, text, letters, words, numbers), parts of something already listed or on the canvas, places and "
        "backgrounds (the sky, the ground, a room), anything only mentioned, not shown, and anything the request doesn't name: \"a ball swings\" names no string, \"a frog jumps\" names no pond. Use the singular noun, 1-3 words "
        "(\"crystal\", \"paper plane\"). At most 3.\n"
        'Reply with JSON only: {"objects": ["<object>", ...]}'
    )}]


def judge_messages(name: str, image: str, count: int) -> list[dict[str, Any]]:
    return [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": image}},
        {"type": "text", "text": (
            f'This grid shows {count} candidate drawing{"s" if count > 1 else ""}, numbered 1-{count}, meant to show "{name}" in a 2D '
            "motion graphic. For each, first say in a few words what it actually looks like, as if you didn't know what it was "
            "meant to be. Then score it from 0 to 10: how clearly it reads as that object at a glance (most important; a few "
            "lines or blobs that only suggest it score low), and how well it would work as an animated icon (a clean, simple "
            "silhouette in flat colours scores higher than a detailed illustration, a scene, text, or a company logo). For "
            "anything below 7, say in a few words what would fix it.\n"
            'Reply with JSON only: {"scores": [{"n": 1, "seen": "<what it looks like>", "score": 0-10, "note": "<short>"}, ...]}'
        )},
    ]}]


def draw_messages(name: str, feedback: str = "", previous: str = "", error: str = "") -> list[dict[str, Any]]:
    text = (
        f'Draw "{name}" as a simple, recognizable flat icon in SVG for a 2D motion graphic.\n'
        'Use viewBox="0 0 200 200", 3-12 <path>, <circle>, <ellipse>, <rect> or <polygon> elements with solid fills in a '
        "small, harmonious palette (an outline stroke is fine), and no text, gradients, filters, images, masks or CSS. "
        "Give the parts that could move on their own (a wing, a wheel, a flame) their own elements, wrapped in a <g id=...> "
        "when several belong together. Fill most of the box, centred.\n"
    )
    if previous:
        text += f"\nYour previous drawing:\n{previous}\n"
    if feedback:
        text += f"A reviewer looking at it said: {feedback}\nFix that.\n"
    if error:
        text += f"It could not be used: {error}\nFix that.\n"
    text += 'Reply with JSON only: {"svg": "<svg ...>...</svg>"}'
    return [{"role": "user", "content": text}]


# The planner draws these well from ellipses, rects, polygons and text; models still list them now and then,
# and small models sometimes list the motion itself ("bounce").
BUILT_IN = {"ball", "dot", "circle", "disc", "square", "box", "rectangle", "triangle", "star", "line", "arrow", "text",
            "letter", "word", "number", "shape", "sky", "ground", "background", "canvas", "screen", "curve", "path",
            "bounce", "fall", "drop", "spin", "rotate", "fade", "slide", "jump", "roll", "move", "grow", "shrink", "pop",
            "wiggle", "orbit", "float", "appear", "disappear", "enter", "exit", "reveal", "shake", "pulse", "squash", "motion",
            "wall", "floor", "platform", "horizon"}


def parse_objects(text: str, intent: str = "") -> list[str]:
    """Objects from the model's reply, minus built-in shapes, motions, and (given the request) anything it doesn't
    name: small models add props a scene implies ("a ball swings" → "string"), and a picker for those is noise."""
    reply = json.loads(text)
    items = reply.get("objects", []) if isinstance(reply, dict) else []
    names = [re.sub(r"^(an?|the|some)\s+", "", " ".join(x.split()).strip(" .").lower())[:40] for x in items if isinstance(x, str)]
    said = _words(intent) if intent else None
    return list(dict.fromkeys(n for n in names if n and _words(n.split()[-1]) - BUILT_IN
                              and (said is None or _words(n) <= said)))[:3]


def parse_scores(text: str, count: int) -> list[dict[str, Any]]:
    reply = json.loads(text)
    items = reply.get("scores", []) if isinstance(reply, dict) else []
    scores: dict[int, dict[str, Any]] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            n, s = int(str(it.get("n")).lstrip("#")), float(it.get("score"))
        except (TypeError, ValueError):
            continue
        if 1 <= n <= count and n not in scores:
            scores[n] = {"n": n, "score": max(0.0, min(10.0, s)), "note": str(it.get("note") or "")[:160]}
    return [scores.get(n, {"n": n, "score": None, "note": ""}) for n in range(1, count + 1)]
