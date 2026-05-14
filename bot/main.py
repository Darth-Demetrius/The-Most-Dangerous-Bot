import asyncio
import json
import inspect
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import aiohttp
import discord
from discord.ext import commands
from dotenv import load_dotenv

# Ensure the project root is on sys.path so sibling packages (like `cogs`)
# can be imported regardless of how this module is executed.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bot.logger import configure_process_logging
from defines.link_text import user_scope_text


handler = configure_process_logging(PROJECT_ROOT)

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN') or ""

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='/', intents=intents, log_handler=handler, log_level=logging.INFO)

cogs_list: list[str] = [
    "cogs.bot_repl",
    "cogs.bot_db",
]

TESTING = True  # Set to False to disable test guild command registration and related logging


@dataclass
class RuntimeState:
    """Mutable runtime state for graceful shutdown orchestration."""

    shutdown_requested: bool = False
    shutdown_task: asyncio.Task[None] | None = None


STATE = RuntimeState()
RESTART_NOTICE_PATH = PROJECT_ROOT / "logs" / "restart_notice.tmp"
RESTART_NOTICE_MAX_AGE_SECONDS = 300


@dataclass(frozen=True)
class RestartNotification:
    """Saved metadata for a restart acknowledgement message."""

    message_id: int
    user_id: int
    guild_id: int | None
    application_id: int
    interaction_token: str


def _parse_test_guild_ids() -> list[int] | None:
    """Parse TEST_GUILD_IDS from env into a list[int] or None."""
    guild_ids_env = os.getenv("TEST_GUILD_IDS", "").strip()
    if not guild_ids_env:
        return None

    try:
        guild_ids = [int(item.strip()) for item in guild_ids_env.split(",") if item.strip()]
        return guild_ids or None
    except ValueError:
        return None


TEST_GUILD_IDS = _parse_test_guild_ids()


