
import asyncio
import io
import logging
import re
from datetime import datetime
from urllib.parse import quote

import aiohttp
import discord
from discord.ext import commands

from respy_repl import Permissions
from respy_repl.imports_policy_tables import DEFAULT_IMPORTS_ALLOW, DEFAULT_IMPORTS_BLOCK
from defines.link_text import (
    ScopeLike,
    UserLike,
    role_link,
    role_scope_text,
    role_text,
    scope_text,
    user_link,
    user_scope_text,
    user_text,
)
from defines.user_session import UserSession

from .bot_db import (
    delete_repl_permissions,
    delete_repl_session,
    get_effective_repl_permissions,
    list_repl_permissions,
    list_repl_sessions,
    load_repl_session,
    save_repl_permissions,
    save_repl_session,
)

_LOGGER = logging.getLogger(__name__)

CODING_INSTRUCTIONS = r"""
To execute a block of code, send a message containing a triple-backtick code block with optional "python" after the opening fences. For example:
> \`\`\`python
> x=5
> print(x\*\*2)
> \`\`\`
You can also use single backticks for short one-liners, e.g. `` `5**3` ``.
If the code produces output, it will be sent back as a message. If there is no output, a ✅ reaction will be added to your message. If there is an error during execution, the error message will be sent back.
If the code generates matplotlib figures, image outputs will be attached to the response.

math, random, and MyDyce are imported by default, and you can import other modules as needed (subject to your permission level). Your session state will persist in-memory as long as the bot is running, and you can optionally save it to the database when closing the session to restore later.
d4, d6, d8, d10, d12, d20, and d100 are initialized by default as dyce.H(sides) objects for convenient use. Use examples:
> \`\`\`python
> (2@d6).roll()  # roll and sum 2d6
> (2@P(d6)).roll()  # roll 2d6 (or `P(d6,d6).roll()`)
> print((d6-d4).format())  # show the distribution for 1d6 minus 1d4
> (d20+5).mean()  # expected value of a d20 roll plus 5
> 
> h_4d6_k3 = (4@P(d6)).h(-1,-2,-3)  # define 4d6k3
> print(h_4d6_k3.format())  # show the distribution for 4d6k3
> stat_block = 6@P(h_4d6_k3)  # create a D&D 5e stat block of 6 4d6k3 rolls
> sorted(stat_block.roll())  # roll a standard array
> print(stat_block.h(0).format())  # distribution for lowest stat in the block
> print(stat_block.h(-1).format())  # distribution for highest stat in the block
> \`\`\`

"""

# Testing default: users without explicit stored permissions get level 3.
DEFAULT_USER_PERMISSION_LEVEL = 3

SessionKey = tuple[int, int | None]  # (user_id, guild_id)

IMPORT_POLICY_CATEGORIES: list[tuple[str, set[str]]] = [
    (
        "Core Python: Data Types",
        {
            "datetime",
            "zoneinfo",
            "calendar",
            "collections",
            "collections.abc",
            "heapq",
            "bisect",
            "array",
            "weakref",
            "types",
            "copy",
            "pprint",
            "reprlib",
            "enum",
            "graphlib",
        },
    ),
    (
        "Core Python: Numeric and Mathematical",
        {"numbers", "math", "cmath", "decimal", "fractions", "random", "statistics"},
    ),
    (
        "Core Python: Functional Programming",
        {"itertools", "functools", "operator"},
    ),
    (
        "Core Python: Internet Data / Multimedia / i18n",
        {"json", "wave", "colorsys", "gettext", "locale"},
    ),
    ("Third-party", {"numpy", "matplotlib", "scipy", "sympy", "MyDyce"}),
]

