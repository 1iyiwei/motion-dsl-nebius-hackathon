"""`canvasmind demo-reset`: a clean, seeded library for recording the video or running the judges' demo bot."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from .library import Library
from .studio import Studio

DEFAULT_FIXTURES = Path(__file__).resolve().parents[2] / "demo"  # apps/canvasmind/demo in the repo


def reset(home: Path, fixtures: Path = DEFAULT_FIXTURES, studio_factory=None) -> dict:
    """Move the current library aside (never deleted) and file the fixtures, backdated, with their feedback."""
    home = Path(home).expanduser()
    manifest = json.loads((fixtures / "manifest.json").read_text())
    backup = None
    if home.exists() and any(home.iterdir()):
        backup = home.with_name(f"{home.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.move(str(home), backup)
    studio = (studio_factory or (lambda h: Studio(Library(h))))(home)
    filed = []
    for entry in manifest["items"]:
        item = studio.file_path(fixtures / entry["file"], note=entry.get("note", ""), source="cli").item
        created = time.time() - float(entry.get("days_ago", 0)) * 86400
        studio.lib.backdate(item.id, created)
        if fb := entry.get("feedback"):
            studio.feedback(item.id, fb[0], fb[1] if len(fb) > 1 else "")
        filed.append(f"{item.id}  {entry['file']}  ({item.title})")
    return {"home": str(home), "backup": str(backup) if backup else None, "filed": filed,
            "style": studio.style.profile()}
