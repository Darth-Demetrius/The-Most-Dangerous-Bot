"""Logging setup helpers for bot runtime and discord logs."""

from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path
import sys
from typing import TextIO


class _MirrorStream:
    """Tee writes to a terminal stream and a log file simultaneously.

    Terminal output is written as-is. Log file output is prefixed with a
    timestamp at the start of each line.
    """

    def __init__(self, terminal: TextIO, log_file: TextIO) -> None:
        self._terminal = terminal
        self._log_file = log_file
        self._at_line_start = True

    def write(self, data: str) -> int:
        self._terminal.write(data)
        for chunk in data.splitlines(keepends=True):
            if self._at_line_start:
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self._log_file.write(f"[{ts}] ")
            self._log_file.write(chunk)
            self._at_line_start = chunk.endswith("\n")
        return len(data)

    def flush(self) -> None:
        self._terminal.flush()
        self._log_file.flush()

    def isatty(self) -> bool:
        return self._terminal.isatty()


def _archive_if_nonempty(log_path: Path) -> None:
    """Archive an existing log file to a timestamped name if non-empty.

    If the file exists but is empty it is deleted. If it is non-empty it is
    renamed using the file's modification time as the timestamp, so the
    archive name reflects when the previous session actually ran.

    Args:
        log_path: Path to the unstamped active log file.
    """
    if not log_path.exists():
        return
    if log_path.stat().st_size == 0:
        log_path.unlink()
        return
    mtime = datetime.fromtimestamp(log_path.stat().st_mtime)
    timestamp = mtime.strftime("%Y%m%d-%H%M%S")
    archive_path = log_path.parent / f"{log_path.stem}-{timestamp}.log"
    # Avoid collision if two sessions ran within the same second.
    counter = 1
    while archive_path.exists():
        archive_path = log_path.parent / f"{log_path.stem}-{timestamp}-{counter}.log"
        counter += 1
    log_path.rename(archive_path)


def configure_process_logging(project_root: Path) -> logging.Handler:
    """Configure runtime and discord logging.

    At startup any prior ``bot.log`` / ``discord.log`` that is non-empty is
    archived to a timestamped file derived from its modification time. Empty
    files are silently discarded. Fresh ``bot.log`` and ``discord.log`` are
    always created for the current session.

    Args:
        project_root: Repository root path.

    Returns:
        A configured logging handler for discord.py.
    """
    log_dir = project_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    runtime_log_path = log_dir / "bot.log"
    discord_log_path = log_dir / "discord.log"

    _archive_if_nonempty(runtime_log_path)
    _archive_if_nonempty(discord_log_path)

    runtime_log_file = runtime_log_path.open("w", encoding="utf-8", buffering=1)
    sys.stdout = _MirrorStream(sys.__stdout__, runtime_log_file)  # type: ignore[assignment]
    sys.stderr = _MirrorStream(sys.__stderr__, runtime_log_file)  # type: ignore[assignment]

    handler = logging.FileHandler(discord_log_path, encoding="utf-8", mode="w")
    handler.setFormatter(
        logging.Formatter("%(asctime)s:%(levelname)s:%(name)s: %(message)s")
    )
    return handler
