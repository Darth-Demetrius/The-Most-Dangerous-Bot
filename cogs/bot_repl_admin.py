"""Discord REPL admin commands."""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from respy_repl import Permissions

from repl_helpers.link_text import role_link, role_scope_text, scope_text, user_link, user_scope_text
from repl_helpers.repl_text import REPL_ADMIN_HELP_LINES
from repl_helpers.support import cached_guild_or_id, cached_user_or_id, format_updated_at

from .bot_db import (
    delete_repl_permissions,
    delete_repl_session,
    list_repl_permissions,
    list_repl_sessions,
    save_repl_permissions,
)

_LOGGER = logging.getLogger(__name__)
_DEFAULT_ROLE_IMPORTS = ["math", "random", "MyDyce", "MyDyce:P,H"]
_PERMISSION_LEVEL_CHOICES = [0, 1, 2, 3]


class REPLAdminCog(commands.Cog):
    """Cog containing REPL admin and audit commands."""

    HELP_TEXT = "\n".join(REPL_ADMIN_HELP_LINES)
    repl_admin = discord.SlashCommandGroup(
        "repl_admin",
        "Admin commands for REPL permissions and saved sessions",
    )

    def __init__(self, bot: commands.Bot) -> None:
        """Store the running bot instance.

        Args:
            bot: Running bot instance.
        """
        self.bot = bot

    def _permission_target_text(
        self,
        ctx: discord.ApplicationContext,
        guild_id: int | None,
        role_id: int,
    ) -> str:
        """Return one display string for a permission target."""
        if guild_id is not None:
            resolved_role = ctx.guild.get_role(role_id) if ctx.guild is not None else None
            return role_link(resolved_role or role_id)
        return user_link(cached_user_or_id(self.bot, role_id))

    @repl_admin.command(
        name="set_permissions",
        description="Set the REPL permission level for a specific guild role (owner only)",
    )
    @discord.option("guild_role", description="The guild role to set permissions for.", required=True)
    @discord.option(
        "permission_level",
        description="The permission level to set (0-3, higher is more permissive)",
        required=True,
        choices=_PERMISSION_LEVEL_CHOICES,
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
        """Set the REPL permission level for a specific guild role."""
        if not (0 <= permission_level <= 3):
            await ctx.respond("Permission level must be between 0 and 3.", ephemeral=True)
            return

        save_repl_permissions(
            ctx.guild_id,
            guild_role.id,
            Permissions(perm_level=permission_level, imports=_DEFAULT_ROLE_IMPORTS),
            can_save=permission_level >= 2 if can_save is None else can_save,
        )
        await ctx.respond(
            f"Set permission level for role {role_link(guild_role)} to {permission_level}.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        _LOGGER.info(
            "Set permissions for %s to level %s",
            role_scope_text(guild_role, ctx.guild),
            permission_level,
        )

    @repl_admin.command(
        name="list_permissions",
        description="List stored REPL permissions for the current guild or DM scope",
    )
    async def list_role_permissions(self, ctx: discord.ApplicationContext) -> None:
        """List stored permissions for the current guild or DM scope."""
        perms_list = list_repl_permissions(ctx.guild_id)
        if not perms_list:
            await ctx.respond("No permissions found for this guild or DM.", ephemeral=True)
            return

        lines: list[str] = []
        for guild_id, role_id, perms, can_save, updated in perms_list:
            if perms is None:
                continue

            if guild_id is not None and role_id == guild_id:
                role_id = 0
            mention = self._permission_target_text(ctx, guild_id, role_id)

            lines.append(
                f"{mention}:  "
                f"Permissions: {getattr(perms, 'base_perms', perms)}  |  "
                f"Can Save: {bool(can_save)}  |  "
                f"Last Updated: {format_updated_at(updated)}"
            )

        await ctx.respond(
            "Stored permissions:\n" + "\n".join(lines),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @repl_admin.command(
        name="delete_permissions",
        description="Delete stored REPL permissions for the current guild",
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
            await ctx.respond("Deleted all REPL permissions for this server.", ephemeral=True)
            return

        delete_repl_permissions(ctx.guild_id, guild_role.id)
        await ctx.respond(
            f"Deleted REPL permissions for {role_link(guild_role)}.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @repl_admin.command(
        name="saved_sessions",
        description="List saved REPL sessions for the current guild or DM scope",
    )
    async def list_saved_sessions(self, ctx: discord.ApplicationContext) -> None:
        """List saved REPL sessions for the current scope."""
        sessions = list_repl_sessions(ctx.guild_id, include_all_scopes=False)
        if not sessions:
            await ctx.respond("No saved REPL sessions found.", ephemeral=True)
            return

        lines = [
            (
                f"User: {user_link(cached_user_or_id(self.bot, user_id))}  |  "
                f"Scope: {scope_text(cached_guild_or_id(self.bot, row_guild_id))}  |  "
                f"Last Updated: {format_updated_at(updated)}"
            )
            for user_id, row_guild_id, updated in sessions
        ]
        await ctx.respond(
            "Saved REPL sessions:\n" + "\n".join(lines),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @repl_admin.command(
        name="purge_session",
        description="Delete a saved REPL session for a user in the current scope (owner only)",
    )
    @discord.option("user_id", description="The user ID whose saved session should be deleted.", required=True)
    @discord.default_permissions(administrator=True)
    @commands.is_owner()
    async def purge_saved_session(
        self,
        ctx: discord.ApplicationContext,
        user_id: int,
    ) -> None:
        """Delete a saved REPL session for a specific user in the current scope."""
        delete_repl_session(user_id, ctx.guild_id)
        await ctx.respond(
            "Deleted saved REPL session for "
            f"{user_scope_text(cached_user_or_id(self.bot, user_id), cached_guild_or_id(self.bot, ctx.guild_id))}.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


def setup(bot: commands.Bot) -> None:
    """Register the REPL admin cog."""
    bot.add_cog(REPLAdminCog(bot))
