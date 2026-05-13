"""SQLite-backed persistence helpers for REPL sessions and permissions.

The module stores Python objects as pickled blobs in SQLite. That is only
safe for trusted data.
"""

from __future__ import annotations

import logging
import os
import pickle
import sqlite3
import time
from collections.abc import Iterable
from typing import Any

import discord
from discord.ext import commands

from defines.user_session import UserSession
from respy_repl import Permissions

_DB_PATH: str | None = None
_CONN: sqlite3.Connection | None = None



def init_db(path: str | None = None) -> None:
    """Initialize the SQLite database and create required tables."""
    global _DB_PATH, _CONN

    if path is None:
        path = os.path.join(os.getcwd(), "bot_data.db")

    _DB_PATH = path
    print(f"Initializing REPL database at: {_DB_PATH}")
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


#def _encode_permission_scope(guild_id: int | None) -> int:
#    """Map DM scope to a stable non-Discord sentinel for storage."""
#    return DM_PERMISSION_SCOPE_ID if guild_id is None else guild_id


#def _decode_permission_scope(stored_guild_id: int | str | None) -> int | None:
#    """Decode stored guild scope back into a guild ID or DM scope."""
#    if stored_guild_id is None:
#        return None
#
#    guild_id = int(stored_guild_id)
#    return None if guild_id == DM_PERMISSION_SCOPE_ID else guild_id


def save_repl_session(user_id: int, guild_id: int | None, session: Any) -> None:
    """Persist a REPL session for a user and optional guild."""
    conn, cursor = _get_conn()
    blob = pickle.dumps(session)
    ts = time.time()
    print(f"Saving REPL session for user ID {user_id}, guild ID {guild_id} ({len(blob)} bytes)")
    cursor.execute(
        "REPLACE INTO sessions(user_id, guild_id, data, updated) VALUES (?, ?, ?, ?)",
        (user_id, guild_id, blob, ts),
    )
    conn.commit()


def load_repl_session(user_id: int, guild_id: int | None = None) -> UserSession | None:
    """Load a saved REPL session for a user and optional guild."""
    conn, cursor = _get_conn()
    row = cursor.execute(
        "SELECT data FROM sessions WHERE user_id = ? AND guild_id IS ?",
        (user_id, guild_id),
    ).fetchone()
    if not row:
        scope = f"guild ID {guild_id}" if guild_id is not None else "DM"
        print(f"No saved REPL session found for user ID {user_id} in {scope}")
        return None

    try:
        session = pickle.loads(row[0])
        session.user_id = user_id
        session.guild_id = guild_id
        scope = f"guild ID {guild_id}" if guild_id is not None else "DM"
        print(f"Loaded REPL session for user ID {user_id} in {scope}")
        return session
    except Exception:
        scope = f"guild ID {guild_id}" if guild_id is not None else "DM"
        logging.exception(
            "Failed to unpickle REPL session for user ID %s in %s",
            user_id,
            scope,
        )
        return None


def delete_repl_session(user_id: int, guild_id: int | None) -> None:
    """Delete a saved REPL session for a user and optional guild."""
    conn, cursor = _get_conn()
    cursor.execute(
        "DELETE FROM sessions WHERE user_id = ? AND guild_id IS ?",
        (user_id, guild_id),
    )
    conn.commit()
    scope = f"guild ID {guild_id}" if guild_id is not None else "DM"
    print(f"Deleted saved REPL session for user ID {user_id} in {scope}")


def list_repl_sessions(
    guild_id: int | None = None,
    *,
    include_all_scopes: bool = False,
) -> list[tuple[int, int | None, float]]:
    """List saved REPL sessions for one scope or for all scopes."""
    conn, cursor = _get_conn()
    if include_all_scopes:
        rows = cursor.execute(
            "SELECT user_id, guild_id, updated FROM sessions ORDER BY updated DESC"
        ).fetchall()
    else:
        rows = cursor.execute(
            "SELECT user_id, guild_id, updated FROM sessions WHERE guild_id IS ? ORDER BY updated DESC",
            (guild_id,),
        ).fetchall()

    return [
        (
            int(row[0]),
            row[1] if row[1] is None else int(row[1]),
            float(row[2]),
        )
        for row in rows
    ]


