

from collections.abc import Mapping

from respy_repl import Permissions, SafeSession


class UserSession(SafeSession):
    """Represents an active REPL session for a user, including their Permissions and execution state."""
    user_id: int
    guild_id: int | None
    can_save: bool

    def __init__(
        self,
        permissions: Permissions,
        user_vars: dict[str, object],
        user_id: int,
        guild_id: int | None,
        can_save: bool = False,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(permissions, user_vars, *args, **kwargs)
        self.user_id = user_id
        self.guild_id = guild_id
        self.can_save = can_save

    def to_relaunch_data(self) -> dict[str, object]:
        relaunch_data = super().to_relaunch_data()
        relaunch_data["user_id"] = self.user_id
        relaunch_data["guild_id"] = self.guild_id
        relaunch_data["can_save"] = self.can_save
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
        return cls(
            permissions=restored.perms,
            user_vars=restored.user_vars,
            user_id=user_id if user_id is not None else int(payload_user_id) if isinstance(payload_user_id, int) else -1,
            guild_id=guild_id if guild_id is not None else payload_guild_id if isinstance(payload_guild_id, int) or payload_guild_id is None else None,
            can_save=bool(payload.get("can_save", False)),
            command_registry=restored.command_registry,
        )
