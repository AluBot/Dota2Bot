"""OpenDota Schemas.

License
-------
* License: MPL-2.0, see LICENSE for more details.
* Copyright: (C) 2020-present @Aluerie.
"""

from typing import Literal, NotRequired, TypedDict

__all__ = ("Matches",)


class Matches(TypedDict):
    """Typing Dict for response json for the OpenDota's `GET matches` endpoint."""

    players: list[MatchesPlayer]
    match_id: int
    lobby_type: int
    game_mode: int


class MatchesPlayer(TypedDict):
    abandons: Literal[0, 1]
    account_id: NotRequired[int]


type ItemsQuery = dict[str, Item]


class Item(TypedDict):
    hint: list[str]
    id: int
    img: str
    dname: str
    qual: str
    cost: int
    notes: str
    attrib: list[ItemAttrib]
    mc: Literal[False] | int
    cd: float
    lore: str
    components: list[str]
    created: bool
    charges: bool


class ItemAttrib(TypedDict):
    key: str
    header: str
    value: str
    generated: NotRequired[bool]