def save_repl_permissions(
    guild_id: int | None,
    role_id: int | None = None,
    permissions: Permissions | None = None,
    can_save: bool = False,
) -> None:
    """Persist REPL permissions for a guild role or DM user."""
    # stored_guild_id = _encode_permission_scope(guild_id)
    if role_id is None:
        role_id = guild_id

    conn, cursor = _get_conn()
    blob = pickle.dumps(permissions)
    ts = time.time()
    cursor.execute(
        "REPLACE INTO guild_permissions(guild_id, role_id, data, can_save, updated) VALUES (?, ?, ?, ?, ?)",
        (guild_id, role_id, blob, can_save, ts),
    )
    conn.commit()


def _load_permission_rows(
    guild_id: int | None,
    role_ids: Iterable[int] | None = None,
) -> list[tuple[Permissions | None, bool]]:
    # stored_guild_id = _encode_permission_scope(guild_id)
    if role_ids is None:
        if guild_id is None:
            return [(None, False)]
        role_ids = [guild_id]

    role_id_set = {int(role_id) for role_id in role_ids}
    conn, cursor = _get_conn()
    rows = cursor.execute(
        "SELECT role_id, data, can_save FROM guild_permissions WHERE guild_id IS ?",
        (guild_id,),
    ).fetchall()

    permissions: list[tuple[Permissions | None, bool]] = []
    for role_id, data, can_save in rows:
        if int(role_id) not in role_id_set:
            continue

        try:
            permissions.append((pickle.loads(data), bool(can_save)))
        except Exception:
            scope = f"guild ID {guild_id}" if guild_id is not None else "DM"
            logging.exception(
                "Failed to unpickle REPL permissions for %s, role ID %s",
                scope,
                role_id,
            )

    return permissions or [(None, False)]


def get_effective_repl_permissions(ctx: discord.ApplicationContext) -> tuple[Permissions, bool]:
    """Merge the caller's effective permissions from guild roles or DM entries."""
    if ctx.guild_id:
        role_ids = [role.id for role in ctx.author.roles]  # type: ignore[attr-defined]
    else:
        role_ids = [ctx.author.id]

    permissions, can_save_flags = zip(*_load_permission_rows(ctx.guild_id, role_ids))
    return Permissions.permissive_merge(*permissions), any(can_save_flags)


def delete_repl_permissions(guild_id: int | None, role_id: int | None = None) -> None:
    """Delete REPL permissions for a role or for the whole guild/DM scope."""
    if guild_id is None and role_id is None:
        raise ValueError("guild_id or role_id must be provided")

    # stored_guild_id = _encode_permission_scope(guild_id)
    conn, cursor = _get_conn()
    if role_id is None:
        cursor.execute("DELETE FROM guild_permissions WHERE guild_id IS ?", (guild_id,))
    else:
        cursor.execute(
            "DELETE FROM guild_permissions WHERE guild_id IS ? AND role_id = ?",
            (guild_id, role_id),
        )
    conn.commit()


def list_repl_permissions(
    guild_id: int | None = None,
    *,
    include_all_scopes: bool = False,
) -> list[tuple[int | None, int, Permissions, bool, float]]:
    """List stored REPL permissions for one scope or for all scopes."""
    conn, cursor = _get_conn()
    if include_all_scopes:
        rows = cursor.execute(
            "SELECT guild_id, role_id, data, can_save, updated FROM guild_permissions ORDER BY updated DESC"
        ).fetchall()
    else:
        rows = cursor.execute(
            "SELECT guild_id, role_id, data, can_save, updated FROM guild_permissions WHERE guild_id IS ? ORDER BY updated DESC",
            (guild_id,),
        ).fetchall()

    entries: list[tuple[int | None, int, Permissions, bool, float]] = []
    for row_guild_id, row_role_id, row_data, can_save, updated in rows:
        try:
            entries.append(
                (
                    int(row_guild_id),
                    int(row_role_id),
                    pickle.loads(row_data),
                    bool(can_save),
                    float(updated),
                )
            )
        except Exception:
            scope = f"guild ID {row_guild_id}" if row_guild_id is not None else "DM"
            logging.exception(
                "Failed to unpickle REPL permissions for %s, role ID %s",
                scope,
                row_role_id,
            )

    return entries


def setup(bot: commands.Bot) -> None:
    """Initialize the database when the extension is loaded."""
    del bot
    init_db()


init_db()
