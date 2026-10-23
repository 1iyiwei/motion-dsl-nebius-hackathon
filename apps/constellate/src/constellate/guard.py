"""Guardrails for a publicly hosted copy: model calls spend the host's Token Factory and Tavily credits, and the
sweep, the video and live mode spend its CPU. Adapted from SketchDynamics' guard (apps never import each other).

All off by default (local use is unchanged). Turn them on with environment variables:

    CONSTELLATE_ACCESS_CODE     model calls, sweeps, videos and live sessions need header X-Access-Code with this value
    CONSTELLATE_RATE_LIMIT      model requests (plan, tweak, make flyable, brief) per visitor per hour (visitor = client IP)
    CONSTELLATE_DAILY_TOKENS    tokens (prompt + completion) all visitors may spend per UTC day
    CONSTELLATE_MAX_JOBS        heavy jobs (make flyable, sweep, video, live) running at once; more get "busy"
    CONSTELLATE_BEHIND_PROXY    1 when a reverse proxy (e.g. Render) sets X-Forwarded-For (implied when $PORT is set)

Counters live in memory: a restart (e.g. a free host waking from sleep) resets them, so the access code is the
real gate and the budget is a backstop.
"""

from __future__ import annotations

import hmac
import os
import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Iterator

from fastapi import HTTPException, Request

HOUR = 3600.0


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


@dataclass
class Guard:
    access_code: str = ""
    per_hour: int = 0  # 0 = unlimited
    daily_tokens: int = 0  # 0 = unlimited
    max_jobs: int = 0  # 0 = unlimited
    behind_proxy: bool = False
    clock: Callable[[], float] = time.monotonic
    _hits: dict[str, deque] = field(default_factory=lambda: defaultdict(deque))
    _day: str = field(default_factory=_today)
    _spent: int = 0
    _jobs: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @classmethod
    def from_env(cls) -> "Guard":
        num = lambda k: int(os.environ.get(k, "0") or 0)  # noqa: E731
        return cls(
            access_code=os.environ.get("CONSTELLATE_ACCESS_CODE", ""),
            per_hour=num("CONSTELLATE_RATE_LIMIT"),
            daily_tokens=num("CONSTELLATE_DAILY_TOKENS"),
            max_jobs=num("CONSTELLATE_MAX_JOBS"),
            behind_proxy=os.environ.get("CONSTELLATE_BEHIND_PROXY", "") not in ("", "0", "false") or bool(os.environ.get("PORT")),
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

    def check(self, request: Request, model: bool = True) -> None:
        """Call before a gated request; raises 401/429 with a message the page shows as is.
        model=False (sweep, video, live) checks only the access code."""
        if self.access_code:
            given = request.headers.get("x-access-code", "")
            if not hmac.compare_digest(given.encode(), self.access_code.encode()):
                raise HTTPException(401, detail="This demo needs an access code (see the submission notes): enter it next to the title.")
        if not model:
            return
        with self._lock:
            self._roll_day()
            if self.daily_tokens and self._spent >= self.daily_tokens:
                raise HTTPException(429, detail="The demo's daily model budget is used up. It resets at 00:00 UTC; run it locally with your own key meanwhile.")
            if self.per_hour:
                now, hits = self.clock(), self._hits[self.visitor(request)]
                while hits and now - hits[0] > HOUR:
                    hits.popleft()
                if len(hits) >= self.per_hour:
                    wait = int(HOUR - (now - hits[0])) // 60 + 1
                    raise HTTPException(429, detail=f"Rate limit: {self.per_hour} model requests per hour. Try again in about {wait} min.")
                hits.append(now)

    def spend(self, tokens: int) -> None:
        with self._lock:
            self._roll_day()
            self._spent += tokens

    def acquire_job(self) -> None:
        """Take a heavy-job slot or raise 429; pair with release_job (see `job`)."""
        with self._lock:
            if self.max_jobs and self._jobs >= self.max_jobs:
                raise HTTPException(429, detail="The demo is busy with other simulations. Try again in a minute.")
            self._jobs += 1

    def release_job(self) -> None:
        with self._lock:
            self._jobs = max(0, self._jobs - 1)

    @contextmanager
    def job(self) -> Iterator[None]:
        self.acquire_job()
        try:
            yield
        finally:
            self.release_job()

    def status(self) -> dict:
        with self._lock:
            self._roll_day()
            left = max(0, self.daily_tokens - self._spent) if self.daily_tokens else None
        return {"access_required": bool(self.access_code), "rate_limit_per_hour": self.per_hour or None, "tokens_left_today": left}
