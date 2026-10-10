"""The persistent local ledger (brief 0001 D1, D2): one SQLite file per installation.

Trades are the source of truth. Lots, gains and carried-forward losses are never stored as
editable state: they are rebuilt by replaying every trade through the same FIFO matcher and
``compute_tax_years`` (D2 option A), so importing an older file later corrects every year.

Uses only the standard library ``sqlite3``; nothing here opens a network connection.
"""

import json
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
from engine.classify.funds import FundClass
from engine.classify.trades import DELIVERY_SUFFIX
from engine.ledger.dedupe import dedupe_keys, details, same_details
from engine.ledger.migrations import LATEST, MIGRATIONS
from engine.ledger.settings import ManualBuy, Settings
from engine.models import Segment, Side, Trade
from engine.money import ZERO
from engine.rules.setoff import LossEntry, LossKind

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


@dataclass(frozen=True, slots=True)
class Conflict:
    """Why a file was refused rather than overwriting or doubling a saved trade.

    ``reason`` is ``"same_id"`` when the trade's key is already saved with different details
    (D3), or ``"other_source"`` when a saved trade from another source has the same execution
    time and details: almost certainly the same trade imported under another broker name
    (e.g. a mapped file named "Groww" once and "Groww India" later)."""

    new: Trade
    existing: Trade
    reason: str = "same_id"


