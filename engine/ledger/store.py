"""The persistent local ledger (brief 0001 D1, D2): one SQLite file per installation.

Trades are the source of truth. Lots, gains and carried-forward losses are never stored as
editable state: they are rebuilt by replaying every trade through the same FIFO matcher and
``compute_tax_years`` (D2 option A), so importing an older file later corrects every year.

Uses only the standard library ``sqlite3``; nothing here opens a network connection.
"""

import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from engine import __version__
from engine.api import TaxYearReport, compute_tax_years
from engine.ledger.migrations import LATEST, MIGRATIONS
from engine.models import Segment, Side, Trade
from engine.money import ZERO

BUSY_TIMEOUT_SECONDS = 5.0
"""How long to wait for another process holding the ledger's write lock."""
IST = timezone(timedelta(hours=5, minutes=30))
BATCH_KINDS = ("tradebook", "cas", "opening", "manual", "taxpnl", "ledger_statement")
_KIND_OF_SEGMENT = {Segment.EQUITY: "EQUITY", Segment.MUTUAL_FUND: "MF", Segment.FNO: "FNO"}


class LedgerError(Exception):
    """The ledger file can't be used as asked (newer version, not a ledger, bad input)."""


def decimal_text(value: Decimal) -> str:
    """Exact text for a ``Decimal``; ``Decimal(decimal_text(v))`` gives back ``v`` with the same
    digits and exponent. Never goes through float (CLAUDE.md rule 2)."""
    if not isinstance(value, Decimal) or not value.is_finite():
        raise LedgerError(f"not a finite Decimal: {value!r}")
    return str(value)


def text_decimal(text: str) -> Decimal:
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise LedgerError(f"stored amount is not a decimal: {text!r}") from None
    if not value.is_finite():
        raise LedgerError(f"stored amount is not finite: {text!r}")
    return value


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class Profile:
    id: int
    display_name: str
    created_at: str


@dataclass(frozen=True, slots=True)
class Batch:
    """Where a set of trades came from. ``file_sha256`` lets a re-import be recognised."""

    kind: str = "tradebook"
    broker: str | None = None
    file_name: str | None = None
    file_sha256: str | None = None


def default_dedupe_key(trade: Trade) -> str:
    """Segment plus the engine's trade id. Brief D3 refines this per broker (task 2)."""
    return f"{trade.segment.value}|{trade.trade_id}"


