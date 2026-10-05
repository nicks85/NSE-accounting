"""Public engine API. The UI and importers depend only on this module.

Owned by the lead engineer. Phase 0 exposes version information only.
"""

from engine import __version__


def engine_version() -> str:
    """Return the engine version string."""
    return __version__
