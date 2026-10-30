# Copyright (C) 2020-present, Irene Adler ❤ (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
"""Main."""

import asyncio
import platform

import aiohttp
import uvicorn

from config import env
from core.bot import Dota2Bot
from shared.concepts import db, logs
from web.app import MyFastAPI
from web.router import router

# generated at https://patorjk.com/software/taag/ using "Standard" font
ASCII_STARTING_UP_ART = r"""
  ____   ___ _____  _    ____  ____   ___ _____   ____ _____  _    ____  ____ _____ ___ _   _  ____
 |  _ \ / _ \_   _|/ \  |___ \| __ ) / _ \_   _| / ___|_   _|/ \  |  _ \|  _ \_   _|_ _| \ | |/ ___|
 | | | | | | || | / _ \   __) |  _ \| | | || |   \___ \ | | / _ \ | |_) | |_) || |  | ||  \| | |  _
 | |_| | |_| || |/ ___ \ / __/| |_) | |_| || |    ___) || |/ ___ \|  _ <|  _ < | |  | || |\  | |_| |
 |____/ \___/ |_/_/   \_\_____|____/ \___/ |_|   |____/ |_/_/   \_\_| \_\_| \_\|_| |___|_| \_|\____|
                    [ DOTA2BOT IS STARTING NOW ]
"""


async def start_the_bot() -> None:
    """Start the bot."""
    postgres_url = env.POSTGRES_VPS if platform.system() == "Linux" else env.POSTGRES_HOME
    pool: db.PoolTypedWithAny = await db.create_pool(postgres_url, "public,shared")

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
        starting_up_art=ASCII_STARTING_UP_ART,
        filename="d2b.log",
    ):
        try:
            asyncio.run(start_the_bot())
        except KeyboardInterrupt:
            print("Aborted! The bot was interrupted with `KeyboardInterrupt`!")  # ruff: ignore[print]


if __name__ == "__main__":
    launch()
