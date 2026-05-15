
"""Discord REPL commands for end users."""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from repl_helpers.link_text import scope_text, user_scope_text
from repl_helpers.repl_text import REPL_CODING_INSTRUCTIONS, REPL_HELP_LINES
from repl_helpers.user_session import DEFAULT_INPUT_NAME_TEMPLATE
from repl_helpers.runtime import execute_code, extract_repl_code
from repl_helpers.support import (
    DICE_INIT_CODE,
    IMPORT_VIEWS,
    OPEN_SESSION_SOURCES,
    VARIABLE_SOURCES,
    ReplSessionService,
)

from .bot_db import delete_repl_session, load_repl_session, save_repl_session

_LOGGER = logging.getLogger(__name__)


class REPLCog(commands.Cog):
    """Cog containing end-user REPL commands."""

    HELP_TEXT = "\n".join(REPL_HELP_LINES)
    repl = discord.SlashCommandGroup("repl", "Commands for managing your Python REPL session")

    def __init__(self, bot: commands.Bot) -> None:
        """Store the bot instance and shared REPL state.

        Args:
            bot: Running bot instance.
        """
        self.bot = bot
        self.state = ReplSessionService(bot)

    def cog_unload(self) -> None:
        """Release REPL HTTP resources when unloading the cog."""
        self.state.unload()

    async def graceful_shutdown(self) -> None:
        """Persist saveable sessions during bot shutdown."""
        await self.state.graceful_shutdown()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Execute REPL code for users with an open session."""
        if message.author.bot:
            return

        session = self.state.active_sessions.get(self.state.message_session_key(message))
        if session is None:
            return

        code, input_name = extract_repl_code(message.content)
        if code is None:
            return

        await execute_code(self.state, message, session, code, input_name=input_name)

    @repl.command(name="open", description="Open a Python REPL session")
    @discord.option(
        "source",
        description="How to open the session: auto, fresh, or saved",
        required=False,
        choices=OPEN_SESSION_SOURCES,
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
        source: str = "auto",
        init_dice_vars: bool = True,
        show_instructions: bool = False,
    ) -> None:
        """Open a REPL for the caller."""
        session_key = self.state.session_key(ctx)
        opened_session = self.state.build_opened_session(ctx, source=source)
        if opened_session is None:
            await ctx.respond("No saved REPL session found.", ephemeral=True)
            return

        made, user_session = opened_session
        if init_dice_vars and made != "Saved":
            user_session.exec(DICE_INIT_CODE, input_name="<session init>")

        self.state.active_sessions[session_key] = user_session
        _LOGGER.info(
            "%s REPL session opened for %s: %s",
            made,
            user_scope_text(ctx.author, ctx.guild),
            self.state.describe_session(user_session),
        )
        await ctx.respond(f"{made} REPL session started.", ephemeral=True)
        if show_instructions:
            await ctx.respond(REPL_CODING_INSTRUCTIONS, ephemeral=True)

    @repl.command(name="instructions", description="Show the REPL coding instructions")
    async def show_instructions(self, ctx: discord.ApplicationContext) -> None:
        """Show the REPL coding instructions for the caller."""
        await ctx.respond(REPL_CODING_INSTRUCTIONS, ephemeral=True)

    @repl.command(name="close", description="Close your current REPL session")
    @discord.option(
        "save",
        description="Save this REPL session for later (True) or discard it (False)",
        required=False,
    )
    async def close_session(self, ctx: discord.ApplicationContext, save: bool = True) -> None:
        """Save and close the caller's REPL session."""
        session_key = self.state.session_key(ctx)
        session = self.state.get_active_session(ctx)
        if session is None:
            await ctx.respond("No active REPL session to close.", ephemeral=True)
            return

        should_save = save and session.can_save
        if should_save:
            try:
                save_repl_session(*session_key, session)
            except Exception as error:
                await ctx.respond(f"Failed to save session, aborting close: {error}", ephemeral=True)
                return

        self.state.active_sessions.pop(session_key)
        _LOGGER.info(
            "REPL session closed for %s [saved=%s]",
            user_scope_text(ctx.author, ctx.guild),
            should_save,
        )
        await ctx.respond(f"REPL session{' saved and' if should_save else ''} closed.", ephemeral=True)

    @repl.command(name="save", description="Save your current active REPL session without closing it")
    async def save_session(self, ctx: discord.ApplicationContext) -> None:
        """Persist the caller's active REPL session without closing it."""
        session = await self.state.require_active_session(
            ctx,
            missing_message="No active REPL session to save.",
        )
        if session is None:
            return
        if not session.can_save:
            await ctx.respond("Your REPL session cannot be saved.", ephemeral=True)
            return

        save_repl_session(*self.state.session_key(ctx), session)
        await ctx.respond("REPL session saved.", ephemeral=True)

    @repl.command(name="delete", description="Delete your saved REPL session")
    async def delete_saved_session(self, ctx: discord.ApplicationContext) -> None:
        """Delete the caller's saved REPL session."""
        delete_repl_session(*self.state.session_key(ctx))
        await ctx.respond("Saved REPL session deleted.", ephemeral=True)

    @repl.command(name="status", description="Show the current active and saved REPL session status")
    async def show_session_status(self, ctx: discord.ApplicationContext) -> None:
        """Show the caller's current active and saved REPL session status."""
        session_key = self.state.session_key(ctx)
        active_session = self.state.get_active_session(ctx)
        saved_session = load_repl_session(*session_key)

        parts = [f"Scope: {scope_text(ctx.guild)}"]
        parts.append(
            "Active session: none"
            if active_session is None
            else f"Active session: {self.state.describe_session(active_session)}"
        )
        parts.append(
            "Saved session: none"
            if saved_session is None
            else f"Saved session: {self.state.describe_session(saved_session)}"
        )
        await ctx.respond("\n".join(parts), ephemeral=True)

    @repl.command(
        name="input_name",
        description="Set or reset the default traceback input-name template for your active REPL session",
    )
    @discord.option(
        "name",
        description="Template for traceback input names. Leave blank to reset. Use {count} for a per-template counter.",
        required=False,
    )
    async def configure_input_name(
        self,
        ctx: discord.ApplicationContext,
        name: str | None = None,
    ) -> None:
        """Set or reset the active session's default traceback input-name template."""
        session = await self.state.require_active_session(ctx)
        if session is None:
            return

        if name is None or not name.strip():
            session.reset_input_name_template()
            await ctx.respond(
                f"Default traceback input name reset to {DEFAULT_INPUT_NAME_TEMPLATE!r}.",
                ephemeral=True,
            )
            return

        session.set_input_name_template(name)
        await ctx.respond(
            f"Default traceback input name set to {session.input_name_template!r}.",
            ephemeral=True,
        )

    @repl.command(name="permissions", description="Show your effective REPL permission level")
    async def show_permissions(self, ctx: discord.ApplicationContext) -> None:
        """Respond with the caller's effective permissions and save capability."""
        perms, can_save = self.state.resolve_permissions(ctx)
        base = getattr(perms, "base_perms", None)
        base_str = str(base) if base is not None else repr(perms)
        await ctx.respond(f"Permission level: {base_str}\nCan save sessions: {can_save}", ephemeral=True)

    @repl.command(name="imports", description="Show imports enabled for your session or allowed by policy")
    @discord.option(
        "view",
        description="Which imports to show: session or policy",
        required=False,
        choices=IMPORT_VIEWS,
    )
    async def show_imports(self, ctx: discord.ApplicationContext, view: str = "session") -> None:
        """Respond with session-enabled or policy-allowed imports."""
        perms, _can_save = self.state.resolve_permissions(ctx)
        if view == "session":
            imports = getattr(perms, "imports", None)
            if not imports:
                await ctx.respond("No imports are available.", ephemeral=True)
                return

            modules = {module_name for module_name, _alias in imports}
            sections = self.state.format_import_sections(modules)
            await ctx.respond("Imports enabled for this session:\n\n" + "\n\n".join(sections), ephemeral=True)
            return

        max_level, modules = self.state.get_policy_allowed_modules(perms)
        if max_level is None:
            await ctx.respond("Could not determine your REPL permission level.", ephemeral=True)
            return

        sections = self.state.format_import_sections(modules)
        if not sections:
            await ctx.respond("No policy imports are available at your current permission level.", ephemeral=True)
            return

        await ctx.respond(
            f"Imports allowed by the REPL policy at level {max_level}:\n\n" + "\n\n".join(sections),
            ephemeral=True,
        )

    @repl.command(name="variables", description="List variables from your REPL session")
    @discord.option(
        "values",
        description="Whether to include variable values in the listing (True) or just names (False)",
        required=False,
    )
    @discord.option(
        "source",
        description="Which session to inspect: active, saved, or both",
        required=False,
        choices=VARIABLE_SOURCES,
    )
    async def list_session_vars(
        self,
        ctx: discord.ApplicationContext,
        values: bool = False,
        source: str = "active",
    ) -> None:
        """List variables from the caller's active session, saved session, or both."""
        session_key = self.state.session_key(ctx)
        active_session = self.state.get_active_session(ctx) if source in {"active", "both"} else None
        saved_session = load_repl_session(*session_key) if source in {"saved", "both"} else None

        sections: list[str] = []
        if active_session is not None:
            sections.append("Active REPL session found.\n" + active_session.print_user_vars(include_values=values))
        if saved_session is not None:
            sections.append("Saved session found.\n" + saved_session.print_user_vars(include_values=values))

        if not sections:
            await ctx.respond(f"No {source} REPL session found.", ephemeral=True)
            return

        await ctx.respond("\n\n".join(sections), ephemeral=True)


def setup(bot: commands.Bot) -> None:
    """Register the REPL cog."""
    bot.add_cog(REPLCog(bot))