class Ledger:
    """An open ledger file. Use ``Ledger.open(path)``, ideally as a context manager."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    @classmethod
    def open(cls, path: Path | str) -> Self:
        """Open (or create) the ledger at ``path`` and bring its schema up to date.

        A file made by a newer Kosh is refused rather than modified."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            db = sqlite3.connect(path, isolation_level=None, timeout=BUSY_TIMEOUT_SECONDS)
        except sqlite3.OperationalError as error:
            raise LedgerError(f"cannot open {path}: {error}") from None
        try:
            db.execute("PRAGMA foreign_keys = ON")
            ledger = cls(db)
            ledger._migrate()
        except sqlite3.OperationalError as error:
            db.close()
            if "locked" in str(error) or "busy" in str(error):
                raise LedgerError(
                    "the ledger is busy in another Kosh window; close it and try again") from None
            raise LedgerError(  # pragma: no cover - e.g. disk I/O errors
                f"cannot use {path}: {error}") from None
        except sqlite3.DatabaseError as error:
            db.close()
            raise LedgerError(f"{path} is not a Kosh ledger: {error}") from None
        except LedgerError:
            db.close()
            raise
        return ledger

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, kind: type[BaseException] | None, error: BaseException | None,
                 trace: TracebackType | None) -> None:
        self.close()

    # -- schema -------------------------------------------------------------------------------

    def schema_version(self) -> int:
        has_meta = self._db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'").fetchone()
        if not has_meta:
            return 0
        row = self._db.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        return int(row[0]) if row else 0

    def _migrate(self) -> None:
        # The version is read under the write lock, so two processes opening a new file at
        # once can't both try to create it.
        with self._transaction():
            current = self.schema_version()
            if current > LATEST:
                raise LedgerError(
                    f"this ledger was made by a newer version of Kosh (schema {current}; this "
                    f"version understands up to {LATEST}). Update Kosh to open it.")
            if current == 0 and self._db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table'").fetchone():
                raise LedgerError("the file has tables but no Kosh schema version")
            pending = [(v, sql) for v, sql in MIGRATIONS if v > current]
            for version, sql in pending:
                for statement in _statements(sql):
                    self._db.execute(statement)
                self._db.execute(
                    "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
                    "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (str(version),))
            if current == 0:
                self._db.executemany("INSERT INTO meta (key, value) VALUES (?, ?)", [
                    ("created_at", _now()), ("created_by_app_version", __version__)])

    def _transaction(self) -> "_Transaction":
        return _Transaction(self._db)

    # -- profiles -----------------------------------------------------------------------------

    def create_profile(self, display_name: str) -> Profile:
        name = display_name.strip()
        if not name:
            raise LedgerError("a profile needs a name")
        created = _now()
        with self._transaction():
            cursor = self._db.execute(
                "INSERT INTO profile (display_name, created_at) VALUES (?, ?)", (name, created))
        return Profile(_rowid(cursor), name, created)

    def profiles(self) -> list[Profile]:
        rows = self._db.execute("SELECT id, display_name, created_at FROM profile ORDER BY id")
        return [Profile(*row) for row in rows]

    def _require_profile(self, profile_id: int) -> None:
        if not self._db.execute("SELECT 1 FROM profile WHERE id = ?", (profile_id,)).fetchone():
            raise LedgerError(f"no profile {profile_id}")

    # -- trades -------------------------------------------------------------------------------

    def add_batch(self, profile_id: int, batch: Batch, trades: Sequence[Trade],
                  dedupe_keys: Sequence[str] | None = None) -> int:
        """Store ``trades`` as one import batch, all or nothing; returns the batch id.

        A trade whose dedupe key is already in this profile's ledger, or a file whose SHA-256
        was imported before, is refused with ``LedgerError`` (task 2 adds skip-and-report)."""
        if batch.kind not in BATCH_KINDS:
            raise LedgerError(f"unknown batch kind {batch.kind!r}")
        keys = list(dedupe_keys) if dedupe_keys is not None else [
            default_dedupe_key(t) for t in trades]
        if len(keys) != len(trades):
            raise LedgerError("one dedupe key is needed per trade")
        self._require_profile(profile_id)
        dates = [t.trade_date for t in trades]
        try:
            with self._transaction():
                cursor = self._db.execute(
                    "INSERT INTO import_batch (profile_id, kind, broker, file_name, file_sha256,"
                    " imported_at, date_from, date_to, rows_read, trades_added,"
                    " duplicates_skipped) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                    (profile_id, batch.kind, batch.broker, batch.file_name, batch.file_sha256,
                     _now(), min(dates).isoformat() if dates else None,
                     max(dates).isoformat() if dates else None, len(trades), len(trades)))
                batch_id = _rowid(cursor)
                for trade, key in zip(trades, keys, strict=True):
                    self._insert_trade(profile_id, batch_id, trade, key)
        except sqlite3.IntegrityError as error:
            if "import_batch" in str(error):
                raise LedgerError("this file is already in the ledger") from None
            if "trade.profile_id, trade.dedupe_key" in str(error):
                raise LedgerError("a trade in this file is already in the ledger") from None
            raise LedgerError(  # pragma: no cover - inputs are validated Trades
                f"the ledger refused the import: {error}") from None
        return batch_id

    def _instrument_id(self, trade: Trade) -> int:
        column = "contract_symbol" if trade.segment is Segment.FNO else "isin"
        row = self._db.execute(
            f"SELECT id FROM instrument WHERE {column} = ?",  # noqa: S608 - fixed column names
            (trade.instrument,)).fetchone()
        if row:
            return int(row[0])
        cursor = self._db.execute(
            f"INSERT INTO instrument ({column}, kind) VALUES (?, ?)",  # noqa: S608
            (trade.instrument, _KIND_OF_SEGMENT[trade.segment]))
        return _rowid(cursor)

    def _insert_trade(self, profile_id: int, batch_id: int, trade: Trade, key: str) -> None:
        cursor = self._db.execute(
            "INSERT INTO trade (profile_id, batch_id, instrument_id, source_id, segment,"
            " trade_date, executed_at, side, quantity, price, dedupe_key)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (profile_id, batch_id, self._instrument_id(trade), trade.trade_id,
             trade.segment.value, trade.trade_date.isoformat(),
             trade.executed_at.isoformat() if trade.executed_at else None, trade.side.value,
             decimal_text(trade.quantity), decimal_text(trade.price), key))
        trade_row = _rowid(cursor)
        # Charges are kept as one total until the per-charge breakdown lands (D8, task 10).
        charges = [("OTHER", trade.charges), ("STT", trade.stt)]
        self._db.executemany(
            "INSERT INTO trade_charge (trade_id, kind, amount) VALUES (?, ?, ?)",
            [(trade_row, kind, decimal_text(amount)) for kind, amount in charges if amount])

    def trades(self, profile_id: int) -> list[Trade]:
        """Every live trade of ``profile_id`` as ``Trade``s, ordered as the importers order a
        file: date, then execution time when known, then import and file order. FIFO and
        intraday netting keep input order within a day, so trades from two files touching the
        same day must interleave by time, not by which file came first."""
        self._require_profile(profile_id)
        charges: dict[int, dict[str, Decimal]] = {}
        for trade_row, kind, amount in self._db.execute(
                "SELECT c.trade_id, c.kind, c.amount FROM trade_charge c"
                " JOIN trade t ON t.id = c.trade_id WHERE t.profile_id = ?", (profile_id,)):
            charges.setdefault(trade_row, {})[kind] = text_decimal(amount)
        rows = self._db.execute(
            "SELECT t.id, t.source_id, t.trade_date, COALESCE(i.isin, i.contract_symbol),"
            " t.side, t.quantity, t.price, t.segment, t.executed_at"
            " FROM trade t JOIN instrument i ON i.id = t.instrument_id"
            " JOIN import_batch b ON b.id = t.batch_id"
            " WHERE t.profile_id = ? AND b.undone_at IS NULL ORDER BY t.id", (profile_id,))
        ordered = sorted(((row[0], _trade(row, charges.get(row[0], {}))) for row in rows),
                         key=_replay_order)
        return [trade for _, trade in ordered]

    def batches(self, profile_id: int) -> list[dict[str, Any]]:
        self._require_profile(profile_id)
        cursor = self._db.execute(
            "SELECT id, kind, broker, file_name, file_sha256, imported_at, date_from, date_to,"
            " trades_added FROM import_batch WHERE profile_id = ? AND undone_at IS NULL"
            " ORDER BY id", (profile_id,))
        names = [c[0] for c in cursor.description]
        return [dict(zip(names, row, strict=True)) for row in cursor]

    # -- replay -------------------------------------------------------------------------------

    def compute(self, profile_id: int, start_years: Iterable[int],
                **inputs: Any) -> list[TaxYearReport]:
        """Replay every stored trade through ``compute_tax_years`` (D2). ``inputs`` are passed
        through (fund classes, FMVs, brought-forward losses) until they are stored (task 3)."""
        return compute_tax_years(start_years, self.trades(profile_id), **inputs)


