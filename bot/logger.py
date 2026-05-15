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

USER_EXCEPTION: int = 35
logging.addLevelName(USER_EXCEPTION, "USER_EXCEPTION")


class _MirrorStream:
    """Tee writes to a terminal stream and a log file simultaneously.

    Terminal output is written as-is. Log file output is prefixed with a
    timestamp at the start of each line.

    Used to mirror process stdout/stderr into the runtime log file.
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


class _SystemLogFilter(logging.Filter):
    """Filter records for system logs.

    Allow application-level lifecycle logs from ``bot.main`` and all errors
    from any logger.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno == USER_EXCEPTION:
            return False
        if record.levelno >= logging.ERROR:
            return True
        return record.name.startswith("bot.main") and record.levelno >= logging.INFO


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


def _matching_timestamped_logs(log_dir: Path, log_stem: str) -> list[tuple[datetime, Path]]:
    """Collect timestamped rolled logs for a given base log name.

    Args:
        log_dir: Directory containing active and rolled logs.
        log_stem: Base log name, such as ``bot`` or ``discord``.

    Returns:
        Sorted pairs of parsed timestamp and log path for matching rolled logs.
    """
    timestamped_logs: list[tuple[datetime, Path]] = []
    for log_file in log_dir.glob(f"{log_stem}-*.log"):
        if not log_file.is_file():
            continue

        timestamp = _timestamp_from_filename(log_file)
        if timestamp is None:
            continue

        timestamped_logs.append((timestamp, log_file))

    timestamped_logs.sort(key=lambda item: (item[0], item[1].name))
    return timestamped_logs


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


def _prune_stale_logs(log_dir: Path, archive_dir: Path, log_stem: str, *, archive: bool) -> None:
    """Prune rolled logs older than ``ARCHIVE_AGE_HOURS`` for one log family.

    Empty stale logs are deleted. Non-empty stale logs are either archived or
    deleted, depending on ``archive``.

    Args:
        log_dir: Directory containing active and recent log files.
        archive_dir: Directory used for long-term log archives.
        log_stem: Base log name, such as ``bot`` or ``discord``.
        archive: When true, move stale logs into ``archive_dir``. When false,
            delete them instead.
    """
    cutoff = datetime.now() - timedelta(hours=ARCHIVE_AGE_HOURS)
    if archive:
        archive_dir.mkdir(parents=True, exist_ok=True)

    for timestamp, log_file in _matching_timestamped_logs(log_dir, log_stem):
        if timestamp >= cutoff:
            continue

        if log_file.stat().st_size == 0:
            log_file.unlink()
            continue

        if archive:
            target_path = archive_dir / log_file.name
            log_file.replace(target_path)
        else:
            log_file.unlink()


def _prune_excess_top_level_logs(
    log_dir: Path,
    archive_dir: Path,
    log_stem: str,
    *,
    archive: bool,
) -> None:
    """Trim rolled logs until the per-family top-level count is within limit.

    Only rolled logs with timestamped names matching ``log_stem`` are counted.
    The active log file is not counted toward this limit.

    Args:
        log_dir: Directory containing active and rolled logs.
        archive_dir: Directory used for long-term log archives.
        log_stem: Base log name, such as ``bot`` or ``discord``.
        archive: When true, move excess logs into ``archive_dir``. When false,
            delete them instead.
    """
    if archive:
        archive_dir.mkdir(parents=True, exist_ok=True)

    timestamped_logs = _matching_timestamped_logs(log_dir, log_stem)

    if len(timestamped_logs) <= MAX_TOP_LEVEL_TIMESTAMPED_LOGS:
        return

    excess_count = len(timestamped_logs) - MAX_TOP_LEVEL_TIMESTAMPED_LOGS
    for _, log_file in timestamped_logs[:excess_count]:
        if archive:
            target_path = archive_dir / log_file.name
            log_file.replace(target_path)
        else:
            log_file.unlink()


def configure_process_logging(project_root: Path) -> None:
    """Configure runtime, discord, and user-exception logging.

    At startup any prior ``bot.log`` / ``discord.log`` / ``user_errors.log``
    that is non-empty is rolled to timestamped files named with the current
    time. Existing ``bot`` logs older than 24 hours are moved into
    ``logs/archive`` and compressed by month (``YYYY-MM.zip``). Old
    ``discord`` and ``user_errors`` logs are deleted instead of archived.
    Each log family keeps at most five rolled top-level log files. Empty
    files are silently discarded. Fresh log files are always created for
    the current session.

    The ``discord`` logger is configured directly here because py-cord 2.x
    ignores the ``log_handler`` constructor parameter on ``commands.Bot``.

    Args:
        project_root: Repository root path.
    """
    log_dir = project_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    runtime_log_path = log_dir / "bot.log"
    discord_log_path = log_dir / "discord.log"
    user_errors_log_path = log_dir / "user_errors.log"
    archive_dir = log_dir / "archive"

    _rollover_if_nonempty(runtime_log_path)
    _rollover_if_nonempty(discord_log_path)
    _rollover_if_nonempty(user_errors_log_path)
    _prune_stale_logs(log_dir, archive_dir, "bot", archive=True)
    _prune_stale_logs(log_dir, archive_dir, "discord", archive=False)
    _prune_stale_logs(log_dir, archive_dir, "user_errors", archive=False)
    _prune_excess_top_level_logs(log_dir, archive_dir, "bot", archive=True)
    _prune_excess_top_level_logs(log_dir, archive_dir, "discord", archive=False)
    _prune_excess_top_level_logs(log_dir, archive_dir, "user_errors", archive=False)
    _compress_monthly_logs(archive_dir)

    # Keep process streams connected to terminal/systemd journal.
    sys.stdout = sys.__stdout__  # type: ignore[assignment]
    sys.stderr = sys.__stderr__  # type: ignore[assignment]

    # Route application loggers to both local and system destinations.
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.handlers.clear()

    local_handler = logging.FileHandler(runtime_log_path, encoding="utf-8", mode="w")
    local_handler.setLevel(logging.DEBUG)
    local_handler.setFormatter(
        logging.Formatter("%(asctime)s:%(levelname)s:%(name)s: %(message)s")
    )
    root_logger.addHandler(local_handler)

    system_handler = logging.StreamHandler(sys.stderr)
    system_handler.setLevel(logging.INFO)
    system_handler.addFilter(_SystemLogFilter())
    system_handler.setFormatter(
        logging.Formatter("%(asctime)s:%(levelname)s:%(name)s: %(message)s")
    )
    root_logger.addHandler(system_handler)

    user_errors_handler = logging.FileHandler(user_errors_log_path, encoding="utf-8", mode="w")
    user_errors_handler.setLevel(USER_EXCEPTION)
    user_errors_handler.addFilter(lambda r: r.levelno == USER_EXCEPTION)
    user_errors_handler.setFormatter(
        logging.Formatter("%(asctime)s:%(name)s: %(message)s")
    )
    root_logger.addHandler(user_errors_handler)

    # py-cord 2.x ignores the log_handler constructor parameter, so we attach
    # the discord.log FileHandler directly and stop propagation to root.
    discord_logger = logging.getLogger("discord")
    discord_handler = logging.FileHandler(discord_log_path, encoding="utf-8", mode="w")
    discord_handler.setFormatter(
        logging.Formatter("%(asctime)s:%(levelname)s:%(name)s: %(message)s")
    )
    discord_logger.addHandler(discord_handler)
    discord_logger.propagate = False
