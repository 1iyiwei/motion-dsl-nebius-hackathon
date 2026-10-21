"""canvasmind as an MCP server (Streamable HTTP), for Hermes Agent running inside an OpenShell sandbox.

OpenShell only lets the sandbox reach MCP servers over HTTPS with a trusted certificate and one bearer token, so:
the server binds to loopback, `tailscale serve` gives it https://<host>.<tailnet>.ts.net, and every request must
carry `Authorization: Bearer $CANVASMIND_MCP_TOKEN` (OpenShell adds it at egress; the sandbox never holds it).
"""

from __future__ import annotations

import base64
import hmac
import json
import time
from typing import Any

from .library import Item
from .studio import Studio

INSTRUCTIONS = (
    "CanvasMind is the user's private studio library: their sketches, logos, references and animations, kept on their "
    "own machine. File what they send (photos: run the canvasmind skill's upload script; links: file_link), and use "
    "recall to find earlier work by description or time (\"the sunrise logo from last week\").")

# Tools only the sandbox's upload script calls; hide them from the model with
# mcp_servers.canvasmind.tools.exclude in the Hermes config.
HIDDEN_TOOLS = ("upload_part", "upload_finish")
UPLOAD_HINT = ("Not a web link. To file an image the user sent in the chat, run this terminal command (it uploads the "
               "file to their library and prints what CanvasMind saw): /opt/hermes/.venv/bin/python "
               '~/.hermes/skills/canvasmind/scripts/cm_upload.py {path} --note "{note}"')


def style_brief(prof: dict) -> dict:
    from .style import summary

    return {"summary": summary(prof) or "no feedback yet", "likes": prof.get("likes", 0), "dislikes": prof.get("dislikes", 0),
            "verbs": prof.get("verbs", {}), "easings": prof.get("easings", {}), "seconds": prof.get("seconds", {}),
            "notes": prof.get("notes", [])}


def _log(studio: Studio, tool: str, calls, seconds: float) -> None:
    rec = {"t": round(time.time(), 1), "tool": tool, "seconds": round(seconds, 2),
           "calls": [{"purpose": c.purpose, "model": c.model, "seconds": round(c.seconds, 2),
                      "prompt_tokens": c.prompt_tokens, "completion_tokens": c.completion_tokens} for c in calls]}
    with open(studio.lib.home / "calls.jsonl", "a") as f:
        f.write(json.dumps(rec) + "\n")


