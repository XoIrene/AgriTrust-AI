"""A small sliding-window rate limiter.

Scoped deliberately. ``slowapi`` is already a dependency, but wiring a global
limiter into ``app.main`` would add middleware to every route in the project -
including the ones this feature must not disturb. The NIDA endpoints need their
own budget and nothing else does, so the limiter lives here and the identity
router calls it explicitly.

Per-process and in-memory, which is the right trade for a single-node deployment:
it needs no table, no migration and no cleanup job. Behind more than one worker,
swap :class:`SlidingWindowLimiter` for a Redis-backed implementation of the same
two methods - ``allow`` and ``retry_after`` - and nothing at the call site moves.

Keys are opaque strings built by the caller. Nothing sensitive is used as a key:
the identity router keys on a truncated IP and on a SHA-256 of the phone number,
never on the number itself.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Deque, Optional


@dataclass(frozen=True)
class Decision:
    """The limiter's answer, plus when the caller may try again."""

    allowed: bool
    retry_after: int
    remaining: int

    def __bool__(self) -> bool:
        return self.allowed


class SlidingWindowLimiter:
    """Allow at most ``limit`` events per ``window_seconds`` for each key."""

    def __init__(self, limit: int, window_seconds: int) -> None:
        if limit < 1 or window_seconds < 1:
            raise ValueError("limit and window_seconds must be positive")
        self.limit = limit
        self.window_seconds = window_seconds
        self._hits: dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> Deque[float]:
        """Drop timestamps that have aged out. Caller holds the lock."""
        hits = self._hits[key]
        cutoff = now - self.window_seconds
        while hits and hits[0] <= cutoff:
            hits.popleft()
        return hits

    def check(self, key: str) -> Decision:
        """Look at the budget for ``key`` without spending any of it."""
        now = time.monotonic()
        with self._lock:
            hits = self._prune(key, now)
            used = len(hits)
        if used < self.limit:
            return Decision(allowed=True, retry_after=0, remaining=self.limit - used)
        # the slot frees up when the oldest hit falls out of the window
        with self._lock:
            hits = self._prune(key, now)
            wait = max(1, int(hits[0] + self.window_seconds - now) + 1) if hits else 1
        return Decision(allowed=False, retry_after=wait, remaining=0)

    def allow(self, key: str) -> Decision:
        """Spend one unit of ``key``'s budget if there is one left."""
        now = time.monotonic()
        with self._lock:
            hits = self._prune(key, now)
            if len(hits) >= self.limit:
                wait = max(1, int(hits[0] + self.window_seconds - now) + 1) if hits else 1
                return Decision(allowed=False, retry_after=wait, remaining=0)
            hits.append(now)
            return Decision(allowed=True, retry_after=0, remaining=self.limit - len(hits))

    def reset(self, key: Optional[str] = None) -> None:
        """Forget one key, or everything. Used by tests and by an operator."""
        with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)

    def used(self, key: str) -> int:
        now = time.monotonic()
        with self._lock:
            return len(self._prune(key, now))
