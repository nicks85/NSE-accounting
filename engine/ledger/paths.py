"""Where the ledger file lives (brief 0001 D1): the OS app-data folder Tauri uses for the app
identifier ``in.kosh.app``, so the desktop and browser builds open the same file."""

import os
import sys
from collections.abc import Mapping
from pathlib import Path

APP_ID = "in.kosh.app"
FILE_NAME = "kosh.sqlite"
ENV_OVERRIDE = "KOSH_DATA_DIR"
"""Set by the desktop shell, tests and E2E runs to choose another folder."""


def data_dir(env: Mapping[str, str] | None = None, platform: str | None = None,
             home: Path | None = None) -> Path:
    """The folder holding ``kosh.sqlite``; not created here.

    macOS ``~/Library/Application Support/in.kosh.app``; Windows ``%APPDATA%\\in.kosh.app``;
    Linux ``$XDG_DATA_HOME/in.kosh.app`` (default ``~/.local/share``)."""
    env = os.environ if env is None else env
    platform = platform or sys.platform
    home = home or Path.home()
    if env.get(ENV_OVERRIDE):
        return Path(env[ENV_OVERRIDE])
    if platform == "darwin":
        return home / "Library" / "Application Support" / APP_ID
    if platform.startswith("win"):
        base = env.get("APPDATA")
        return (Path(base) if base else home / "AppData" / "Roaming") / APP_ID
    base = env.get("XDG_DATA_HOME")
    return (Path(base) if base else home / ".local" / "share") / APP_ID


def ledger_path(env: Mapping[str, str] | None = None) -> Path:
    return data_dir(env) / FILE_NAME
