"""The persistent local ledger (brief 0001 D1, D2): one SQLite file per installation.

Trades are the source of truth. Lots, gains and carried-forward losses are never stored as
editable state: they are rebuilt by replaying every trade through the same FIFO matcher and
``compute_tax_years`` (D2 option A), so importing an older file later corrects every year.

Uses only the standard library ``sqlite3``; nothing here opens a network connection.
"""

import hashlib
import json
import sqlite3
import tempfile
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from engine import __version__
from engine.api import TaxYearReport, compute_tax_years
from engine.classify.funds import FundClass
from engine.classify.trades import DELIVERY_SUFFIX, OPENING_PREFIX
from engine.identifiers import is_valid_isin, scrip_key
from engine.ledger.dedupe import account_key, dedupe_keys, details, same_details
from engine.ledger.migrations import LATEST, MIGRATIONS
from engine.ledger.settings import ManualBuy, Settings
from engine.models import CHARGE_KINDS, Segment, Side, Trade, Transfer
from engine.money import ZERO
from engine.rules.setoff import LossEntry, LossKind

NAME_KEY = "NAME:"
"""Instrument key of a company imported by name, before its ISIN is known (brief 0002)."""
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
class FiledYear:
    """A year marked as filed, with its figures exactly as they were then."""

    start_year: int
    filed_at: str
    itr_form: str | None
    engine_version: str
    report: dict[str, Any]


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

    def __init__(self, connection: sqlite3.Connection, path: Path | None = None) -> None:
        self._db = connection
        self._path = path
        self._rehearsing = False

    @contextmanager
    def rehearsal(self) -> Iterator[None]:
        """Run real changes inside one transaction and roll them all back at the end.

        The import preview (brief 0004) saves the files exactly as Save would, in order, so a
        file sees the files before it in the same request, then undoes everything: the preview
        can't differ from the save."""
        self._db.execute("BEGIN IMMEDIATE")
        self._rehearsing = True
        try:
            yield
        finally:
            self._rehearsing = False
            # SQLite may already have rolled back by itself (disk full, I/O error): rolling
            # back again would raise and hide that error.
            if self._db.in_transaction:
                self._db.execute("ROLLBACK")

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
            # Functions in a file's own triggers or views can't run (defence for restores).
            db.execute("PRAGMA trusted_schema = OFF")
            ledger = cls(db, path)
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

    # -- backup and restore (task 5) ---------------------------------------------------------

    def backup(self) -> bytes:
        """A consistent copy of the whole ledger (every person), as one SQLite file.

        Uses ``VACUUM INTO``, which copies a snapshot even while the file is in use. The copy
        is not encrypted (answer 4 in brief 0001)."""
        with tempfile.TemporaryDirectory(prefix="kosh-backup-") as folder:
            target = Path(folder) / "backup.kosh"
            self._db.execute("VACUUM INTO ?", (str(target),))
            return target.read_bytes()

    def restore(self, data: bytes) -> Path:
        """Replace this ledger's contents with the backup ``data``; returns where the data
        from before the restore was kept (``kosh.sqlite.before-restore-<time>``, never
        overwritten, so a second restore can't lose the original).

        Nothing is touched until the backup passes every check: the SQLite header and
        integrity, a Kosh schema no newer than this app (upgraded if older), and exactly the
        tables, indexes and nothing else that this app's schema has, so a doctored file can't
        bring triggers or views that act later. The backup is then copied in with SQLite's
        backup API under the ledger's own lock: no file is swapped, so a failure part-way,
        another Kosh window or Windows file locking can't leave a half-restored ledger."""
        if self._path is None:
            raise LedgerError("this ledger has no file to restore into")
        current = self._path
        incoming = current.with_name(current.name + ".restoring")
        incoming.write_bytes(data)
        try:
            _check_integrity(incoming)
            with Ledger.open(incoming) as checked:  # refuses foreign or newer, upgrades older
                if checked._schema() != _expected_schema():
                    raise LedgerError("the backup's database structure isn't Kosh's own (it has "
                                      "extra or changed tables, views or triggers)")
                before = _unused(current.with_name(
                    f"{current.name}.before-restore-{datetime.now(UTC):%Y%m%d-%H%M%S}"))
                try:
                    keep = sqlite3.connect(before)
                    try:
                        _copy(self._db, keep)  # a consistent copy of the current data
                    finally:
                        keep.close()
                    _copy(checked._db, self._db)  # all or nothing, under the ledger's lock
                except sqlite3.OperationalError as error:
                    if "locked" in str(error) or "busy" in str(error):
                        raise LedgerError("the ledger is busy in another Kosh window; close it "
                                          "and try again") from None
                    raise LedgerError(f"the restore failed: {error}") from None
        finally:
            incoming.unlink(missing_ok=True)
        return before

    def _schema(self) -> set[tuple[str, str, str, str]]:
        """Every table, index, view and trigger, with its SQL (SQLite's own objects aside)."""
        return {(str(t), str(n), str(tbl), str(sql)) for t, n, tbl, sql in self._db.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master"
            " WHERE name NOT LIKE 'sqlite_%'")}

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
                if version == 3:  # accounts now identify opening lots (brief 0003)
                    self._rekey_opening()
                self._db.execute(
                    "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
                    "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (str(version),))
            if current == 0:
                self._db.executemany("INSERT INTO meta (key, value) VALUES (?, ?)", [
                    ("created_at", _now()), ("created_by_app_version", __version__)])

    def _transaction(self) -> "_Transaction | _Savepoint":
        return _Savepoint(self._db) if self._rehearsing else _Transaction(self._db)

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

    def imported_into(self, profile_id: int, file_sha256: str) -> str | None:
        """The account a live import of this exact file went into, if any."""
        row = self._db.execute(
            "SELECT a.label FROM import_batch b JOIN account a ON a.id = b.account_id"
            " WHERE b.profile_id = ? AND b.file_sha256 = ? AND b.undone_at IS NULL",
            (profile_id, file_sha256)).fetchone()
        return str(row[0]) if row else None

    def imported_on(self, profile_id: int, file_sha256: str) -> str | None:
        """When this exact file was imported, if it is in a live batch."""
        row = self._db.execute(
            "SELECT imported_at FROM import_batch WHERE profile_id = ? AND file_sha256 = ?"
            " AND undone_at IS NULL", (profile_id, file_sha256)).fetchone()
        return str(row[0]) if row else None

    def import_trades(self, profile_id: int, batch: Batch, trades: Sequence[Trade], *,
                      warnings: Sequence[str] = (),
                      how_acquired: Mapping[str, str] | None = None,
                      account: str | None = None,
                      raw_names: Mapping[str, str] | None = None) -> ImportOutcome:
        """Import one file (D3): skip trades already in the ledger, refuse the whole file if a
        trade's key is there with different details, and recognise a file already imported.
        Nothing is stored unless at least one trade is new. Inside ``rehearsal()`` everything
        is rolled back afterwards (the import preview). ``raw_names`` (trade id → company name
        as the file writes it) is kept with each trade, so a name confirmed as an ISIN can be
        undone (brief 0007)."""
        if batch.kind not in BATCH_KINDS:
            raise LedgerError(f"unknown batch kind {batch.kind!r}")
        self._require_profile(profile_id)
        if account is not None:  # the file's account, unless a row names its own
            trades = [t if t.account else replace(t, account=account) for t in trades]
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
                elif same_details(trade, earlier) and not self._corrected(
                        profile_id, trade, earlier, key, how_acquired):
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
            if account is not None:
                self._db.execute("UPDATE import_batch SET account_id = ? WHERE id = ?",
                                 (self._account_id(profile_id, account), batch_id))
            for trade, key in fresh:
                if how_acquired and trade.trade_id in how_acquired:
                    self._db.execute(
                        "UPDATE trade SET how_acquired = ? WHERE profile_id = ? AND dedupe_key = ?",
                        (how_acquired[trade.trade_id], profile_id, key))
                if raw_names and trade.trade_id in raw_names:
                    self._db.execute(
                        "UPDATE trade SET raw_symbol = ? WHERE profile_id = ? AND dedupe_key = ?",
                        (raw_names[trade.trade_id], profile_id, key))
        return ImportOutcome(batch_id, len(fresh), duplicates,
                             possible_duplicates=tuple(possible))

    def _corrected(self, profile_id: int, new: Trade, saved: Trade, key: str,
                   how_acquired: Mapping[str, str] | None) -> bool:
        """An opening lot entered again with other charges or another way of acquiring is a
        correction, not a duplicate: it's refused as a conflict, never silently dropped."""
        if not new.trade_id.startswith(OPENING_PREFIX):
            return False
        if new.charges != saved.charges:
            return True
        row = self._db.execute("SELECT how_acquired FROM trade WHERE profile_id = ? AND"
                               " dedupe_key = ?", (profile_id, key)).fetchone()
        stored_how = row[0] if row else None
        new_how = (how_acquired or {}).get(new.trade_id)
        return stored_how is not None and new_how is not None and stored_how != new_how

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
        account = (None if trade.segment is Segment.MUTUAL_FUND  # funds are per folio
                   else self._account_id(profile_id, trade.account))
        cursor = self._db.execute(
            "INSERT INTO trade (profile_id, batch_id, instrument_id, source_id, segment,"
            " trade_date, executed_at, side, quantity, price, dedupe_key, account_id,"
            " entered_on, raw_symbol) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (profile_id, batch_id, self._instrument_id(trade), trade.trade_id,
             trade.segment.value, trade.trade_date.isoformat(),
             trade.executed_at.isoformat() if trade.executed_at else None, trade.side.value,
             decimal_text(trade.quantity), decimal_text(trade.price), key, account,
             trade.entered_on.isoformat() if trade.entered_on else None,
             # A company known only by name keeps it, so confirming its ISIN can be undone.
             trade.instrument.removeprefix(NAME_KEY)
             if trade.instrument.startswith(NAME_KEY) else None))
        trade_row = _rowid(cursor)
        # Charges by type when the file gives them (brief 0005), else one "other" total.
        charges = [*(trade.charge_parts or (("OTHER", trade.charges),)), ("STT", trade.stt)]
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
            " t.side, t.quantity, t.price, t.segment, t.executed_at, ac.label, t.entered_on,"
            " t.dedupe_key"
            " FROM trade t JOIN instrument i ON i.id = t.instrument_id"
            " JOIN import_batch b ON b.id = t.batch_id"
            " LEFT JOIN account ac ON ac.id = t.account_id"
            " WHERE t.profile_id = ? AND b.undone_at IS NULL AND (b.kind = 'manual') = ?"
            " ORDER BY t.id", (profile_id, manual))
        return [(row[0], _trade(row[:11], charges.get(row[0], {})), row[11]) for row in rows]



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
            "brought_forward": saved.brought_forward, "excluded": saved.excluded,
            "late_returns": saved.late_returns, "transfers": self.transfers(profile_id),
            "residency": saved.residency}
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
        on_time = {int(year): bool(value) for year, value in self._db.execute(
            "SELECT start_year, return_filed_on_time FROM year_setting WHERE profile_id = ?"
            " AND return_filed_on_time IS NOT NULL ORDER BY start_year", (profile_id,))}
        status = {int(year): str(value) for year, value in self._db.execute(
            "SELECT start_year, residency FROM year_setting WHERE profile_id = ?"
            " AND residency <> 'RES' ORDER BY start_year", (profile_id,))}
        return Settings(classes, frozenset(guessed), fmv, names, losses, manual, excluded,
                        on_time, status)

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
            # Year rows also hold the filed date, so they're updated, never deleted here.
            self._db.execute("UPDATE year_setting SET return_filed_on_time = NULL,"
                             " residency = 'RES' WHERE profile_id = ?", (profile_id,))
            for year, status in settings.residency.items():
                self._db.execute(
                    "INSERT INTO year_setting (profile_id, start_year, residency)"
                    " VALUES (?, ?, ?) ON CONFLICT (profile_id, start_year)"
                    " DO UPDATE SET residency = excluded.residency", (profile_id, year, status))
            for year, on_time in settings.filed_on_time.items():
                self._db.execute(
                    "INSERT INTO year_setting (profile_id, start_year, return_filed_on_time)"
                    " VALUES (?, ?, ?) ON CONFLICT (profile_id, start_year)"
                    " DO UPDATE SET return_filed_on_time = excluded.return_filed_on_time",
                    (profile_id, year, int(on_time)))
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

    # -- filed years (task 4) ------------------------------------------------------------------

    def mark_filed(self, profile_id: int, start_year: int, report: Mapping[str, Any],
                   itr_form: str | None = None) -> str:
        """Keep ``report`` (RPC report JSON) as the year's "as filed" figures; returns when."""
        self._require_profile(profile_id)
        if not report.get("complete", True):
            raise LedgerError("a year with sales missing purchase history can't be marked filed")
        when = _now()
        text = json.dumps(report, sort_keys=True, ensure_ascii=False)
        with self._transaction():
            self._db.execute(
                "INSERT INTO year_setting (profile_id, start_year, itr_form, filed_at)"
                " VALUES (?, ?, ?, ?) ON CONFLICT (profile_id, start_year) DO UPDATE SET"
                " itr_form = excluded.itr_form, filed_at = excluded.filed_at",
                (profile_id, start_year, itr_form, when))
            self._db.execute(
                "INSERT OR REPLACE INTO year_snapshot (profile_id, start_year,"
                " inputs_fingerprint, engine_version, computed_at, report_json, open_lots_json)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                # The column holds a hash of the filed report itself: changes are found by
                # comparing figures (filing.changes), not by hashing inputs.
                (profile_id, start_year, hashlib.sha256(text.encode()).hexdigest(),
                 __version__, when, text,
                 json.dumps(report.get("open_lots", []), sort_keys=True, ensure_ascii=False)))
        return when

    def unmark_filed(self, profile_id: int, start_year: int) -> None:
        """Forget that the year was filed, and its "as filed" figures."""
        self._require_profile(profile_id)
        with self._transaction():
            self._db.execute("UPDATE year_setting SET filed_at = NULL, itr_form = NULL"
                             " WHERE profile_id = ? AND start_year = ?", (profile_id, start_year))
            self._db.execute("DELETE FROM year_snapshot WHERE profile_id = ? AND start_year = ?",
                             (profile_id, start_year))

    def filed(self, profile_id: int, start_year: int) -> FiledYear | None:
        row = self._db.execute(
            "SELECT y.filed_at, y.itr_form, s.engine_version, s.report_json FROM year_setting y"
            " JOIN year_snapshot s ON s.profile_id = y.profile_id AND s.start_year = y.start_year"
            " WHERE y.profile_id = ? AND y.start_year = ? AND y.filed_at IS NOT NULL",
            (profile_id, start_year)).fetchone()
        if row is None:
            return None
        return FiledYear(start_year, row[0], row[1], row[2], json.loads(row[3]))

    def filed_years(self, profile_id: int) -> list[int]:
        return [int(r[0]) for r in self._db.execute(
            "SELECT start_year FROM year_setting WHERE profile_id = ? AND filed_at IS NOT NULL"
            " ORDER BY start_year", (profile_id,))]

    def _save_manual(self, profile_id: int, buys: Sequence[ManualBuy]) -> None:
        row = self._db.execute(
            "SELECT id FROM import_batch WHERE profile_id = ? AND kind = 'manual'"
            " AND undone_at IS NULL", (profile_id,)).fetchone()
        # A purchase entered under a company's name and moved to its ISIN keeps the name, so
        # undoing the ISIN still finds it (brief 0007).
        names: dict[tuple[str, str], str] = {}
        if row:
            batch_id = int(row[0])
            names = {(str(source), str(code)): str(raw) for source, code, raw in self._db.execute(
                "SELECT t.source_id, COALESCE(i.isin, i.contract_symbol), t.raw_symbol"
                " FROM trade t JOIN instrument i ON i.id = t.instrument_id"
                " WHERE t.batch_id = ? AND t.raw_symbol IS NOT NULL", (batch_id,))}
            self._db.execute("DELETE FROM trade WHERE batch_id = ?", (batch_id,))
        elif buys:
            batch_id = self._insert_batch(profile_id, Batch(kind="manual"), [], [],
                                          rows_read=0, duplicates=0, warnings=())
        else:
            return
        for buy in buys:
            self._insert_trade(profile_id, batch_id, buy.trade, f"MANUAL|{buy.trade.trade_id}")
            self._db.execute(
                "UPDATE trade SET how_acquired = ?, resolves_source_id = ?,"
                # Else the name of the sale it's for, if that sale was read by name.
                " raw_symbol = COALESCE(?, raw_symbol, (SELECT s.raw_symbol FROM trade s"
                "   WHERE s.profile_id = trade.profile_id AND s.source_id = ?"
                "   AND s.instrument_id = trade.instrument_id AND s.batch_id <> trade.batch_id))"
                " WHERE profile_id = ? AND dedupe_key = ?",
                (buy.how, buy.for_trade.removesuffix(DELIVERY_SUFFIX),
                 names.get((buy.trade.trade_id, buy.trade.instrument)),
                 buy.for_trade.removesuffix(DELIVERY_SUFFIX), profile_id,
                 f"MANUAL|{buy.trade.trade_id}"))
        self._db.execute("UPDATE import_batch SET trades_added = ? WHERE id = ?",
                         (len(buys), batch_id))

    # -- accounts and transfers (brief 0003) --------------------------------------------------

    def accounts(self, profile_id: int) -> list[str]:
        """The person's demat accounts, by name, in the order they were first used."""
        self._require_profile(profile_id)
        return [str(r[0]) for r in self._db.execute(
            "SELECT label FROM account WHERE profile_id = ? ORDER BY id", (profile_id,))]

    def _find_account(self, profile_id: int, name: str) -> tuple[int, str] | None:
        """(id, stored name) of the account ``name`` refers to: case, spaces and punctuation
        are ignored, so "ICICI Direct", "icici-direct" and "ICICIDIRECT" are one account."""
        wanted = account_key(name)
        for account_id, label in self._db.execute(
                "SELECT id, label FROM account WHERE profile_id = ? ORDER BY id", (profile_id,)):
            if account_key(label) == wanted:
                return int(account_id), str(label)
        return None

    def account_name(self, profile_id: int, name: str) -> str | None:
        """The stored name of the account ``name`` refers to, if there is one."""
        found = self._find_account(profile_id, name)
        return found[1] if found else None

    def _account_id(self, profile_id: int, name: str | None) -> int | None:
        """The account ``name`` refers to, created on first use."""
        if name is None:
            return None
        label = " ".join(name.split())
        if not account_key(label):
            raise LedgerError("an account needs a name with a letter or digit")
        found = self._find_account(profile_id, label)
        if found:
            return found[0]
        return _rowid(self._db.execute(
            "INSERT INTO account (profile_id, broker, label) VALUES (?, ?, ?)",
            (profile_id, account_key(label), label)))

    def rename_account(self, profile_id: int, old: str, new: str) -> None:
        self._require_profile(profile_id)
        label = " ".join(new.split())
        if not account_key(label):
            raise LedgerError("an account needs a name with a letter or digit")
        with self._transaction():
            account = self._find_account(profile_id, old)
            if account is None:
                raise LedgerError(f"no account called {old!r}")
            clash = self._find_account(profile_id, label)
            if clash and clash[0] != account[0]:
                raise LedgerError(f"there is already an account called {clash[1]!r}")
            self._db.execute("UPDATE account SET label = ?, broker = ? WHERE id = ?",
                             (label, account_key(label), account[0]))
            self._rekey_opening(profile_id)  # opening lots' keys carry the account name

    def assign_account(self, profile_id: int, name: str,
                       trade_ids: Sequence[str] | None = None) -> int:
        """Put share trades that have no account yet into ``name`` (all of them, or just
        ``trade_ids``); returns how many moved. Opening lots get new duplicate keys, since
        the account is part of what identifies them."""
        self._require_profile(profile_id)
        with self._transaction():
            account = self._account_id(profile_id, name)
            where = "profile_id = ? AND account_id IS NULL AND segment <> 'MF'"
            args: list[Any] = [profile_id]
            if trade_ids is not None:
                where += f" AND source_id IN ({','.join('?' * len(trade_ids))})"
                args += list(trade_ids)
            changed = self._db.execute(
                f"UPDATE trade SET account_id = ? WHERE {where}",  # noqa: S608 - placeholders
                (account, *args))
            try:
                self._rekey_opening(profile_id)
            except sqlite3.IntegrityError:
                raise LedgerError(
                    f"{name!r} already has the same holding saved (same share, date, quantity "
                    "and price), so this would count it twice. Undo one of the two entries in "
                    "the import history first.") from None
        return changed.rowcount

    def _rekey_opening(self, profile_id: int | None = None) -> None:
        """Recompute opening lots' duplicate keys (they depend on the lot's account)."""
        query = ("SELECT t.id, t.profile_id, t.batch_id, t.source_id, t.trade_date,"
                 " COALESCE(i.isin, i.contract_symbol), t.side, t.quantity, t.price, t.segment,"
                 " t.executed_at, a.label, t.entered_on FROM trade t"
                 " JOIN instrument i ON i.id = t.instrument_id"
                 " LEFT JOIN account a ON a.id = t.account_id"
                 " WHERE t.source_id LIKE 'OPENING:%'")
        args: tuple[Any, ...] = ()
        if profile_id is not None:
            query += " AND t.profile_id = ?"
            args = (profile_id,)
        batches: dict[tuple[int, int], list[tuple[int, Trade]]] = {}
        for row in self._db.execute(query + " ORDER BY t.id", args).fetchall():
            trade = _trade((row[0], *row[3:]), {})
            batches.setdefault((row[1], row[2]), []).append((row[0], trade))
        for rows in batches.values():  # occurrence counts are per save, as on entry
            for (row_id, _), key in zip(rows, dedupe_keys([t for _, t in rows]), strict=True):
                self._db.execute("UPDATE trade SET dedupe_key = ? WHERE id = ?", (key, row_id))

    def add_transfer(self, profile_id: int, on: date, instrument: str, quantity: Decimal,
                     from_account: str, to_account: str) -> int:
        """Record shares moved between two of the person's accounts; returns its id."""
        self._require_profile(profile_id)
        if not isinstance(quantity, Decimal) or quantity <= 0:
            raise LedgerError("a transfer needs a positive quantity")
        with self._transaction():
            source = self._account_id(profile_id, from_account)
            target = self._account_id(profile_id, to_account)
            if source == target:
                raise LedgerError("a transfer needs two different accounts")
            return _rowid(self._db.execute(
                "INSERT INTO transfer (profile_id, on_date, instrument_id, quantity,"
                " from_account_id, to_account_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (profile_id, on.isoformat(), self._instrument_for(instrument),
                 decimal_text(quantity), source, target, _now())))

    def remove_transfer(self, profile_id: int, transfer_id: int) -> None:
        with self._transaction():
            gone = self._db.execute("DELETE FROM transfer WHERE id = ? AND profile_id = ?",
                                    (transfer_id, profile_id))
            if gone.rowcount == 0:
                raise LedgerError(f"no transfer {transfer_id} for this person")

    def transfers(self, profile_id: int) -> list[Transfer]:
        self._require_profile(profile_id)
        return [Transfer(f"TRANSFER:{row[0]}", date.fromisoformat(row[1]), row[2],
                         text_decimal(row[3]), row[4], row[5])
                for row in self._db.execute(
                    "SELECT t.id, t.on_date, COALESCE(i.isin, i.contract_symbol), t.quantity,"
                    " f.label, g.label FROM transfer t"
                    " JOIN instrument i ON i.id = t.instrument_id"
                    " JOIN account f ON f.id = t.from_account_id"
                    " JOIN account g ON g.id = t.to_account_id"
                    " WHERE t.profile_id = ? ORDER BY t.on_date, t.id", (profile_id,))]

    # -- company names to ISINs (brief 0007) ----------------------------------------------------

    def unmapped_names(self, profile_id: int) -> list[tuple[str, int]]:
        """Companies this person has imported only by name (``NAME:`` keys, e.g. Angel One),
        with how many imported trades each. Hand-entered purchases aren't counted."""
        self._require_profile(profile_id)
        return [(str(code)[len(NAME_KEY):], int(count)) for code, count in self._db.execute(
            "SELECT i.isin, COUNT(*) FROM trade t JOIN instrument i ON i.id = t.instrument_id"
            " JOIN import_batch b ON b.id = t.batch_id"
            " WHERE t.profile_id = ? AND b.undone_at IS NULL AND b.kind <> 'manual'"
            " AND i.isin LIKE 'NAME:%' GROUP BY i.isin ORDER BY i.isin", (profile_id,))]

    def mapped_names(self, profile_id: int, broker: str = "angelone") -> list[tuple[str, str]]:
        """(name as in the file, ISIN) for every name this person has confirmed."""
        self._require_profile(profile_id)
        return [(str(raw), str(isin)) for raw, isin in self._db.execute(
            "SELECT a.raw_name, i.isin FROM name_alias a JOIN instrument i"
            " ON i.id = a.instrument_id WHERE a.profile_id = ? AND a.broker = ?"
            " ORDER BY a.raw_name", (profile_id, broker))]

    def aliases(self, profile_id: int, broker: str) -> dict[str, str]:
        """Name as ``broker`` writes it → ISIN, as this person confirmed (used on import)."""
        return dict(self.mapped_names(profile_id, broker))

    def map_name(self, profile_id: int, broker: str, name: str, isin: str) -> int:
        """Confirm that ``name`` (as ``broker`` writes it) is ``isin`` for this person: remember
        it for their future imports, and move their trades, settings and transfers under that
        name to the ISIN in one transaction. Returns how many trades moved.

        Duplicate keys use the broker's trade id, so they don't change, and a later import
        with the ISIN is a duplicate. What the name's settings and transfers were is recorded,
        so ``unmap_name`` can put them back."""
        self._require_profile(profile_id)
        code = "".join(isin.split()).upper()
        if not is_valid_isin(code):
            raise LedgerError(f"{isin!r} isn't a valid Indian ISIN (check for a typo)")
        name = scrip_key(name)
        if not name:
            raise LedgerError("a company name is needed")
        with self._transaction():
            known = self._db.execute(
                "SELECT i.isin FROM name_alias a JOIN instrument i ON i.id = a.instrument_id"
                " WHERE a.profile_id = ? AND a.broker = ? AND a.raw_name = ?",
                (profile_id, broker, name)).fetchone()
            if known:
                if known[0] == code:
                    return 0
                raise LedgerError(f"{name} is already matched to {known[0]}; undo that first")
            target = self._instrument_for(code)
            row = self._db.execute("SELECT id FROM instrument WHERE isin = ?",
                                   (NAME_KEY + name,)).fetchone()
            source = int(row[0]) if row else None
            transfers = [] if source is None else [int(r[0]) for r in self._db.execute(
                "SELECT id FROM transfer WHERE profile_id = ? AND instrument_id = ?",
                (profile_id, source))]
            self._db.execute(
                "INSERT INTO name_alias (profile_id, broker, raw_name, instrument_id,"
                " confirmed_at, name_setting_json, isin_setting_json, moved_transfers_json)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (profile_id, broker, name, target, _now(),
                 json.dumps(self._setting(profile_id, source) if source else None),
                 json.dumps(self._setting(profile_id, target)), json.dumps(transfers)))
            if source is None:
                return 0
            moved = self._db.execute(
                "UPDATE trade SET raw_symbol = COALESCE(raw_symbol, ?), instrument_id = ?"
                " WHERE profile_id = ? AND instrument_id = ?", (name, target, profile_id, source))
            self._db.execute("UPDATE transfer SET instrument_id = ? WHERE profile_id = ? AND"
                             " instrument_id = ?", (target, profile_id, source))
            self._move_setting(profile_id, source, target, name)
        return moved.rowcount

    def unmap_name(self, profile_id: int, broker: str, name: str) -> int:
        """Undo a confirmation for this person. Every trade read under ``name`` (as the file
        wrote it, including those imported after the confirmation, and purchases entered under
        the name or for one of its sales) goes back to it, and the remembered answer is
        dropped. Returns how many trades moved back.

        - **Transfers** go back if the confirmation moved them, or if the shares they move can
          only be the name's: the sending account holds nothing else of the ISIN.
        - **Settings** (fund class, 31-Jan-2018 price, 112A name): the name gets back what it
          had. What the confirmation put on the ISIN is taken off again, unless it was changed
          since; then, if nothing else of this person uses the ISIN, the change was about the
          name's shares and goes with them. Another name still matched to the ISIN fills in
          what is taken off, as its own confirmation would have."""
        self._require_profile(profile_id)
        name = scrip_key(name)
        with self._transaction():
            alias = self._db.execute(
                "SELECT instrument_id, name_setting_json, isin_setting_json,"
                " moved_transfers_json FROM name_alias"
                " WHERE profile_id = ? AND broker = ? AND raw_name = ?",
                (profile_id, broker, name)).fetchone()
            if alias is None:
                raise LedgerError(f"{name} isn't matched to an ISIN")
            target = int(alias[0])
            name_before, isin_before = json.loads(alias[1]), json.loads(alias[2])
            shared = self._db.execute(
                "SELECT 1 FROM trade WHERE profile_id = ? AND instrument_id = ?"
                " AND (raw_symbol IS NULL OR raw_symbol <> ?) LIMIT 1",
                (profile_id, target, name)).fetchone() is not None
            transfers = self._names_transfers(profile_id, target, name, json.loads(alias[3]))
            back = self._instrument_for(NAME_KEY + name)
            moved = self._db.execute(
                "UPDATE trade SET instrument_id = ? WHERE profile_id = ? AND instrument_id = ?"
                " AND raw_symbol = ?", (back, profile_id, target, name))
            self._db.executemany(
                "UPDATE transfer SET instrument_id = ? WHERE id = ?",
                [(back, transfer) for transfer in transfers])
            self._db.execute("DELETE FROM name_alias WHERE profile_id = ? AND broker = ? AND"
                             " raw_name = ?", (profile_id, broker, name))
            self._unmerge_setting(profile_id, target, back, name, name_before,
                                  isin_before, shared=shared)
        return moved.rowcount

    def _names_transfers(self, profile_id: int, target: int, name: str,
                         recorded: list[int]) -> set[int]:
        """Transfers of the ISIN that carry the name's shares: those the confirmation moved,
        and those sent from an account whose only shares of the ISIN are the name's (bought
        under it, or received only that way)."""
        holders: dict[int, set[bool]] = {}
        for account, own in self._db.execute(
                "SELECT account_id, raw_symbol IS ? FROM trade WHERE profile_id = ? AND"
                " instrument_id = ?", (name, profile_id, target)):
            holders.setdefault(account, set()).add(bool(own))
        names_only = {a for a, kinds in holders.items() if kinds == {True}}
        moves = [(int(i), f, t) for i, f, t in self._db.execute(
            "SELECT id, from_account_id, to_account_id FROM transfer WHERE profile_id = ? AND"
            " instrument_id = ? ORDER BY on_date, id", (profile_id, target))]
        while True:  # an account holding only what it received from such accounts is one too
            received: dict[int, set[bool]] = {}
            for _, source, to in moves:
                received.setdefault(to, set()).add(source in names_only)
            more = {a for a, kinds in received.items()
                    if kinds == {True} and a not in holders and a not in names_only}
            if not more:
                break
            names_only |= more
        return {i for i, source, _ in moves if source in names_only} | (
            set(recorded) & {i for i, _, _ in moves})

    def _unmerge_setting(self, profile_id: int, target: int, back: int, name: str,
                         name_before: list[Any] | None, isin_before: list[Any] | None, *,
                         shared: bool) -> None:
        """Settings when a confirmation is undone (see ``unmap_name``), field by field."""
        now = _fields(self._setting(profile_id, target))
        own = _fields(name_before)
        brought = _fields(name_before, name)  # what the name brought (its name if unset)
        before = _fields(isin_before)
        others = [(str(raw), json.loads(n), json.loads(i), str(b)) for raw, n, i, b in (
            self._db.execute(
            "SELECT raw_name, name_setting_json, isin_setting_json, broker FROM name_alias"
            " WHERE profile_id = ? AND instrument_id = ? ORDER BY confirmed_at, raw_name",
            (profile_id, target)))]
        later = [_fields(i) for _, _, i, _ in others]
        to_name, to_isin = list(own), list(now)
        for f in range(len(now)):
            from_name = before[f] is None and brought[f] is not None
            if not from_name or now[f] != brought[f]:  # the ISIN's own, or changed since
                if not shared and now[f] != (before[f] if before[f] is not None else brought[f]):
                    to_name[f], to_isin[f] = now[f], before[f]
                elif not shared:
                    to_isin[f] = before[f]
                continue
            # Take the name's value off the ISIN; the next name matched to it fills in.
            to_isin[f] = None
            for fields in later:
                if fields[f] == brought[f]:
                    fields[f] = None  # its snapshot held this name's value
            for n, (raw, name_row, _, _) in enumerate(others):
                value = _fields(name_row, raw)[f]
                if value is not None and later[n][f] is None:
                    to_isin[f] = value
                    for fields in later[n + 1:]:
                        if fields[f] is None:
                            fields[f] = value
                    break
        self._put_setting(profile_id, back, _setting_of(to_name))
        self._put_setting(profile_id, target, _setting_of(to_isin))
        for (raw, _, _, its_broker), fields in zip(others, later, strict=True):
            self._db.execute(
                "UPDATE name_alias SET isin_setting_json = ? WHERE profile_id = ? AND"
                " broker = ? AND raw_name = ?",
                (json.dumps(_setting_of(fields)), profile_id, its_broker, raw))

    def _setting(self, profile_id: int, instrument: int) -> list[Any] | None:
        """[fund class, guessed, 31-Jan-2018 price, name for 112A] for one instrument."""
        row = self._db.execute(
            "SELECT fund_class, fund_class_guessed, fmv_2018, name_for_112a FROM"
            " instrument_setting WHERE profile_id = ? AND instrument_id = ?",
            (profile_id, instrument)).fetchone()
        return list(row) if row else None

    def _put_setting(self, profile_id: int, instrument: int, setting: list[Any] | None) -> None:
        self._db.execute("DELETE FROM instrument_setting WHERE profile_id = ? AND"
                         " instrument_id = ?", (profile_id, instrument))
        if setting is not None:
            self._db.execute(
                "INSERT INTO instrument_setting (profile_id, instrument_id, fund_class,"
                " fund_class_guessed, fmv_2018, name_for_112a) VALUES (?, ?, ?, ?, ?, ?)",
                (profile_id, instrument, *setting))

    def _move_setting(self, profile_id: int, source: int, target: int, name: str) -> None:
        """Move the name's settings (fund class, 31-Jan-2018 price, name for 112A) to the ISIN,
        keeping anything already set for the ISIN; the name becomes the ISIN's name if unset.
        A fund class taken from the name keeps its "guessed" flag."""
        old = self._setting(profile_id, source) or [None, 0, None, None]
        self._db.execute(
            "INSERT INTO instrument_setting (profile_id, instrument_id, fund_class,"
            " fund_class_guessed, fmv_2018, name_for_112a) VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (profile_id, instrument_id) DO UPDATE SET"
            " fund_class_guessed = CASE WHEN fund_class IS NULL"
            "   THEN excluded.fund_class_guessed ELSE fund_class_guessed END,"
            " fund_class = COALESCE(fund_class, excluded.fund_class),"
            " fmv_2018 = COALESCE(fmv_2018, excluded.fmv_2018),"
            " name_for_112a = COALESCE(name_for_112a, excluded.name_for_112a)",
            (profile_id, target, old[0], old[1], old[2], old[3] or name))
        self._db.execute("DELETE FROM instrument_setting WHERE profile_id = ? AND"
                         " instrument_id = ?", (profile_id, source))

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


