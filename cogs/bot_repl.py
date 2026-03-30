
import discord
from discord.ext import commands
from respy_repl import Permissions, SafeSession
import re
import asyncio
import threading
import aiohttp
from urllib.parse import quote

from .bot_db import (
    fetch_user_guild_permissions,
    save_session,
    load_session,
    delete_session,
    fetch_permission,
)

CODING_INSTRUCTIONS = r"""
To execute a block of code, send a message containing a triple-backtick code block with optional "python" after the opening fences. For example:
> \`\`\`python
> x=5
> print(x\*\*2)
> \`\`\`
You can also use single backticks for short one-liners, e.g. `` `5**3` ``.
If the code produces output, it will be sent back as a message. If there is no output, a ✅ reaction will be added to your message. If there is an error during execution, the error message will be sent back.
"""

# Testing default: users without explicit stored permissions get level 3.
DEFAULT_USER_PERMISSION_LEVEL = 3

ActiveSessionDict = dict[str | None, dict[str, SafeSession]]  # guild_id -> user_id -> session

class REPLCog(commands.Cog):
    """Cog containing REPL-related commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Active sessions kept in-memory on the bot instance
        self.active_sessions: ActiveSessionDict = {}
        # Serialize execution per user to keep output/result ordering stable.
        self.session_locks: dict[str, asyncio.Lock] = {}
        # Use a dedicated HTTP session for reactions to avoid py-cord route-lock stalls.
        self.reaction_http: aiohttp.ClientSession | None = None

    def cog_unload(self) -> None:
        if self.reaction_http is not None and not self.reaction_http.closed:
            self.bot.loop.create_task(self.reaction_http.close())

    repl = discord.SlashCommandGroup("repl", "Commands for managing your Python REPL session")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Execute python code sent as a triple-backtick block by users with an open session.

        Example: ```python\nprint(1+1)\n``` or ```\n1+1\n```
        """
        if message.author.bot:
            return

        user_id = str(message.author.id)
        guild_id = str(message.guild.id) if message.guild else None
        session = self.active_sessions.get(guild_id, {}).get(user_id)
        if session is None:
            return

        # Find the first code block (```python ...```) in the message
        m = re.search(r"```(?:python\s)?(.*?)```", message.content, re.DOTALL | re.IGNORECASE)
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
    async def open_repl_command(self, ctx: discord.ApplicationContext, new: bool = False) -> None:
        """
        Open a REPL for the caller.

        new (optional):
            - True: always open a fresh REPL
            - False (default): load a saved REPL if present, else create a new one
        """
        guild_id = str(ctx.guild_id) if ctx.guild_id else None
        user_id = str(ctx.author.id)

        user_perms, user_can_save = _get_user_permissions(self, ctx)

        if user_perms is None:
            await ctx.respond('You do not have permission to open a REPL session.', ephemeral=True)
            return
        if not user_can_save:
            new = True  # force new session if user can't save, to avoid confusion

        if guild_id not in self.active_sessions:
            self.active_sessions[guild_id] = {}

        if not new:
            if user_id in self.active_sessions[guild_id]:
                await ctx.respond(f'You already have an active REPL session.', ephemeral=True)
                await ctx.respond(CODING_INSTRUCTIONS, ephemeral=True)
                return
            # try to restore a pickled session from the DB
            session = load_session(guild_id, user_id)
            if session is not None:
                self.active_sessions[guild_id][user_id] = session
                print(f"REPL session restored for {ctx.author} in guild {ctx.guild} [perms={user_perms}]")
                await ctx.respond(f'Saved REPL session loaded.', ephemeral=True)
                await ctx.respond(CODING_INSTRUCTIONS, ephemeral=True)
                return

        # No saved session found or user requested a fresh one
        delete_session(guild_id, user_id)  # clear any existing saved session
        session = SafeSession(user_perms)
        self.active_sessions[guild_id][user_id] = session
        print(f"REPL session opened for {ctx.author} in guild {ctx.guild} [perms={user_perms}, fresh={new}]")
        await ctx.respond(f'New REPL session started.', ephemeral=True)
        await ctx.respond(CODING_INSTRUCTIONS, ephemeral=True)
        return


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
        guild_id = str(ctx.guild_id) if ctx.guild_id else None
        user_id = str(ctx.author.id)

        user_perms, user_can_save = _get_user_permissions(self, ctx)

        current = self.active_sessions.get(guild_id, {}).get(user_id)
        if current is None:
            await ctx.respond('No active REPL session to close.', ephemeral=True)
            return

        save &= user_can_save  # only save if user has permission, otherwise just close
        if save:
            try:
                save_session(guild_id, user_id, current)
            except Exception as e:
                await ctx.respond(f'Failed to save session, aborting close: {e}', ephemeral=True)
                return

        self.active_sessions[guild_id].pop(user_id, None)
        print(f"REPL session closed for {ctx.author} in guild {ctx.guild} [saved={save}]")
        await ctx.respond(f'REPL session {"saved and " if save else ""}closed.', ephemeral=True)


    @repl.command(
        name='perms',
        description='Show your effective REPL permission level'
    )
    async def show_permissions(self, ctx: discord.ApplicationContext) -> None:
        """Respond with the caller's effective Permissions and whether they can save sessions."""
        perms, can_save = _get_user_permissions(self, ctx)
        if perms is None:
            await ctx.respond('You do not have permission to use the REPL.')
            return

        # Try to present a compact, informative summary of the Permissions object
        base = getattr(perms, 'base_perms', None)
        base_str = str(base) if base is not None else repr(perms)
        await ctx.respond(f'Permission level: {base_str}\nCan save sessions: {can_save}', ephemeral=True)


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
        guild_id = str(ctx.guild_id) if ctx.guild_id else None
        user_id = str(ctx.author.id)

        active_session = self.active_sessions.get(guild_id, {}).get(user_id)
        if active_session is not None:
            await ctx.respond('Active REPL session found.\n' + active_session.print_user_vars(include_values=values), ephemeral=hide)

        loaded_session = load_session(guild_id, user_id) if saved or not active_session else None
        if loaded_session is not None:
            await ctx.respond('Saved session found.\n' + loaded_session.print_user_vars(include_values=values), ephemeral=hide)

        if not active_session and not loaded_session:
            await ctx.respond('No REPL session found.', ephemeral=hide)


