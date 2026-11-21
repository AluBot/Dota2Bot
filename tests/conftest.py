# Copyright (C) 2020-present, Irene Adler ❤ (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
"""Test setup.

The app reads its secrets at import time, so tests give it fake ones before anything gets imported.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Generator

FAKE_ENV = {
    "PROJECT_NAME": "Dota2Bot",
    "POSTGRES_VPS": "postgresql://fake",
    "POSTGRES_HOME": "postgresql://fake",
    "STEAM_IRENESTEST_USERNAME": "fake",
    "STEAM_IRENESTEST_PASSWORD": "fake",
    "STEAM_IRENESBOT_USERNAME": "fake",
    "STEAM_IRENESBOT_PASSWORD": "fake",
    "STEAM_API_KEY": "fake",
    "WEBHOOK_ERROR": "https://fake.invalid/error",
    "WEBHOOK_NOTIFICATION": "https://fake.invalid/notification",
    "WEBHOOK_LOGGER": "https://fake.invalid/logger",
    "LOCAL_API_KEY": "test-local-api-key",
}
# not `setdefault`: a real `.env` or environment must never leak into the tests
os.environ.update(FAKE_ENV)


@pytest.fixture
def caplog(caplog: pytest.LogCaptureFixture) -> Generator[pytest.LogCaptureFixture]:
    """Make pytest's ``caplog`` see loguru records too (loguru doesn't go through the standard ``logging``)."""
    handler_id = logger.add(caplog.handler, format="{message}", level=0)
    yield caplog
    logger.remove(handler_id)
