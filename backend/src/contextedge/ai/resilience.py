"""LLM call resilience: timeout, circuit breaker, fallback (backlog E1).

Budget gates, retries-on-validation, and schema repair already exist;
what was missing is protection against a *hanging or dying provider*:

- **Timeout** — every provider call is bounded; a hung connection
  becomes a counted failure instead of a stuck worker.
- **Circuit breaker** — per model, in-process (each worker protects
  itself): after ``FAILURE_THRESHOLD`` consecutive failures the circuit
  opens for ``COOLDOWN_SECONDS`` and calls fail fast with
  ``LlmCircuitOpenError`` — the pipeline's existing fail-soft paths
  treat it like any provider error, without queueing more work onto a
  dead endpoint. One probe call is allowed after the cooldown
  (half-open); success closes the circuit.
- **Fallback** — when ``settings.llm_fallback_model`` names a model,
  a failed primary call is retried ONCE on it (usage is recorded
  against the model that actually served).
"""

from __future__ import annotations

import time

import structlog

logger = structlog.get_logger()

LLM_CALL_TIMEOUT_SECONDS = 120.0
FAILURE_THRESHOLD = 5
COOLDOWN_SECONDS = 60.0


class LlmCircuitOpenError(RuntimeError):
    def __init__(self, model: str, retry_in: float):
        super().__init__(
            f"LLM circuit open for {model!r}; retry in ~{retry_in:.0f}s"
        )
        self.model = model
        self.retry_in = retry_in


class CircuitBreaker:
    """Consecutive-failure breaker per key. In-process by design — each
    worker protects itself; there is no cross-process coordination."""

    def __init__(
        self,
        threshold: int = FAILURE_THRESHOLD,
        cooldown: float = COOLDOWN_SECONDS,
        clock=time.monotonic,
    ):
        self.threshold = threshold
        self.cooldown = cooldown
        self.clock = clock
        self._failures: dict[str, int] = {}
        self._opened_at: dict[str, float] = {}
        self._probing: set[str] = set()

    def check(self, key: str) -> None:
        """Raise fast while open; allow ONE probe after cooldown."""
        opened = self._opened_at.get(key)
        if opened is None:
            return
        elapsed = self.clock() - opened
        if elapsed < self.cooldown:
            raise LlmCircuitOpenError(key, self.cooldown - elapsed)
        if key in self._probing:
            raise LlmCircuitOpenError(key, 0.0)
        self._probing.add(key)  # half-open: this caller probes

    def record_success(self, key: str) -> None:
        self._failures.pop(key, None)
        self._opened_at.pop(key, None)
        self._probing.discard(key)

    def record_failure(self, key: str) -> None:
        self._probing.discard(key)
        if key in self._opened_at:
            # Failed probe: re-open the window from now.
            self._opened_at[key] = self.clock()
            return
        count = self._failures.get(key, 0) + 1
        self._failures[key] = count
        if count >= self.threshold:
            self._opened_at[key] = self.clock()
            logger.warning(
                "llm.circuit_opened",
                model=key,
                consecutive_failures=count,
                cooldown_seconds=self.cooldown,
            )


# Module-level breaker shared by all calls in this process.
breaker = CircuitBreaker()
