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
    from aiohttp.client import ClientSession

    from types_ import stratz as schemas

    class GraphQLData(TypedDict):
        data: Any


__all__ = ("StratzClient",)
log = logging.getLogger(__name__)


class StratzClient:
    """A class for interacting with Stratz GraphQL API."""

    def __init__(self, *, bearer_token: str, session: aiohttp.ClientSession) -> None:
        self.session: ClientSession = session
        self.bearer_token: str = bearer_token

    async def _invoke(self, query: str) -> Any:
        """Invoke a request to Stratz GraphQL API."""
        async with self.session.post(
            url="https://api.stratz.com/graphql",
            json={"query": query},
            headers={
                "User-Agent": "STRATZ_API",
                "Authorization": f"Bearer {self.bearer_token}",
                "Content-Type": "application/json",
            },
        ) as resp:
            graphql_json: GraphQLData = await resp.json(loads=orjson.loads)
            try:
                return graphql_json["data"]
            except KeyError:
                msg = "Stratz GraphQL API Error:"
                raise errors.APIDataError(msg, graphql_json) from None

    async def get_items(self) -> list[schemas.Item]:
        """Get Constants for Dota 2 Items."""
        log.debug("🍋 Stratz GraphQL API: getting items.")
        query = """
        query AllItemsQuery {
            constants {
                items {
                    id
                    displayName
                }
            }
        }
        """
        data: schemas.ItemData = await self._invoke(query)
        return data["constants"]["items"]
