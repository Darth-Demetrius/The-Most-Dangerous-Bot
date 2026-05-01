
import discord
from discord.ext import commands
import re
import asyncio
import aiohttp
import io
import logging
from urllib.parse import quote

from respy_repl import Permissions
from respy_repl.imports_policy_tables import DEFAULT_IMPORTS_ALLOW, DEFAULT_IMPORTS_BLOCK
from .bot_db import (
    save_session,
    load_session,
    delete_session,
    list_sessions,
    save_permissions,
    fetch_user_guild_permissions,
    delete_permissions,
    list_permissions,
)
from defines.user_session import UserSession

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

IDTuple = tuple[int, int | None]  # (user_id, guild_id)

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
    active_sessions: dict[IDTuple, UserSession]  # (user_id, guild_id) -> session
    session_locks: dict[IDTuple, asyncio.Lock]
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

    async def graceful_shutdown(self) -> None:
        """Persist saveable active sessions and release HTTP resources during shutdown."""
        if self._shutdown_started:
            return

        self._shutdown_started = True
        session_count = len(self.active_sessions)
        saved_count = 0
        failed_count = 0

        for id_tuple, session in self.active_sessions.items():
            #self.active_sessions.pop(id_tuple)
            if not session.can_save:
                continue

            lock = self.session_locks.setdefault(id_tuple, asyncio.Lock())
            async with lock:
                try:
                    save_session(*id_tuple, session)
                    saved_count += 1
                except Exception:
                    failed_count += 1
                    logging.exception(
                        "Failed to autosave REPL session during shutdown for user_id=%s guild_id=%s",
                        *id_tuple,
                    )

        if self.reaction_http is not None and not self.reaction_http.closed:
            await self.reaction_http.close()

        print(
            "REPL graceful shutdown complete "
            f"[autosaved={saved_count}, failed={failed_count}]"
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
        session = self.active_sessions.get((user_id, guild_id), None)
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
        "new",
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
    async def open_repl_command(
        self,
        ctx: discord.ApplicationContext,
        new: bool = False,
        init_dice_vars: bool = True,
        show_instructions: bool = False,
    ) -> None:
        """
        Open a REPL for the caller.

        new (optional):
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
        id_tuple = (ctx.author.id, ctx.guild_id)

        user_session = load_session(*id_tuple)
        if user_session is None:
            perms, can_save = fetch_user_guild_permissions(ctx)
            if perms:
                user_session = UserSession(perms, {}, *id_tuple, can_save)
                _made = "New"
        elif new or not user_session.can_save:
            user_session.user_vars = {}
            _made = "Fresh"
        else:
            _made = "Saved"

        if user_session is None:
            await ctx.respond('You do not have permission to open a REPL session.', ephemeral=True)
            print(f"Denied REPL open for {ctx.author}{f' in guild {ctx.guild}' if ctx.guild else ''} due to insufficient permissions")
            return

        if init_dice_vars and _made != 'Saved':
            user_session.exec(
                "d4, d6, d8, d10, d12, d20, d100 = [MyDyce.H(sides) for sides in (4, 6, 8, 10, 12, 20, 100)]"
            )

        self.active_sessions[id_tuple] = user_session

        print(f"{_made} REPL session opened for {ctx.author}{f' in guild {ctx.guild}' if ctx.guild else ''} [perms={user_session.perms}, can_save={user_session.can_save}]")
        await ctx.respond(f'{_made} REPL session started.', ephemeral=True)
        if show_instructions:
            await ctx.respond(CODING_INSTRUCTIONS, ephemeral=True)


    @repl.command(
        name='instructions',
        description='Show the REPL coding instructions'
    )
    async def show_coding_instructions(self, ctx: discord.ApplicationContext) -> None:
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
    async def close_repl_command(self, ctx: discord.ApplicationContext, save: bool = True) -> None:
        """Save and close the caller's REPL session."""
        id_tuple = (ctx.author.id, ctx.guild_id)

        session = self.active_sessions.get(id_tuple, None)
        if session is None:
            await ctx.respond('No active REPL session to close.', ephemeral=True)
            return

        # Only save if user has permission, otherwise just close
        save = save and session.can_save
        if save:
            try:
                save_session(*id_tuple, session)
            except Exception as e:
                await ctx.respond(f'Failed to save session, aborting close: {e}', ephemeral=True)
                return

        self.active_sessions.pop(id_tuple)
        print(f"REPL session closed for {ctx.author}{f' in guild {ctx.guild}' if ctx.guild else ''} [saved={save}]")
        await ctx.respond(f'REPL session {"saved and " if save else ""}closed.', ephemeral=True)


    @repl.command(
        name='perms',
        description='Show your effective REPL permission level'
    )
    async def show_permissions(self, ctx: discord.ApplicationContext) -> None:
        """Respond with the caller's effective Permissions and whether they can save sessions."""
        perms, can_save = fetch_user_guild_permissions(ctx)
        if perms is None:
            await ctx.respond('You do not have permission to use the REPL.')
            return

        # Try to present a compact, informative summary of the Permissions object
        base = getattr(perms, 'base_perms', None)
        base_str = str(base) if base is not None else repr(perms)
        await ctx.respond(f'Permission level: {base_str}\nCan save sessions: {can_save}', ephemeral=True)


    @repl.command(
        name='session_imports',
        description='Show the imports currently enabled for your REPL session'
    )
    async def show_session_imports(self, ctx: discord.ApplicationContext) -> None:
        """Respond with the imports currently enabled for the caller's REPL session."""
        perms, _can_save = fetch_user_guild_permissions(ctx)
        if perms is None:
            await ctx.respond('You do not have permission to use the REPL.', ephemeral=True)
            return

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
        name='possible_imports',
        description='Show the imports allowed by the REPL policy at your permission level'
    )
    async def show_possible_imports(self, ctx: discord.ApplicationContext) -> None:
        """Respond with policy-allowed imports for the caller's current permission level."""
        perms, _can_save = fetch_user_guild_permissions(ctx)
        if perms is None:
            await ctx.respond('You do not have permission to use the REPL.', ephemeral=True)
            return

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
        name='set_perms',
        description='Set the REPL permission level for a specific guild role (admin only)',
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
    @commands.has_permissions(administrator=True)
    async def set_permissions(
        self,
        ctx: discord.ApplicationContext,
        guild_role: discord.Role,
        permission_level: int,
        can_save: bool | None = None,
    ) -> None:
        """Set the REPL permission level for a specific guild role. Requires admin permissions."""
        if not await self.bot.is_owner(ctx.author):
            await ctx.respond('Only the bot owner may use this command.', ephemeral=True)
            return

        if permission_level < 0 or permission_level > 3:
            await ctx.respond('Permission level must be between 0 and 3.', ephemeral=True)
            return

        if can_save is None:
            can_save = permission_level >= 2
        save_permissions(
            ctx.guild_id,
            guild_role.id if ctx.guild_id else ctx.author.id,
            Permissions(
                perm_level=permission_level,
                imports=["math", "random", "MyDyce", "MyDyce:P,H"],
            ),
            can_save=can_save
        )
        await ctx.respond(f'Set permission level {permission_level} for role {guild_role.name}.', ephemeral=True)
        txt = f"'guild {ctx.guild.name}' " if ctx.guild else "DM "
        print(f"Set permissions for role {guild_role.name} (id={guild_role.id}) in {txt}(id={ctx.guild_id}) to level {permission_level}")


    @repl.command(
        name='list_perms',
        description='List user-defined variables in your active REPL session'
    )
    @discord.option(
        "hide",
        description="Whether response should be ephemeral (True) or public (False)",
        required=False,
    )
    async def list_permissions_cmd(
        self,
        ctx: discord.ApplicationContext,
        hide: bool = True
    ) -> None:
        """List stored permissions for the current guild or DM."""
        perms_list = list_permissions(ctx.guild_id)
        if not perms_list:
            await ctx.respond('No permissions found for this guild or DM.', ephemeral=hide)
            return

        lines = []
        for (guild_id, role_id, perms, can_save, updated) in perms_list:
            if perms is None:
                continue
            lines.append(f"Role/User ID: {"@everyone" if role_id == guild_id else role_id}  |  Permissions: {getattr(perms, 'base_perms', perms)}  |  Can Save: {bool(can_save)}  |  Last Updated: {updated}")

        response = "Stored permissions:\n" + "\n".join(lines)
        await ctx.respond(response, ephemeral=hide)


    @repl.command(
        name='vars',
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
    async def list_vars(self, ctx: discord.ApplicationContext, hide: bool = True, values: bool = False, saved: bool = False) -> None:
        """List variables currently defined in the caller's in-memory or saved REPL session."""
        id_tuple = (ctx.author.id, ctx.guild_id)

        active_session = self.active_sessions.get(id_tuple, None)
        if active_session is not None:
            await ctx.respond('Active REPL session found.\n' + active_session.print_user_vars(include_values=values), ephemeral=hide)

        loaded_session = load_session(*id_tuple) if saved or not active_session else None
        if loaded_session is not None:
            await ctx.respond('Saved session found.\n' + loaded_session.print_user_vars(include_values=values), ephemeral=hide)

        if not active_session and not loaded_session:
            await ctx.respond('No REPL session found.', ephemeral=hide)


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
    lock = self.session_locks.setdefault(str(message.author.id), asyncio.Lock())
    await message.channel.trigger_typing()
    async with lock:
        try:
            code_preview = code if len(code) <= 60 else code[:57] + "..."
            print(f"Executing code for {message.author} in guild {message.guild}: {code_preview!r}")
            result, stdout, files = await loop.run_in_executor(None, _exec)
        except Exception as e:
            print(f"Error during execution: {e}")
            await _add_reaction(self, message, "❌")
            await message.channel.send(f"`Error during execution: {e}`", reference=message)
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


def setup(bot: commands.Bot):
    bot.add_cog(REPLCog(bot))
