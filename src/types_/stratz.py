"""Stratz GraphQL Schemas.

License
-------
* License: MPL-2.0, see LICENSE for more details.
* Copyright: (C) 2020-present @Aluerie.
"""

from typing import TypedDict

__all__ = ("AllItemsQuery",)


class AllItemsQuery(TypedDict):
    """Schema for Stratz GraphQL `get_items` response."""

    data: ItemData


class ItemData(TypedDict):
    constants: ItemConstants


class ItemConstants(TypedDict):
    items: list[Item]


class Item(TypedDict):
    id: int
    displayName: str
