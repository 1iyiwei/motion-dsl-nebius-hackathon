"""Turn whatever gets filed (photo, SVG, MotionLang document, GIF) into a still preview the vision model can read."""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path

from PIL import Image, ImageOps

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
SVG_EXT = {".svg"}
MOTION_SUFFIX = ".motion.json"
PREVIEW_PX = 1024  # longest side sent to the vision model
THUMB_PX = 384  # longest side returned to the chat


def kind_of(name: str) -> str | None:
    n = name.lower()
    if n.endswith(MOTION_SUFFIX):
        return "motion"
    ext = Path(n).suffix
    if ext in IMAGE_EXT:
        return "image"
    if ext in SVG_EXT:
        return "svg"
    return None


def sniff(data: bytes) -> str | None:
    """A file extension from the content, for uploads whose name has none (or the wrong one)."""
    head = data[:512].lstrip()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in data[:4096]):
        return ".svg"
    if head.startswith(b"{") and b'"timeline"' in data[:200_000]:
        return MOTION_SUFFIX
    try:
        fmt = Image.open(io.BytesIO(data)).format
    except Exception:
        return None
    return {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif", "BMP": ".bmp"}.get(fmt or "")


def _flatten(img: Image.Image) -> Image.Image:
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, "white")
        bg.paste(img, mask=img.split()[-1])
        return bg
    return img.convert("RGB")


def _svg_doc(text: str, size: int = 768) -> dict:
    from motionlang.importers import import_svg

    imp = import_svg(text, fit=(size, size), margin=0.06)
    return {"motion": "0.1", "canvas": dict(imp["canvas"], fps=10, background="#ffffff"),
            "assets": imp["assets"], "actors": imp["actors"], "timeline": {"id": "still", "seq": []}}


def _render(doc: dict, frames: int) -> list[Image.Image]:
    from motionlang import check
    from motionlang.render import to_frames

    r = check(doc)
    if r.baked is None:
        raise ValueError("document doesn't bake: " + "; ".join(i.message for i in r.report.errors[:3]))
    all_frames = to_frames(r.baked.to_json(), width=512)
    if frames <= 1 or len(all_frames) <= frames:
        return all_frames[-1:] if frames <= 1 else all_frames
    step = (len(all_frames) - 1) / (frames - 1)
    return [all_frames[round(i * step)] for i in range(frames)]


def _strip(images: list[Image.Image]) -> Image.Image:
    """Frames side by side, so one image shows how an animation unfolds."""
    w, h = images[0].size
    out = Image.new("RGB", (w * len(images), h), "white")
    for i, im in enumerate(images):
        out.paste(im.convert("RGB"), (i * w, 0))
    return out


def preview(data: bytes, name: str) -> Image.Image:
    """A still RGB image for this file. Animations become a 4-frame strip."""
    kind = kind_of(name)
    if kind == "image":
        img = Image.open(io.BytesIO(data))
        if getattr(img, "n_frames", 1) > 1:
            n = img.n_frames
            stills = []
            for i in (0, n // 3, 2 * n // 3, n - 1):
                img.seek(i)
                stills.append(_flatten(img.copy()))
            return _strip(stills)
        return _flatten(img)
    if kind == "svg":
        return _render(_svg_doc(data.decode("utf-8", "replace")), 1)[0]
    if kind == "motion":
        return _strip(_render(json.loads(data), 4))
    raise ValueError(f"can't preview {name!r}: supported are images, .svg and .motion.json")


def fit(img: Image.Image, px: int) -> Image.Image:
    img = img.copy()
    img.thumbnail((px, px))
    return img


def jpeg(img: Image.Image, quality: int = 85) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def data_url(jpeg_bytes: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg_bytes).decode()


def motion_facts(doc: dict) -> dict:
    """Facts about a MotionLang document that need no model: verbs and easings used, duration."""
    verbs: list[str] = []
    easings: list[str] = []

    def walk(block):
        if isinstance(block, dict):
            if "verb" in block:
                verbs.append(block["verb"])
                e = block.get("easing") or (block.get("params") or {}).get("easing")
                if isinstance(e, str):
                    easings.append(e)
            for v in block.values():
                walk(v)
        elif isinstance(block, list):
            for v in block:
                walk(v)

    walk(doc.get("timeline"))
    facts: dict = {"verbs": sorted(set(verbs)), "easings": sorted(set(easings))}
    try:
        from motionlang import check

        r = check(doc)
        if r.baked is not None:
            facts["seconds"] = round(r.baked.to_json().get("duration", 0), 2)
    except Exception:  # a broken document is still worth filing
        pass
    return facts
