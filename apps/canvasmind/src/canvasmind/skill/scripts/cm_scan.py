"""Hermes cron script (--no-agent): file new work from the user's watched folders into CanvasMind.

Prints one line per run only when something new was filed; empty output means Hermes sends nothing. No model call
in the sandbox: canvasmind captions new items on the user's machine.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx
import yaml


def main() -> None:
    cfg = yaml.safe_load(Path(os.path.expanduser("~/.hermes/config.yaml")).read_text())
    server = (cfg.get("mcp_servers") or {}).get("canvasmind") or {}
    if not server.get("url"):
        sys.exit("canvasmind MCP server is not configured")
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "scan_studio", "arguments": {}}}
    r = httpx.post(server["url"], json=body, timeout=300,
                   headers={**(server.get("headers") or {}), "Accept": "application/json, text/event-stream"})
    r.raise_for_status()
    text = r.text
    if r.headers.get("content-type", "").startswith("text/event-stream"):
        text = next(line[5:] for line in text.splitlines() if line.startswith("data:"))
    result = json.loads(text)["result"]
    data = result.get("structuredContent") or json.loads(result["content"][0]["text"])
    data = data.get("result", data)
    filed = data.get("filed") or []
    if filed:
        titles = ", ".join(f["title"] for f in filed[:5]) + (f" and {len(filed) - 5} more" if len(filed) > 5 else "")
        print(f"📥 Filed {len(filed)} new item{'s' if len(filed) != 1 else ''} from your studio: {titles}")


if __name__ == "__main__":
    main()
