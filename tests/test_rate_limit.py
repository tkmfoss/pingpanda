"""Tests for per-host rate limiting and Retry-After handling."""

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from pingpanda.cancellation import CancellationToken
from pingpanda.dns_cache import DnsCache
from pingpanda.rate_limit import (
    DEFAULT_PER_HOST_CONCURRENT,
    HostRateLimiter,
    RateLimitCancelled,
    host_key,
    parse_retry_after,
)


class _Clock:
    def __init__(self, value=0.0):
        self.value = float(value)

    def __call__(self):
        return self.value

    async def sleep(self, delay):
        self.value += delay
        await asyncio.sleep(0)


def test_host_key_normalizes_hostname_and_port():
    assert host_key("https://Example.COM./a") == ("example.com", 443)
    assert host_key("http://example.com/a") == ("example.com", 80)
    assert host_key("https://example.com:8443/a") == ("example.com", 8443)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("5", 5.0),
        ("0", 0.0),
        ("-1", None),
        ("", None),
        ("not-a-date", None),
        (None, None),
    ],
)
def test_parse_retry_after_seconds_and_invalid_values(value, expected):
    assert parse_retry_after(value) == expected


def test_parse_retry_after_http_date_and_expired_date():
    now = datetime(2026, 11, 28, 12, 0, tzinfo=timezone.utc)
    future = now + timedelta(seconds=30)
    past = now - timedelta(seconds=30)

    assert parse_retry_after(
        future.strftime("%a, %d %b %Y %H:%M:%S GMT"),
        now=now,
    ) == 30.0
    assert parse_retry_after(
        past.strftime("%a, %d %b %Y %H:%M:%S GMT"),
        now=now,
    ) == 0.0


async def test_same_host_never_exceeds_per_host_limit():
    limiter = HostRateLimiter(max_per_host=2)
    active = 0
    max_active = 0
    two_entered = asyncio.Event()
    release = asyncio.Event()

    async def worker():
        nonlocal active, max_active
        async with limiter.limit("https://example.com/api"):
            active += 1
            max_active = max(max_active, active)
            if active == 2:
                two_entered.set()
            await release.wait()
            active -= 1

    tasks = [asyncio.create_task(worker()) for _ in range(6)]
    await asyncio.wait_for(two_entered.wait(), timeout=1)

    assert max_active == 2

    release.set()
    await asyncio.gather(*tasks)
    assert max_active == 2


async def test_different_hosts_do_not_block_each_other():
    limiter = HostRateLimiter(max_per_host=1)
    entered = set()
    both_entered = asyncio.Event()
    release = asyncio.Event()

    async def worker(url):
        async with limiter.limit(url):
            entered.add(host_key(url)[0])
            if len(entered) == 2:
                both_entered.set()
            await release.wait()

    tasks = [
        asyncio.create_task(worker("https://one.test/a")),
        asyncio.create_task(worker("https://two.test/b")),
    ]

    await asyncio.wait_for(both_entered.wait(), timeout=1)
    assert entered == {"one.test", "two.test"}

    release.set()
    await asyncio.gather(*tasks)


async def test_cooldown_delays_only_affected_host():
    clock = _Clock()
    limiter = HostRateLimiter(
        max_per_host=1,
        clock=clock,
        sleep=clock.sleep,
        poll_interval=10,
    )

    limiter.defer("https://slow.test/a", 5)

    async with limiter.limit("https://fast.test/a"):
        pass
    assert clock.value == 0

    async with limiter.limit("https://slow.test/b"):
        pass
    assert clock.value == 5


async def test_longer_existing_cooldown_is_not_shortened():
    clock = _Clock()
    limiter = HostRateLimiter(
        clock=clock,
        sleep=clock.sleep,
        poll_interval=10,
    )

    limiter.defer("https://example.test", 10)
    limiter.defer("https://example.test", 2)

    assert limiter.cooldown_remaining("https://example.test") == 10


async def test_cancellation_interrupts_retry_after_wait():
    clock = _Clock()
    token = CancellationToken()

    async def cancelling_sleep(delay):
        clock.value += delay
        token.cancel()
        await asyncio.sleep(0)

    limiter = HostRateLimiter(
        clock=clock,
        sleep=cancelling_sleep,
        poll_interval=1,
    )
    limiter.defer("https://slow.test", 30)

    with pytest.raises(RateLimitCancelled):
        async with limiter.limit(
            "https://slow.test",
            cancellation_token=token,
        ):
            pass


