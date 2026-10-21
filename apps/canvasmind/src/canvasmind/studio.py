"""Filing and recall: what the MCP tools and the CLI call."""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from html import unescape
from pathlib import Path
from urllib.parse import urljoin, urlparse

from motionlang.agent.llm import LLM, Call

from . import media, models
from .library import Item, Library
from .style import Style

MAX_BYTES = 20 * 1024 * 1024
# Super-120B with structured output was the most reliable MotionLang planner we measured (SketchDynamics, Oct 18).
PLANNER = os.environ.get("CANVASMIND_PLANNER_MODEL", "nvidia/nemotron-3-super-120b-a12b")


def default_llm() -> LLM:
    return LLM(models={"planner": PLANNER})
UPLOAD_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")


@dataclass
class Filed:
    item: Item
    duplicate: bool = False
    calls: list[Call] = field(default_factory=list)


class Studio:
    def __init__(self, library: Library, llm_factory: Callable[[], LLM] = default_llm, watch: list[Path] | None = None,
                 fetch: Callable[[str], tuple[bytes, str, str]] | None = None):
        self.lib = library
        self.make_llm = llm_factory
        # a watched path ending in /** is scanned recursively; ~/CanvasMind/inbox is always watched
        self.watch = [(Path(str(p).removesuffix("/**")).expanduser().resolve(), str(p).endswith("/**")) for p in (watch or [])]
        self.watch.insert(0, (self.lib.home / "inbox", False))
        self.fetch = fetch or fetch_url
        self.uploads = self.lib.home / ".uploads"
        self.style = Style(self.lib.home)

    # --- filing -------------------------------------------------------------------------------------------

    def file_bytes(self, data: bytes, filename: str, *, note: str = "", source: str = "chat", origin: str | None = None) -> Filed:
        if len(data) > MAX_BYTES:
            raise ValueError(f"{filename} is {len(data) // 1_000_000} MB; the limit is {MAX_BYTES // 1_000_000} MB")
        kind = media.kind_of(filename)
        if kind is None and (ext := media.sniff(data)):
            filename = Path(filename).name + ext
            kind = media.kind_of(filename)
        if kind is None:
            raise ValueError(f"can't file {filename!r}: send an image (jpg/png/webp/gif), an .svg or a .motion.json")
        sha = Library.sha(data)
        if (old := self.lib.find_sha(sha)) is not None:
            return Filed(old, duplicate=True)
        facts = media.motion_facts(json.loads(data)) if kind == "motion" else {}
        still = media.preview(data, filename)
        prev_jpg = media.jpeg(media.fit(still, media.PREVIEW_PX))
        thumb_jpg = media.jpeg(media.fit(still, media.THUMB_PX), quality=80)
        llm = self.make_llm()
        desc, call = models.caption(llm, media.data_url(prev_jpg), filename=filename, note=note, facts=facts or None)
        if desc["palette"]:
            facts["palette"] = desc["palette"]
        item = Item(id=self.lib.new_id(), kind=kind, filename=Path(filename).name, source=source, created=time.time(),
                    title=desc["title"], caption=desc["caption"], tags=desc["tags"], note=note, facts=facts,
                    origin=origin, sha256=sha)
        return Filed(self.lib.add(item, data, prev_jpg, thumb_jpg), calls=[call])

    def file_path(self, path: Path, *, note: str = "", source: str = "cli") -> Filed:
        path = Path(path).expanduser().resolve()
        return self.file_bytes(path.read_bytes(), path.name, note=note, source=source, origin=str(path))

    def file_url(self, url: str, *, note: str = "") -> Filed:
        data, ctype, final = self.fetch(url)
        name = Path(urlparse(final).path).name or "page"
        if ctype.startswith("image/") or media.kind_of(name):
            if not media.kind_of(name):
                name += {"image/png": ".png", "image/svg+xml": ".svg", "image/gif": ".gif", "image/webp": ".webp"}.get(ctype, ".jpg")
            return self.file_bytes(data, name, note=note, source="url", origin=final)
        return self._file_page(data.decode("utf-8", "replace"), final, note)

    def _file_page(self, html: str, url: str, note: str) -> Filed:
        """A web page: keep its title/description, and caption its preview image when it has one."""
        title = _meta(html, "og:title") or _tag_text(html, "title") or url
        desc = _meta(html, "og:description") or _meta(html, "description") or ""
        image = _meta(html, "og:image")
        if image:
            try:
                data, ctype, final = self.fetch(urljoin(url, image))
                name = Path(urlparse(final).path).name or "preview.jpg"
                if media.kind_of(name) != "image":
                    name = "preview.jpg"
                filed = self.file_bytes(data, name, note=" ".join(x for x in (note, title, desc) if x)[:500], source="url", origin=url)
                return filed
            except Exception:  # the page alone is still worth keeping
                pass
        sha = Library.sha(url.encode())
        if (old := self.lib.find_sha(sha)) is not None:
            return Filed(old, duplicate=True)
        words = sorted({w for w in re.findall(r"[a-z]{4,}", f"{title} {desc}".lower())})[:12]
        item = Item(id=self.lib.new_id(), kind="link", filename=urlparse(url).netloc, source="url", created=time.time(),
                    title=unescape(title)[:80], caption=unescape(desc)[:500], tags=["link", *words], note=note, origin=url, sha256=sha)
        return Filed(self.lib.add(item, None, None, None))

    # Chunked upload: the sandbox's helper script sends a file in pieces, because OpenShell caps one MCP request
    # at 128 KB and the model can't (and shouldn't) put image bytes in a tool call.

    def upload_part(self, upload_id: str, index: int, data: bytes) -> int:
        if not UPLOAD_ID.fullmatch(upload_id) or not 0 <= index < 512:
            raise ValueError("bad upload id or part index")
        d = self.uploads / upload_id
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{index:04d}").write_bytes(data)
        total = sum(p.stat().st_size for p in d.iterdir())
        if total > MAX_BYTES:
            self._drop_upload(upload_id)
            raise ValueError(f"upload is over {MAX_BYTES // 1_000_000} MB")
        return total

    def upload_finish(self, upload_id: str, filename: str, parts: int, note: str = "") -> Filed:
        if not UPLOAD_ID.fullmatch(upload_id):
            raise ValueError("bad upload id")
        d = self.uploads / upload_id
        try:
            chunks = [(d / f"{i:04d}").read_bytes() for i in range(parts)]
        except FileNotFoundError as e:
            raise ValueError(f"upload {upload_id} is missing a part ({e.filename}); send all parts again") from e
        finally:
            self._drop_upload(upload_id)
        return self.file_bytes(b"".join(chunks), Path(filename).name, note=note, source="chat")

    def _drop_upload(self, upload_id: str) -> None:
        d = self.uploads / upload_id
        if d.is_dir():
            for p in d.iterdir():
                p.unlink()
            d.rmdir()

    def scan(self, limit: int = 50) -> dict:
        """File new work from the watched folders (CANVASMIND_WATCH). Paths never come from the agent."""
        filed, skipped, errors = [], 0, []
        for root, recursive in self.watch:
            if not root.is_dir():
                continue
            for p in sorted(root.rglob("*") if recursive else root.glob("*"), key=lambda q: q.stat().st_mtime if q.is_file() else 0, reverse=True):
                if len(filed) >= limit:
                    break
                if not p.is_file() or p.name.startswith(".") or not media.kind_of(p.name):
                    continue
                if self.lib.find_sha(Library.sha(p.read_bytes())):
                    skipped += 1
                    continue
                try:
                    filed.append(self.file_path(p, source="scan").item.brief())
                except Exception as e:  # one bad file shouldn't stop the scan
                    errors.append(f"{p.name}: {e}")
        return {"filed": filed, "already_filed": skipped, "errors": errors[:10]}

    # --- animation and style ------------------------------------------------------------------------------

    def animate(self, item_id: str, brief: str = "", in_my_style: bool = True):
        from . import animation

        return animation.animate(self, item_id, brief, in_my_style=in_my_style)

    def tweak(self, item_id: str, request: str):
        from . import animation

        return animation.tweak(self, item_id, request)

    def feedback(self, item_id: str, verdict: str, why: str = "") -> tuple[dict, list[Call]]:
        item = self.lib.get(item_id)
        if item.kind != "motion":
            raise ValueError(f"{item_id} is not an animation; feedback is for animations CanvasMind made")
        return self.style.record(item_id, verdict, why, item.facts, self.make_llm())

    def digest(self, draft: bool = True):
        from . import digest

        return digest.make(self, draft=draft)

    # --- your data -----------------------------------------------------------------------------------------

    def forget(self, item_id: str) -> dict:
        """Delete an item and its files, and its feedback (the style profile is recounted without it)."""
        item = self.lib.remove(item_id)
        return {"forgotten": item.brief(), "feedback_removed": self.style.forget(item_id)}

    def export(self) -> Path:
        """Everything CanvasMind keeps, as one zip under ~/CanvasMind/exports/."""
        import shutil
        import tempfile

        out_dir = self.lib.home / "exports"
        out_dir.mkdir(exist_ok=True)
        self.lib.db.commit()
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp) / "canvasmind"
            shutil.copytree(self.lib.home, stage, ignore=shutil.ignore_patterns("exports", ".uploads", "*.sqlite-*"))
            return Path(shutil.make_archive(str(out_dir / time.strftime("canvasmind-%Y%m%d-%H%M%S")), "zip", tmp))

    # --- recall -------------------------------------------------------------------------------------------

    def recall(self, query: str, limit: int = 5) -> tuple[list[Item], list[Call]]:
        limit = max(1, min(limit, 10))
        hits = self.lib.search(query, 20)
        pool = {i.id: i for i in hits}
        for i in self.lib.recent(30 if len(hits) < 5 else 10):  # time words ("last week") and vague asks match no text
            pool.setdefault(i.id, i)
        if not pool:
            return [], []
        ids, call = models.rerank(self.make_llm(), query, [i.brief() for i in pool.values()], limit)
        return [pool[i] for i in ids], [call]


