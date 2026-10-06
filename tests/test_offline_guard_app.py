"""Offline guard for the desktop shell: no network capability may be granted (CLAUDE.md rule 1)."""

import json
import re
import tomllib
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"
TAURI = APP / "src-tauri"
NETWORK_PLUGINS = ("http", "updater", "websocket", "upload", "shell", "opener")
ALLOWED_PLUGINS = {"dialog"}
"""Tauri plugins reviewed as local-only. Anything else fails until reviewed and added here."""


def test_cargo_has_no_network_plugins() -> None:
    deps = tomllib.loads((TAURI / "Cargo.toml").read_text())["dependencies"]
    bad = [d for d in deps if any(d == f"tauri-plugin-{p}" for p in NETWORK_PLUGINS)]
    assert not bad, f"network-capable Tauri plugins in Cargo.toml: {bad}"


def test_package_json_has_no_network_plugins() -> None:
    pkg = json.loads((APP / "package.json").read_text())
    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    bad = [d for d in deps if any(d == f"@tauri-apps/plugin-{p}" for p in NETWORK_PLUGINS)]
    assert not bad, f"network-capable Tauri plugins in package.json: {bad}"


def test_capabilities_grant_no_network_permissions() -> None:
    for cap_file in (TAURI / "capabilities").glob("*.json"):
        perms = json.loads(cap_file.read_text())["permissions"]
        ids = [p if isinstance(p, str) else p["identifier"] for p in perms]
        bad = [p for p in ids if p.split(":")[0] in NETWORK_PLUGINS]
        assert not bad, f"{cap_file.name} grants network permissions: {bad}"


def test_csp_blocks_remote_connections() -> None:
    conf = json.loads((TAURI / "tauri.conf.json").read_text())
    csp = conf["app"]["security"]["csp"]
    directives = dict(d.strip().split(" ", 1) for d in csp.split(";") if d.strip())
    assert directives["default-src"] == "'self'"
    for src in directives["connect-src"].split():
        assert src in {"ipc:", "http://ipc.localhost", "'self'"}, f"remote connect-src: {src}"
    assert not re.search(r"https?://(?!ipc\.localhost)", csp), "CSP allows a remote origin"


def test_no_updater_configured() -> None:
    conf = json.loads((TAURI / "tauri.conf.json").read_text())
    assert "updater" not in conf.get("plugins", {})
    assert not conf["bundle"].get("createUpdaterArtifacts")


def test_only_reviewed_tauri_plugins() -> None:
    deps = tomllib.loads((TAURI / "Cargo.toml").read_text())["dependencies"]
    plugins = {d.removeprefix("tauri-plugin-") for d in deps if d.startswith("tauri-plugin-")}
    unreviewed = plugins - ALLOWED_PLUGINS
    assert not unreviewed, f"unreviewed Tauri plugins (check they're local-only): {unreviewed}"
    pkg = json.loads((APP / "package.json").read_text())
    js = {d.removeprefix("@tauri-apps/plugin-")
          for d in {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
          if d.startswith("@tauri-apps/plugin-")}
    assert not js - ALLOWED_PLUGINS, f"unreviewed Tauri JS plugins: {js - ALLOWED_PLUGINS}"
