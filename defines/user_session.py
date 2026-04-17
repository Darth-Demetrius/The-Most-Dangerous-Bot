

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
        restored = super().from_relaunch_data(payload)
        return cls(
            permissions=restored.perms,
            user_vars=restored.user_vars,
            user_id=user_id or -1,
            guild_id=guild_id,
            can_save=bool(payload.get("can_save", False)),
        )