async def test_check_url_status_honours_numeric_retry_after(checker):
    clock = _Clock()
    limiter = HostRateLimiter(
        clock=clock,
        sleep=clock.sleep,
        poll_interval=10,
    )

    async def dns_resolver(hostname, port):
        return f"203.0.113.10:{port}"

    dns_cache = DnsCache(resolver=dns_resolver)
    calls = 0

    class Session:
        async def get(self, url, **kwargs):
            nonlocal calls
            calls += 1
            request = httpx.Request("GET", url)
            if calls == 1:
                return httpx.Response(
                    429,
                    headers={"Retry-After": "3"},
                    request=request,
                )
            return httpx.Response(200, request=request)

    result = await checker.check_url_status(
        Session(),
        0,
        "https://example.test/api",
        max_retries=1,
        retry_delay=1,
        dns_cache=dns_cache,
        host_limiter=limiter,
    )

    assert result.status == 200
    assert result.attempts == 2
    assert calls == 2
    assert clock.value == 3


async def test_503_retry_after_defers_host_even_without_retry(checker):
    clock = _Clock()
    limiter = HostRateLimiter(
        clock=clock,
        sleep=clock.sleep,
        poll_interval=10,
    )

    async def dns_resolver(hostname, port):
        return f"203.0.113.12:{port}"

    dns_cache = DnsCache(resolver=dns_resolver)

    class Session:
        async def get(self, url, **kwargs):
            request = httpx.Request("GET", url)
            return httpx.Response(
                503,
                headers={"Retry-After": "4"},
                request=request,
            )

    result = await checker.check_url_status(
        Session(),
        0,
        "https://throttled.test/api",
        max_retries=0,
        dns_cache=dns_cache,
        host_limiter=limiter,
    )

    assert result.status == 503
    assert limiter.cooldown_remaining("https://throttled.test/other") == 4

    async with limiter.limit("https://throttled.test/other"):
        pass

    assert clock.value == 4


async def test_invalid_retry_after_falls_back_to_existing_backoff(checker):
    clock = _Clock()
    limiter = HostRateLimiter(
        clock=clock,
        sleep=clock.sleep,
        poll_interval=10,
    )

    async def dns_resolver(hostname, port):
        return f"203.0.113.11:{port}"

    dns_cache = DnsCache(resolver=dns_resolver)
    calls = 0

    class Session:
        async def get(self, url, **kwargs):
            nonlocal calls
            calls += 1
            request = httpx.Request("GET", url)
            if calls == 1:
                return httpx.Response(
                    503,
                    headers={"Retry-After": "invalid"},
                    request=request,
                )
            return httpx.Response(200, request=request)

    result = await checker.check_url_status(
        Session(),
        0,
        "https://example.test/api",
        max_retries=1,
        retry_delay=2,
        dns_cache=dns_cache,
        host_limiter=limiter,
    )

    assert result.status == 200
    assert calls == 2
    assert clock.value == 2


async def test_check_all_enforces_per_host_limit(
    checker, mock_transport, fake_dns
):
    active = 0
    max_active = 0
    two_entered = asyncio.Event()
    release = asyncio.Event()

    async def handler(request):
        nonlocal active, max_active
        if request.url.host == "same.test":
            active += 1
            max_active = max(max_active, active)
            if active == 2:
                two_entered.set()
            await release.wait()
            active -= 1

        return httpx.Response(200, request=request)

    mock_transport(handler)

    urls = [f"https://same.test/{index}" for index in range(6)]
    scan = asyncio.create_task(
        checker.check_all(
            urls,
            max_concurrent=6,
            per_host_concurrent=2,
            show_progress=False,
        )
    )

    await asyncio.wait_for(two_entered.wait(), timeout=1)
    assert max_active == 2

    release.set()
    results = await scan

    assert len(results) == 6
    assert max_active == 2


def test_per_host_cli_default_and_custom_value(checker):
    defaults = checker.parse_arguments([])
    custom = checker.parse_arguments(["--per-host-concurrent", "7"])

    assert defaults.per_host_concurrent == DEFAULT_PER_HOST_CONCURRENT
    assert custom.per_host_concurrent == 7


@pytest.mark.parametrize("value", ["0", "-1"])
def test_invalid_per_host_cli_value_is_rejected(checker, value):
    with pytest.raises(SystemExit):
        checker.parse_arguments(["--per-host-concurrent", value])


@pytest.mark.parametrize("value", [0, -1, "2", 1.5, True])
async def test_check_all_rejects_invalid_per_host_concurrency(checker, value):
    with pytest.raises(ValueError, match="positive integer"):
        await checker.check_all(
            [],
            per_host_concurrent=value,
            show_progress=False,
        )
