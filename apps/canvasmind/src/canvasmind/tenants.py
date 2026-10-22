"""Per-user demo libraries for the public demo bot.

The Hermes plugin adds a reserved `_cm_user` argument (the Telegram user id) to every canvasmind tool call. The server
strips it before FastMCP sees the call and binds that user's Studio for the duration of the request, so the tools
themselves don't change. Each user's library lives under <home>/users/<id>/, is seeded from the demo fixtures on first
use (copied from a template library that is built, with its model captions, once a day), and starts over once it
is a day old. Without the argument (the private bot), the single library is used.
"""

from __future__ import annotations

import contextvars
import json
import re
import shutil
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .library import Library
from .studio import Studio

USER_ARG = "_cm_user"
MAX_AGE = 24 * 3600
_current: contextvars.ContextVar[str] = contextvars.ContextVar("canvasmind_user", default="")


class Tenants:
    def __init__(self, home: Path, fixtures: Path, make: Callable[[Path], Studio] | None = None, max_age: float = MAX_AGE):
        self.root = Path(home) / "users"
        self.fixtures = fixtures
        self.make = make or (lambda h: Studio(Library(h)))
        self.max_age = max_age
        self._cache: dict[str, Studio] = {}
        self._lock = threading.Lock()

    def get(self, user: str) -> Studio:
        uid = re.sub(r"[^0-9A-Za-z_-]", "", str(user))[:40] or "anonymous"
        home = self.root / uid
        stamp = home / ".seeded"
        with self._lock:
            fresh = stamp.is_file() and time.time() - float(stamp.read_text() or 0) < self.max_age
            if not fresh:
                template = self._template()
                self._cache.pop(uid, None)
                if home.exists():
                    shutil.rmtree(home)  # a demo library: nothing in it is worth keeping past a day
                shutil.copytree(template, home, ignore=shutil.ignore_patterns(".seeded"))
                stamp.write_text(str(time.time()))
            if uid not in self._cache:
                self._cache[uid] = self.make(home)
            return self._cache[uid]

    def _template(self) -> Path:
        """The seeded library every new user starts from, rebuilt once a day so "last week" stays last week."""
        from .demo import reset

        template = self.root.parent / "template"
        stamp = template / ".seeded"
        if not (stamp.is_file() and time.time() - float(stamp.read_text() or 0) < self.max_age):
            if template.exists():
                shutil.rmtree(template)
            reset(template, self.fixtures, self.make)
            stamp.write_text(str(time.time()))
        return template


class CurrentStudio:
    """Stands in for a Studio in the MCP tools: resolves to the calling user's Studio, or the default one."""

    def __init__(self, default: Studio, tenants: Tenants | None):
        self._default, self._tenants = default, tenants

    def resolve(self) -> Studio:
        user = _current.get()
        return self._tenants.get(user) if (self._tenants and user) else self._default

    def __getattr__(self, name):
        return getattr(self.resolve(), name)


def with_user(inner):
    """ASGI wrapper: take `_cm_user` out of a tools/call request and bind it while the request runs."""

    async def app(scope, receive, send):
        if scope["type"] != "http" or scope.get("method") != "POST":
            return await inner(scope, receive, send)
        chunks, more = [], True
        while more:
            msg = await receive()
            chunks.append(msg.get("body", b""))
            more = msg.get("more_body", False)
        body, user = b"".join(chunks), ""
        try:
            req = json.loads(body)
            args = (req.get("params") or {}).get("arguments") if isinstance(req, dict) else None
            if isinstance(args, dict) and USER_ARG in args:
                user = str(args.pop(USER_ARG))
                body = json.dumps(req).encode()
        except (ValueError, AttributeError):
            pass
        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        headers = [(k, v) for k, v in scope["headers"] if k != b"content-length"] + [(b"content-length", str(len(body)).encode())]
        token = _current.set(user)
        try:
            return await inner({**scope, "headers": headers}, replay, send)
        finally:
            _current.reset(token)

    return app