class _Transaction:
    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def __enter__(self) -> None:
        self._db.execute("BEGIN IMMEDIATE")

    def __exit__(self, kind: type[BaseException] | None, error: BaseException | None,
                 trace: TracebackType | None) -> None:
        self._db.execute("ROLLBACK" if kind else "COMMIT")


def _statements(sql: str) -> list[str]:
    """Split a migration into complete statements (a ``;`` inside a string doesn't split)."""
    statements, buffer = [], ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statements.append(buffer.strip())
            buffer = ""
    if buffer.strip():
        raise LedgerError(f"incomplete migration statement: {buffer.strip()[:60]}")
    return statements


def _replay_order(item: tuple[int, Trade]) -> tuple[date, bool, datetime, int]:
    """Same order as ``importers.tabular._order_key``; an aware time is compared as IST wall
    time so it sorts with the naive exchange times the importers give."""
    row_id, trade = item
    at = trade.executed_at
    if at is not None and at.tzinfo is not None:
        at = at.astimezone(IST).replace(tzinfo=None)
    return (trade.trade_date, at is None, at or datetime.min, row_id)


def _rowid(cursor: sqlite3.Cursor) -> int:
    if cursor.lastrowid is None:  # pragma: no cover - INSERT always sets it
        raise LedgerError("insert returned no row id")
    return cursor.lastrowid


def _trade(row: tuple[Any, ...], charges: dict[str, Decimal]) -> Trade:
    _, source_id, on, instrument, side, quantity, price, segment, executed_at = row
    return Trade(
        trade_id=source_id,
        trade_date=date.fromisoformat(on),
        instrument=instrument,
        side=Side(side),
        quantity=text_decimal(quantity),
        price=text_decimal(price),
        charges=sum((v for k, v in charges.items() if k != "STT"), ZERO),
        stt=charges.get("STT", ZERO),
        segment=Segment(segment),
        executed_at=datetime.fromisoformat(executed_at) if executed_at else None,
    )
