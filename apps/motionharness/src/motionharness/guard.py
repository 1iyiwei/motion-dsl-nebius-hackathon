"""Guardrails for a publicly hosted copy: every model call spends the host's Token Factory credits.

All off by default (local use is unchanged). Turn them on with environment variables:

    MOTIONHARNESS_ACCESS_CODE    model calls need header X-Access-Code with this value (share it with judges)
    MOTIONHARNESS_RATE_LIMIT     runs and scene trials per visitor per hour (a visitor = client IP)
    MOTIONHARNESS_DAILY_TOKENS   tokens (prompt + completion) all visitors may spend per UTC day
    MOTIONHARNESS_BEHIND_PROXY   1 when a reverse proxy (e.g. Render) sets X-Forwarded-For

(Same design as SketchDynamics' guard; each app keeps its own copy so it can be split out on its own.)

Counters live in memory: a restart (e.g. a free host waking from sleep) resets them, so the access code is the
real gate and the budget is a backstop.
"""

from __future__ import annotations

import hmac
import os
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import HTTPException, Request

HOUR = 3600.0


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


@dataclass
class Guard:
    access_code: str = ""
    per_hour: int = 0  # 0 = unlimited
    daily_tokens: int = 0  # 0 = unlimited
    behind_proxy: bool = False
    clock: "callable" = time.monotonic
    _hits: dict[str, deque] = field(default_factory=lambda: defaultdict(deque))
    _day: str = field(default_factory=_today)
    _spent: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @classmethod
    def from_env(cls) -> "Guard":
        num = lambda k: int(os.environ.get(k, "0") or 0)  # noqa: E731
        return cls(
            access_code=os.environ.get("MOTIONHARNESS_ACCESS_CODE", ""),
            per_hour=num("MOTIONHARNESS_RATE_LIMIT"),
            daily_tokens=num("MOTIONHARNESS_DAILY_TOKENS"),
            behind_proxy=os.environ.get("MOTIONHARNESS_BEHIND_PROXY", "") not in ("", "0", "false"),
        )

    def visitor(self, request: Request) -> str:
        if self.behind_proxy:
            # The proxy appends the address it saw; anything to the left is client-supplied and spoofable.
            fwd = request.headers.get("x-forwarded-for", "")
            if fwd.strip():
                return fwd.split(",")[-1].strip()
        return request.client.host if request.client else "?"

    def _roll_day(self) -> None:
        if (d := _today()) != self._day:
            self._day, self._spent = d, 0

    def check(self, request: Request) -> None:
        """Call before a model request; raises 401/429 with a message the UI shows as is."""
        if self.access_code:
            given = request.headers.get("x-access-code", "")
            if not hmac.compare_digest(given.encode(), self.access_code.encode()):
                raise HTTPException(401, detail="This demo needs an access code (see the submission notes): enter it next to the title.")
        with self._lock:
            self._roll_day()
            if self.daily_tokens and self._spent >= self.daily_tokens:
                raise HTTPException(429, detail="The demo's daily model budget is used up. It resets at 00:00 UTC; run MotionHarness locally with your own key meanwhile.")
            if self.per_hour:
                now, hits = self.clock(), self._hits[self.visitor(request)]
                while hits and now - hits[0] > HOUR:
                    hits.popleft()
                if len(hits) >= self.per_hour:
                    wait = int(HOUR - (now - hits[0])) // 60 + 1
                    raise HTTPException(429, detail=f"Rate limit: {self.per_hour} runs per hour. Try again in about {wait} min.")
                hits.append(now)

    def spend(self, tokens: int) -> None:
        with self._lock:
            self._roll_day()
            self._spent += tokens

    def status(self) -> dict:
        with self._lock:
            self._roll_day()
            left = max(0, self.daily_tokens - self._spent) if self.daily_tokens else None
        return {"access_required": bool(self.access_code), "rate_limit_per_hour": self.per_hour or None, "tokens_left_today": left}
