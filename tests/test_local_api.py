# Copyright (C) 2020-present, Irene Adler 💜 (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
"""Tests for the local API server: the API key check.

The client (``shared.apis.local.LocalAPI``) is tested in the ``shared`` repo.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from config import env
from web.router import router

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
