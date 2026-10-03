"""Dota 2 API Clients.

License
-------
* License: MPL-2.0, see LICENSE for more details.
* Copyright: (C) 2020-present @Aluerie.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, TypedDict

import orjson

if TYPE_CHECKING:
    import aiohttp

    from types_ import opendota as schemas

    class GraphQLData(TypedDict):
        data: Any


__all__ = ("OpenDotaClient",)
log = logging.getLogger(__name__)


class OpenDotaClient:
    """A class for interacting with OpenDota API."""

    def __init__(self, *, session: aiohttp.ClientSession) -> None:
        self.session = session

    async def _invoke(self, endpoint: str) -> Any:
        """Invoke a request to OpenDota API."""
        url = f"https://api.opendota.com/api/{endpoint}"
        async with self.session.get(url=url) as resp:
            return await resp.json(loads=orjson.loads)

    async def matches(self, match_id: int) -> schemas.Matches:
        """Get match from opendota API via GET matches endpoint."""
        return await self._invoke(f"matches/{match_id}")

    async def get_items(self) -> schemas.ItemsQuery:
        """Get Opendota constants items.

        Links
        -----
        * https://api.opendota.com/api/constants/items
        * https://raw.githubusercontent.com/odota/dotaconstants/master/build/items.json
        """
        log.debug("🍋 Opendota Constants API: getting items.")
        return await self._invoke("constants/items")
