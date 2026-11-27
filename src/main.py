# Copyright (C) 2020-present, Irene Adler 💜 (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
"""Main."""

import asyncio
from pathlib import Path

import aiohttp
import uvicorn

from config import env
from core.bot import Dota2Bot
from shared import runtime
from shared.concepts import db, logs
from web.app import MyFastAPI
from web.router import router

LOCAL_API_HOST = "127.0.0.1"
LOCAL_API_PORT = 8000

# The bot's database migrations, applied when it starts (`shared.concepts.migrations`).
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

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
    pool = await db.try_create_pool(
        env.DATABASE_URL, search_path="dota2bot", application_name="dota2bot", migrations_dir=MIGRATIONS_DIR
    )

    async with (
        aiohttp.ClientSession() as session,
        pool as pool,
        Dota2Bot(session=session, pool=pool) as bot,
    ):
        web_app = MyFastAPI(bot)
        web_app.include_router(router)
        config = uvicorn.Config(web_app, host=LOCAL_API_HOST, port=LOCAL_API_PORT, log_config=None, access_log=False)
        server = uvicorn.Server(config)
        async with asyncio.TaskGroup() as group:
            group.create_task(bot.login())
            group.create_task(server.serve())
            group.create_task(runtime.watchdog_loop())


def launch() -> None:
    """Set up logging and run the bot together with its local API."""
    with logs.setup_logging(
        starting_up_art=ASCII_STARTING_UP_ART,
        level=env.LOG_LEVEL,
    ):
        try:
            asyncio.run(runtime.run_until_sigterm(start_the_bot()))
        except KeyboardInterrupt:
            print("Aborted! The bot was interrupted with `KeyboardInterrupt`!")  # ruff: ignore[print]


if __name__ == "__main__":
    launch()
