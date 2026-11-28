"""Per-host request limiting and Retry-After coordination."""

from __future__ import annotations

import asyncio
import math
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

from pingpanda.cancellation import CancellationToken

DEFAULT_PER_HOST_CONCURRENT = 2
RETRY_AFTER_STATUS_CODES = frozenset({429, 503})
_CANCEL_POLL_SECONDS = 0.1


class RateLimitCancelled(Exception):
    """Raised when cooperative cancellation interrupts limiter waiting."""


def host_key(url: str) -> tuple[str, int]:
    """Return a normalized hostname/port key for a URL."""
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL does not contain a hostname")

    hostname = hostname.rstrip(".").lower()
    try:
        explicit_port = parsed.port
    except ValueError:
        explicit_port = 0

    if explicit_port is not None:
        port = explicit_port
    elif parsed.scheme == "http":
        port = 80
    else:
        port = 443

    return hostname, port


def parse_retry_after(
    value: str | None,
    *,
    now: datetime | None = None,
) -> float | None:
    """Parse Retry-After seconds or HTTP-date into a non-negative delay."""
    if value is None:
        return None

    value = value.strip()
    if not value:
        return None

    try:
        seconds = int(value, 10)
    except ValueError:
        seconds = None

    if seconds is not None:
        return float(seconds) if seconds >= 0 else None

    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None

    if retry_at is None:
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    delay = (retry_at - current).total_seconds()
    return max(0.0, delay)


class HostRateLimiter:
    """Scan-scoped per-host concurrency limiter with cooldown tracking."""

    def __init__(
        self,
        max_per_host: int = DEFAULT_PER_HOST_CONCURRENT,
        *,
        clock=time.monotonic,
        sleep=asyncio.sleep,
        poll_interval: float = _CANCEL_POLL_SECONDS,
    ):
        if (
            isinstance(max_per_host, bool)
            or not isinstance(max_per_host, int)
            or max_per_host <= 0
        ):
            raise ValueError("per-host concurrency must be a positive integer")
        if (
            isinstance(poll_interval, bool)
            or not isinstance(poll_interval, (int, float))
            or not math.isfinite(poll_interval)
            or poll_interval <= 0
        ):
            raise ValueError("rate-limit poll interval must be positive")

        self.max_per_host = max_per_host
        self._clock = clock
        self._sleep = sleep
        self._poll_interval = float(poll_interval)
        self._semaphores: dict[tuple[str, int], asyncio.Semaphore] = {}
        self._cooldowns: dict[tuple[str, int], float] = {}

    def _semaphore_for(self, key: tuple[str, int]) -> asyncio.Semaphore:
        semaphore = self._semaphores.get(key)
        if semaphore is None:
            semaphore = asyncio.Semaphore(self.max_per_host)
            self._semaphores[key] = semaphore
        return semaphore

    def defer(self, url: str, delay: float) -> None:
        """Delay future requests to one host without blocking other hosts."""
        if (
            isinstance(delay, bool)
            or not isinstance(delay, (int, float))
            or not math.isfinite(delay)
            or delay < 0
        ):
            raise ValueError("rate-limit delay must be a finite, non-negative number")

        key = host_key(url)
        deadline = self._clock() + float(delay)
        self._cooldowns[key] = max(self._cooldowns.get(key, 0.0), deadline)

    def cooldown_remaining(self, url: str) -> float:
        """Return the remaining cooldown for one host in seconds."""
        key = host_key(url)
        deadline = self._cooldowns.get(key, 0.0)
        remaining = deadline - self._clock()
        if remaining <= 0:
            self._cooldowns.pop(key, None)
            return 0.0
        return remaining

    async def sleep_cancellable(
        self,
        delay: float,
        cancellation_token: CancellationToken | None = None,
    ) -> None:
        """Sleep without making cooperative Ctrl+C wait for the full delay."""
        if delay <= 0:
            if cancellation_token is not None and cancellation_token.cancelled:
                raise RateLimitCancelled()
            return

        deadline = self._clock() + delay
        while True:
            if cancellation_token is not None and cancellation_token.cancelled:
                raise RateLimitCancelled()

            remaining = deadline - self._clock()
            if remaining <= 0:
                return

            await self._sleep(min(self._poll_interval, remaining))

    async def _wait_for_cooldown(
        self,
        key: tuple[str, int],
        cancellation_token: CancellationToken | None,
    ) -> None:
        while True:
            if cancellation_token is not None and cancellation_token.cancelled:
                raise RateLimitCancelled()

            deadline = self._cooldowns.get(key, 0.0)
            remaining = deadline - self._clock()
            if remaining <= 0:
                self._cooldowns.pop(key, None)
                return

            await self._sleep(min(self._poll_interval, remaining))

    async def _acquire(
        self,
        semaphore: asyncio.Semaphore,
        cancellation_token: CancellationToken | None,
    ) -> None:
        if cancellation_token is None:
            await semaphore.acquire()
            return

        acquire_task = asyncio.create_task(semaphore.acquire())
        try:
            while True:
                if cancellation_token.cancelled:
                    raise RateLimitCancelled()

                try:
                    await asyncio.wait_for(
                        asyncio.shield(acquire_task),
                        timeout=self._poll_interval,
                    )
                    return
                except asyncio.TimeoutError:
                    continue
        except BaseException:
            if not acquire_task.done():
                acquire_task.cancel()
                await asyncio.gather(acquire_task, return_exceptions=True)
            elif acquire_task.cancelled() is False and acquire_task.exception() is None:
                # The semaphore may have been acquired just as cancellation won
                # the race. Return that permit before propagating the exception.
                semaphore.release()
            raise

    @asynccontextmanager
    async def limit(
        self,
        url: str,
        cancellation_token: CancellationToken | None = None,
    ):
        """Wait for host cooldown and concurrency capacity, then yield."""
        key = host_key(url)
        semaphore = self._semaphore_for(key)

        # Waiting before acquisition prevents a throttled host from occupying
        # all its concurrency permits during a Retry-After cooldown.
        await self._wait_for_cooldown(key, cancellation_token)
        await self._acquire(semaphore, cancellation_token)

        try:
            # A cooldown may have been introduced while this request waited for
            # a permit, so re-check immediately before allowing network I/O.
            await self._wait_for_cooldown(key, cancellation_token)
            yield
        finally:
            semaphore.release()