class _Savepoint:
    """A nested all-or-nothing step inside a rehearsal's transaction."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def __enter__(self) -> None:
        self._db.execute("SAVEPOINT step")

    def __exit__(self, kind: type[BaseException] | None, error: BaseException | None,
                 trace: TracebackType | None) -> None:
        if kind:
            self._db.execute("ROLLBACK TO step")
        self._db.execute("RELEASE step")


class _Transaction:
    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def __enter__(self) -> None:
        self._db.execute("BEGIN IMMEDIATE")

    def __exit__(self, kind: type[BaseException] | None, error: BaseException | None,
                 trace: TracebackType | None) -> None:
        self._db.execute("ROLLBACK" if kind else "COMMIT")


def _copy(source: sqlite3.Connection, target: sqlite3.Connection) -> None:
    """SQLite's online backup: a consistent copy of ``source`` into ``target`` in one step.
    SQLite waits while another connection holds a write lock; Kosh windows hold it only
    for the length of one save."""
    source.backup(target)


def _fields(setting: list[Any] | None, name: str | None = None) -> list[Any]:
    """An instrument's settings as three independent fields: (fund class, guessed), the
    31-Jan-2018 price, and the name for 112A (``name`` when unset, as ``_move_setting`` does)."""
    fund_class, guessed, fmv, named = setting or (None, 0, None, None)
    return [(fund_class, int(guessed)) if fund_class is not None else None, fmv, named or name]


def _setting_of(fields: list[Any]) -> list[Any] | None:
    """The row for ``_put_setting`` from ``_fields``; None when nothing is set."""
    if all(f is None for f in fields):
        return None
    fund_class, guessed = fields[0] or (None, 0)
    return [fund_class, guessed, fields[1], fields[2]]


def _expected_schema() -> set[tuple[str, str, str, str]]:
    """The schema a ledger made by this app has, built fresh in memory by the migrations."""
    fresh = Ledger(sqlite3.connect(":memory:", isolation_level=None))
    try:
        fresh._migrate()
        return fresh._schema()
    finally:
        fresh.close()


def _unused(path: Path) -> Path:
    """``path``, or with ``-2``, ``-3``… added if a file already has that name."""
    candidate, n = path, 1
    while candidate.exists():
        n += 1
        candidate = path.with_name(f"{path.name}-{n}")
    return candidate


def _check_integrity(path: Path) -> None:
    """Refuse a damaged, empty or non-Kosh file before it can replace the ledger. An empty
    file would otherwise open as a new, blank ledger and wipe everything."""
    with path.open("rb") as file:
        if file.read(16) != b"SQLite format 3\x00":
            raise LedgerError("the backup is not a Kosh ledger (not an SQLite file)")
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            result = db.execute("PRAGMA integrity_check").fetchall()
            # integrity_check doesn't look at foreign keys: rows pointing at a person, import
            # or instrument that isn't there would otherwise vanish silently after a restore.
            broken_links = db.execute("PRAGMA foreign_key_check").fetchall()
            has_version = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'").fetchone()
            if has_version:
                has_version = db.execute(
                    "SELECT 1 FROM meta WHERE key = 'schema_version'").fetchone()
        finally:
            db.close()
    except sqlite3.DatabaseError as error:
        raise LedgerError(f"the backup is not a Kosh ledger: {error}") from None
    if result != [("ok",)]:
        raise LedgerError(  # pragma: no cover - SQLite raises on most damage first
            "the backup is damaged (SQLite integrity check failed)")
    if not has_version:
        raise LedgerError("the backup has no Kosh schema version")
    if broken_links:
        raise LedgerError(f"the backup is damaged: {len(broken_links)} row(s) point at records "
                          "that aren't there")


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
    (_, source_id, on, instrument, side, quantity, price, segment, executed_at, account,
     entered) = row
    return Trade(
        trade_id=source_id,
        trade_date=date.fromisoformat(on),
        instrument=instrument,
        side=Side(side),
        quantity=text_decimal(quantity),
        price=text_decimal(price),
        charges=sum((v for k, v in charges.items() if k != "STT"), ZERO),
        stt=charges.get("STT", ZERO),
        # A lone "other" figure is a total that was never broken down.
        charge_parts=tuple((k, charges[k]) for k in CHARGE_KINDS if k in charges)
        if set(charges) - {"STT", "OTHER"} else (),
        segment=Segment(segment),
        executed_at=datetime.fromisoformat(executed_at) if executed_at else None,
        account=account,
        entered_on=date.fromisoformat(entered) if entered else None,
    )
