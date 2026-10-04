"""Main.

License
-------
* License: MPL-2.0, see LICENSE for more details.
* Copyright: (C) 2020-present @Aluerie.
"""

import asyncio
import logging
import platform

import aiohttp
import uvicorn

from config import env
from core.bot import Dota2Bot
from shared.concepts import db, logs
from web.app import MyFastAPI
from web.router import router

log = logging.getLogger(__name__)


async def start_the_bot() -> None:
    """Start the bot."""
    postgres_url = env.POSTGRES_VPS if platform.system() == "Linux" else env.POSTGRES_HOME
    pool: db.PoolTypedWithAny = await db.create_pool(postgres_url)  # pyright: ignore[reportAssignmentType]

    async with (
        aiohttp.ClientSession() as session,
        pool as pool,
        Dota2Bot(session=session, pool=pool) as bot,
    ):
        web_app = MyFastAPI(bot)
        web_app.include_router(router)
        config = uvicorn.Config(web_app)
        server = uvicorn.Server(config)
        await asyncio.gather(bot.login(), server.serve())


def launch() -> None:
    with logs.setup_logging(
        starting_up_art="xd",
        filename="d2rpw.log",
    ):
        try:
            asyncio.run(start_the_bot())
        except KeyboardInterrupt:
            log.info("Closing the event loop")


if __name__ == "__main__":
    launch()