class REPLCog(commands.Cog):
    """Cog containing REPL-related commands."""

    HELP_TEXT = "\n".join(
        [
            "/repl open - Open a Python REPL session.",
            "/repl instructions - Show REPL coding instructions.",
            "/repl close - Close your current REPL session.",
            "/repl save - Save your current active REPL session.",
            "/repl delete - Delete your saved REPL session.",
            "/repl status - Show your active and saved REPL session state.",
            "/repl variables - List variables in your active or saved REPL session.",
            "/repl permissions - Show your effective REPL permission level.",
            "/repl imports - Show the imports currently enabled for your REPL session.",
            "/repl allowed_imports - Show the imports allowed by the REPL policy at your permission level.",
            "/repl list_permissions - List stored REPL permissions for this guild or DM.",
            "/repl delete_permissions - Delete stored REPL permissions for this guild.",
            "/repl saved_sessions - List saved REPL sessions for this scope (owner required for other guilds).",
            "/repl purge_session - Delete a saved REPL session for any user (owner only).",
            "/repl set_permissions - Set REPL permissions for a guild role (bot owner only).",
        ]
    )

    active_sessions: dict[SessionKey, UserSession]  # (user_id, guild_id) -> session
    session_locks: dict[SessionKey, asyncio.Lock]
    reaction_http: aiohttp.ClientSession | None
    _shutdown_started: bool

    def __init__(self, bot: commands.Bot):
        self.bot = bot

        self.active_sessions = {}
        self._shutdown_started = False

        # Serialize execution per user to keep output/result ordering stable.
        self.session_locks = {}

        # Use a dedicated HTTP session for reactions to avoid py-cord route-lock stalls.
        self.reaction_http = None

    def cog_unload(self) -> None:
        if self.reaction_http is not None and not self.reaction_http.closed:
            self.bot.loop.create_task(self.reaction_http.close())

    @staticmethod
    def _describe_session(session: UserSession) -> str:
        """Return a compact human-readable session summary."""
        return (
            f"perms={session.perms}, can_save={session.can_save}, "
            f"vars={len(session.user_vars)}"
        )

    @staticmethod
    def _format_updated_at(updated_at: float) -> str:
        """Format a UNIX timestamp into a readable local datetime string."""
        return datetime.fromtimestamp(updated_at).strftime("%Y-%m-%d %H:%M:%S")

    def _resolve_permissions(self, ctx: discord.ApplicationContext) -> tuple[Permissions, bool]:
        """Return stored permissions or the configured default fallback."""
        permissions, can_save = get_effective_repl_permissions(ctx)
        if permissions is None:
            return Permissions(perm_level=DEFAULT_USER_PERMISSION_LEVEL), False
        return permissions, can_save

    def _cached_user_or_id(self, user_id: int) -> UserLike:
        """Return a cached user when available, otherwise keep the ID."""
        return self.bot.get_user(user_id) or user_id

    def _cached_guild_or_id(self, guild_id: int | None) -> ScopeLike:
        """Return a cached guild when available, otherwise keep the ID/None."""
        if guild_id is None:
            return None
        return self.bot.get_guild(guild_id) or guild_id

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
                            self._cached_user_or_id(user_id),
                            self._cached_guild_or_id(guild_id),
                        ),
                    )

        if self.reaction_http is not None and not self.reaction_http.closed:
            await self.reaction_http.close()

        _LOGGER.info(
            "REPL graceful shutdown complete "
            f"[active={session_count}, autosaved={saved_count}, failed={failed_count}]"
        )

    repl = discord.SlashCommandGroup("repl", "Commands for managing your Python REPL session")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Execute python code sent as a backtick block by users with an open session.

        Example: ```python\nprint(1+1)\n``` or ```\n1+1\n```
        """
        if message.author.bot:
            return

        user_id = message.author.id
        guild_id = message.guild.id if message.guild else None
        session = self.active_sessions.get((user_id, guild_id))
        if session is None:
            return

        # Find the first code block (```python ...```) in the message
        m = re.search(r"```(?:python\s)?(.*?)```", message.content, re.DOTALL | re.IGNORECASE)
        # Else find the first inline code block (`...`) in the message
        if not m:
            m = re.search(r"`(.*?)`", message.content, re.DOTALL)
        if not m:
            return

        if code := m.group(1).strip():
            await _execute_code(self, message, session, code)

    @repl.command(
        name='open',
        description='Open a Python REPL session'
    )
    @discord.option(
        "fresh",
        description="Start a fresh REPL (True) or load your saved session (False)",
        required=False,
    )
    @discord.option(
        "init_dice_vars",
        description="Initialize d4, d6, d8, d10, d12, d20, and d100 variables in the REPL session",
        required=False,
    )
    @discord.option(
        "show_instructions",
        description="Show coding instructions after opening the REPL session",
        required=False,
    )
    async def open_session(
        self,
        ctx: discord.ApplicationContext,
        fresh: bool = False,
        init_dice_vars: bool = True,
        show_instructions: bool = False,
    ) -> None:
        """
        Open a REPL for the caller.

        fresh (optional):
            - True: always open a fresh REPL
            - False (default): load a saved REPL if present, else create a new one
            
        init_dice_vars (optional, default True):
            - True: initialize d4, d6, d8, d10, d12, d20, and d100 variables as MyDyce.H(sides) objects for convenient use in the REPL
            - False: do not initialize these variables
        show_instructions (optional, default False):
            - True: show coding instructions after opening the REPL session
            - False: do not show coding instructions
        """
        _made = ""
        session_key = (ctx.author.id, ctx.guild_id)

        user_session = load_repl_session(*session_key)
        if user_session is None:
            perms, can_save = self._resolve_permissions(ctx)
            user_session = UserSession(perms, {}, *session_key, can_save)
            _made = "New"
        elif fresh or not user_session.can_save:
            user_session.user_vars = {}
            _made = "Fresh"
        else:
            _made = "Saved"

        if init_dice_vars and _made != 'Saved':
            user_session.exec(
                "d4, d6, d8, d10, d12, d20, d100 = [MyDyce.H(sides) for sides in (4, 6, 8, 10, 12, 20, 100)]"
            )

        self.active_sessions[session_key] = user_session

        _LOGGER.info(
            "%s REPL session opened for %s: %s",
            _made,
            user_scope_text(ctx.author, ctx.guild),
            f"{self._describe_session(user_session)}"
        )
        await ctx.respond(f'{_made} REPL session started.', ephemeral=True)
        if show_instructions:
            await ctx.respond(CODING_INSTRUCTIONS, ephemeral=True)


    @repl.command(
        name='instructions',
        description='Show the REPL coding instructions'
    )
    async def show_instructions(self, ctx: discord.ApplicationContext) -> None:
        """Show the REPL coding instructions for the caller."""
        await ctx.respond(CODING_INSTRUCTIONS, ephemeral=True)


    @repl.command(
        name='close',
        description='Close your current REPL session'
    )
    @discord.option(
        "save",
        description="Save this REPL session for later (True) or discard it (False)",
        required=False,
    )
    async def close_session(self, ctx: discord.ApplicationContext, save: bool = True) -> None:
        """Save and close the caller's REPL session."""
        session_key = (ctx.author.id, ctx.guild_id)

        session = self.active_sessions.get(session_key)
        if session is None:
            await ctx.respond('No active REPL session to close.', ephemeral=True)
            return

        # Only save if user has permission, otherwise just close
        save = save and session.can_save
        if save:
            try:
                save_repl_session(*session_key, session)
            except Exception as e:
                await ctx.respond(f'Failed to save session, aborting close: {e}', ephemeral=True)
                return

        self.active_sessions.pop(session_key)

        _LOGGER.info(
            "REPL session closed for %s [saved=%s]",
            user_scope_text(ctx.author, ctx.guild),
            save,
        )
        await ctx.respond(f'REPL session{" saved and " if save else " "}closed.', ephemeral=True)


    @repl.command(
        name='save',
        description='Save your current active REPL session without closing it'
    )
    async def save_session(self, ctx: discord.ApplicationContext) -> None:
        """Persist the caller's active REPL session without closing it."""
        session_key = (ctx.author.id, ctx.guild_id)
        session = self.active_sessions.get(session_key)

        if session is None:
            await ctx.respond('No active REPL session to save.', ephemeral=True)
            return
        if not session.can_save:
            await ctx.respond('Your REPL session cannot be saved.', ephemeral=True)
            return

        save_repl_session(*session_key, session)
        await ctx.respond('REPL session saved.', ephemeral=True)


    @repl.command(
        name='delete',
        description='Delete your saved REPL session'
    )
    async def delete_saved_session(self, ctx: discord.ApplicationContext) -> None:
        """Delete the caller's saved REPL session."""
        session_key = (ctx.author.id, ctx.guild_id)
        delete_repl_session(*session_key)
        await ctx.respond('Saved REPL session deleted.', ephemeral=True)


    @repl.command(
        name='status',
        description='Show the current active and saved REPL session status'
    )
    async def show_session_status(self, ctx: discord.ApplicationContext) -> None:
        """Show the caller's current active and saved REPL session status."""
        session_key = (ctx.author.id, ctx.guild_id)
        active_session = self.active_sessions.get(session_key)
        saved_session = load_repl_session(*session_key)

        parts: list[str] = [
            f"Scope: {scope_text(ctx.guild)}"
        ]
        if active_session is None:
            parts.append('Active session: none')
        else:
            parts.append(f'Active session: {self._describe_session(active_session)}')

        if saved_session is None:
            parts.append('Saved session: none')
        else:
            parts.append(f'Saved session: {self._describe_session(saved_session)}')

        await ctx.respond('\n'.join(parts), ephemeral=True)


    @repl.command(
        name='permissions',
        description='Show your effective REPL permission level'
    )
    async def show_permissions(self, ctx: discord.ApplicationContext) -> None:
        """Respond with the caller's effective Permissions and whether they can save sessions."""
        perms, can_save = self._resolve_permissions(ctx)

        # Try to present a compact, informative summary of the Permissions object
        base = getattr(perms, 'base_perms', None)
        base_str = str(base) if base is not None else repr(perms)
        await ctx.respond(f'Permission level: {base_str}\nCan save sessions: {can_save}', ephemeral=True)


    @repl.command(
        name='imports',
        description='Show the imports currently enabled for your REPL session'
    )
    async def show_enabled_imports(self, ctx: discord.ApplicationContext) -> None:
        """Respond with the imports currently enabled for the caller's REPL session."""
        perms, _can_save = self._resolve_permissions(ctx)

        imports = getattr(perms, "imports", None)
        if not imports:
            await ctx.respond('No imports are available.', ephemeral=True)
            return

        modules = {module_name for module_name, _alias in imports}
        sections: list[str] = []
        for category_name, category_modules in IMPORT_POLICY_CATEGORIES:
            category_list = sorted(modules & category_modules)
            if category_list:
                sections.append(f"{category_name}\n> {', '.join(category_list)}")
                modules -= category_modules

        if modules:
            sections.append(f"Other\n> {', '.join(sorted(modules))}")

        await ctx.respond("Imports enabled for this session:\n\n" + "\n\n".join(sections), ephemeral=True)


    @repl.command(
        name='allowed_imports',
        description='Show the imports allowed by the REPL policy at your permission level'
    )
    async def show_allowed_imports(self, ctx: discord.ApplicationContext) -> None:
        """Respond with policy-allowed imports for the caller's current permission level."""
        perms, _can_save = self._resolve_permissions(ctx)

        level = getattr(perms, '_level', None)
        if level is None:
            await ctx.respond('Could not determine your REPL permission level.', ephemeral=True)
            return

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

            if '*' in allowed_symbols or any(symbol not in blocked_symbols for symbol in allowed_symbols):
                modules.add(module_name)

        sections: list[str] = []
        for category_name, category_modules in IMPORT_POLICY_CATEGORIES:
            category_list = sorted(modules & category_modules)
            if category_list:
                sections.append(f"{category_name}\n> {', '.join(category_list)}")
                modules -= category_modules

        if modules:
            sections.append(f"Other\n> {', '.join(sorted(modules))}")

        if not sections:
            await ctx.respond('No policy imports are available at your current permission level.', ephemeral=True)
            return

        await ctx.respond(
            f"Imports allowed by the REPL policy at level {max_level}:\n\n" + "\n\n".join(sections),
            ephemeral=True,
        )


    @repl.command(
        name='set_permissions',
        description='Set the REPL permission level for a specific guild role (owner only)',
    )
    @discord.option(
        "guild_role",
        description="The guild role to set permissions for.",
        required=True,
    )
    @discord.option(
        "permission_level",
        description="The permission level to set (0-3, higher is more permissive)",
        required=True,
    )
    @discord.option(
        "can_save",
        description="Whether sessions with this role's permissions can be saved (True) or not (False)",
        required=False,
    )
    @discord.default_permissions(administrator=True)
    @commands.guild_only()
    @commands.is_owner()
    async def set_role_permissions(
        self,
        ctx: discord.ApplicationContext,
        guild_role: discord.Role,
        permission_level: int,
        can_save: bool | None = None,
    ) -> None:
        """Set the REPL permission level for a specific guild role. Owner-only."""
        if not await self.bot.is_owner(ctx.author):
            await ctx.respond('Only the bot owner may use this command.', ephemeral=True)
            return

        if not (0 <= permission_level <= 3):
            await ctx.respond('Permission level must be between 0 and 3.', ephemeral=True)
            return

        if can_save is None:
            can_save = permission_level >= 2
        save_repl_permissions(
            ctx.guild_id,
            guild_role.id if ctx.guild_id else ctx.author.id,
            Permissions(
                perm_level=permission_level,
                imports=["math", "random", "MyDyce", "MyDyce:P,H"],
            ),
            can_save=can_save
        )
        await ctx.respond(
            f'Set permission level for role {role_link(guild_role)} to {permission_level}.',
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

        _LOGGER.info(
            "Set permissions for %s to level %s",
            role_scope_text(guild_role, ctx.guild),
            permission_level,
        )


    @repl.command(
        name='list_permissions',
        description='List stored REPL permissions for this guild or DM'
    )
    @discord.option(
        "hide",
        description="Whether response should be ephemeral (True) or public (False)",
        required=False,
    )
    async def list_role_permissions(
        self,
        ctx: discord.ApplicationContext,
        hide: bool = True
    ) -> None:
        """List stored permissions for the current guild or DM."""
        perms_list = list_repl_permissions(ctx.guild_id)
        if not perms_list:
            await ctx.respond('No permissions found for this guild or DM.', ephemeral=hide)
            return

        lines = []
        for (guild_id, role_id, perms, can_save, updated) in perms_list:
            if perms is None:
                continue
            if role_id == guild_id:
                role_id = 0  # Use 0 to represent @everyone

            if guild_id:
                resolved_role = ctx.guild.get_role(role_id) if ctx.guild is not None else None
                mention = role_link(resolved_role or role_id)
            else:
                mention = user_link(self._cached_user_or_id(role_id))

            lines.append(
                f"{mention}:  "
                f"Permissions: {getattr(perms, 'base_perms', perms)}  |  "
                f"Can Save: {bool(can_save)}  |  "
                f"Last Updated: {self._format_updated_at(updated)}"
            )

        response = "Stored permissions:\n" + "\n".join(lines)
        await ctx.respond(
            response,
            ephemeral=hide,
            allowed_mentions=discord.AllowedMentions.none(),
        )


    @repl.command(
        name='delete_permissions',
        description='Delete stored REPL permissions for the current guild or DM'
    )
    @discord.option(
        "guild_role",
        description="The guild role to delete permissions for. Leave blank to delete all for the current scope.",
        required=False,
    )
    @discord.default_permissions(administrator=True)
    @commands.guild_only()
    @commands.is_owner()
    async def delete_role_permissions(
        self,
        ctx: discord.ApplicationContext,
        guild_role: discord.Role | None = None,
    ) -> None:
        """Delete one stored permission row or the full guild scope."""
        if guild_role is None:
            delete_repl_permissions(ctx.guild_id)
            await ctx.respond('Deleted all REPL permissions for this server.', ephemeral=True)
            return

        delete_repl_permissions(ctx.guild_id, guild_role.id)
        await ctx.respond(
            f'Deleted REPL permissions for {role_link(guild_role)}.',
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


    @repl.command(
        name='saved_sessions',
        description='List saved REPL sessions for this guild/DM scope; owner can query any scope'
    )
    @discord.option(
        "guild_id",
        description="Optional guild ID to query. Leave blank to use the current guild/DM scope.",
        required=False,
    )
    async def list_saved_sessions(
        self,
        ctx: discord.ApplicationContext,
        guild_id: int | None = None,
    ) -> None:
        """List saved REPL sessions for current scope; owner can query any scope."""
        requested_scope = guild_id or ctx.guild_id
        if requested_scope != ctx.guild_id and not await self.bot.is_owner(ctx.author):
            await ctx.respond(
                'Only the bot owner may list saved sessions outside the current scope.',
                ephemeral=True,
            )
            return

        sessions = list_repl_sessions(requested_scope, include_all_scopes=False)
        if not sessions:
            await ctx.respond('No saved REPL sessions found.', ephemeral=True)
            return

        lines: list[str] = []
        for user_id, row_guild_id, updated in sessions:
            lines.append(
                f"User: {user_link(self._cached_user_or_id(user_id))}  |  "
                f"Scope: {scope_text(self._cached_guild_or_id(row_guild_id))}  |  "
                f"Last Updated: {self._format_updated_at(updated)}"
            )
        await ctx.respond(
            "Saved REPL sessions:\n" + "\n".join(lines),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


    @repl.command(
        name='purge_session',
        description='Delete a saved REPL session for any user (owner only)'
    )
    @discord.option(
        "user_id",
        description="The user ID whose saved session should be deleted.",
        required=True,
    )
    @discord.option(
        "guild_id",
        description="Optional guild ID. Leave blank to delete the DM session.",
        required=False,
    )
    @discord.default_permissions(administrator=True)
    @commands.is_owner()
    async def purge_saved_session(
        self,
        ctx: discord.ApplicationContext,
        user_id: int,
        guild_id: int | None = None,
    ) -> None:
        """Delete a saved REPL session for a specific user and scope."""
        delete_repl_session(user_id, guild_id)
        await ctx.respond(
            f'Deleted saved REPL session for '
            f'{user_scope_text(self._cached_user_or_id(user_id), self._cached_guild_or_id(guild_id))}.',
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


    @repl.command(
        name='variables',
        description='List user-defined variables in your active REPL session'
    )
    @discord.option(
        "hide",
        description="Whether response should be ephemeral (True) or public (False)",
        required=False,
    )
    @discord.option(
        "values",
        description="Whether to include variable values in the listing (True) or just names (False)",
        required=False,
        )
    @discord.option(
        "saved",
        description="Whether to list variables from the active session (False) or a saved session (True, if present)",
        required=False,
    )
    async def list_session_vars(self, ctx: discord.ApplicationContext, hide: bool = True, values: bool = False, saved: bool = False) -> None:
        """List variables currently defined in the caller's in-memory or saved REPL session."""
        session_key = (ctx.author.id, ctx.guild_id)

        active_session = self.active_sessions.get(session_key)
        loaded_session = load_repl_session(*session_key) if saved or not active_session else None

        sections: list[str] = []
        if active_session is not None:
            sections.append('Active REPL session found.\n' + active_session.print_user_vars(include_values=values))
        if loaded_session is not None:
            sections.append('Saved session found.\n' + loaded_session.print_user_vars(include_values=values))

        if not sections:
            await ctx.respond('No REPL session found.', ephemeral=hide)
            return

        await ctx.respond('\n\n'.join(sections), ephemeral=hide)


async def _execute_code(
    self,
    message: discord.Message,
    session: UserSession,
    code: str,
) -> None:
    """Execute code and return a formatted result string, or None.

    Handles the typing indicator, per-user execution lock,
    and output formatting. Sends the error message directly and adds a ✅
    reaction when there is no output; in both cases returns None so the
    caller knows there is nothing left to send.
    """
    def _exec() -> tuple[object | None, str, list[discord.File]]:
        response = session.exec_response(code)
        files: list[discord.File] = []
        image_count = 0
        for artifact in response.display_artifacts:
            if getattr(artifact, "mime_type", "") != "image/png":
                continue
            image_count += 1
            filename = f"repl-output-{image_count}.png"
            payload = io.BytesIO(getattr(artifact, "data", b""))
            if payload.getbuffer().nbytes == 0:
                continue
            files.append(discord.File(payload, filename=filename))

        return response.result, response.output.rstrip(), files

    loop = asyncio.get_running_loop()
    user_id = message.author.id
    guild_id = message.guild.id if message.guild else None
    session_key = (user_id, guild_id)

    lock = self.session_locks.setdefault(session_key, asyncio.Lock())
    await message.channel.trigger_typing()
    async with lock:
        try:
            code_preview = code if len(code) <= 60 else code[:57] + "..."
            _LOGGER.debug(
                "Executing code for %s: %r",
                user_scope_text(message.author, message.guild),
                code_preview,
            )
            result, stdout, files = await loop.run_in_executor(None, _exec)
        except Exception as e:
            await _add_reaction(self, message, "❌")
            error_msg = (
                f"Error executing code in "
                f"{scope_text(message.guild)}: {e}"
            )
            _LOGGER.exception(
                "Error executing code for %s",
                user_scope_text(message.author, message.guild),
                extra={"user_code_error": True},
            )
            await message.channel.send(f"`{error_msg}`", reference=message)
            return

    parts: list[str] = []
    if stdout:
        parts.append(stdout)
    if result is not None:
        try:
            rep = repr(result)
        except Exception as e:
            rep = f"{type(e).__name__}: {e}"
        parts.append(rep)

    if not parts and not files:
        await _add_reaction(self, message, "✅")
        return

    message_text = None
    if parts:
        out = "\n".join(parts)
        # Discord message limit ~2000 chars; keep room for fences
        if len(out) > 1900:
            message_text = "```python\n" + out[:1900] + f"\n...\n```\n[{len(out)-1900} characters truncated]"
        else:
            message_text = f"```python\n{out}\n```"

    if files:
        try:
            await message.channel.send(content=message_text, files=files, reference=message)
        finally:
            for file in files:
                try:
                    file.close()
                except Exception:
                    pass
        return

    await message.channel.send(message_text, reference=message)


async def _add_reaction(self, message: discord.Message, emoji: str) -> None:
    """Add a reaction using direct REST to avoid py-cord reaction-lock hangs."""
    token = getattr(self.bot.http, "token", None)
    if not token:
        raise RuntimeError("Bot token unavailable for reaction request")

    encoded = quote(emoji, safe="")
    url = (
        f"https://discord.com/api/v10/channels/{message.channel.id}"
        f"/messages/{message.id}/reactions/{encoded}/@me"
    )
    headers = {
        "Authorization": f"Bot {token}",
        "User-Agent": getattr(self.bot.http, "user_agent", "DiscordBot"),
    }

    if self.reaction_http is None or self.reaction_http.closed:
        self.reaction_http = aiohttp.ClientSession()

    async with self.reaction_http.put(url, headers=headers) as response:
        if response.status in {200, 201, 204}:
            return

        body = await response.text()
        raise RuntimeError(f"Reaction request failed ({response.status}): {body}")


def setup(bot: commands.Bot) -> None:
    bot.add_cog(REPLCog(bot))
