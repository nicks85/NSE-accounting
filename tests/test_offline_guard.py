"""Offline guard: runtime code must never import networking modules (CLAUDE.md rule 1)."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNTIME_PACKAGES = ("engine", "importers")
FORBIDDEN = {"requests", "httpx", "urllib.request", "socket", "http.client", "urllib3", "aiohttp"}


def _imported_modules(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def _is_forbidden(module: str) -> bool:
    return any(module == f or module.startswith(f + ".") for f in FORBIDDEN)


def _runtime_files() -> list[Path]:
    return [p for pkg in RUNTIME_PACKAGES for p in sorted((ROOT / pkg).rglob("*.py"))]


def test_runtime_modules_exist() -> None:
    assert _runtime_files(), "offline guard found no runtime modules to scan"


def test_no_network_imports_in_runtime_code() -> None:
    violations = [
        f"{path.relative_to(ROOT)}: {mod}"
        for path in _runtime_files()
        for mod in _imported_modules(ast.parse(path.read_text(), filename=str(path)))
        if _is_forbidden(mod)
    ]
    assert not violations, "network imports in runtime code:\n" + "\n".join(violations)


def test_guard_detects_forbidden_imports() -> None:
    samples = [
        "import socket",
        "import requests",
        "import httpx",
        "import urllib.request",
        "from urllib import request",
        "from urllib.request import urlopen",
        "from http.client import HTTPConnection",
    ]
    for src in samples:
        assert any(_is_forbidden(m) for m in _imported_modules(ast.parse(src))), src


def test_guard_allows_safe_imports() -> None:
    for src in ["from decimal import Decimal", "import urllib.parse", "import json"]:
        assert not any(_is_forbidden(m) for m in _imported_modules(ast.parse(src))), src


def test_runtime_dependencies_load_no_network_modules() -> None:
    """Importing the engine, importers and msoffcrypto (used for protected XLSX) must not pull
    in networking modules, checked in a fresh interpreter."""
    import subprocess
    import sys

    code = (
        "import sys, engine.api, importers.zerodha, importers.upstox, importers.mapped, "
        "importers.xlsx, msoffcrypto, msoffcrypto.format.ooxml; "
        "bad = sorted(m for m in sys.modules if m in "
        "{'socket', 'ssl', 'urllib.request', 'http.client', 'requests', 'httpx', 'urllib3'}); "
        "print(','.join(bad))"
    )
    out = subprocess.run(  # noqa: S603 - fixed command, no user input
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=ROOT)
    assert out.stdout.strip() == ""
