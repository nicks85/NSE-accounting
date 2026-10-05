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


NETWORK_ONLY_MODULES = {"ssl", "urllib.request", "http.client", "requests", "httpx", "urllib3",
                        "aiohttp", "casparser_isin.cli"}
"""Modules whose only purpose is networking (or casparser-isin's update downloader). ``socket``
itself is not listed: the standard library imports it incidentally (e.g. email.utils, pulled
in by importlib.metadata on Python 3.12), which is not network use. Actual socket use is
trapped below instead."""


def test_runtime_dependencies_make_no_network_calls() -> None:
    """In a fresh interpreter, with socket connect/DNS replaced by tripwires, import the engine,
    importers, exporters and their dependencies (msoffcrypto for protected XLSX; casparser with
    its parser and ISIN-database modules for CAS PDFs; jsonschema for ITR validation), run an
    ISIN lookup, a CAS parse, a schema validation and a PDF render (reportlab). No tripwire may
    fire and no network-only module may be loaded."""
    import subprocess
    import sys

    code = (
        "import socket, sys, traceback\n"
        "calls = []\n"
        "def trip(name):\n"
        "    def fn(*a, **k):\n"
        "        calls.append(name + ''.join(traceback.format_stack(limit=8)))\n"
        "        raise OSError('network disabled by offline guard')\n"
        "    return fn\n"
        "for name in ('getaddrinfo', 'gethostbyname', 'gethostbyname_ex', 'create_connection'):\n"
        "    setattr(socket, name, trip(name))\n"
        "socket.socket.connect = trip('connect')\n"
        "socket.socket.connect_ex = trip('connect_ex')\n"
        "import engine.api, importers.zerodha, importers.upstox, importers.mapped, "
        "importers.xlsx, importers.cas, msoffcrypto, msoffcrypto.format.ooxml, casparser, "
        "casparser.parsers.cams_detailed, casparser.parsers._isin, casparser.analysis, "
        "casparser_isin, rapidfuzz, dateutil, pypdfium2, jsonschema, referencing, "
        "engine.export.itr, engine.export.pdf, reportlab\n"
        "from engine.export.pdf import render_summary\n"
        "from tests.unit.export.test_pdf import _report\n"
        "render_summary(_report())\n"
        "from engine.export.schemas_registry import validate\n"
        "validate({}, 'ITR-2', 2025, 'Schedule112A')\n"
        "from casparser.parsers._isin import isin_search\n"
        "isin_search('Synthetic Flexi Cap Fund', 'CAMS', 'X1', 'INF000E01011')\n"
        "from importers.cas import load_cas\n"
        "try:\n    load_cas(b'not a pdf', password='x')\nexcept Exception:\n    pass\n"
        f"bad = sorted(m for m in sys.modules if m in {sorted(NETWORK_ONLY_MODULES)!r})\n"
        "print(','.join(bad))\n"
        "print('\\n'.join(calls), file=sys.stderr)\n"
    )
    out = subprocess.run(  # noqa: S603 - fixed command, no user input
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=ROOT)
    assert out.stdout.strip() == "", "network-only modules loaded"
    assert out.stderr.strip() == "", f"network calls attempted:\n{out.stderr[-4000:]}"


def test_tripwire_catches_a_connection_attempt() -> None:
    """The guard's tripwire really fires (so a silent pass means no network use)."""
    import subprocess
    import sys

    code = (
        "import socket\n"
        "def trip(*a, **k):\n    raise OSError('network disabled by offline guard')\n"
        "socket.getaddrinfo = trip\n"
        "import urllib.request\n"
        "try:\n    urllib.request.urlopen('http://example.invalid', timeout=1)\n"
        "except Exception as e:\n    print(type(e).__name__, e)\n"
    )
    out = subprocess.run(  # noqa: S603 - fixed command, no user input
        [sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert "network disabled by offline guard" in out.stdout
