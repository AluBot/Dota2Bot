import asyncio

import aiohttp

from shared.apis import local


async def main():
    async with aiohttp.ClientSession() as session:
        x = local.LocalAPI(session)
        print(await x.get_previous_match(198649370))


if __name__ == "__main__":
    asyncio.run(main())
