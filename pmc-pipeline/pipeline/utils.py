"""Shared utilities — rate limiting, retries, logging, checkpoints.

Every module in the pipeline imports from here rather than rolling its
own retry / rate-limit / logging logic.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import aiohttp


# ═══════════════════════════════════════════════════════════════════════
# Token-bucket rate limiter
# ═══════════════════════════════════════════════════════════════════════

class TokenBucketRateLimiter:
    """Async token-bucket rate limiter.

    Parameters
    ----------
    rate : float
        Sustained tokens (requests) per second.
    max_tokens : float, optional
        Bucket capacity.  Defaults to *rate* (one-second burst).
    """

    def __init__(self, rate: float, max_tokens: float | None = None) -> None:
        self.rate = rate
        self.max_tokens = max_tokens if max_tokens is not None else rate
        self._tokens = self.max_tokens
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Block until a token is available, then consume it."""
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._tokens = min(
                    self.max_tokens,
                    self._tokens + elapsed * self.rate,
                )
                self._last_refill = now

                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return

            # Wait a fraction of the refill interval before retrying.
            await asyncio.sleep(1.0 / self.rate)


# ═══════════════════════════════════════════════════════════════════════
# Retry decorator
# ═══════════════════════════════════════════════════════════════════════

def retry_with_backoff(
    max_retries: int = 5,
    base_delay: float = 2.0,
    max_delay: float = 120.0,
    retryable_exceptions: tuple = (
        aiohttp.ClientError,
        asyncio.TimeoutError,
        ConnectionError,
        OSError,
    ),
):
    """Decorator: retries an ``async`` function with exponential back-off + jitter.

    The decorated function receives an extra ``_tracker`` keyword-only
    argument.  If a :class:`ThroughputTracker` is passed, retries are
    counted automatically.
    """

    def decorator(func):
        async def wrapper(*args, _tracker: ThroughputTracker | None = None, **kwargs):
            last_exc: Exception | None = None
            for attempt in range(1, max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except retryable_exceptions as exc:
                    last_exc = exc
                    if attempt == max_retries:
                        break
                    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    delay *= 0.5 + random.random()  # jitter
                    logger = logging.getLogger("pmc_pipeline")
                    logger.warning(
                        "Retry %d/%d for %s: %s — waiting %.1fs",
                        attempt,
                        max_retries,
                        func.__name__,
                        exc,
                        delay,
                    )
                    if _tracker:
                        _tracker.record_retry()
                    await asyncio.sleep(delay)
            raise last_exc  # type: ignore[misc]

        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper

    return decorator


# ═══════════════════════════════════════════════════════════════════════
# Logging
# ═══════════════════════════════════════════════════════════════════════

def setup_logging(log_dir: str, level: str = "INFO") -> logging.Logger:
    """Configure the ``pmc_pipeline`` logger (console + rotating file).

    Returns the configured :class:`logging.Logger`.
    """
    logger = logging.getLogger("pmc_pipeline")
    if logger.handlers:
        return logger  # already configured

    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    fmt = logging.Formatter(
        "%(asctime)s │ %(levelname)-8s │ %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # Rotating file (10 MB × 5 backups)
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fh = RotatingFileHandler(
        os.path.join(log_dir, f"pipeline_{ts}.log"),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


# ═══════════════════════════════════════════════════════════════════════
# Throughput tracker
# ═══════════════════════════════════════════════════════════════════════

class ThroughputTracker:
    """Thread-safe counters for download throughput statistics."""

    def __init__(self) -> None:
        self.start_time: float = time.time()
        self.completed: int = 0
        self.failed: int = 0
        self.retries: int = 0
        self.excluded: int = 0
        self._lock = asyncio.Lock()

    async def _inc(self, attr: str) -> None:
        async with self._lock:
            setattr(self, attr, getattr(self, attr) + 1)

    # Provide both sync and async versions for flexibility.

    def record_success(self) -> None:
        self.completed += 1

    def record_failure(self) -> None:
        self.failed += 1

    def record_retry(self) -> None:
        self.retries += 1

    def record_excluded(self) -> None:
        self.excluded += 1

    def get_stats(self) -> dict[str, Any]:
        elapsed = max(time.time() - self.start_time, 0.001)
        return {
            "completed": self.completed,
            "failed": self.failed,
            "excluded": self.excluded,
            "retries": self.retries,
            "elapsed_s": round(elapsed, 1),
            "papers_per_min": round(self.completed / (elapsed / 60), 1),
        }

    def log_stats(self, logger: logging.Logger) -> None:
        s = self.get_stats()
        logger.info(
            "Throughput: %d completed | %d failed | %d excluded | "
            "%d retries | %.1f papers/min | %.1fs elapsed",
            s["completed"],
            s["failed"],
            s["excluded"],
            s["retries"],
            s["papers_per_min"],
            s["elapsed_s"],
        )


# ═══════════════════════════════════════════════════════════════════════
# Checkpoint manager
# ═══════════════════════════════════════════════════════════════════════

class CheckpointManager:
    """Persist pipeline state across runs using JSON files.

    Files live in ``checkpoint_dir/``.
    """

    def __init__(self, checkpoint_dir: str) -> None:
        self.dir = Path(checkpoint_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        return self.dir / f"{name}.json"

    def save_state(self, stage: str, data: dict) -> None:
        with open(self._path(stage), "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def load_state(self, stage: str) -> dict | None:
        p = self._path(stage)
        if not p.exists():
            return None
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)

    def save_last_run_date(self, date_str: str) -> None:
        self.save_state("last_run", {"last_successful_run_date": date_str})

    def get_last_run_date(self) -> str | None:
        data = self.load_state("last_run")
        if data:
            return data.get("last_successful_run_date")
        return None

    def clear(self) -> None:
        for f in self.dir.glob("*.json"):
            f.unlink(missing_ok=True)