def _meta(html: str, name: str) -> str | None:
    m = re.search(rf'<meta[^>]+(?:property|name)=["\']{re.escape(name)}["\'][^>]*content=["\']([^"\']*)', html, re.I) or \
        re.search(rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]*(?:property|name)=["\']{re.escape(name)}["\']', html, re.I)
    return unescape(m.group(1)).strip() if m else None


def _tag_text(html: str, tag: str) -> str | None:
    m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", html, re.I | re.S)
    return unescape(re.sub(r"\s+", " ", m.group(1))).strip() if m else None


def fetch_url(url: str) -> tuple[bytes, str, str]:
    """GET a public http(s) URL, at most MAX_BYTES. Returns (body, content type, final URL)."""
    import ipaddress
    import socket

    import httpx

    def check(u: str) -> None:
        p = urlparse(u)
        if p.scheme not in ("http", "https") or not p.hostname:
            raise ValueError("only http(s) links can be filed")
        for info in socket.getaddrinfo(p.hostname, None):
            ip = ipaddress.ip_address(info[4][0])
            if not ip.is_global:  # keep the agent from reaching this machine's private network through us
                raise ValueError(f"{p.hostname} is not a public address")

    check(url)
    with httpx.Client(timeout=20, follow_redirects=False, headers={"User-Agent": "CanvasMind/0.1"}) as c:
        for _ in range(5):
            with c.stream("GET", url) as r:
                if r.is_redirect:
                    url = urljoin(url, r.headers["location"])
                    check(url)
                    continue
                r.raise_for_status()
                body = b""
                for chunk in r.iter_bytes():
                    body += chunk
                    if len(body) > MAX_BYTES:
                        raise ValueError("file is too large to keep")
                return body, r.headers.get("content-type", "").split(";")[0].strip().lower(), str(r.url)
    raise ValueError("too many redirects")
