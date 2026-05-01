import asyncio
import inspect
import signal

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
SHUTDOWN_TASK: asyncio.Task[None] | None = None
HELP_TEXT = "\n".join(
    [
        "Available commands:",
        "/help - Show this message.",
        "/repl open - Open a Python REPL session.",
        "/repl instructions - Show REPL coding instructions.",
        "/repl close - Close your current REPL session.",
        "/repl vars - List variables in your active or saved REPL session.",
        "/repl perms - Show your effective REPL permission level.",
        "/repl session_imports - Show the imports currently enabled for your REPL session.",
        "/repl possible_imports - Show the imports allowed by the REPL policy at your permission level.",
        "/repl list_perms - List stored REPL permissions for this guild or DM.",
        "/repl set_perms - Set REPL permissions for a guild role (bot owner only).",
        "/shutdown - Shut down the bot (owner only).",
    ]
)


async def _run_shutdown_hooks() -> None:
    for cog_name, cog in bot.cogs.items():
        hook = getattr(cog, "graceful_shutdown", None)
        if hook is None:
            continue

        print(f"Running graceful shutdown hook for cog: {cog_name}")
        try:
            result = hook()
            if inspect.isawaitable(result):
                await result
        except Exception:
            logging.exception("Shutdown hook failed for cog=%s", cog_name)


async def request_shutdown(reason: str) -> None:
    global SHUTDOWN_REQUESTED

    if SHUTDOWN_REQUESTED:
        return

    SHUTDOWN_REQUESTED = True
    print(f"Graceful shutdown requested: {reason}")

    try:
        await _run_shutdown_hooks()
    finally:
        if not bot.is_closed():
            await bot.close()


def schedule_shutdown(reason: str) -> asyncio.Task[None]:
    global SHUTDOWN_TASK

    if SHUTDOWN_TASK is None or SHUTDOWN_TASK.done():
        SHUTDOWN_TASK = asyncio.create_task(request_shutdown(reason))
    return SHUTDOWN_TASK


async def main():
    global SHUTDOWN_REQUESTED

    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN is missing")

    SHUTDOWN_REQUESTED = False

    loop = asyncio.get_running_loop()
    for shutdown_signal in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(
                shutdown_signal,
                lambda sig=shutdown_signal: schedule_shutdown(f"received {sig.name}"),
            )
        except NotImplementedError:
            pass

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
    name='help',
    description='Show the available bot commands',
)
async def help_command(ctx: discord.ApplicationContext):
    """Show the currently available bot commands."""
    await ctx.respond(HELP_TEXT, ephemeral=True)

@bot.slash_command(
    name='shutdown',
    description='Shut down the bot (owner only)',
    default_member_permissions=discord.Permissions(administrator=True),
)
#@commands.is_owner()
async def shutdown(ctx: discord.ApplicationContext):
    if not await bot.is_owner(ctx.author):
        await ctx.respond('Only the bot owner may use this command.', ephemeral=True)
        return

    print(f"Shutdown requested by {ctx.author} ({ctx.author.id})")
    await ctx.respond('Shutting down...')
    await schedule_shutdown(f"slash command by {ctx.author} ({ctx.author.id})")


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