def create_mcp(studio: Studio, allowed_hosts: list[str]):
    from mcp.server.fastmcp import FastMCP, Image
    from mcp.server.transport_security import TransportSecuritySettings

    mcp = FastMCP("canvasmind", instructions=INSTRUCTIONS, stateless_http=True,
                  transport_security=TransportSecuritySettings(
                      enable_dns_rebinding_protection=True, allowed_hosts=allowed_hosts,
                      allowed_origins=[f"https://{h}" for h in allowed_hosts]))

    def with_thumbs(payload: Any, items: list[Item], images: bool) -> list:
        out: list = [json.dumps(payload, ensure_ascii=False)]
        if images:
            for it in items:
                if (t := studio.lib.thumb(it.id)) is not None:
                    out.append(Image(data=t, format="jpeg"))
        return out

    def filed_reply(tool: str, filed, t0: float) -> list:
        _log(studio, tool, filed.calls, time.time() - t0)
        payload = {"filed": filed.item.brief(), "duplicate": filed.duplicate}
        if filed.duplicate:
            payload["message"] = "already in the library"
        return [json.dumps(payload, ensure_ascii=False)]

    @mcp.tool()
    def file_link(url: str, note: str = "") -> list:
        """File a web link (an image, SVG, MotionLang file or a page) into the library. note: what the user said about it.
        Not for images the user sent in the chat: those are uploaded with the canvasmind upload command."""
        if not url.startswith(("http://", "https://")):  # the model's usual slip: a chat attachment's local path
            return [json.dumps({"filed": None, "message": UPLOAD_HINT.format(path=url or "--latest", note=note.replace('"', "'"))})]
        t0 = time.time()
        return filed_reply("file_link", studio.file_url(url, note=note), t0)

    @mcp.tool()
    def recall(query: str, limit: int = 3, images: bool = True) -> list:
        """Find items in the user's library by description, project or time ("bouncy blue logo", "café rebrand",
        "what I filed last week"). Returns the matches (best first) and, with images=true, a thumbnail of each to show
        the user."""
        t0 = time.time()
        items, calls = studio.recall(query, limit)
        _log(studio, "recall", calls, time.time() - t0)
        payload = {"query": query, "matches": [i.brief() for i in items]}
        if not items:
            payload["message"] = "nothing in the library matches"
        return with_thumbs(payload, items, images)

    @mcp.tool()
    def show(item_id: str) -> list:
        """One library item's details and its thumbnail."""
        item = studio.lib.get(item_id)
        return with_thumbs(item.brief(), [item], True)

    @mcp.tool()
    def studio_status() -> dict:
        """How many items of each kind the library holds, and the most recent ones."""
        return {"counts": studio.lib.count(), "recent": [i.brief() for i in studio.lib.recent(5)]}

    @mcp.tool()
    def scan_studio() -> dict:
        """File new work from the user's watched folders (set on their machine, not chosen by the agent)."""
        t0 = time.time()
        out = studio.scan()
        _log(studio, "scan_studio", [], time.time() - t0)
        return out

    def animated_reply(tool: str, res, t0: float) -> list:
        _log(studio, tool, res.calls, time.time() - t0)
        payload = {"animation": res.item.brief(), "valid": res.ok,
                   "next": "show the GIF, then ask whether they like it (👍/👎) or want a change"}
        if res.why:
            payload["style_used"] = res.why
        if res.note:
            payload["note"] = res.note
        return [json.dumps(payload, ensure_ascii=False), Image(data=res.gif, format="gif")]

    @mcp.tool()
    def animate(item_id: str, brief: str = "", in_my_style: bool = True) -> list:
        """Animate an SVG item from the library (find its id with recall). brief: what should happen, in the user's
        words; leave empty to let CanvasMind propose an animation. Returns the new animation item and its GIF.
        Only SVG artwork can be animated, not photos. Takes 20-60 s."""
        from .animation import NotAnimatable

        t0 = time.time()
        try:
            return animated_reply("animate", studio.animate(item_id, brief, in_my_style), t0)
        except NotAnimatable as e:
            return [json.dumps({"animation": None, "message": str(e)})]

    @mcp.tool()
    def tweak(item_id: str, request: str) -> list:
        """Change an animation CanvasMind made ("slower", "less bounce", "make the steam rise twice"). Returns a new
        version with its GIF; the old one stays in the library."""
        from .animation import NotAnimatable

        t0 = time.time()
        try:
            return animated_reply("tweak", studio.tweak(item_id, request), t0)
        except NotAnimatable as e:
            return [json.dumps({"animation": None, "message": str(e)})]

    @mcp.tool()
    def feedback(item_id: str, verdict: str, why: str = "") -> dict:
        """Record the user's 👍 ("like") or 👎 ("dislike") on an animation, with their reason if they gave one. This is
        how CanvasMind learns their motion style; call it whenever they react to an animation."""
        t0 = time.time()
        prof, calls = studio.feedback(item_id, verdict, why)
        _log(studio, "feedback", calls, time.time() - t0)
        return {"recorded": True, "style": style_brief(prof)}

    @mcp.tool()
    def style_profile() -> dict:
        """The user's motion style as CanvasMind has learned it, with the evidence (counts of liked/disliked verbs
        and easings, typical length, notes from their feedback)."""
        return style_brief(studio.style.profile())

    @mcp.tool()
    def upload_part(upload_id: str, index: int, data_b64: str) -> dict:
        """Internal: called by the canvasmind upload script, never by the model."""
        return {"received": studio.upload_part(upload_id, index, base64.b64decode(data_b64, validate=True))}

    @mcp.tool()
    def upload_finish(upload_id: str, filename: str, parts: int, note: str = "") -> list:
        """Internal: called by the canvasmind upload script, never by the model."""
        t0 = time.time()
        return filed_reply("file_upload", studio.upload_finish(upload_id, filename, parts, note), t0)

    _hide(mcp)
    return mcp


def _hide(mcp) -> None:
    """Keep the model's view small: the upload tools stay callable but unlisted, and the server advertises no
    resources or prompts (Hermes otherwise adds list/read-resource tools, and the model reads the empty resource
    list as an empty library). Done here rather than in Hermes's config, which NemoClaw regenerates."""
    import mcp.types as t

    async def listed():
        return [tool for tool in await mcp.list_tools() if tool.name not in HIDDEN_TOOLS]

    mcp._mcp_server.list_tools()(listed)
    for req in (t.ListResourcesRequest, t.ListResourceTemplatesRequest, t.ReadResourceRequest,
                t.ListPromptsRequest, t.GetPromptRequest):
        mcp._mcp_server.request_handlers.pop(req, None)


def create_app(studio: Studio, token: str, public_host: str = "", port: int = 8770):
    """The ASGI app: bearer-token check in front of the MCP endpoint at /mcp."""
    from starlette.responses import JSONResponse

    if not token:
        raise RuntimeError("CANVASMIND_MCP_TOKEN is not set; the MCP server never runs without a token")
    hosts = [f"127.0.0.1:{port}", f"localhost:{port}"] + ([public_host] if public_host else [])
    inner = create_mcp(studio, hosts).streamable_http_app()
    expected = f"Bearer {token}".encode()

    async def app(scope, receive, send):
        if scope["type"] == "http":
            got = dict(scope["headers"]).get(b"authorization", b"")
            if not hmac.compare_digest(got, expected):
                return await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
        return await inner(scope, receive, send)

    return app
