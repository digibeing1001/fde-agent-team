"""Feishu integration for the FDE Agent Team."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .team_cli import FeishuTeamProvisioner, SocraticIntake

__all__ = ["FeishuTeamProvisioner", "SocraticIntake"]


def __getattr__(name: str) -> Any:
    """Load CLI exports lazily so ``python -m ...team_cli`` stays warning-free."""
    if name in __all__:
        from . import team_cli

        return getattr(team_cli, name)
    raise AttributeError(name)
