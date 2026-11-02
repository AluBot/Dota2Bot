# Copyright (C) 2020-present, Irene Adler ❤ (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
"""Tests for the async cache."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest import mock

import pytest

from shared import cache as cache_module
from shared.cache import ExpiringCache, Strategy, cache

if TYPE_CHECKING:
    from collections.abc import Iterator


class Clock:
    """A clock that only moves when told to."""

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


@contextlib.contextmanager
def patched_clock() -> Iterator[Clock]:
    """Replace the ``time`` module of the cache, so TTL can be tested without sleeping.

    Only the cache's reference is replaced, ``asyncio`` keeps its own clock.
    """
    clock = Clock()
    with mock.patch.object(cache_module, "time", SimpleNamespace(monotonic=clock.monotonic)):
        yield clock


class TestExpiringCache:
    def test_entry_lives_until_ttl_passes(self) -> None:
        with patched_clock() as clock:
            expiring = ExpiringCache(10)
            expiring["key"] = "value"
            clock.now += 9
            assert "key" in expiring
            assert expiring["key"] == "value"
            clock.now += 2
            assert "key" not in expiring

    def test_get_does_not_return_expired_entries(self) -> None:
        with patched_clock() as clock:
            expiring = ExpiringCache(10)
            expiring["key"] = "value"
            assert expiring.get("key") == "value"
            clock.now += 11
            assert expiring.get("key") is None
            assert expiring.get("key", "default") == "default"

    def test_len_iteration_values_and_items_skip_expired_entries(self) -> None:
        with patched_clock() as clock:
            expiring = ExpiringCache(10)
            expiring["old"] = 1
            clock.now += 6
            expiring["new"] = 2
            clock.now += 6  # "old" is 12 seconds old, "new" is 6
            assert len(expiring) == 1
            assert list(expiring) == ["new"]
            assert list(expiring.values()) == [2]
            assert list(expiring.items()) == [("new", 2)]


class Counter:
    """Counts how many times the cached function really ran."""

    def __init__(self) -> None:
        self.calls = 0


class TestCache:
    def test_concurrent_calls_share_one_task(self) -> None:
        counter = Counter()

        @cache()
        async def square(x: int) -> int:
            counter.calls += 1
            await asyncio.sleep(0)
            return x * x

        async def main() -> list[int]:
            return await asyncio.gather(square(3), square(3), square(4))

        assert asyncio.run(main()) == [9, 9, 16]
        assert counter.calls == 2

    def test_result_is_remembered(self) -> None:
        counter = Counter()

        @cache()
        async def square(x: int) -> int:
            counter.calls += 1
            return x * x

        async def main() -> None:
            assert await square(3) == 9
            assert await square(3) == 9

        asyncio.run(main())
        assert counter.calls == 1

    def test_keyword_order_does_not_matter(self) -> None:
        @cache()
        async def add(*, a: int, b: int) -> int:
            return a + b

        async def main() -> None:
            first = add(a=1, b=2)
            assert add(b=2, a=1) is first
            await first

        asyncio.run(main())

    def test_ignore_kwargs(self) -> None:
        @cache(ignore_kwargs=True)
        async def add(a: int, *, extra: int = 0) -> int:
            return a + extra

        async def main() -> None:
            first = add(1, extra=1)
            assert add(1, extra=2) is first
            await first

        asyncio.run(main())

    def test_failed_call_is_not_remembered(self) -> None:
        counter = Counter()

        @cache()
        async def flaky() -> str:
            counter.calls += 1
            if counter.calls == 1:
                msg = "first attempt fails"
                raise ValueError(msg)
            return "ok"

        async def main() -> None:
            with pytest.raises(ValueError, match="first attempt fails"):
                await flaky()
            await asyncio.sleep(0)
            assert await flaky() == "ok"

        asyncio.run(main())
        assert counter.calls == 2

    def test_cancelled_call_is_not_remembered(self) -> None:
        @cache()
        async def slow() -> str:
            await asyncio.sleep(10)
            return "late"

        async def main() -> None:
            first = slow()
            await asyncio.sleep(0)
            first.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await first
            await asyncio.sleep(0)
            second = slow()
            assert second is not first
            second.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await second

        asyncio.run(main())

    def test_failure_is_logged_even_if_nobody_awaits(self) -> None:
        records: list[logging.LogRecord] = []

        class Collector(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record)

        @cache()
        async def broken() -> None:
            msg = "nobody sees me"
            raise RuntimeError(msg)

        async def main() -> None:
            broken()  # fire and forget
            await asyncio.sleep(0.01)

        logger = logging.getLogger("shared.cache")
        handler = Collector(level=logging.WARNING)
        logger.addHandler(handler)
        try:
            asyncio.run(main())
        finally:
            logger.removeHandler(handler)

        assert len(records) == 1
        assert records[0].exc_info is not None
        assert isinstance(records[0].exc_info[1], RuntimeError)

    def test_invalidate(self) -> None:
        counter = Counter()

        @cache()
        async def value(x: int) -> int:
            counter.calls += 1
            return x

        async def main() -> None:
            await value(1)
            assert value.invalidate(1) is True
            assert value.invalidate(1) is False
            await value(1)

        asyncio.run(main())
        assert counter.calls == 2

    def test_invalidate_containing(self) -> None:
        counter = Counter()

        @cache()
        async def value(x: int) -> int:
            counter.calls += 1
            return x

        async def main() -> None:
            await value(1)
            await value(2)
            value.invalidate_containing("1")
            await value(1)
            await value(2)

        asyncio.run(main())
        assert counter.calls == 3

    def test_lru_forgets_the_least_recently_used(self) -> None:
        counter = Counter()

        @cache(maxsize=1)
        async def value(x: int) -> int:
            counter.calls += 1
            return x

        async def main() -> None:
            await value(1)
            await value(2)
            await value(1)

        asyncio.run(main())
        assert counter.calls == 3

    def test_timed_strategy_expires_results(self) -> None:
        counter = Counter()

        @cache(maxsize=10, strategy=Strategy.timed)
        async def value() -> int:
            counter.calls += 1
            return counter.calls

        async def main(clock: Clock) -> list[Any]:
            results = [await value()]
            clock.now += 5
            results.append(await value())
            clock.now += 6
            results.append(await value())
            return results

        with patched_clock() as clock:
            assert asyncio.run(main(clock)) == [1, 1, 2]
