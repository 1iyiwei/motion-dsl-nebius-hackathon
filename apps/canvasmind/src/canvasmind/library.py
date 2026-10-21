"""The studio library: files on disk under CANVASMIND_HOME, metadata and full-text search in SQLite (FTS5).

    ~/CanvasMind/
      library.sqlite
      items/<id>/original.<ext>   what was filed, byte for byte
      items/<id>/preview.jpg      what the vision model saw (≤1024 px)
      items/<id>/thumb.jpg        what the chat gets back (≤384 px)
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,          -- image | svg | motion | link
    filename TEXT NOT NULL,
    sha256 TEXT,
    source TEXT NOT NULL,        -- chat | url | scan | cli
    origin TEXT,                 -- URL or host path it came from
    title TEXT NOT NULL DEFAULT '',
    caption TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    note TEXT NOT NULL DEFAULT '',
    facts TEXT NOT NULL DEFAULT '{}',
    created REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS items_sha ON items(sha256) WHERE sha256 IS NOT NULL;
CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(id UNINDEXED, title, caption, tags, note, filename, tokenize='porter unicode61');
"""


@dataclass
class Item:
    id: str
    kind: str
    filename: str
    source: str
    created: float
    title: str = ""
    caption: str = ""
    tags: list[str] = field(default_factory=list)
    note: str = ""
    facts: dict = field(default_factory=dict)
    origin: str | None = None
    sha256: str | None = None

    @property
    def date(self) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(self.created))

    def brief(self) -> dict:
        """What the agent sees about an item: short, no paths."""
        d = {"id": self.id, "kind": self.kind, "title": self.title, "caption": self.caption, "tags": self.tags, "filed": self.date}
        if self.note:
            d["note"] = self.note
        if self.facts:
            d["facts"] = self.facts
        if self.origin and self.source == "url":
            d["url"] = self.origin
        return d

    def to_dict(self) -> dict:
        return asdict(self)


_WORD = re.compile(r"[\w#]+", re.UNICODE)


def fts_query(text: str) -> str:
    """Any word matches, prefix-matched, quoted so user text can't inject FTS syntax."""
    words = [w for w in _WORD.findall(text.lower()) if len(w) > 1]
    return " OR ".join(f'"{w}"*' for w in words[:16])


class Library:
    def __init__(self, home: Path):
        self.home = Path(home).expanduser()
        (self.home / "items").mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.home / "library.sqlite", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def dir(self, item_id: str) -> Path:
        if not re.fullmatch(r"\d{8}-[0-9a-f]{6}", item_id):
            raise KeyError(f"no item {item_id!r}")
        return self.home / "items" / item_id

    @staticmethod
    def new_id() -> str:
        return time.strftime("%Y%m%d") + "-" + secrets.token_hex(3)

    def find_sha(self, sha: str) -> Item | None:
        row = self.db.execute("SELECT * FROM items WHERE sha256 = ?", (sha,)).fetchone()
        return self._item(row) if row else None

    def add(self, item: Item, original: bytes | None, preview_jpg: bytes | None, thumb_jpg: bytes | None) -> Item:
        d = self.dir(item.id)
        d.mkdir(parents=True, exist_ok=True)
        if original is not None:
            ext = ".motion.json" if item.kind == "motion" else Path(item.filename).suffix.lower()
            (d / f"original{ext}").write_bytes(original)
        if preview_jpg:
            (d / "preview.jpg").write_bytes(preview_jpg)
        if thumb_jpg:
            (d / "thumb.jpg").write_bytes(thumb_jpg)
        with self.db:
            self.db.execute(
                "INSERT INTO items (id, kind, filename, sha256, source, origin, title, caption, tags, note, facts, created) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (item.id, item.kind, item.filename, item.sha256, item.source, item.origin, item.title, item.caption,
                 json.dumps(item.tags), item.note, json.dumps(item.facts), item.created))
            self.db.execute("INSERT INTO items_fts (id, title, caption, tags, note, filename) VALUES (?,?,?,?,?,?)",
                            (item.id, item.title, item.caption, " ".join(item.tags), item.note, item.filename))
        return item

    def get(self, item_id: str) -> Item:
        self.dir(item_id)
        row = self.db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        if not row:
            raise KeyError(f"no item {item_id!r}")
        return self._item(row)

    def thumb(self, item_id: str) -> bytes | None:
        p = self.dir(item_id) / "thumb.jpg"
        return p.read_bytes() if p.is_file() else None

    def original(self, item_id: str) -> Path | None:
        return next(iter(sorted(self.dir(item_id).glob("original*"))), None)

    def search(self, text: str, limit: int = 20) -> list[Item]:
        q = fts_query(text)
        if not q:
            return []
        rows = self.db.execute(
            "SELECT items.* FROM items_fts JOIN items ON items.id = items_fts.id WHERE items_fts MATCH ? "
            "ORDER BY bm25(items_fts, 0, 4.0, 2.0, 3.0, 2.0, 1.0) LIMIT ?", (q, limit)).fetchall()
        return [self._item(r) for r in rows]

    def recent(self, limit: int = 30, since: float | None = None) -> list[Item]:
        rows = self.db.execute("SELECT * FROM items WHERE created >= ? ORDER BY created DESC LIMIT ?",
                               (since or 0, limit)).fetchall()
        return [self._item(r) for r in rows]

    def count(self) -> dict[str, int]:
        return {r["kind"]: r["n"] for r in self.db.execute("SELECT kind, COUNT(*) AS n FROM items GROUP BY kind")}

    @staticmethod
    def sha(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _item(self, row: sqlite3.Row) -> Item:
        return Item(id=row["id"], kind=row["kind"], filename=row["filename"], source=row["source"], created=row["created"],
                    title=row["title"], caption=row["caption"], tags=json.loads(row["tags"]), note=row["note"],
                    facts=json.loads(row["facts"]), origin=row["origin"], sha256=row["sha256"])
