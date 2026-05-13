"""Logging setup helpers for bot runtime and discord logs."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from pathlib import Path
import re
import sys
from typing import TextIO
from zipfile import ZIP_DEFLATED, ZipFile


LOG_TIMESTAMP_PATTERN = re.compile(r"^.+-(?P<stamp>\d{8}-\d{6})(?:-\d+)?$")
ARCHIVE_AGE_HOURS = 24
MAX_TOP_LEVEL_TIMESTAMPED_LOGS = 5


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


def _rollover_if_nonempty(log_path: Path) -> None:
    """Roll over an existing active log file to a timestamped name if non-empty.

    If the file exists but is empty it is deleted. If it is non-empty it is
    renamed in place using the current time as the timestamp.

    Args:
        log_path: Path to the unstamped active log file.
    """
    if not log_path.exists():
        return
    if log_path.stat().st_size == 0:
        log_path.unlink()
        return
    rolled_at = datetime.now()
    timestamp = rolled_at.strftime("%Y%m%d-%H%M%S")
    rollover_path = log_path.parent / f"{log_path.stem}-{timestamp}.log"
    log_path.replace(rollover_path)


def _timestamp_from_filename(log_path: Path) -> datetime | None:
    """Extract a timestamp from a log filename.

    Expected filename pattern: ``<name>-YYYYMMDD-HHMMSS.log`` with an optional
    numeric collision suffix.

    Args:
        log_path: Path to a log file.

    Returns:
        Parsed timestamp, or ``None`` when the filename does not match.
    """
    match = LOG_TIMESTAMP_PATTERN.match(log_path.stem)
    if not match:
        return None

    try:
        return datetime.strptime(match.group("stamp"), "%Y%m%d-%H%M%S")
    except ValueError:
        return None


def _compress_monthly_logs(archive_dir: Path) -> None:
    """Compress archived logs by month when multiple logs exist for that month.

    If a month zip already exists, new logs for that month are merged into it.

    Args:
        archive_dir: Root archive directory.
    """
    monthly_groups: dict[str, list[Path]] = {}
    for log_file in archive_dir.glob("*.log"):
        if not log_file.is_file():
            continue

        timestamp = _timestamp_from_filename(log_file)
        if timestamp is None:
            continue

        key = timestamp.strftime("%Y-%m")
        monthly_groups.setdefault(key, []).append(log_file)

    for month_key, files in monthly_groups.items():
        zip_path = archive_dir / f"{month_key}.zip"
        if len(files) <= 1 and not zip_path.exists():
            continue

        existing_entries: dict[str, bytes] = {}
        if zip_path.exists():
            with ZipFile(zip_path, "r") as existing_zip:
                for name in existing_zip.namelist():
                    existing_entries[name] = existing_zip.read(name)

        with ZipFile(zip_path, "w", compression=ZIP_DEFLATED) as month_zip:
            for name, data in sorted(existing_entries.items()):
                month_zip.writestr(name, data)
            for log_file in sorted(files, key=lambda item: item.name):
                month_zip.write(log_file, arcname=log_file.name)

        for log_file in files:
            log_file.unlink()


def _archive_stale_logs(log_dir: Path, archive_dir: Path) -> None:
    """Move log files older than ``ARCHIVE_AGE_HOURS`` into ``archive_dir``.

    Empty stale logs are deleted instead of archived.

    Args:
        log_dir: Directory containing active and recent log files.
        archive_dir: Directory used for long-term log archives.
    """
    cutoff = datetime.now() - timedelta(hours=ARCHIVE_AGE_HOURS)
    archive_dir.mkdir(parents=True, exist_ok=True)

    for log_file in log_dir.glob("*.log"):
        if not log_file.is_file():
            continue

        timestamp = _timestamp_from_filename(log_file)
        if timestamp is None:
            continue
        if timestamp >= cutoff:
            continue

        if log_file.stat().st_size == 0:
            log_file.unlink()
            continue

        target_path = archive_dir / log_file.name
        log_file.replace(target_path)


def _archive_excess_top_level_logs(log_dir: Path, archive_dir: Path) -> None:
    """Archive oldest timestamped logs until the top-level count is within limit.

    Only rolled logs with timestamped names are counted. Active ``bot.log`` and
    ``discord.log`` are not counted toward this limit.

    Args:
        log_dir: Directory containing active and rolled logs.
        archive_dir: Directory used for long-term log archives.
    """
    archive_dir.mkdir(parents=True, exist_ok=True)

    timestamped_logs: list[tuple[datetime, Path]] = []
    for log_file in log_dir.glob("*.log"):
        if not log_file.is_file():
            continue

        timestamp = _timestamp_from_filename(log_file)
        if timestamp is None:
            continue

        timestamped_logs.append((timestamp, log_file))

    if len(timestamped_logs) <= MAX_TOP_LEVEL_TIMESTAMPED_LOGS:
        return

    timestamped_logs.sort(key=lambda item: (item[0], item[1].name))
    excess_count = len(timestamped_logs) - MAX_TOP_LEVEL_TIMESTAMPED_LOGS
    for _, log_file in timestamped_logs[:excess_count]:
        target_path = archive_dir / log_file.name
        log_file.replace(target_path)


def configure_process_logging(project_root: Path) -> logging.Handler:
    """Configure runtime and discord logging.

    At startup any prior ``bot.log`` / ``discord.log`` that is non-empty is
    archived to a timestamped file named with the current time. Existing log
    files with timestamped names older than 24 hours are moved into
    ``logs/archive`` and compressed by month (``YYYY-MM.zip``). Empty files are
    silently discarded. Fresh ``bot.log`` and ``discord.log`` are always
    created for the current session.

    Args:
        project_root: Repository root path.

    Returns:
        A configured logging handler for discord.py.
    """
    log_dir = project_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    runtime_log_path = log_dir / "bot.log"
    discord_log_path = log_dir / "discord.log"
    archive_dir = log_dir / "archive"

    _rollover_if_nonempty(runtime_log_path)
    _rollover_if_nonempty(discord_log_path)
    _archive_stale_logs(log_dir, archive_dir)
    _archive_excess_top_level_logs(log_dir, archive_dir)
    _compress_monthly_logs(archive_dir)

    runtime_log_file = runtime_log_path.open("w", encoding="utf-8", buffering=1)
    sys.stdout = _MirrorStream(sys.__stdout__, runtime_log_file)  # type: ignore[assignment]
    sys.stderr = _MirrorStream(sys.__stderr__, runtime_log_file)  # type: ignore[assignment]

    handler = logging.FileHandler(discord_log_path, encoding="utf-8", mode="w")
    handler.setFormatter(
        logging.Formatter("%(asctime)s:%(levelname)s:%(name)s: %(message)s")
    )
    return handler
