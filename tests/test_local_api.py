# Copyright (C) 2020-present, Irene Adler ❤ (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
"""Tests for the local API: the server's key check and the client that talks to it."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from fastapi import FastAPI
from fastapi.testclient import TestClient

from config import env
from shared import errors
from shared.apis.local import LocalAPI
from web.router import router

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

KEY_HEADER = "X-API-Key"


class GCNotReadyBot:
    """Stand-in for the bot: the routes that need the Game Coordinator answer 503 for it."""

    def is_gc_ready(self) -> bool:
        return False


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.bot = GCNotReadyBot()  # ty: ignore[unresolved-attribute]
    return TestClient(app)


class TestServerSide:
    def test_missing_key(self, client: TestClient) -> None:
        assert client.get("/profile_card/1").status_code == 401

    def test_wrong_key(self, client: TestClient) -> None:
        assert client.get("/profile_card/1", headers={KEY_HEADER: "nope"}).status_code == 401

    def test_right_key_gets_through(self, client: TestClient) -> None:
        # 503 comes from the route itself, so the key check let the request through
        response = client.get("/profile_card/1", headers={KEY_HEADER: env.LOCAL_API_KEY})
        assert response.status_code == 503

    def test_accept_invite_is_post_only(self, client: TestClient) -> None:
        headers = {KEY_HEADER: env.LOCAL_API_KEY}
        params = {"friend_id": 1, "twitch_id": "2"}
        assert client.get("/accept_invite/", params=params, headers=headers).status_code == 405

    def test_accept_invite_needs_the_key(self, client: TestClient) -> None:
        assert client.post("/accept_invite/", params={"friend_id": 1, "twitch_id": "2"}).status_code == 401


async def with_server(
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    check: Callable[[LocalAPI], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        monkeypatch.setattr(LocalAPI, "BASE_URL", str(server.make_url("")).rstrip("/"))
        await check(LocalAPI(session, api_key="client-key"))


class TestClientSide:
    def test_accept_invite_is_a_post_with_the_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict[str, Any] = {}

        async def handler(request: web.Request) -> web.StreamResponse:  # ruff: ignore[unused-async]
            seen["method"] = request.method
            seen["path"] = request.path
            seen["key"] = request.headers.get(KEY_HEADER)
            seen["query"] = dict(request.query)
            return web.json_response({"id": 1, "name": "Streamer"})

        async def check(api: LocalAPI) -> None:
            user = await api.post_accept_invite(friend_id=1, twitch_id="42")
            assert user.id == 1
            assert user.name == "Streamer"

        asyncio.run(with_server(handler, check, monkeypatch))
        assert seen == {
            "method": "POST",
            "path": "/accept_invite/",
            "key": "client-key",
            "query": {"friend_id": "1", "twitch_id": "42"},
        }

    def test_get_requests_send_the_key_too(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict[str, Any] = {}

        async def handler(request: web.Request) -> web.StreamResponse:  # ruff: ignore[unused-async]
            seen["method"] = request.method
            seen["key"] = request.headers.get(KEY_HEADER)
            return web.json_response({"id": 1, "name": "Streamer"})

        async def check(api: LocalAPI) -> None:
            await api.get_steam_user(1)

        asyncio.run(with_server(handler, check, monkeypatch))
        assert seen == {"method": "GET", "key": "client-key"}

    def test_rejected_key_has_its_own_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def handler(_: web.Request) -> web.StreamResponse:  # ruff: ignore[unused-async]
            return web.json_response({"detail": "Invalid or missing API key"}, status=401)

        async def check(api: LocalAPI) -> None:
            with pytest.raises(errors.RespondWithError, match="API key"):
                await api.get_steam_user(1)

        asyncio.run(with_server(handler, check, monkeypatch))

    def test_detail_of_other_errors_is_passed_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def handler(_: web.Request) -> web.StreamResponse:  # ruff: ignore[unused-async]
            return web.json_response({"detail": "Steam user not found"}, status=404)

        async def check(api: LocalAPI) -> None:
            with pytest.raises(errors.RespondWithError, match="Steam user not found"):
                await api.get_steam_user(1)

        asyncio.run(with_server(handler, check, monkeypatch))
