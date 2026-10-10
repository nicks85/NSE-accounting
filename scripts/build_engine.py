"""Build the standalone engine executable that the desktop app ships as a Tauri sidecar.

    uv run --group build python scripts/build_engine.py

Produces app/src-tauri/binaries/kosh-engine-<target-triple>[.exe], a single file containing
Python, the engine, the importers and their dependencies. Build-time only; nothing here runs in
the app. casparser-isin's network-capable update CLI is excluded from the bundle.

Reproducibility: PYTHONHASHSEED is fixed and SOURCE_DATE_EPOCH is taken from the last commit,
so two builds of the same commit on the same toolchain should match (see docs/RELEASING.md).
"""

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "app" / "src-tauri" / "binaries"

HIDDEN_IMPORTS = [
    # imported lazily by engine.rpc
    "importers.cas", "importers.mapped", "importers.tabular", "importers.upstox",
    "importers.zerodha", "importers.xlsx", "engine.export.itr", "engine.export.json_out",
    "engine.export.pdf", "engine.export.schemas_registry", "engine.export.skeleton",
    "engine.export.schemas", "engine.ledger", "engine.ledger.store", "engine.ledger.dedupe",
    "engine.ledger.migrations", "engine.ledger.paths", "importers.angel_one",
    "msoffcrypto.format.ooxml", "casparser.parsers.cams_detailed",
]
DATA_PACKAGES = ["engine.export.schemas", "reportlab", "casparser", "casparser_isin",
                 "jsonschema_specifications"]
EXCLUDES = [
    "casparser_isin.cli",  # downloads ISIN database updates over the network
    "tkinter", "unittest", "pydoc", "pytest", "IPython",
]


def target_triple() -> str:
    """Rust target triple, as Tauri names sidecars (rustc's host when available)."""
    rustc = shutil.which("rustc")
    if rustc:
        out = subprocess.run([rustc, "-vV"], capture_output=True, text=True, check=True).stdout
        for line in out.splitlines():
            if line.startswith("host:"):
                return line.split()[1]
    machine = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "aarch64",
               "aarch64": "aarch64"}[platform.machine().lower()]
    system = platform.system()
    if system == "Darwin":
        return f"{machine}-apple-darwin"
    if system == "Windows":
        return f"{machine}-pc-windows-msvc"
    return f"{machine}-unknown-linux-gnu"


def commit_epoch() -> str:
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%ct"], cwd=ROOT,
                             capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "0"


def main() -> None:
    triple = target_triple()
    name = f"kosh-engine-{triple}"
    work = ROOT / "build" / "engine"
    args = [
        sys.executable, "-m", "PyInstaller", "--onefile", "--noconfirm", "--clean",
        "--name", name, "--distpath", str(OUT), "--workpath", str(work / "work"),
        "--specpath", str(work), "--paths", str(ROOT),
        *[f"--hidden-import={m}" for m in HIDDEN_IMPORTS],
        *[f"--collect-data={p}" for p in DATA_PACKAGES],
        *[f"--exclude-module={m}" for m in EXCLUDES],
        str(ROOT / "packaging" / "engine_entry.py"),
    ]
    env = {**os.environ, "PYTHONHASHSEED": "0", "SOURCE_DATE_EPOCH": commit_epoch()}
    subprocess.run(args, cwd=ROOT, env=env, check=True)
    suffix = ".exe" if platform.system() == "Windows" else ""
    print(OUT / f"{name}{suffix}")


if __name__ == "__main__":
    main()
