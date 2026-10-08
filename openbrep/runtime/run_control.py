"""Shared, deterministic control for one runtime execution.

RunControl owns the run-wide deadline, cancellation probe, tool budget and
terminal decision. Callers check it after blocking model work and immediately
before invoking a tool or applying a fallback mutation.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Literal, TypeVar

StopReason = Literal["cancelled", "deadline", "tool_budget", "terminal"]
T = TypeVar("T")


@dataclass(frozen=True)
class DeliveryDecision:
    allowed: bool
    reason: StopReason | None
    stage: str
    tool_calls: int
    checked_at: float


class RunControl:
    """Single-run control plane; no HTTP/session or prompt behavior lives here."""

    def __init__(
        self,
        *,
        tool_budget: int,
        task_timeout: float,
        should_cancel: Callable[[], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.tool_budget = max(0, int(tool_budget))
        self._clock = clock
        self.started_at = clock()
        self._deadline_at = self.started_at + max(0.0, float(task_timeout))
        self._should_cancel = should_cancel
        self._lock = threading.RLock()
        self._tool_calls = 0
        self._stage = "accepted"
        self._terminal_reason: str | None = None

    def __deepcopy__(self, memo):
        """TaskRequest copies retain the same per-run control plane."""
        memo[id(self)] = self
        return self

    @property
    def tool_calls(self) -> int:
        with self._lock:
            return self._tool_calls

    @property
    def stage(self) -> str:
        with self._lock:
            return self._stage

    @property
    def terminal_reason(self) -> str | None:
        with self._lock:
            return self._terminal_reason

    @property
    def deadline_at(self) -> float:
        return self._deadline_at

    def snapshot(self) -> dict[str, object]:
        """Return structured execution facts for reports; never parse status text."""
        now = self._clock()
        with self._lock:
            return {
                "stage": self._stage,
                "tool_calls": self._tool_calls,
                "tool_budget": self.tool_budget,
                "elapsed_seconds": max(0.0, now - self.started_at),
                "remaining_seconds": max(0.0, self._deadline_at - now),
                "terminal_reason": self._terminal_reason,
            }

    def set_stage(self, stage: str) -> None:
        with self._lock:
            if self._terminal_reason is None:
                self._stage = str(stage)

    def check(self, *, stage: str | None = None) -> DeliveryDecision:
        """Check cancellation/deadline/terminal state without consuming budget."""
        now = self._clock()
        with self._lock:
            if stage is not None and self._terminal_reason is None:
                self._stage = str(stage)
            reason: StopReason | None = "terminal" if self._terminal_reason else None
            if reason is None and self._is_cancelled():
                reason = "cancelled"
            if reason is None and now >= self._deadline_at:
                reason = "deadline"
            return DeliveryDecision(reason is None, reason, self._stage, self._tool_calls, now)

    def begin_tool(self, *, stage: str | None = None) -> DeliveryDecision:
        """Atomically authorize and count one tool call, or deny it."""
        now = self._clock()
        with self._lock:
            if stage is not None and self._terminal_reason is None:
                self._stage = str(stage)
            reason: StopReason | None = "terminal" if self._terminal_reason else None
            if reason is None and self._is_cancelled():
                reason = "cancelled"
            if reason is None and now >= self.deadline_at:
                reason = "deadline"
            if reason is None and self._tool_calls >= self.tool_budget:
                reason = "tool_budget"
            if reason is None:
                self._tool_calls += 1
            return DeliveryDecision(reason is None, reason, self._stage, self._tool_calls, now)

    def commit(self, mutation: Callable[[], T], *, stage: str = "commit") -> tuple[DeliveryDecision, T | None]:
        """Recheck and perform one synchronous mutation at the commit boundary."""
        with self._lock:
            decision = self.check(stage=stage)
            if not decision.allowed:
                return decision, None
            return decision, mutation()

    def finish(self, reason: str) -> bool:
        """Record the first terminal decision; duplicate finishes are rejected."""
        with self._lock:
            if self._terminal_reason is not None:
                return False
            self._terminal_reason = str(reason)
            self._stage = "terminal"
            return True

    def _is_cancelled(self) -> bool:
        if self._should_cancel is None:
            return False
        try:
            return bool(self._should_cancel())
        except Exception:
            # Cancellation probes are advisory; a broken probe cannot authorize
            # a write by raising through the runtime.
            return True