CORE_HELP_TEXT = "\n".join(
    [
        "Available commands:",
        "/help - Show this message.",
        "/status - Show bot status and loaded cogs.",
        "/shutdown - Shut down the bot (owner only).",
        "/restart - Restart the bot (owner only).",
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
            logging.exception("Shutdown hook failed for cog='%s'", cog_name)


async def request_shutdown(reason: str) -> None:
    if STATE.shutdown_requested:
        return

    STATE.shutdown_requested = True
    print(f"Graceful shutdown requested: {reason}")

    try:
        await _run_shutdown_hooks()
    finally:
        if not bot.is_closed():
            await bot.close()


def schedule_shutdown(reason: str) -> asyncio.Task[None]:
    if STATE.shutdown_task is None or STATE.shutdown_task.done():
        STATE.shutdown_task = asyncio.create_task(request_shutdown(reason))
    return STATE.shutdown_task


async def main():
    """Load cogs and start the Discord bot event loop."""
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN is missing")

    STATE.shutdown_requested = False

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
        print(f"Loaded cog: {cog} successfully")

    print("Connecting to Discord...")
    try:
        await bot.start(TOKEN, reconnect=True)
    except RuntimeError as exc:
        # py-cord may raise this during normal /shutdown teardown while
        # the websocket connect task is still unwinding.
        if str(exc) == "Session is closed" and STATE.shutdown_requested:
            return
        raise


@bot.event
async def on_ready():
    guild_names = ", ".join(f"{g.name} ({g.id})" for g in bot.guilds) or "(none)"
    print(f"{bot.user} has connected to Discord")
    print(f"  Guilds : {guild_names}")
    print(f"  Latency: {bot.latency * 1000:.1f} ms")

    await _deliver_restart_notice()

    if not TESTING:
        print("Syncing commands globally...")
        await bot.sync_commands()
        print("Commands synced globally.")
    else:
        guild_ids_str = ", ".join(str(g) for g in TEST_GUILD_IDS) if TEST_GUILD_IDS else "(all guilds)"
        print(f"Syncing commands to test guilds: {guild_ids_str}")
        await bot.sync_commands(guild_ids=TEST_GUILD_IDS)
        print("Commands synced.")


def _build_help_text() -> str:
    """Build help text from the core commands and cog-exported help blocks."""
    sections = [CORE_HELP_TEXT]
    for cog in bot.cogs.values():
        help_text = getattr(cog, "HELP_TEXT", "")
        if help_text:
            sections.append(help_text)
    return "\n".join(sections)


@bot.slash_command(
    name='help',
    description='Show the available bot commands',
)
async def show_help(ctx: discord.ApplicationContext):
    """Show the currently available bot commands."""
    await ctx.respond(_build_help_text(), ephemeral=True)


@bot.slash_command(
    name='status',
    description='Show bot status and loaded cogs',
)
async def show_status(ctx: discord.ApplicationContext):
    """Show the current runtime status of the bot."""
    loaded_cogs = ", ".join(sorted(bot.cogs)) or "(none)"
    await ctx.respond(
        "\n".join(
            [
                f"Guilds: {len(bot.guilds)}",
                f"Loaded cogs: {loaded_cogs}",
                f"Latency: {bot.latency * 1000:.1f} ms",
                f"Testing mode: {TESTING}",
            ]
        ),
        ephemeral=True,
    )

@bot.slash_command(
    name='shutdown',
    description='Shut down the bot (owner only)',
)
@discord.default_permissions(administrator=True)
@commands.is_owner()
async def stop_bot(ctx: discord.ApplicationContext):
    """Shut down the bot via systemd."""
    await _run_owner_systemctl(ctx, action="stop", action_label="Shutting down")


@bot.slash_command(
    name='restart',
    description='Restart the bot (owner only)',
)
@discord.default_permissions(administrator=True)
@commands.is_owner()
async def restart_bot(ctx: discord.ApplicationContext):
    """Restart the bot via systemd."""
    await _run_owner_systemctl(ctx, action="restart", action_label="Restarting")


async def _run_owner_systemctl(
    ctx: discord.ApplicationContext,
    *,
    action: str,
    action_label: str,
) -> None:
    """Run a systemctl action for an owner-only command."""

    actor_scope = user_scope_text(ctx.author, ctx.guild)
    print(f"{action.title()} requested by {actor_scope}")
    await ctx.respond(f"{action_label}...")
    if action == "restart":
        try:
            restart_message = await ctx.interaction.original_response()

            _save_restart_notice(
                message_id=restart_message.id,
                user_id=ctx.author.id,
                guild_id=ctx.guild_id,
                application_id=ctx.interaction.application_id,
                interaction_token=ctx.interaction.token,
            )
            restart_scope = user_scope_text(ctx.author, ctx.guild)
            print(
                "Saved restart notice for "
                f"{restart_scope} "
                f"application={ctx.interaction.application_id} "
                f"message={restart_message.id}"
            )
        except Exception:
            error_scope = user_scope_text(ctx.author, ctx.guild)
            logging.exception(
                "Failed to save restart notification for %s",
                error_scope,
            )
    proc = await asyncio.create_subprocess_exec(
        'systemctl', '--user', action, 'the-most-dangerous-bot.service'
    )
    await proc.wait()

    if proc.returncode == 0:
        return

    raise RuntimeError(f"systemctl {action} failed with exit code {proc.returncode}")


async def _deliver_restart_notice() -> None:
    """Edit the saved restart response once the new process is ready."""
    notification = _load_restart_notice()
    if notification is None:
        return

    user = bot.get_user(notification.user_id) or notification.user_id
    guild = bot.get_guild(notification.guild_id) if notification.guild_id is not None else None
    notice_scope = user_scope_text(user, guild if guild is not None else notification.guild_id)

    try:
        async with aiohttp.ClientSession() as session:
            webhook = discord.Webhook.partial(
                notification.application_id,
                notification.interaction_token,
                session=session,
                bot_token=TOKEN,
            )
            await webhook.edit_message(notification.message_id, content="Restarted")
        print(
            "Delivered restart notice for "
            f"{notice_scope} "
            f"application={notification.application_id} "
            f"message={notification.message_id}"
        )
    except Exception:
        logging.exception(
            "Failed to deliver restart notice for %s application=%s message=%s",
            notice_scope,
            notification.application_id,
            notification.message_id,
        )
    finally:
        _delete_restart_notice()


def _save_restart_notice(
    *,
    message_id: int,
    user_id: int,
    guild_id: int | None,
    application_id: int,
    interaction_token: str,
) -> None:
    """Write restart metadata to a small temp file in logs."""
    RESTART_NOTICE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "message_id": message_id,
        "user_id": user_id,
        "guild_id": guild_id,
        "application_id": application_id,
        "interaction_token": interaction_token,
    }
    with RESTART_NOTICE_PATH.open("w", encoding="utf-8") as temp_file:
        json.dump(payload, temp_file)
        temp_file.flush()
        os.fsync(temp_file.fileno())


def _load_restart_notice() -> RestartNotification | None:
    """Read restart metadata from the temp file if it exists."""
    if not RESTART_NOTICE_PATH.exists():
        print("No restart notice to deliver")
        return None

    try:
        payload = json.loads(RESTART_NOTICE_PATH.read_text(encoding="utf-8"))
    except Exception:
        logging.exception("Failed to read restart notice from %s", RESTART_NOTICE_PATH)
        return None

    if not isinstance(payload, dict):
        return None

    try:
        return RestartNotification(
            message_id=int(payload["message_id"]),
            user_id=int(payload["user_id"]),
            guild_id=None if payload.get("guild_id") is None else int(payload["guild_id"]),
            application_id=int(payload["application_id"]),
            interaction_token=str(payload["interaction_token"])
        )
    except Exception:
        logging.exception("Invalid restart notice payload in %s", RESTART_NOTICE_PATH)
        return None


def _delete_restart_notice() -> None:
    """Remove the restart temp file if it exists."""
    try:
        RESTART_NOTICE_PATH.unlink(missing_ok=True)
    except Exception:
        logging.exception("Failed to delete restart notice at %s", RESTART_NOTICE_PATH)


if __name__ == "__main__":
    asyncio.run(main())
