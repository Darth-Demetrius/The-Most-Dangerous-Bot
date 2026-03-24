
import discord
from discord.ext import commands
from respy_repl import Permissions, SafeSession
import re
import asyncio

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

        code = m.group(1).strip()
        if not code:
            return

        out = await _execute_code(self, message, user_id, session, code)
        if out is not None:
            await message.channel.send(out, reference=message)

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
            await ctx.respond('You do not have permission to open a REPL session.')
            return
        if not user_can_save:
            new = True  # force new session if user can't save, to avoid confusion

        if guild_id not in self.active_sessions:
            self.active_sessions[guild_id] = {}

        if not new:
            if user_id in self.active_sessions[guild_id]:
                await ctx.respond(f'You already have an active REPL session.\n{CODING_INSTRUCTIONS}')
                return
            # try to restore a pickled session from the DB
            session = load_session(guild_id, user_id)
            if session is not None:
                self.active_sessions[guild_id][user_id] = session
                await ctx.respond(f'Saved REPL session loaded.\n{CODING_INSTRUCTIONS}')
                return

        # No saved session found or user requested a fresh one
        delete_session(guild_id, user_id)  # clear any existing saved session
        session = SafeSession(user_perms)
        self.active_sessions[guild_id][user_id] = session
        await ctx.respond(f'New REPL session started.\n{CODING_INSTRUCTIONS}')
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
            await ctx.respond('No active REPL session to close.')
            return

        save &= user_can_save  # only save if user has permission, otherwise just close
        if save:
            try:
                save_session(guild_id, user_id, current)
            except Exception as e:
                await ctx.respond(f'Failed to save session, aborting close: {e}')
                return

        self.active_sessions[guild_id].pop(user_id, None)
        await ctx.respond(f'REPL session {"saved and " if save else ""}closed.')


async def _execute_code(
    self,
    message: discord.Message,
    user_id: str,
    session: SafeSession,
    code: str,
) -> str | None:
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
    lock = self.session_locks.setdefault(user_id, asyncio.Lock())
    await message.channel.trigger_typing()
    async with lock:
        try:
            result, stdout = await loop.run_in_executor(None, _exec)
        except Exception as e:
            await message.channel.send(f"Error during execution: {e}")
            return None

    parts: list[str] = []
    if stdout:
        parts.append(stdout)
    if result is not None:
        parts.append(repr(result))

    if not parts:
        await message.add_reaction("✅")
        return None

    out = "\n".join(parts)
    # Discord message limit ~2000 chars; keep room for fences
    if len(out) > 1900:
        out = out[:1900] + "..."
    return f"```\n{out}\n```"

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


def setup(bot: commands.Bot):
    bot.add_cog(REPLCog(bot))