@dataclass(frozen=True, slots=True)
class ImportOutcome:
    batch_id: int | None
    """The new batch, or None when nothing was stored."""
    added: int
    duplicates: int
    """Trades already in the ledger with the same details, skipped."""
    already_imported_on: str | None = None
    """Set when this exact file is already in the ledger; nothing was read from it."""
    conflicts: tuple[Conflict, ...] = ()
    """Non-empty means the whole file was refused."""
    possible_duplicates: tuple[Conflict, ...] = ()
    """Saved anyway, but each matches a saved trade from another source on date, share, side,
    quantity and price. Without execution times Kosh can't tell a re-import under another
    broker name from a genuine second trade, so it warns instead of refusing."""


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

    def ensure_profile(self, display_name: str) -> Profile:
        """The profile with this name, created if there is none yet."""
        name = display_name.strip()
        if not name:
            raise LedgerError("a profile needs a name")
        with self._transaction():
            row = self._db.execute(
                "SELECT id, display_name, created_at FROM profile WHERE display_name = ?"
                " ORDER BY id LIMIT 1", (name,)).fetchone()
            if row:
                return Profile(*row)
            created = _now()
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
                  keys: Sequence[str] | None = None) -> int:
        """Store ``trades`` as one import batch, all or nothing; returns the batch id.

        Strict: a trade whose key is already in this profile's ledger, or a file whose SHA-256
        is in a live batch, raises ``LedgerError``. ``import_trades`` skips and reports."""
        if batch.kind not in BATCH_KINDS:
            raise LedgerError(f"unknown batch kind {batch.kind!r}")
        keys = list(keys) if keys is not None else dedupe_keys(trades)
        if len(keys) != len(trades):
            raise LedgerError("one dedupe key is needed per trade")
        self._require_profile(profile_id)
        try:
            with self._transaction():
                batch_id = self._insert_batch(profile_id, batch, trades, keys,
                                              rows_read=len(trades), duplicates=0, warnings=())
        except sqlite3.IntegrityError as error:
            if "import_batch" in str(error):
                raise LedgerError("this file is already in the ledger") from None
            if "trade.profile_id, trade.dedupe_key" in str(error):
                raise LedgerError("a trade in this file is already in the ledger") from None
            raise LedgerError(  # pragma: no cover - inputs are validated Trades
                f"the ledger refused the import: {error}") from None
        return batch_id

    def imported_on(self, profile_id: int, file_sha256: str) -> str | None:
        """When this exact file was imported, if it is in a live batch."""
        row = self._db.execute(
            "SELECT imported_at FROM import_batch WHERE profile_id = ? AND file_sha256 = ?"
            " AND undone_at IS NULL", (profile_id, file_sha256)).fetchone()
        return str(row[0]) if row else None

    def import_trades(self, profile_id: int, batch: Batch, trades: Sequence[Trade], *,
                      warnings: Sequence[str] = ()) -> ImportOutcome:
        """Import one file (D3): skip trades already in the ledger, refuse the whole file if a
        trade's key is there with different details, and recognise a file already imported.
        Nothing is stored unless at least one trade is new."""
        if batch.kind not in BATCH_KINDS:
            raise LedgerError(f"unknown batch kind {batch.kind!r}")
        self._require_profile(profile_id)
        keys = dedupe_keys(trades)
        with self._transaction():
            when = self.imported_on(profile_id, batch.file_sha256) if batch.file_sha256 else None
            if when:
                return ImportOutcome(None, 0, 0, already_imported_on=when)
            saved = self._select(profile_id)
            wanted = set(keys)
            stored = {key: trade for _, trade, key in saved if key in wanted}
            timed = {_timed_details(t): t for _, t, _ in saved if t.executed_at is not None}
            untimed = {details(t): t for _, t, _ in saved}
            possible: list[Conflict] = []
            fresh: list[tuple[Trade, str]] = []
            in_file: dict[str, Trade] = {}
            conflicts: list[Conflict] = []
            duplicates = 0
            for trade, key in zip(trades, keys, strict=True):
                earlier = stored.get(key) or in_file.get(key)
                if earlier is None:
                    twin = timed.get(_timed_details(trade)) if trade.executed_at else None
                    if twin is not None and _source(twin) != _source(trade):
                        conflicts.append(Conflict(trade, twin, "other_source"))
                        continue
                    lookalike = untimed.get(details(trade))
                    if (trade.executed_at is None and lookalike is not None
                            and _source(lookalike) != _source(trade)):
                        possible.append(Conflict(trade, lookalike, "other_source"))
                    in_file[key] = trade
                    fresh.append((trade, key))
                elif same_details(trade, earlier):
                    duplicates += 1
                else:
                    conflicts.append(Conflict(trade, earlier))
            if conflicts:
                return ImportOutcome(None, 0, duplicates, conflicts=tuple(conflicts))
            if not fresh:
                return ImportOutcome(None, 0, duplicates)
            batch_id = self._insert_batch(
                profile_id, batch, [t for t, _ in fresh], [k for _, k in fresh],
                rows_read=len(trades), duplicates=duplicates, warnings=warnings, read=trades)
        return ImportOutcome(batch_id, len(fresh), duplicates,
                             possible_duplicates=tuple(possible))

    def undo_batch(self, profile_id: int, batch_id: int) -> int:
        """Remove a batch's trades and mark it undone (kept in the history); returns how many
        trades were removed. The same file can then be imported again."""
        with self._transaction():
            row = self._db.execute(
                "SELECT undone_at FROM import_batch WHERE id = ? AND profile_id = ?",
                (batch_id, profile_id)).fetchone()
            if row is None:
                raise LedgerError(f"no import {batch_id} for this profile")
            if row[0] is not None:
                raise LedgerError(f"import {batch_id} was already undone on {row[0]}")
            if self._db.execute("SELECT kind FROM import_batch WHERE id = ?",
                                (batch_id,)).fetchone()[0] == "manual":
                raise LedgerError("hand-entered purchases are changed in the settings, not undone")
            # Purchases entered for this import's sales go with them.
            self._db.execute(
                "DELETE FROM trade WHERE profile_id = ? AND resolves_source_id IN"
                " (SELECT source_id FROM trade WHERE batch_id = ?)", (profile_id, batch_id))
            removed = self._db.execute("DELETE FROM trade WHERE batch_id = ?", (batch_id,))
            self._db.execute("UPDATE import_batch SET undone_at = ? WHERE id = ?",
                             (_now(), batch_id))
        return removed.rowcount

    def _insert_batch(self, profile_id: int, batch: Batch, trades: Sequence[Trade],
                      keys: Sequence[str], *, rows_read: int, duplicates: int,
                      warnings: Sequence[str], read: Sequence[Trade] = ()) -> int:
        """``read`` is every trade in the file (duplicates included), for its date range."""
        dates = [t.trade_date for t in (read or trades)]
        cursor = self._db.execute(
            "INSERT INTO import_batch (profile_id, kind, broker, file_name, file_sha256,"
            " imported_at, date_from, date_to, rows_read, trades_added, duplicates_skipped,"
            " warnings_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (profile_id, batch.kind, batch.broker, batch.file_name, batch.file_sha256, _now(),
             min(dates).isoformat() if dates else None,
             max(dates).isoformat() if dates else None, rows_read, len(trades), duplicates,
             json.dumps(list(warnings)) if warnings else None))
        batch_id = _rowid(cursor)
        for trade, key in zip(trades, keys, strict=True):
            self._insert_trade(profile_id, batch_id, trade, key)
        return batch_id

    def _instrument_id(self, trade: Trade) -> int:
        column = "contract_symbol" if trade.segment is Segment.FNO else "isin"
        row = self._db.execute(
            f"SELECT id, kind FROM instrument WHERE {column} = ?",  # noqa: S608 - fixed columns
            (trade.instrument,)).fetchone()
        if row:
            if row[1] != _KIND_OF_SEGMENT[trade.segment]:
                self._db.execute("UPDATE instrument SET kind = ? WHERE id = ?",
                                 (_KIND_OF_SEGMENT[trade.segment], row[0]))
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
        ordered = sorted(self._select(profile_id), key=_replay_order)
        return [trade for _, trade, _ in ordered]

    def _select(self, profile_id: int, *, manual: bool = False) -> list[tuple[int, Trade, str]]:
        """(row id, trade, dedupe key) for every live imported trade of the profile, or with
        ``manual`` its hand-entered purchases instead."""
        charges: dict[int, dict[str, Decimal]] = {}
        for trade_row, kind, amount in self._db.execute(
                "SELECT c.trade_id, c.kind, c.amount FROM trade_charge c"
                " JOIN trade t ON t.id = c.trade_id WHERE t.profile_id = ?", (profile_id,)):
            charges.setdefault(trade_row, {})[kind] = text_decimal(amount)
        rows = self._db.execute(
            "SELECT t.id, t.source_id, t.trade_date, COALESCE(i.isin, i.contract_symbol),"
            " t.side, t.quantity, t.price, t.segment, t.executed_at, t.dedupe_key"
            " FROM trade t JOIN instrument i ON i.id = t.instrument_id"
            " JOIN import_batch b ON b.id = t.batch_id"
            " WHERE t.profile_id = ? AND b.undone_at IS NULL AND (b.kind = 'manual') = ?"
            " ORDER BY t.id", (profile_id, manual))
        return [(row[0], _trade(row[:9], charges.get(row[0], {})), row[9]) for row in rows]



    def batches(self, profile_id: int) -> list[dict[str, Any]]:
        self._require_profile(profile_id)
        cursor = self._db.execute(
            "SELECT id, kind, broker, file_name, file_sha256, imported_at, date_from, date_to,"
            " rows_read, trades_added, duplicates_skipped FROM import_batch"
            " WHERE profile_id = ? AND undone_at IS NULL AND kind <> 'manual' ORDER BY id",
            (profile_id,))
        names = [c[0] for c in cursor.description]
        return [dict(zip(names, row, strict=True)) for row in cursor]

    # -- replay -------------------------------------------------------------------------------

    def compute(self, profile_id: int, start_years: Iterable[int],
                **inputs: Any) -> list[TaxYearReport]:
        """Replay every stored trade and hand-entered purchase through ``compute_tax_years``
        (D2) with the profile's saved settings. ``inputs`` override a setting by name."""
        saved = self.settings(profile_id)
        stored: dict[str, Any] = {
            "fund_classes": saved.fund_classes, "fmv_2018": saved.fmv_2018,
            "brought_forward": saved.brought_forward, "excluded": saved.excluded}
        trades = self.trades(profile_id) + [m.trade for m in saved.manual_buys]
        return compute_tax_years(start_years, trades, **{**stored, **inputs})

    # -- settings (task 3) --------------------------------------------------------------------

    def settings(self, profile_id: int) -> Settings:
        self._require_profile(profile_id)
        classes: dict[str, FundClass] = {}
        guessed: set[str] = set()
        fmv: dict[str, Decimal] = {}
        names: dict[str, str] = {}
        for isin, fund_class, was_guessed, price, name in self._db.execute(
                "SELECT COALESCE(i.isin, i.contract_symbol), s.fund_class, s.fund_class_guessed,"
                " s.fmv_2018, s.name_for_112a FROM instrument_setting s"
                " JOIN instrument i ON i.id = s.instrument_id WHERE s.profile_id = ?"
                " ORDER BY i.id", (profile_id,)):
            if fund_class:
                classes[isin] = FundClass(fund_class)
                if was_guessed:
                    guessed.add(isin)
            if price:
                fmv[isin] = text_decimal(price)
            if name:
                names[isin] = name
        losses = tuple(
            LossEntry(year, LossKind(kind), text_decimal(amount))
            for kind, year, amount in self._db.execute(
                "SELECT kind, year_arose, amount FROM brought_forward_loss WHERE profile_id = ?"
                " ORDER BY id", (profile_id,)))
        how = {row[0]: (row[1], row[2]) for row in self._db.execute(
            "SELECT t.id, t.how_acquired, t.resolves_source_id FROM trade t"
            " JOIN import_batch b ON b.id = t.batch_id"
            " WHERE t.profile_id = ? AND b.kind = 'manual'", (profile_id,))}
        manual = tuple(ManualBuy(trade, *how[row])
                       for row, trade, _ in self._select(profile_id, manual=True))
        excluded = tuple(row[0] for row in self._db.execute(
            "SELECT t.source_id FROM excluded_sale e JOIN trade t ON t.id = e.trade_id"
            " WHERE t.profile_id = ? ORDER BY e.excluded_at, t.id", (profile_id,)))
        return Settings(classes, frozenset(guessed), fmv, names, losses, manual, excluded)

    def save_settings(self, profile_id: int, settings: Settings) -> None:
        """Replace the profile's settings with ``settings``, all or nothing. An exclusion for a
        sale that isn't saved (its import was undone) is dropped."""
        self._require_profile(profile_id)
        with self._transaction():
            self._db.execute("DELETE FROM instrument_setting WHERE profile_id = ?", (profile_id,))
            for isin in sorted({*settings.fund_classes, *settings.fmv_2018, *settings.names}):
                fund_class = settings.fund_classes.get(isin)
                price = settings.fmv_2018.get(isin)
                self._db.execute(
                    "INSERT INTO instrument_setting (profile_id, instrument_id, fund_class,"
                    " fund_class_guessed, fmv_2018, name_for_112a) VALUES (?, ?, ?, ?, ?, ?)",
                    (profile_id, self._instrument_for(isin),
                     fund_class.value if fund_class else None, int(isin in settings.guessed),
                     decimal_text(price) if price is not None else None,
                     settings.names.get(isin) or None))
            self._db.execute("DELETE FROM brought_forward_loss WHERE profile_id = ?",
                             (profile_id,))
            self._db.executemany(
                "INSERT INTO brought_forward_loss (profile_id, kind, year_arose, amount)"
                " VALUES (?, ?, ?, ?)",
                [(profile_id, e.kind.value, e.origin_year, decimal_text(e.amount))
                 for e in settings.brought_forward])
            self._save_manual(profile_id, settings.manual_buys)
            # Keep when each sale was first excluded, so the user's order survives a save.
            first: dict[int, str] = dict(self._db.execute(
                "SELECT trade_id, excluded_at FROM excluded_sale WHERE trade_id IN"
                " (SELECT id FROM trade WHERE profile_id = ?)", (profile_id,)).fetchall())
            self._db.execute(
                "DELETE FROM excluded_sale WHERE trade_id IN"
                " (SELECT id FROM trade WHERE profile_id = ?)", (profile_id,))
            moment = datetime.now(UTC).replace(microsecond=0)
            for n, sale in enumerate(dict.fromkeys(
                    s.removesuffix(DELIVERY_SUFFIX) for s in settings.excluded)):
                row = self._db.execute(
                    "SELECT t.id FROM trade t JOIN import_batch b ON b.id = t.batch_id"
                    " WHERE t.profile_id = ? AND t.source_id = ? AND b.undone_at IS NULL"
                    " AND b.kind <> 'manual'", (profile_id, sale)).fetchone()
                if row:
                    at = first.get(row[0]) or (moment + timedelta(microseconds=n)).isoformat()
                    self._db.execute(
                        "INSERT INTO excluded_sale (trade_id, reason, excluded_at)"
                        " VALUES (?, 'missing purchase history', ?)", (row[0], at))

    def _save_manual(self, profile_id: int, buys: Sequence[ManualBuy]) -> None:
        row = self._db.execute(
            "SELECT id FROM import_batch WHERE profile_id = ? AND kind = 'manual'"
            " AND undone_at IS NULL", (profile_id,)).fetchone()
        if row:
            batch_id = int(row[0])
            self._db.execute("DELETE FROM trade WHERE batch_id = ?", (batch_id,))
        elif buys:
            batch_id = self._insert_batch(profile_id, Batch(kind="manual"), [], [],
                                          rows_read=0, duplicates=0, warnings=())
        else:
            return
        for buy in buys:
            self._insert_trade(profile_id, batch_id, buy.trade, f"MANUAL|{buy.trade.trade_id}")
            self._db.execute(
                "UPDATE trade SET how_acquired = ?, resolves_source_id = ?"
                " WHERE profile_id = ? AND dedupe_key = ?",
                (buy.how, buy.for_trade.removesuffix(DELIVERY_SUFFIX), profile_id,
                 f"MANUAL|{buy.trade.trade_id}"))
        self._db.execute("UPDATE import_batch SET trades_added = ? WHERE id = ?",
                         (len(buys), batch_id))

    def _instrument_for(self, code: str) -> int:
        row = self._db.execute(
            "SELECT id FROM instrument WHERE isin = ? OR contract_symbol = ?",
            (code, code)).fetchone()
        if row:
            return int(row[0])
        # Provisional until a trade is imported for it (an ETF's ISIN also starts with INF);
        # `_instrument_id` sets the kind from the trade's segment.
        kind = "MF" if code.startswith("INF") else "EQUITY"
        return _rowid(self._db.execute(
            "INSERT INTO instrument (isin, kind) VALUES (?, ?)", (code, kind)))


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


def _source(trade: Trade) -> str:
    """The importer that made the trade id: the text before its first ':'."""
    return trade.trade_id.split(":", 1)[0]


def _timed_details(trade: Trade) -> tuple[str, ...]:
    at = trade.executed_at
    return (*details(trade), at.isoformat() if at else "")


def _replay_order(item: tuple[int, Trade, str]) -> tuple[date, bool, datetime, int]:
    """Same order as ``importers.tabular._order_key``; an aware time is compared as IST wall
    time so it sorts with the naive exchange times the importers give."""
    row_id, trade, _ = item
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
