"""Simple SQLite-backed DB helpers for bot data (REPL sessions).

This module stores Python objects as pickled blobs in SQLite. Use only
for trusted data: unpickling arbitrary data is unsafe.

Public functions:
- `init_db(path)` to initialize the database file
- `save_session(user_id, session)` to persist a session
- `load_session(user_id)` to restore a session
- `delete_session(user_id)` to remove a persisted session
- `list_sessions()` to list saved user ids
 - `save_guild_permission(guild_id, role_id, perms)` to persist per-guild role permissions
 - `load_guild_permission(guild_id, role_id)` to restore per-guild role permissions
 - `delete_guild_permission(guild_id, role_id=None)` to remove per-guild role permissions
 - `list_guild_permissions(guild_id=None)` to list stored guild/role permissions
"""
from __future__ import annotations
from discord.ext import commands

import os
import sqlite3
import pickle
import time
from typing import Any

_DB_PATH: str | None = None
_CONN: sqlite3.Connection | None = None

class DBCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot


def init_db(path: str | None = None) -> None:
    """Initialize the SQLite database and create required tables.

    Args:
        path: Path to the SQLite database file. Defaults to
            ./bot_data.db in the current working directory.
    """
    global _DB_PATH, _CONN
    if path is None:
        path = os.path.join(os.getcwd(), "bot_data.db")
    _DB_PATH = path
    _CONN = sqlite3.connect(_DB_PATH, check_same_thread=False)
    _CONN.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            user_id TEXT NOT NULL,
            guild_id TEXT,
            data BLOB NOT NULL,
            updated REAL NOT NULL,
            PRIMARY KEY(user_id, guild_id)
        )
        """
    )
    _CONN.commit()

    # Ensure guild_permissions table exists for per-guild/role permission storage
    _CONN.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_permissions (
            guild_id TEXT NOT NULL,
            role_id TEXT NOT NULL,
            data BLOB NOT NULL,
            can_save INTEGER NOT NULL DEFAULT 0,
            updated REAL NOT NULL,
            PRIMARY KEY(guild_id, role_id)
        )
        """
    )
    _CONN.commit()

    # Migrate older DBs that predate the `can_save` column.
    cols = [row[1] for row in _CONN.execute("PRAGMA table_info(guild_permissions)").fetchall()]
    if "can_save" not in cols:
        _CONN.execute(
            "ALTER TABLE guild_permissions ADD COLUMN can_save INTEGER NOT NULL DEFAULT 1"
        )
        _CONN.commit()


def _get_conn() -> tuple[sqlite3.Connection, sqlite3.Cursor]:
    global _CONN
    if _CONN is None:
        init_db()
    assert _CONN is not None
    return _CONN, _CONN.cursor()


def save_session(guild_id: str | None, user_id: str, session: Any) -> None:
    """Persist a Python object as the saved session for `user_id` in `guild_id`.

    This uses pickle to serialize `session` into the database.
    """
    conn, cursor = _get_conn()
    blob = pickle.dumps(session)
    ts = time.time()
    # Use REPLACE to upsert the row for portability across SQLite versions.
    cursor.execute(
        "REPLACE INTO sessions(user_id, guild_id, data, updated) VALUES (?, ?, ?, ?)",
        (user_id, guild_id, blob, ts),
    )
    conn.commit()


def load_session(guild_id: str | None, user_id: str) -> Any | None:
    """Load and return the saved session for `user_id` in `guild_id`, or None if missing.

    If `guild_id` is None this will load the session with a NULL guild_id (aka dm).
    """
    conn, cursor = _get_conn()
    user_session = cursor.execute(
        "SELECT data FROM sessions WHERE user_id = ? AND guild_id IS ?",
        (user_id, guild_id),
    )
    row = user_session.fetchone()
    if not row:
        return None
    try:
        return pickle.loads(row[0])
    except Exception:
        return None


def delete_session(guild_id: str | None, user_id: str) -> None:
    """Delete the saved session for `user_id` in `guild_id` if it exists.

    If `guild_id` is None this will delete the NULL-guild row (aka dm).
    """
    conn, cursor = _get_conn()
    cursor.execute("DELETE FROM sessions WHERE user_id = ? AND guild_id IS ?", (user_id, guild_id))
    conn.commit()


