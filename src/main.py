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

LOCAL_API_HOST = "127.0.0.1"
LOCAL_API_PORT = 8000

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
    pool: db.PoolTypedWithAny = await db.create_pool(postgres_url, "shared")

    async with (
        aiohttp.ClientSession() as session,
        pool as pool,
        Dota2Bot(session=session, pool=pool) as bot,
    ):
        web_app = MyFastAPI(bot)
        web_app.include_router(router)
        # host and port have to match `LocalAPI.BASE_URL` in `shared.apis.local`
        config = uvicorn.Config(web_app, host=LOCAL_API_HOST, port=LOCAL_API_PORT)
        server = uvicorn.Server(config)
        # unlike `asyncio.gather`, a task group cancels the other service if one of them dies
        async with asyncio.TaskGroup() as group:
            group.create_task(bot.login())
            group.create_task(server.serve())


def launch() -> None:
    """Set up logging and run the bot together with its local API."""
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
