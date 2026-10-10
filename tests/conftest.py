"""Every test gets its own ledger folder, so no test can ever read or write the user's real
saved data (``~/Library/Application Support/in.kosh.app`` and the like)."""

from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def ledger_dir(tmp_path_factory: pytest.TempPathFactory,
               monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    from engine import rpc

    folder = tmp_path_factory.mktemp("ledger")
    monkeypatch.setenv("KOSH_DATA_DIR", str(folder))
    monkeypatch.setattr(rpc, "_LEDGER", None)
    yield folder
    if rpc._LEDGER is not None:
        rpc._LEDGER.close()
