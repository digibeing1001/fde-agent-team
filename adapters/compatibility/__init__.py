"""Cross-host compatibility layer for the FDE Agent Team."""

from .compiler import CompatibilityCompiler, CompatibilityInstallError
from .bridge import HostBridgeError, JsonCommandBridge
from .registry import CompatibilityRegistry, REQUIRED_INVARIANTS

__all__ = [
    "CompatibilityCompiler",
    "CompatibilityInstallError",
    "CompatibilityRegistry",
    "HostBridgeError",
    "JsonCommandBridge",
    "REQUIRED_INVARIANTS",
]
