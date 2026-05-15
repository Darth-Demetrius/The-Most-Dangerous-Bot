"""Runtime helpers for Discord REPL message execution."""

from __future__ import annotations

import asyncio
import io
import logging
import re
import shlex
from collections.abc import Sequence
from urllib.parse import quote

import aiohttp
import discord

from respy_repl import ExecutionError

from bot.logger import USER_EXCEPTION
from repl_helpers.link_text import user_scope_text
from repl_helpers.user_session import UserSession

from .support import ReplSessionService

_LOGGER = logging.getLogger(__name__)

_ANSI_RESET = "\x1b[0m"
_ANSI_RED = "\x1b[31m"
_ANSI_CYAN = "\x1b[36m"


async def execute_code(
    state: ReplSessionService,
    message: discord.Message,
    session: UserSession,
    code: str,
    input_name: str | None = None,
) -> None:
    """Execute one snippet, send the response, and handle reactions."""

    def _exec() -> tuple[object | None, str, list[discord.File]]:
        response = session.exec_response(code, input_name=input_name)
        return (
            response.result,
            response.output.rstrip(),
            _image_artifacts_to_files(response.display_artifacts),
        )

    loop = asyncio.get_running_loop()
    session_key = state.message_session_key(message)

    lock = state.session_locks.setdefault(session_key, asyncio.Lock())
    await message.channel.trigger_typing()
    async with lock:
        try:
            code_preview = code if len(code) <= 60 else code[:57] + "..."
            _LOGGER.debug(
                "Executing code for %s: %r",
                user_scope_text(message.author, message.guild),
                code_preview,
            )
            result, stdout, files = await loop.run_in_executor(None, _exec)
        except ExecutionError as error:
            await _add_reaction(state, message, "❌")
            _LOGGER.log(
                USER_EXCEPTION,
                "Execution error for %s",
                user_scope_text(message.author, message.guild),
                exc_info=True,
            )
            partial_stdout = error.output.rstrip()
            partial_files = _image_artifacts_to_files(error.display_artifacts)
            await _send_with_optional_files(
                message,
                text=_format_code_block_output(partial_stdout, language="ansi") if partial_stdout else None,
                files=partial_files,
            )
            await message.channel.send(
                _format_code_block_output(_colorize(error.user_message, _ANSI_RED), language="ansi"),
                reference=message,
            )
            return
        except SyntaxError as error:
            await _add_reaction(state, message, "❌")
            _LOGGER.log(
                USER_EXCEPTION,
                "Syntax error for %s",
                user_scope_text(message.author, message.guild),
                exc_info=True,
            )
            await message.channel.send(
                _format_code_block_output(_colorize(str(error), _ANSI_RED), language="ansi"),
                reference=message,
            )
            return
        except Exception as error:
            await _add_reaction(state, message, "❌")
            _LOGGER.exception(
                "Unexpected error executing code for %s",
                user_scope_text(message.author, message.guild),
            )
            await message.channel.send(
                _format_code_block_output(_colorize(str(error), _ANSI_RED), language="ansi"),
                reference=message,
            )
            return

    parts: list[str] = []
    if stdout:
        parts.append(stdout)
    if result is not None:
        try:
            parts.append(_colorize(repr(result), _ANSI_CYAN))
        except Exception as error:
            parts.append(_colorize(f"{type(error).__name__}: {error}", _ANSI_CYAN))

    if not parts and not files:
        await _add_reaction(state, message, "✅")
        return

    message_text = _format_code_block_output("\n".join(parts), language="ansi") if parts else None
    if files:
        await _send_with_optional_files(message, text=message_text, files=files)
        return

    await message.channel.send(message_text, reference=message)


async def add_reaction(state: ReplSessionService, message: discord.Message, emoji: str) -> None:
    """Add a reaction using direct REST to avoid py-cord reaction-lock hangs."""
    await _add_reaction(state, message, emoji)


