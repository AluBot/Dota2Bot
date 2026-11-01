# Copyright (C) 2020-present, Irene Adler ❤ (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
"""Tests for the ``Loop`` subclass."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from shared.concepts import tasks

if TYPE_CHECKING:
    from discord.ext.tasks import Loop


class FakeBot:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def wait_until_ready(self) -> None:
        self.calls.append("ready")


class FakeCog:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.bot = FakeBot(calls)

    @tasks.loop(count=1, wait_for_ready=True)
    async def wait_and_hook(self) -> None:
        self.calls.append("body")

    @wait_and_hook.before_loop
    async def wait_and_hook_before(self) -> None:
        self.calls.append("hook")

    @tasks.loop(count=1, wait_for_ready=True)
    async def wait_only(self) -> None:
        self.calls.append("body")

    @tasks.loop(count=1)
    async def hook_only(self) -> None:
        self.calls.append("body")

    @hook_only.before_loop
    async def hook_only_before(self) -> None:
        self.calls.append("hook")


async def run_to_the_end(calls: list[str], task_name: str) -> None:
    """Start the task of a fresh cog and wait until it is done."""
    task: Loop = getattr(FakeCog(calls), task_name)
    task.start()
    running = task.get_task()
    assert running is not None
    await running


def test_wait_for_ready_runs_before_the_hook() -> None:
    """The bot has to be ready before the hook, this combination used to silently drop ``wait_for_ready``."""
    calls: list[str] = []
    asyncio.run(run_to_the_end(calls, "wait_and_hook"))
    assert calls == ["ready", "hook", "body"]


def test_wait_for_ready_without_a_hook() -> None:
    calls: list[str] = []
    asyncio.run(run_to_the_end(calls, "wait_only"))
    assert calls == ["ready", "body"]


def test_hook_without_wait_for_ready() -> None:
    calls: list[str] = []
    asyncio.run(run_to_the_end(calls, "hook_only"))
    assert calls == ["hook", "body"]
