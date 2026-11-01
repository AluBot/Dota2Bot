# Copyright (C) 2020-present, Irene Adler ❤ (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
"""Tests for the Dota2com client."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, override
from unittest.mock import MagicMock

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from shared import errors
from shared.apis import dota2com
from shared.apis.dota2com import Dota2comClient

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class ScriptedClient(Dota2comClient):
    """Client whose single requests follow a script instead of the network."""

    SLEEP_BETWEEN_ATTEMPTS = 0.0

    def __init__(self, *outcomes: object) -> None:
        super().__init__(session=MagicMock())
        self.outcomes = list(outcomes)
        self.attempts = 0

    @override
    async def _invoke(self, endpoint: str, **query_kwargs: Any) -> Any:
        self.attempts += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class TestRetries:
    def test_retries_until_success(self) -> None:
        client = ScriptedClient(
            errors.ResponseNotOkError("url"),
            errors.EmptyResponseError("url"),
            TimeoutError(),
            {"heroes": []},
        )
        assert asyncio.run(client._get_json("datafeed/herolist")) == {"heroes": []}
        assert client.attempts == 4

    def test_gives_up(self) -> None:
        client = ScriptedClient(*[TimeoutError() for _ in range(Dota2comClient.MAX_RETRY_ATTEMPTS)])
        with pytest.raises(errors.TooManyFailsError) as exc_info:
            asyncio.run(client._get_json("datafeed/herolist"))
        assert isinstance(exc_info.value.__cause__, TimeoutError)
        assert client.attempts == Dota2comClient.MAX_RETRY_ATTEMPTS

    def test_does_not_retry_bugs(self) -> None:
        client = ScriptedClient(KeyError("oops"), {"heroes": []})
        with pytest.raises(KeyError):
            asyncio.run(client._get_json("datafeed/herolist"))
        assert client.attempts == 1


async def with_server(
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    check: Callable[[Dota2comClient, str], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = web.Application()
    app.router.add_get("/{tail:.*}", handler)
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        base_url = str(server.make_url("")).rstrip("/")
        monkeypatch.setattr(dota2com, "BASE_URL", base_url)
        await check(Dota2comClient(session=session), base_url)


class TestInvoke:
    def test_unwraps_result_data(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def handler(_: web.Request) -> web.StreamResponse:  # ruff: ignore[unused-async]
            return web.json_response({"result": {"data": {"heroes": [1]}}})

        async def check(client: Dota2comClient, _: str) -> None:
            assert await client._invoke("datafeed/herolist") == {"heroes": [1]}

        asyncio.run(with_server(handler, check, monkeypatch))

    def test_empty_result_data(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def handler(_: web.Request) -> web.StreamResponse:  # ruff: ignore[unused-async]
            return web.json_response({"result": {}})

        async def check(client: Dota2comClient, _: str) -> None:
            with pytest.raises(errors.EmptyResponseError):
                await client._invoke("datafeed/herolist")

        asyncio.run(with_server(handler, check, monkeypatch))

    def test_not_ok_status(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def handler(_: web.Request) -> web.StreamResponse:  # ruff: ignore[unused-async]
            return web.json_response({}, status=500)

        async def check(client: Dota2comClient, _: str) -> None:
            with pytest.raises(errors.ResponseNotOkError):
                await client._invoke("datafeed/herolist")

        asyncio.run(with_server(handler, check, monkeypatch))