def extract_repl_code(message_content: str) -> tuple[str | None, str | None]:
    """Extract the first REPL code snippet and optional per-block input-name override."""
    code_block_match = re.search(
        r"```(?P<lang>\w*)(?P<info>[^\n`]*)\n(?P<code>.*?)```",
        message_content,
        re.DOTALL | re.IGNORECASE,
    )
    if code_block_match:
        lang = code_block_match.group("lang").lower()
        if lang and lang not in {"python", "py"}:
            return None, None

        code = code_block_match.group("code").strip()
        if not code:
            return None, None
        return code, _parse_code_block_input_name(code_block_match.group("info").strip())

    inline_match = re.search(r"`(.*?)`", message_content, re.DOTALL)
    if not inline_match:
        return None, None

    code = inline_match.group(1).strip()
    return (code, None) if code else (None, None)


async def _add_reaction(state: ReplSessionService, message: discord.Message, emoji: str) -> None:
    """Add a reaction using direct REST to avoid py-cord reaction-lock hangs."""
    token = getattr(state.bot.http, "token", None)
    if not token:
        raise RuntimeError("Bot token unavailable for reaction request")

    encoded = quote(emoji, safe="")
    url = (
        f"https://discord.com/api/v10/channels/{message.channel.id}"
        f"/messages/{message.id}/reactions/{encoded}/@me"
    )
    headers = {
        "Authorization": f"Bot {token}",
        "User-Agent": getattr(state.bot.http, "user_agent", "DiscordBot"),
    }

    if state.reaction_http is None or state.reaction_http.closed:
        state.reaction_http = aiohttp.ClientSession()

    async with state.reaction_http.put(url, headers=headers) as response:
        if response.status in {200, 201, 204}:
            return

        body = await response.text()
        raise RuntimeError(f"Reaction request failed ({response.status}): {body}")


def _parse_code_block_input_name(info: str) -> str | None:
    """Extract an input-name override from a fenced code block header."""
    if not info:
        return None

    try:
        tokens = shlex.split(info)
    except ValueError:
        tokens = info.split()

    for token in tokens:
        for prefix in ("input_name=", "input=", "name=", "i="):
            if token.startswith(prefix):
                value = token.split("=", 1)[1].strip()
                return value or None

    return None


def _format_code_block_output(
    text: str,
    *,
    language: str,
    max_length: int = 1900,
) -> str:
    """Format text as a Discord code block with truncation when needed."""
    if len(text) <= max_length:
        return f"```{language}\n{text}\n```"

    truncated = text[:max_length]
    remainder = len(text) - max_length
    return f"```{language}\n{truncated}\n...\n```\n[{remainder} characters truncated]"


def _colorize(text: str, color_code: str) -> str:
    """Wrap text in ANSI color codes for Discord ansi code blocks."""
    if not text:
        return text
    return f"{color_code}{text}{_ANSI_RESET}"


def _image_artifacts_to_files(display_artifacts: Sequence[object]) -> list[discord.File]:
    """Convert PNG display artifacts to Discord files."""
    files: list[discord.File] = []
    image_count = 0
    for artifact in display_artifacts:
        if getattr(artifact, "mime_type", "") != "image/png":
            continue
        image_count += 1
        payload = io.BytesIO(getattr(artifact, "data", b""))
        if payload.getbuffer().nbytes == 0:
            continue
        files.append(discord.File(payload, filename=f"repl-output-{image_count}.png"))
    return files


async def _send_with_optional_files(
    message: discord.Message,
    *,
    text: str | None,
    files: list[discord.File],
) -> None:
    """Send a message with optional attachments and always close file handles."""
    if not files:
        if text:
            await message.channel.send(text, reference=message)
        return

    try:
        await message.channel.send(content=text, files=files, reference=message)
    finally:
        for file in files:
            try:
                file.close()
            except Exception:
                pass