def list_sessions() -> list[tuple[str, str | None]]:
    """Return a list of tuples `(user_id, guild_id)` for saved sessions."""
    conn, cursor = _get_conn()
    sessions = cursor.execute("SELECT user_id, guild_id FROM sessions")
    return [(row[0], row[1]) for row in sessions.fetchall()]


def save_permissions(
    guild_id: str | None,
    role_id: str | None = None,
    perms: Any = 0,
    can_save: bool = False
) -> None:
    """
    Persist a Permissions (or PermissionLevel) object for a guild/role.
    
    If `guild_id` is None, role_id will act as user_id for a DM permission entry.
    If `role_id` is None, will set `role_id` to `guild_id` (@everyone role) for a guild default entry.
    If both `guild_id` and `role_id` are None, will store a global default entry.
    """
    if role_id is None:
        role_id = guild_id
    conn, cursor = _get_conn()
    blob = pickle.dumps(perms)
    ts = time.time()
    cursor.execute(
        "REPLACE INTO guild_permissions(guild_id, role_id, data, can_save, updated) VALUES (?, ?, ?, ?, ?)",
        (guild_id, role_id, blob, can_save, ts),
    )
    conn.commit()


def fetch_permission(guild_id: str | None, role_id: str | None = None) -> tuple[Any | None, bool]:
    """Load persisted permissions for a guild/role, or None if missing.

    If `guild_id` is None, role_id will act as user_id for a DM permission entry.
    If `role_id` is None, will set `role_id` to `guild_id` (@everyone role).
    """
    if role_id is None:
        role_id = guild_id
    conn, cursor = _get_conn()
    cursor.execute(
        "SELECT data, can_save FROM guild_permissions WHERE guild_id IS ? AND role_id IS ?",
        (guild_id, role_id),
    )
    row = cursor.fetchone()
    if not row:
        return None, False
    data, can_save = row
    try:
        return pickle.loads(data), bool(can_save)
    except Exception:
        return None, False


def fetch_user_guild_permissions(
    guild_id: str,
    user_role_ids: list[str]
) -> tuple[list[Any], bool]:
    """Fetch and merge permissions for a user based on their guild roles.

    This will fetch permissions for each role in `user_role_ids` and merge them together.
    If `guild_id` is None, this will look for DM permission entries with role_id=user_id.
    """
    user_perms = []
    can_save = False
    for role_id in user_role_ids:
        role_perms, role_can_save = fetch_permission(guild_id, role_id)
        if role_perms is not None:
            user_perms.append(role_perms)
            can_save |= role_can_save
    return user_perms, can_save


def delete_permissions(guild_id: str | None, role_id: str | None = None) -> None:
    """Delete a guild/role permission entry. If `role_id` is None delete all for guild.
    
    If `guild_id` is None, role_id will act as user_id for a DM permission entry.
    """
    if guild_id is None and role_id is None:
        raise ValueError("Must specify at least guild_id or role_id to delete a permission entry.")

    conn, cursor = _get_conn()
    if role_id is None:
        cursor.execute("DELETE FROM guild_permissions WHERE guild_id IS ?", (guild_id,))
    else:
        cursor.execute("DELETE FROM guild_permissions WHERE guild_id IS ? AND role_id = ?", (guild_id, role_id))
    conn.commit()


def list_permissions(guild_id: str | None = ...) -> list[tuple[str, str]]:  # type: ignore[assignment]
    """Return list of `(guild_id, role_id)` tuples for stored permissions.

    If `guild_id` is provided, only returns entries for that guild.
    If `guild_id` is None, role_id will act as user_id for a DM permission entry.
    """
    conn, cursor = _get_conn()
    if guild_id is ...:
        cur = cursor.execute("SELECT guild_id, role_id FROM guild_permissions")
    else:
        cur = cursor.execute("SELECT guild_id, role_id FROM guild_permissions WHERE guild_id IS ?", (guild_id,))
    return [(r[0], r[1]) for r in cur.fetchall()]


def setup(bot: commands.Bot) -> None:
    bot.add_cog(DBCog(bot))

# initialize default DB on import
init_db()
