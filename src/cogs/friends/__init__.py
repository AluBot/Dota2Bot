from typing import TYPE_CHECKING

from .cog import FriendsCog

if TYPE_CHECKING:
    from core import Dota2Bot


async def setup(bot: Dota2Bot) -> None:
    """Load Dota2Bot's extension. Framework of steamio."""
    await bot.add_cog(FriendsCog())
