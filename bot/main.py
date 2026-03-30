import asyncio

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
from bot.logger import configure_process_logging


handler = configure_process_logging(PROJECT_ROOT)

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

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='/', intents=intents, log_handler=handler, log_level=logging.INFO)

cogs_list: list[str] = [
    "cogs.bot_repl",
    "cogs.bot_db",
]

TESTING = True  # Set to False to disable test guild command registration and related logging
SHUTDOWN_REQUESTED = False


async def main():
    global SHUTDOWN_REQUESTED

    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN is missing")

    SHUTDOWN_REQUESTED = False

    for cog in cogs_list:
        print(f"Loading cog: {cog}")
        bot.load_extension(cog)
        print(f"Loaded cog: {cog}")

    print("Connecting to Discord...")
    try:
        await bot.start(TOKEN, reconnect=False)
    except RuntimeError as exc:
        # py-cord may raise this during normal /shutdown teardown while
        # the websocket connect task is still unwinding.
        if str(exc) == "Session is closed" and SHUTDOWN_REQUESTED:
            return
        raise


@bot.event
async def on_ready():
    guild_names = ", ".join(f"{g.name} ({g.id})" for g in bot.guilds) or "(none)"
    print(f"{bot.user} has connected to Discord")
    print(f"  Guilds : {guild_names}")
    print(f"  Latency: {bot.latency * 1000:.1f} ms")
    if not TESTING:
        print("Syncing commands globally...")
        await bot.sync_commands()
        print("Commands synced globally.")
    else:
        guild_ids_str = ", ".join(str(g) for g in TEST_GUILD_IDS) if TEST_GUILD_IDS else "(all guilds)"
        print(f"Syncing commands to test guilds: {guild_ids_str}")
        await bot.sync_commands(guild_ids=TEST_GUILD_IDS)
        print("Commands synced.")

@bot.slash_command(
    name='shutdown',
    description='Shut down the bot (owner only)',
)
#@commands.is_owner()
async def shutdown(ctx: discord.ApplicationContext):
    global SHUTDOWN_REQUESTED

    if not await bot.is_owner(ctx.author):
        await ctx.respond('Only the bot owner may use this command.', ephemeral=True)
        return

    print(f"Shutdown requested by {ctx.author} ({ctx.author.id})")
    await ctx.respond('Shutting down...')
    SHUTDOWN_REQUESTED = True
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