async def _execute_code(
    self,
    message: discord.Message,
    session: SafeSession,
    code: str,
) -> None:
    """Execute code and return a formatted result string, or None.

    Handles the typing indicator, per-user execution lock,
    and output formatting. Sends the error message directly and adds a ✅
    reaction when there is no output; in both cases returns None so the
    caller knows there is nothing left to send.
    """
    def _exec() -> tuple[object | None, str]:
        result, output = session.exec(code)
        return result, output.rstrip()

    loop = asyncio.get_running_loop()
    lock = self.session_locks.setdefault(str(message.author.id), asyncio.Lock())
    await message.channel.trigger_typing()
    async with lock:
        try:
            code_preview = code if len(code) <= 60 else code[:57] + "..."
            print(f"Executing code for {message.author} in guild {message.guild}: {code_preview!r}")
            result, stdout = await loop.run_in_executor(None, _exec)
        except Exception as e:
            print(f"Error during execution: {e}")
            await _add_reaction(self, message, "❌")
            await message.channel.send(f"Error during execution: {e}", reference=message)
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

    if not parts:
        await _add_reaction(self, message, "✅")
        return

    out = "\n".join(parts)
    # Discord message limit ~2000 chars; keep room for fences
    if len(out) > 1900:
        out = "```python\n" + out[:1900] + f"\n...\n```\n[{len(out)-1900} characters truncated]"
    else:
        out = f"```python\n{out}\n```"
    await message.channel.send(out, reference=message)

def _get_user_permissions(self, ctx: discord.ApplicationContext) -> tuple[Permissions | None, bool]:
    user_id = str(ctx.author.id)
    guild_id = str(ctx.guild_id) if ctx.guild_id else None

    if guild_id is None:
        dm_perms, user_can_save = fetch_permission(None, user_id)
        user_perms = [dm_perms] if dm_perms is not None else []
    else:
        user_roles = ctx.author.roles if isinstance(ctx.author, discord.Member) else []
        user_role_ids = [str(role.id) for role in user_roles]
        user_perms, user_can_save = fetch_user_guild_permissions(guild_id, user_role_ids)

    if not user_perms:
        default_permissions = Permissions(DEFAULT_USER_PERMISSION_LEVEL)
        return default_permissions, default_permissions.can_save

    return Permissions.permissive_merge(*user_perms), user_can_save


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
