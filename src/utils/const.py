# Copyright (C) 2020-present, Irene Adler ❤ (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
"""Const."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, override

from steam.enums import Enum as SteampyEnum

__all__ = ("SteampyStrEnum",)


class SteampyStrEnum(SteampyEnum, str):
    """An enumeration where all the values are strings, emulates `enum.StrEnum`.

    Note, that Enum class is from `steam` package, not from the `enum` stdlib.
    """

    __slots__ = ()

    if TYPE_CHECKING:

        @override
        def __new__(cls, value: str) -> Self: ...  # ty: ignore[invalid-method-override]

        @override
        @classmethod
        def try_value(cls, value: str) -> Self: ...


IRENE_STEAM_ID32 = 112636165
