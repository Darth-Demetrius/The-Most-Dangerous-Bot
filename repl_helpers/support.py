"""Shared helpers and state for the Discord REPL cogs."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import aiohttp
import discord
from discord.ext import commands

from respy_repl import Permissions
from respy_repl.imports_policy_tables import (
    DEFAULT_IMPORTS_ALLOW,
    DEFAULT_IMPORTS_BLOCK,
    IMPORT_POLICY_CATEGORIES,
)

from repl_helpers.link_text import ScopeLike, UserLike, user_scope_text
from repl_helpers.user_session import UserSession

from cogs.bot_db import (
    get_effective_repl_permissions,
    load_repl_session,
    save_repl_session,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_USER_PERMISSION_LEVEL = 3
OPEN_SESSION_SOURCES = ("auto", "fresh", "saved")
IMPORT_VIEWS = ("session", "policy")
VARIABLE_SOURCES = ("active", "saved", "both")
SessionKey = tuple[int, int | None]
DICE_INIT_CODE = (
    "d4, d6, d8, d10, d12, d20, d100 = "
    "[MyDyce.H(sides) for sides in (4, 6, 8, 10, 12, 20, 100)]"
)


def format_updated_at(updated_at: float) -> str:
    """Format a UNIX timestamp into a readable local datetime string.

    Args:
        updated_at: UNIX timestamp.

    Returns:
        Formatted local datetime string.
    """
    return datetime.fromtimestamp(updated_at).strftime("%Y-%m-%d %H:%M:%S")


def cached_user_or_id(bot: commands.Bot, user_id: int) -> UserLike:
    """Return a cached user when available, otherwise keep the raw ID.

    Args:
        bot: Running bot instance.
        user_id: Discord user ID.

    Returns:
        Cached user object or the numeric ID.
    """
    return bot.get_user(user_id) or user_id


def cached_guild_or_id(bot: commands.Bot, guild_id: int | None) -> ScopeLike:
    """Return a cached guild when available, otherwise keep the ID or None.

    Args:
        bot: Running bot instance.
        guild_id: Discord guild ID.

    Returns:
        Cached guild object, the numeric ID, or None.
    """
    if guild_id is None:
        return None
    return bot.get_guild(guild_id) or guild_id


class ReplSessionService:
    """Manage REPL session lifecycle and shared presentation helpers."""

    bot: commands.Bot
    active_sessions: dict[SessionKey, UserSession]
    session_locks: dict[SessionKey, asyncio.Lock]
    reaction_http: aiohttp.ClientSession | None
    _shutdown_started: bool

    def __init__(self, bot: commands.Bot) -> None:
        """Initialize per-bot REPL runtime state.

        Args:
            bot: Running bot instance.
        """
        self.bot = bot
        self.active_sessions = {}
        self.session_locks = {}
        self.reaction_http = None
        self._shutdown_started = False

    def unload(self) -> None:
        """Close the dedicated reaction HTTP session when unloading the cog."""
        if self.reaction_http is not None and not self.reaction_http.closed:
            self.bot.loop.create_task(self.reaction_http.close())

    @staticmethod
    def describe_session(session: UserSession) -> str:
        """Return a compact human-readable session summary.

        Args:
            session: Active or saved user session.

        Returns:
            Summary string suitable for status output.
        """
        session._sync_input_name_state()
        return (
            f"perms={session.perms}, can_save={session.can_save}, "
            f"vars={len(session.user_vars)}, input_name={session.input_name_template!r}"
        )

    def resolve_permissions(self, ctx: discord.ApplicationContext) -> tuple[Permissions, bool]:
        """Return stored permissions or the configured default fallback.

        Args:
            ctx: Discord interaction context.

        Returns:
            Effective permissions and whether the session can be saved.
        """
        permissions, can_save = get_effective_repl_permissions(ctx)
        if permissions is None:
            return Permissions(perm_level=DEFAULT_USER_PERMISSION_LEVEL), False
        return permissions, can_save

    @staticmethod
    def session_key(ctx: discord.ApplicationContext) -> SessionKey:
        """Return the active-session key for one interaction context.

        Args:
            ctx: Discord interaction context.

        Returns:
            User and guild key tuple.
        """
        return (ctx.author.id, ctx.guild_id)

    @staticmethod
    def message_session_key(message: discord.Message) -> SessionKey:
        """Return the active-session key for one Discord message.

        Args:
            message: Incoming Discord message.

        Returns:
            User and guild key tuple.
        """
        return (message.author.id, message.guild.id if message.guild else None)

    def get_active_session(self, ctx: discord.ApplicationContext) -> UserSession | None:
        """Return the caller's active REPL session when present.

        Args:
            ctx: Discord interaction context.

        Returns:
            Active user session or None.
        """
        return self.active_sessions.get(self.session_key(ctx))

    async def require_active_session(
        self,
        ctx: discord.ApplicationContext,
        *,
        missing_message: str = "No active REPL session found.",
    ) -> UserSession | None:
        """Return the active REPL session or send the default missing-session response.

        Args:
            ctx: Discord interaction context.
            missing_message: Ephemeral response text when no session exists.

        Returns:
            Active session or None.
        """
        session = self.get_active_session(ctx)
        if session is None:
            await ctx.respond(missing_message, ephemeral=True)
        return session

    @staticmethod
    def format_import_sections(modules: set[str]) -> list[str]:
        """Group modules into import-policy categories for response output.

        Args:
            modules: Module names to categorize.

        Returns:
            Render-ready grouped sections.
        """
        remaining_modules = set(modules)
        sections: list[str] = []
        for category_name, category_modules in IMPORT_POLICY_CATEGORIES.items():
            category_list = sorted(remaining_modules & category_modules)
            if category_list:
                sections.append(f"{category_name}\n> {', '.join(category_list)}")
                remaining_modules -= category_modules

        if remaining_modules:
            sections.append(f"Other\n> {', '.join(sorted(remaining_modules))}")

        return sections

    @staticmethod
    def get_policy_allowed_modules(perms: Permissions) -> tuple[int | None, set[str]]:
        """Return policy-allowed modules plus the resolved permission level.

        Args:
            perms: Effective permissions object.

        Returns:
            Permission level and set of allowed modules.
        """
        level = getattr(perms, "_level", None)
        if level is None:
            return None, set()

        max_level = int(level)
        modules: set[str] = set()
        for module_name in sorted(DEFAULT_IMPORTS_ALLOW):
            allowed_rules = DEFAULT_IMPORTS_ALLOW[module_name]
            allowed_symbols = set().union(
                *(symbols for rule_level, symbols in allowed_rules.items() if rule_level <= max_level)
            )
            if not allowed_symbols:
                continue

            blocked_rules = DEFAULT_IMPORTS_BLOCK.get(module_name, {})
            blocked_symbols = set().union(
                *(symbols for rule_level, symbols in blocked_rules.items() if rule_level <= max_level)
            )

            if "*" in allowed_symbols or any(symbol not in blocked_symbols for symbol in allowed_symbols):
                modules.add(module_name)

        return max_level, modules

    def build_opened_session(
        self,
        ctx: discord.ApplicationContext,
        *,
        source: str,
    ) -> tuple[str, UserSession] | None:
        """Choose the session object that should become active for opening commands.

        Args:
            ctx: Discord interaction context.
            source: One of auto, fresh, or saved.

        Returns:
            Session source label and session object, or None when saved was required but absent.
        """
        session_key = self.session_key(ctx)
        saved_session = load_repl_session(*session_key)

        if source == "saved":
            if saved_session is None:
                return None
            return "Saved", saved_session

        if source == "fresh":
            if saved_session is None:
                perms, can_save = self.resolve_permissions(ctx)
                return "Fresh", UserSession(perms, {}, *session_key, can_save)

            saved_session.user_vars = {}
            saved_session.reset_input_name_state()
            return "Fresh", saved_session

        if saved_session is None:
            perms, can_save = self.resolve_permissions(ctx)
            return "New", UserSession(perms, {}, *session_key, can_save)
        if not saved_session.can_save:
            saved_session.user_vars = {}
            saved_session.reset_input_name_state()
            return "Fresh", saved_session
        return "Saved", saved_session

    async def graceful_shutdown(self) -> None:
        """Persist saveable active sessions and release HTTP resources during shutdown."""
        if self._shutdown_started:
            return

        self._shutdown_started = True
        session_count = len(self.active_sessions)
        saved_count = 0
        failed_count = 0

        for session_key, session in list(self.active_sessions.items()):
            if not session.can_save:
                continue

            lock = self.session_locks.setdefault(session_key, asyncio.Lock())
            async with lock:
                try:
                    save_repl_session(*session_key, session)
                    saved_count += 1
                except Exception:
                    failed_count += 1
                    user_id, guild_id = session_key
                    _LOGGER.exception(
                        "Failed to autosave REPL session during shutdown for %s",
                        user_scope_text(
                            cached_user_or_id(self.bot, user_id),
                            cached_guild_or_id(self.bot, guild_id),
                        ),
                    )

        if self.reaction_http is not None and not self.reaction_http.closed:
            await self.reaction_http.close()

        _LOGGER.info(
            "REPL graceful shutdown complete "
            f"[active={session_count}, autosaved={saved_count}, failed={failed_count}]"
        )
