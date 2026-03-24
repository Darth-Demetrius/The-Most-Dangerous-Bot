import asyncio

# Ensure a default event loop exists on the main thread for libraries
# that call asyncio.get_event_loop()/get_running_loop() at import time.
try:
    asyncio.get_running_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

import discord
from discord.ext import commands
import sys
from pathlib import Path

# Ensure the project root is on sys.path so sibling packages (like `cogs`)
# can be imported regardless of how this module is executed.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from dotenv import load_dotenv
import logging
import os

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN') or ""

# Parse TEST_GUILD_IDS env var into a list[int] or None.
_g_env = os.getenv("TEST_GUILD_IDS", "").strip()
if _g_env:
    try:
        TEST_GUILD_IDS = [int(x.strip()) for x in _g_env.split(",") if x.strip()]
        if not TEST_GUILD_IDS:
            TEST_GUILD_IDS = None
    except Exception:
        TEST_GUILD_IDS = None
else:
    TEST_GUILD_IDS = None

handler = logging.FileHandler(filename='discord.log', encoding='utf-8', mode='w')
handler.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s'))

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='/', intents=intents)

cogs_list: list[str] = [
    "cogs.bot_repl",
    "cogs.bot_db",
]

TESTING = True  # Set to False to disable test guild command registration and related logging


async def main():
    for cog in cogs_list:
        bot.load_extension(cog)

    await bot.start(TOKEN)


@bot.event
async def on_ready():
    if not TESTING:
        await bot.sync_commands()
    else:
        await bot.sync_commands(guild_ids=TEST_GUILD_IDS)
    print(f'{bot.user} has connected to Discord')

@bot.slash_command(
    name='shutdown',
    description='Shut down the bot (owner only)',
)
#@commands.is_owner()
async def shutdown(ctx: discord.ApplicationContext):
    owner_user = ctx.author
    if isinstance(owner_user, discord.Member):
        owner_user = await bot.fetch_user(owner_user.id)

    if not await bot.is_owner(owner_user):
        await ctx.respond('Only the bot owner may use this command.', ephemeral=True)
        return

    await ctx.respond('Shutting down...')
    await bot.close()


def get_id(obj, id_type: str = "") -> str:
    match obj:
        case discord.role.Role:
            id_type = "role"
            obj = str(obj.id)
        case discord.channel.TextChannel:
            id_type = "channel"
            obj = str(obj.id)
        case discord.guild.Guild:
            id_type = "guild"
            obj = str(obj.id)
        case commands.context.Context:
            id_type = "guild"
            obj = str(obj.guild.id)
        case _:
            obj = str(obj)

    if id_type == "role":
        return f"<@&{obj}>"
    elif id_type == "channel":
        return f"<#{obj}>"
    return obj


if __name__ == "__main__":
    asyncio.run(main())
