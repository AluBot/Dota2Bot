import asyncio

import aiohttp

from shared.local_api import client


async def main():
    async with aiohttp.ClientSession() as session:
        x = client.LocalAPI(session)
        print(await x.get_previous_match(198649370))


if __name__ == "__main__":
    asyncio.run(main())
