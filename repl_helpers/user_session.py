

from collections.abc import Mapping

from respy_repl import Permissions, SafeSession


DEFAULT_INPUT_NAME_TEMPLATE = "<discord input: {count}>"
COUNT_PLACEHOLDER = "{count}"


class UserSession(SafeSession):
    """Represents an active REPL session for a user, including their Permissions and execution state."""
    user_id: int
    guild_id: int | None
    can_save: bool
    input_name_template: str
    input_name_counts: dict[str, int]

    def __init__(
        self,
        permissions: Permissions,
        user_vars: dict[str, object],
        user_id: int,
        guild_id: int | None,
        can_save: bool = False,
        input_name_template: str = DEFAULT_INPUT_NAME_TEMPLATE,
        input_name_counts: dict[str, int] | None = None,
        *args,
        **kwargs,
    ) -> None:
        normalized_template = self._normalize_input_name_template(input_name_template)
        super().__init__(
            permissions,
            user_vars,
            user_traceback_filename=normalized_template,
            *args,
            **kwargs,
        )
        self.user_id = user_id
        self.guild_id = guild_id
        self.can_save = can_save
        self.input_name_template = normalized_template
        self.input_name_counts = dict(input_name_counts) if input_name_counts is not None else {}

    @staticmethod
    def _normalize_input_name_template(template: object) -> str:
        if isinstance(template, str):
            stripped = template.strip()
            if stripped:
                return stripped
        return DEFAULT_INPUT_NAME_TEMPLATE

    def _sync_input_name_state(self) -> None:
        template = self._normalize_input_name_template(self.input_name_template)
        self.input_name_template = template
        self.user_traceback_filename = template

    def set_input_name_template(self, template: str) -> None:
        """Set the default input-name template used for future executions."""
        normalized_template = self._normalize_input_name_template(template)
        self.input_name_template = normalized_template
        self.user_traceback_filename = normalized_template

    def reset_input_name_template(self) -> None:
        """Reset the default input-name template to the project default."""
        self.set_input_name_template(DEFAULT_INPUT_NAME_TEMPLATE)

    def reset_input_name_state(self) -> None:
        """Reset the input-name template and all persisted per-template counters."""
        self.input_name_counts = {}
        self.reset_input_name_template()

    def resolve_input_name(self, input_name: str | None = None) -> str:
        """Resolve one execution label, expanding the ``{count}`` placeholder when used."""
        self._sync_input_name_state()
        template = self.input_name_template
        if isinstance(input_name, str) and input_name.strip():
            template = self._normalize_input_name_template(input_name)

        if COUNT_PLACEHOLDER not in template:
            return template

        next_count = self.input_name_counts.get(template, 0) + 1
        self.input_name_counts[template] = next_count
        return template.replace(COUNT_PLACEHOLDER, str(next_count))

    def exec_response(self, code: str, *, input_name: str | None = None):
        """Execute code and apply the session's input-name templating rules."""
        return super().exec_response(code, input_name=self.resolve_input_name(input_name))

    def exec(self, code: str, *, input_name: str | None = None):
        """Execute code and apply the session's input-name templating rules."""
        return super().exec(code, input_name=self.resolve_input_name(input_name))

    async def async_exec_response(
        self,
        code: str,
        *,
        input_name: str | None = None,
        timeout: float | None = None,
    ):
        """Execute code asynchronously and apply the session's input-name templating rules."""
        return await super().async_exec_response(
            code,
            input_name=self.resolve_input_name(input_name),
            timeout=timeout,
        )

    async def async_exec(
        self,
        code: str,
        *,
        input_name: str | None = None,
        timeout: float | None = None,
    ):
        """Execute code asynchronously and apply the session's input-name templating rules."""
        return await super().async_exec(
            code,
            input_name=self.resolve_input_name(input_name),
            timeout=timeout,
        )

    def to_relaunch_data(self) -> dict[str, object]:
        self._sync_input_name_state()
        relaunch_data = super().to_relaunch_data()
        relaunch_data["user_id"] = self.user_id
        relaunch_data["guild_id"] = self.guild_id
        relaunch_data["can_save"] = self.can_save
        relaunch_data["input_name_template"] = self.input_name_template
        relaunch_data["input_name_counts"] = dict(self.input_name_counts)
        if not self.can_save:
            relaunch_data["user_vars"] = None
        return relaunch_data

    @classmethod
    def from_relaunch_data(
        cls,
        payload: Mapping[str, object],
        user_id: int | None = None,
        guild_id: int | None = None,
    ) -> "UserSession":
        restored = SafeSession.from_relaunch_data(payload)
        payload_user_id = payload.get("user_id")
        payload_guild_id = payload.get("guild_id")
        input_name_template_raw = payload.get(
            "input_name_template",
            DEFAULT_INPUT_NAME_TEMPLATE,
        )
        input_name_counts_raw = payload.get("input_name_counts", {})
        session = cls(
            permissions=restored.perms,
            user_vars=restored.user_vars,
            user_id=user_id if user_id is not None else int(payload_user_id) if isinstance(payload_user_id, int) else -1,
            guild_id=guild_id if guild_id is not None else payload_guild_id if isinstance(payload_guild_id, int) or payload_guild_id is None else None,
            can_save=bool(payload.get("can_save", False)),
            input_name_template=input_name_template_raw if isinstance(input_name_template_raw, str) else DEFAULT_INPUT_NAME_TEMPLATE,
            input_name_counts=input_name_counts_raw if isinstance(input_name_counts_raw, dict) else None,
            command_registry=restored.command_registry,
        )
        return session
