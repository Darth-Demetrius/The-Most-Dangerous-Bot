"""Shared mention/link formatting helpers for users, roles, channels, and scopes."""

from __future__ import annotations

import discord

UserLike = discord.User | discord.Member | int
RoleLike = discord.Role | int
ChannelLike = discord.TextChannel | int
ScopeLike = discord.Guild | int | None


def in_scope_text(entity_text: str, guild: ScopeLike) -> str:
    """Return standardized '<entity> in <scope>' text."""
    return f"{entity_text} in {scope_text(guild)}"


def user_link(user: UserLike) -> str:
    """Return a mention-style link to a user."""
    user_id = user.id if isinstance(user, (discord.User, discord.Member)) else user
    return f"<@{user_id}>"


def user_text(
    user: UserLike,
) -> str:
    """Return a readable label for a user."""
    if isinstance(user, (discord.User, discord.Member)):
        return f"'@{user.name}' {user_link(user)}"

    return user_link(user)


def user_scope_text(user: UserLike, guild: ScopeLike) -> str:
    """Return standardized user text within a scope."""
    return in_scope_text(user_text(user), guild)


def role_link(role: RoleLike) -> str:
    """Return a mention-style link to a role."""
    if not role or (isinstance(role, discord.Role) and role.is_default()):
        return "@everyone"

    role_id = role.id if isinstance(role, discord.Role) else role
    return f"<@&{role_id}>"


def role_text(
    role: RoleLike,
) -> str:
    """Return a readable label for a role."""
    if isinstance(role, discord.Role):
        if role.is_default():
            return "@everyone"
        return f"'@{role.name}' {role_link(role)}"

    return role_link(role)


def role_scope_text(role: RoleLike, guild: ScopeLike) -> str:
    """Return standardized role text within a scope."""
    return in_scope_text(role_text(role), guild)


def channel_link(channel: ChannelLike) -> str:
    """Return a mention-style link to a channel."""
    channel_id = channel.id if isinstance(channel, discord.TextChannel) else channel
    return f"<#{channel_id}>"


def channel_text(
    channel: ChannelLike | None,
) -> str:
    """Return a readable label for a channel or DM."""
    if channel is None:
        return "DM"

    if isinstance(channel, discord.TextChannel):
        return f"'#{channel.name}' {channel_link(channel)}"

    return channel_link(channel)


def scope_text(
    guild: ScopeLike,
) -> str:
    """Return a readable label for a guild or DM scope."""
    if guild is None:
        return "DM"

    if isinstance(guild, discord.Guild):
        return f"guild '{guild.name}' ({guild.id})"

    return f"guild ({guild})"
