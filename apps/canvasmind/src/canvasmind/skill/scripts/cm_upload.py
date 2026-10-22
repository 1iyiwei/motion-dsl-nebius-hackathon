"""Send a file from the Hermes sandbox to the CanvasMind library on the user's machine.

    /opt/hermes/.venv/bin/python cm_upload.py PATH [--note "what the user said"]
    /opt/hermes/.venv/bin/python cm_upload.py --latest [--note …]   # newest file the chat received (stickers, .svg files)

Runs inside the OpenShell sandbox. It talks to the canvasmind MCP server configured in ~/.hermes/config.yaml, in
chunks under OpenShell's 128 KB MCP request cap; the bearer token in that config is an OpenShell placeholder that
is resolved at egress, so the real token never enters the sandbox. Prints the filed item as JSON.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import secrets
import sys
from pathlib import Path

import httpx
import yaml

CHUNK = 90_000  # raw bytes per request; ~120 KB once base64'd into JSON-RPC
MAX_PX = 1600
CACHE = Path(os.path.expanduser("~/.hermes/cache"))  # images/, documents/ (an .svg sent as a file), …
LATEST_MAX_AGE = 30 * 60  # seconds; older attachments are probably not the one the user means


def server() -> tuple[str, dict]:
    cfg = yaml.safe_load(Path(os.path.expanduser("~/.hermes/config.yaml")).read_text())
    s = (cfg.get("mcp_servers") or {}).get("canvasmind")
    if not s:
        sys.exit("canvasmind MCP server is not configured in ~/.hermes/config.yaml")
    return s["url"], dict(s.get("headers") or {})


def shrink(data: bytes, name: str) -> tuple[bytes, str]:
    """Photos from a phone are big; the library keeps a 1600 px JPEG, which is plenty for a reference."""
    if Path(name).suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".heic", ".bmp"} or len(data) < 400_000:
        return data, name
    try:
        from PIL import Image, ImageOps

        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
        if img.mode in ("RGBA", "LA", "P"):
            return data, name  # keep transparency (logos) untouched
        img.thumbnail((MAX_PX, MAX_PX))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=85, optimize=True)
        return buf.getvalue(), Path(name).stem + ".jpg"
    except Exception:
        return data, name


def latest() -> Path:
    import time

    files = [f for f in CACHE.rglob("*") if f.is_file()] if CACHE.is_dir() else []
    if not files:
        sys.exit("no files have been received in this chat")
    newest = max(files, key=lambda f: f.stat().st_mtime)
    if time.time() - newest.stat().st_mtime > LATEST_MAX_AGE:
        sys.exit("nothing received in the last 30 minutes; ask the user to send it again")
    return newest


USER: dict = {}


def call(client: httpx.Client, url: str, headers: dict, tool: str, args: dict) -> dict:
    args = {**args, **USER}
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}
    r = client.post(url, json=body, headers={**headers, "Accept": "application/json, text/event-stream"})
    r.raise_for_status()
    text = r.text
    if r.headers.get("content-type", "").startswith("text/event-stream"):
        text = next(line[5:] for line in text.splitlines() if line.startswith("data:"))
    msg = json.loads(text)
    if "error" in msg:
        sys.exit(f"canvasmind: {msg['error'].get('message')}")
    res = msg["result"]
    if res.get("isError"):
        sys.exit("canvasmind: " + " ".join(c.get("text", "") for c in res.get("content", [])))
    return res


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("path", nargs="?")
    p.add_argument("--latest", action="store_true", help="file the newest file the chat received")
    p.add_argument("--note", default="")
    p.add_argument("--user", default="", help=argparse.SUPPRESS)  # added by the demo plugin: whose library
    a = p.parse_args()
    if a.latest:
        path = latest()
    elif a.path:
        path = Path(a.path).expanduser()
    else:
        sys.exit("give a path or --latest")
    data, name = shrink(path.read_bytes(), path.name)
    if a.user:
        USER["_cm_user"] = a.user
    url, headers = server()
    upload_id = "upl_" + secrets.token_hex(8)
    parts = [data[i:i + CHUNK] for i in range(0, len(data), CHUNK)] or [b""]
    with httpx.Client(timeout=120) as c:
        for i, part in enumerate(parts):
            call(c, url, headers, "upload_part", {"upload_id": upload_id, "index": i, "data_b64": base64.b64encode(part).decode()})
        res = call(c, url, headers, "upload_finish", {"upload_id": upload_id, "filename": name, "parts": len(parts), "note": a.note})
    print(res["content"][0]["text"])


if __name__ == "__main__":
    main()
