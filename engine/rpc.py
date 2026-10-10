"""JSON request/response bridge between the UI and the engine, over stdin/stdout.

The desktop app (Tauri) and the dev/preview web server start ``python -m engine.rpc`` as a
local child process and exchange one JSON object per line. There is no socket and no server:
nothing here can talk to the network (CLAUDE.md rule 1).

Request:  {"id": 1, "method": "compute", "params": {...}}
Response: {"id": 1, "result": {...}}  or  {"id": 1, "error": {"type": "...", "message": "..."}}

Amounts travel as decimal strings (never floats) and dates as ISO text. Imported trades are
kept in the local ledger file (``ledger_*`` methods, brief 0001); the calculation methods
are stateless and take the trades with each request.
"""

import base64
import hashlib
import json
import sys
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from engine import __version__
from engine.api import (
    FundClass,
    LossEntry,
    Shortfall,
    TaxYearReport,
    compute_tax_year,
    unclassified_funds,
)
from engine.classify.funds import isin_of
from engine.matching.corporate_actions import Bonus, CorporateAction, Split
from engine.models import Lot, Segment, Side, Trade, Transfer
from engine.rules.base import Citation
from engine.rules.setoff import LossKind

JSON = dict[str, Any]


class RequestError(ValueError):
    """The request is malformed (bad method, missing or invalid parameter)."""


# --- decoding -------------------------------------------------------------------------------

def _dec(value: Any, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise RequestError(f"{name} must be a decimal string, got {value!r}")
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise RequestError(f"{name} must be a decimal string, got {value!r}") from None
    if not result.is_finite():
        raise RequestError(f"{name} must be finite")
    return result


def _date(value: Any, name: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise RequestError(f"{name} must be an ISO date, got {value!r}") from None


def _text_or_none(value: Any) -> str | None:
    text = " ".join(str(value).split()) if value is not None else ""
    return text or None


def _transfers(items: Any) -> list[Transfer]:
    if not isinstance(items, list):
        raise RequestError("transfers must be a list")
    try:
        return [Transfer(str(t.get("transfer_id") or f"TRANSFER:{n}"), _date(t["on"], "on"),
                         str(t["instrument"]), _dec(t["quantity"], "quantity"),
                         _text_or_none(t.get("from_account")), _text_or_none(t.get("to_account")))
                for n, t in enumerate(items, start=1)]
    except KeyError as missing:
        raise RequestError(f"transfer is missing {missing}") from None


def _transfer_json(move: Transfer) -> JSON:
    return {"transfer_id": move.transfer_id, "on": move.on.isoformat(),
            "instrument": move.instrument, "isin": isin_of(move.instrument),
            "quantity": _s(move.quantity), "from_account": move.from_account,
            "to_account": move.to_account}


def trade_from_json(data: JSON) -> Trade:
    try:
        executed = data.get("executed_at")
        return Trade(
            trade_id=str(data["trade_id"]),
            trade_date=_date(data["trade_date"], "trade_date"),
            instrument=str(data["instrument"]),
            side=Side(data["side"]),
            quantity=_dec(data["quantity"], "quantity"),
            price=_dec(data["price"], "price"),
            charges=_dec(data.get("charges", "0"), "charges"),
            stt=_dec(data.get("stt", "0"), "stt"),
            segment=Segment(data.get("segment", Segment.EQUITY.value)),
            executed_at=datetime.fromisoformat(executed) if executed else None,
            account=_text_or_none(data.get("account")),
            entered_on=_date(data["entered_on"], "entered_on") if data.get("entered_on") else None,
        )
    except KeyError as missing:
        raise RequestError(f"trade is missing {missing}") from None


def _actions(items: list[JSON]) -> list[CorporateAction]:
    actions: list[CorporateAction] = []
    for item in items:
        kind = item.get("kind")
        if kind == "split":
            actions.append(Split(item["instrument"], _date(item["ex_date"], "ex_date"),
                                 old=int(item["old"]), new=int(item["new"])))
        elif kind == "bonus":
            allot = item.get("allotment_date")
            record = item.get("record_date")
            actions.append(Bonus(item["instrument"], _date(item["ex_date"], "ex_date"),
                                 held=int(item["held"]), bonus=int(item["bonus"]),
                                 allotment_date=_date(allot, "allotment_date") if allot else None,
                                 record_date=_date(record, "record_date") if record else None))
        else:
            raise RequestError(f"unknown corporate action kind {kind!r}")
    return actions


def _losses(items: list[JSON]) -> list[LossEntry]:
    try:
        return [LossEntry(int(e["origin_year"]), LossKind(e["kind"]), _dec(e["amount"], "amount"))
                for e in items]
    except KeyError as missing:
        raise RequestError(f"brought-forward loss is missing {missing}") from None


def _compute_inputs(params: JSON) -> tuple[int, dict[str, Any]]:
    year = int(params["year"])
    return year, {
        "trades": [trade_from_json(t) for t in params.get("trades", [])],
        "actions": _actions(params.get("actions", [])),
        "fmv_2018": {isin: _dec(v, f"fmv_2018[{isin}]")
                     for isin, v in params.get("fmv_2018", {}).items()},
        "fund_classes": {isin: FundClass(v) for isin, v in params.get("fund_classes", {}).items()},
        "brought_forward": _losses(params.get("brought_forward", [])),
        "excluded": [str(t) for t in params.get("excluded", [])],
        "late_returns": [int(y) for y in params.get("late_returns", [])],
        "transfers": _transfers(params.get("transfers", [])),
    }


def _report(params: JSON) -> TaxYearReport:
    year, inputs = _compute_inputs(params)
    trades = inputs.pop("trades")
    return compute_tax_year(year, trades, **inputs)


# --- encoding -------------------------------------------------------------------------------

def _s(value: Decimal) -> str:
    return format(value, "f")


def trade_to_json(trade: Trade) -> JSON:
    return {
        "trade_id": trade.trade_id, "trade_date": trade.trade_date.isoformat(),
        "instrument": trade.instrument, "side": trade.side.value,
        "quantity": _s(trade.quantity), "price": _s(trade.price),
        "charges": _s(trade.charges), "stt": _s(trade.stt), "segment": trade.segment.value,
        "executed_at": trade.executed_at.isoformat() if trade.executed_at else None,
        "account": trade.account,
        "entered_on": trade.entered_on.isoformat() if trade.entered_on else None,
    }


def citation_to_json(citation: Citation, report: TaxYearReport) -> JSON:
    return {
        "topic": citation.topic, "section": citation.section(report.pack.act),
        "act_1961": citation.act_1961, "act_2025": citation.act_2025, "url": citation.url,
        "unverified": citation.unverified, "question": citation.question,
    }


def _lot(lot: Lot) -> JSON:
    return {"instrument": lot.instrument, "isin": isin_of(lot.instrument),
            "acquired_on": lot.acquired_on.isoformat(), "quantity": _s(lot.quantity),
            "cost": _s(lot.cost), "segment": lot.segment.value, "account": lot.account,
            "entered_on": lot.entered_on.isoformat() if lot.entered_on else None}


def _loss(entry: LossEntry) -> JSON:
    return {"origin_year": entry.origin_year, "kind": entry.kind.value,
            "amount": _s(entry.amount)}


def _shortfall(gap: Shortfall) -> JSON:
    return {"trade_id": gap.trade_id, "instrument": gap.instrument,
            "isin": isin_of(gap.instrument), "sold_on": gap.sold_on.isoformat(),
            "quantity": _s(gap.quantity), "price": _s(gap.price),
            "sale_value": _s(gap.sale_value), "segment": gap.segment.value,
            "account": gap.account}


def report_to_json(report: TaxYearReport) -> JSON:
    def amounts(items: Mapping[Any, Decimal]) -> list[JSON]:
        return [{"label": b.label, "amount": _s(v)}
                for b, v in sorted(items.items(), key=lambda kv: kv[0].sort_key)]

    return {
        "tax_year": report.pack.label,
        "start_year": report.pack.start_year,
        "act": report.pack.act.value,
        "summary": {
            "bucket_nets": amounts(report.bucket_nets),
            "exemption_used": amounts(report.setoff.exemption_used),
            "taxable": [x for x in amounts(report.setoff.gains) if Decimal(x["amount"])],
            "special_rate_tax": _s(report.special_rate_tax),
            "special_rate_tax_rounded": _s(report.special_rate_tax_rounded),
            "speculative_income": _s(report.business.speculative),
            "non_speculative_income": _s(report.business.non_speculative),
            "speculative_after_setoff": _s(report.setoff.speculative_income),
            "non_speculative_after_setoff": _s(report.setoff.business_income),
        },
        "capital_gains": [{
            "instrument": line.disposal.instrument,
            "isin": isin_of(line.disposal.instrument),
            "acquired_on": line.disposal.acquired_on.isoformat(),
            "sold_on": line.disposal.sold_on.isoformat(),
            "long_term_after": line.long_term_after.isoformat(),
            "quantity": _s(line.disposal.quantity),
            "sale_value": _s(line.disposal.sale_value),
            "transfer_expenses": _s(line.disposal.transfer_expenses),
            "actual_cost": _s(line.disposal.cost),
            "cost": _s(line.cost),
            "grandfathered_fmv": _s(line.grandfathered_fmv)
            if line.grandfathered_fmv is not None else None,
            "stripped_loss": _s(line.disposal.stripped_loss),
            "gain": _s(line.gain),
            "bucket": line.bucket.label,
            "manual": line.manual,
            "account": line.disposal.account,
            "open_trade_id": line.disposal.open_trade_id,
            "close_trade_id": line.disposal.close_trade_id,
            "citations": [citation_to_json(c, report) for c in line.citations],
        } for line in report.capital_gains],
        "business_lines": [{
            "instrument": bl.disposal.instrument,
            "opened_on": bl.disposal.acquired_on.isoformat(),
            "closed_on": bl.disposal.sold_on.isoformat(),
            "quantity": _s(bl.disposal.quantity),
            "income": _s(bl.income),
            "speculative": bl.speculative,
            "citations": [citation_to_json(c, report) for c in bl.citations],
        } for bl in report.business.lines],
        "setoff_steps": [{"loss": st.loss, "against": st.against, "amount": _s(st.amount),
                          "citation": citation_to_json(st.citation, report)}
                         for st in report.setoff.steps],
        "carried_forward": [_loss(e) for e in report.carried_forward],
        "lapsed": [_loss(e) for e in report.lapsed],
        "not_carried": [_loss(e) for e in report.not_carried],
        "expired": [{"origin_year": e.origin_year, "kind": e.kind.value, "amount": _s(e.amount)}
                    for e in report.setoff.expired],
        "open_lots": [_lot(lot) for lot in report.open_lots],
        "complete": report.complete,
        "missing_history": [_shortfall(s) for s in report.missing_history],
        "excluded_sales": [_shortfall(s) for s in report.excluded_sales],
        "excluded_value": _s(report.excluded_value),
        "warnings": [{"code": n.code, "message": n.message, "question": n.question,
                      "ref": n.ref} for n in report.warnings],
    }


# --- methods --------------------------------------------------------------------------------

MAX_IMPORT_BYTES = 100 * 1024 * 1024
"""Total size accepted in one import request (decoded)."""


def _files(params: JSON) -> list[tuple[str, bytes]]:
    files = params.get("files") or []
    if not files:
        raise RequestError("no files given")
    try:
        decoded = [(str(f["name"]), base64.b64decode(f["data_base64"], validate=True))
                   for f in files]
    except (KeyError, ValueError):
        raise RequestError("each file needs a name and base64 data") from None
    if sum(len(data) for _, data in decoded) > MAX_IMPORT_BYTES:
        raise RequestError(f"files are larger than {MAX_IMPORT_BYTES // (1024 * 1024)} MB in total")
    return decoded


def _parse(params: JSON, files: list[tuple[str, bytes]]) -> tuple[Any, dict[str, str],
                                                                  dict[str, str]]:
    """Run the importer chosen by ``params["broker"]`` over ``files``: (result, suggested fund
    classes, names)."""
    broker = params.get("broker")
    password = params.get("password") or None
    result: Any
    suggested: dict[str, str] = {}
    names: dict[str, str] = {}
    if broker == "cas":
        from importers.cas import load_cas

        if len(files) != 1:
            raise RequestError("import one CAS PDF at a time")
        name, data = files[0]
        cas = load_cas(data, password=password or "", name=name,
                       allow_mergers=bool(params.get("allow_mergers")))
        result = cas.result
        suggested = {k: v.value for k, v in cas.suggested_classes.items()}
        names = cas.scheme_names
    elif broker in ("zerodha", "upstox"):
        from importers import upstox, zerodha
        from importers.tabular import load_tradebooks

        profile = zerodha.PROFILE if broker == "zerodha" else upstox.PROFILE
        result = load_tradebooks(files, profile, password=password)
    elif broker == "angelone":
        from importers.angel_one import load_angel_one_tradebooks

        isin_map = params.get("isin_map") or {}
        if not isinstance(isin_map, dict):
            raise RequestError("isin_map must be an object of scrip name → ISIN")
        angel = load_angel_one_tradebooks(
            files, isin_map={str(k): str(v) for k, v in isin_map.items()}, password=password)
        result = angel.result
        names = angel.names
    elif broker == "opening":
        from importers.opening import load_opening_csv

        if len(files) != 1:
            raise RequestError("import one opening-holdings file at a time")
        name, data = files[0]
        opening = load_opening_csv(data, name=name, today=date.today())
        result = _OpeningResult(opening)
        names = dict(opening.names)
    elif broker == "mapped":
        from importers.mapped import mapped_profile
        from importers.tabular import load_tradebooks

        mapping = params.get("mapping") or {}
        profile = mapped_profile(mapping, source=str(params.get("source") or "Mapped tradebook"),
                                 key=str(params.get("key") or "MAPPED"))
        result = load_tradebooks(files, profile, password=password)
    else:
        raise RequestError(f"unknown broker {broker!r}")
    return result, suggested, names


class _OpeningResult:
    """Opening holdings in the shape the import methods use for a parsed file."""

    source = "Opening holdings"
    format_confirmed = True
    warnings: tuple[str, ...] = ()

    def __init__(self, opening: Any) -> None:
        self.trades = opening.trades
        self.how_acquired = opening.how_acquired
        self.warnings = opening.warnings


def m_opening_template(_params: JSON) -> JSON:
    from importers.opening import template_csv

    return {"name": "kosh-opening-holdings.csv", "csv": template_csv()}


def m_ledger_add_opening(params: JSON) -> JSON:
    """Save holdings entered in the form as one "opening" batch (it can be undone)."""
    from engine.ledger import Batch
    from importers.opening import parse_rows

    profile_id = _profile_id(params)
    rows = params.get("rows")
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise RequestError("rows must be a list of objects")
    opening = parse_rows(rows, today=date.today())
    outcome = _ledger().import_trades(profile_id, Batch("opening", "opening", "Entered by hand"),
                                      opening.trades, how_acquired=opening.how_acquired,
                                      account=_text_or_none(params.get("account")))
    return {"added": outcome.added, "duplicates": outcome.duplicates,
            "conflicts": [{"new": trade_to_json(c.new), "existing": trade_to_json(c.existing),
                           "reason": c.reason} for c in outcome.conflicts],
            "warnings": list(opening.warnings),
            "names": dict(opening.names) if not outcome.conflicts else {},
            **_ledger_state(profile_id)}


def m_import(params: JSON) -> JSON:
    result, suggested, names = _parse(params, _files(params))
    return {
        "source": result.source,
        "format_confirmed": result.format_confirmed,
        "trades": [trade_to_json(t) for t in result.trades],
        "warnings": list(result.warnings),
        "suggested_classes": suggested,
        "scheme_names": names,
    }


# --- ledger ---------------------------------------------------------------------------------

_LEDGER: Any = None
"""The open ledger (``engine.ledger.Ledger``), opened on first use and kept for the life of the
process so its write lock is taken only once."""


def _ledger() -> Any:
    global _LEDGER
    if _LEDGER is None:
        from engine.ledger import Ledger, ledger_path

        _LEDGER = Ledger.open(ledger_path())
    return _LEDGER


def _profile_id(params: JSON) -> int:
    value = params.get("profile_id")
    if not isinstance(value, int) or isinstance(value, bool):
        raise RequestError("profile_id must be a number")
    return value


def _ledger_state(profile_id: int) -> JSON:
    ledger = _ledger()
    return {"trades": [trade_to_json(t) for t in ledger.trades(profile_id)],
            "batches": ledger.batches(profile_id), "accounts": ledger.accounts(profile_id),
            "transfers": [_transfer_json(t) for t in ledger.transfers(profile_id)]}


def _settings_to_json(settings: Any) -> JSON:
    return {
        "fund_classes": {k: v.value for k, v in settings.fund_classes.items()},
        "unconfirmed": sorted(settings.guessed),
        "fmv_2018": {k: _s(v) for k, v in settings.fmv_2018.items()},
        "names": dict(settings.names),
        "brought_forward": [{"origin_year": e.origin_year, "kind": e.kind.value,
                             "amount": _s(e.amount)} for e in settings.brought_forward],
        "manual_buys": [{"trade": trade_to_json(m.trade), "how": m.how, "for_trade": m.for_trade}
                        for m in settings.manual_buys],
        "excluded": list(settings.excluded),
        "filed_on_time": {str(y): v for y, v in settings.filed_on_time.items()},
    }


def _settings_from_json(data: Any) -> Any:
    from engine.ledger.settings import ManualBuy, Settings

    if not isinstance(data, dict):
        raise RequestError("settings must be an object")
    try:
        classes = {str(k): FundClass(v) for k, v in data.get("fund_classes", {}).items()}
        return Settings(
            fund_classes=classes,
            guessed=frozenset(str(i) for i in data.get("unconfirmed", []) if i in classes),
            fmv_2018={str(k): _dec(v, f"31-Jan-2018 price for {k}")
                      for k, v in data.get("fmv_2018", {}).items()},
            names={str(k): str(v) for k, v in data.get("names", {}).items() if str(v).strip()},
            brought_forward=tuple(_losses(data.get("brought_forward", []))),
            manual_buys=tuple(ManualBuy(trade_from_json(m["trade"]), str(m["how"]),
                                        str(m["for_trade"]))
                              for m in data.get("manual_buys", [])),
            excluded=tuple(str(e) for e in data.get("excluded", [])),
            filed_on_time={_year(y): bool(v) for y, v in data.get("filed_on_time", {}).items()},
        )
    except (KeyError, TypeError, AttributeError) as error:
        raise RequestError(f"settings are malformed: {error}") from None


def _year(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise RequestError(f"settings are malformed: {value!r} is not a tax year") from None


def _profiles() -> list[JSON]:
    return [{"id": p.id, "name": p.display_name} for p in _ledger().profiles()]


def m_ledger_profile(params: JSON) -> JSON:
    """Open (or create) the profile named ``name`` (default "Me") and return its trades,
    import history and settings, with the list of all profiles."""
    name = str(params.get("name") or "Me")
    profile = _ledger().ensure_profile(name)
    return {"profile": {"id": profile.id, "name": profile.display_name},
            "profiles": _profiles(),
            "settings": _settings_to_json(_ledger().settings(profile.id)),
            **_ledger_state(profile.id)}


def m_ledger_profiles(_params: JSON) -> JSON:
    return {"profiles": _profiles()}


def m_ledger_save_settings(params: JSON) -> JSON:
    """Replace the profile's saved settings with ``settings`` (the whole set)."""
    profile_id = _profile_id(params)
    _ledger().save_settings(profile_id, _settings_from_json(params.get("settings")))
    return {"settings": _settings_to_json(_ledger().settings(profile_id))}


def m_ledger_state(params: JSON) -> JSON:
    return _ledger_state(_profile_id(params))


def m_ledger_import(params: JSON) -> JSON:
    """Import each file into the ledger as its own batch (so each can be undone). A file
    already imported is recognised by its SHA-256 before it is read."""
    from engine.ledger import Batch

    profile_id = _profile_id(params)
    ledger = _ledger()
    kind = {"cas": "cas", "opening": "opening"}.get(str(params.get("broker")), "tradebook")
    # The demat account these files are from (brief 0003 A); funds have none.
    account = None if kind == "cas" else (
        _text_or_none(params.get("account")) or BROKER_ACCOUNTS.get(str(params.get("broker")))
        or _text_or_none(params.get("key")) or None)
    broker = str(params.get("key") or params.get("broker") or "")
    files: list[JSON] = []
    suggested: dict[str, str] = {}
    names: dict[str, str] = {}
    warnings: list[str] = []
    sources: list[str] = []
    confirmed = True
    # Read every file before saving any, so a bad second file doesn't leave the first saved.
    parsed: list[tuple[str, str, str | None, Any]] = []
    for name, data in _files(params):
        sha = hashlib.sha256(data).hexdigest()
        when = ledger.imported_on(profile_id, sha)
        parsed.append((name, sha, when, None if when else _parse(params, [(name, data)])))
    for name, sha, when, parsed_file in parsed:
        if when:
            files.append({"name": name, "added": 0, "duplicates": 0,
                          "already_imported_on": when, "conflicts": [],
                          "possible_duplicates": 0,
                          "imported_into": ledger.imported_into(profile_id, sha)})
            continue
        result, file_suggested, file_names = parsed_file
        outcome = ledger.import_trades(
            profile_id, Batch(kind, broker, name, sha), result.trades, warnings=result.warnings,
            how_acquired=getattr(result, "how_acquired", None), account=account)
        files.append({
            "name": name, "added": outcome.added, "duplicates": outcome.duplicates,
            "already_imported_on": outcome.already_imported_on,
            "conflicts": [{"new": trade_to_json(c.new), "existing": trade_to_json(c.existing),
                           "reason": c.reason} for c in outcome.conflicts],
            "possible_duplicates": len(outcome.possible_duplicates)})
        if not outcome.conflicts:
            suggested.update(file_suggested)
            names.update(file_names)
            warnings.extend(result.warnings)
        sources.append(result.source)
        confirmed = confirmed and result.format_confirmed
    return {"files": files, "source": sources[0] if sources else None,
            "format_confirmed": confirmed, "warnings": warnings,
            "suggested_classes": suggested, "scheme_names": names,
            **_ledger_state(profile_id)}


BROKER_ACCOUNTS = {"zerodha": "Zerodha", "upstox": "Upstox", "angelone": "Angel One"}
"""Default account name per broker: one account per broker unless the user names another."""


def m_ledger_add_transfer(params: JSON) -> JSON:
    """Record shares moved between two of the person's accounts. Refused if the source
    account didn't hold that many on the day (counting earlier transfers)."""
    from engine.api import held_on

    profile_id = _profile_id(params)
    ledger = _ledger()
    [move] = _transfers([{**params, "transfer_id": "NEW"}])
    if move.from_account is None or move.to_account is None:
        raise RequestError("a transfer needs both accounts")
    # Trades carry the stored account name; the user may type it in any case.
    source = ledger.account_name(profile_id, move.from_account)
    if source is None:
        raise RequestError(f"no account called {move.from_account!r}")
    move = replace(move, from_account=source,
                   to_account=ledger.account_name(profile_id, move.to_account) or move.to_account)
    settings = ledger.settings(profile_id)
    trades = ledger.trades(profile_id) + [m.trade for m in settings.manual_buys]
    held = held_on(trades, ledger.transfers(profile_id), move.from_account, move.instrument,
                   move.on)
    if held < move.quantity:
        raise RequestError(f"{move.from_account} held {held} of {move.instrument} on "
                           f"{move.on}, fewer than {move.quantity}; import its older purchases "
                           "first, or check the date and quantity")
    ledger.add_transfer(profile_id, move.on, move.instrument, move.quantity, move.from_account,
                        move.to_account)
    return _ledger_state(profile_id)


def m_ledger_remove_transfer(params: JSON) -> JSON:
    profile_id = _profile_id(params)
    transfer_id = str(params.get("transfer_id") or "")
    if not transfer_id.startswith("TRANSFER:") or not transfer_id[9:].isdigit():
        raise RequestError("transfer_id must look like TRANSFER:<number>")
    _ledger().remove_transfer(profile_id, int(transfer_id[9:]))
    return _ledger_state(profile_id)


def m_ledger_rename_account(params: JSON) -> JSON:
    """Rename an account. The reply carries the settings too: hand-entered purchases name
    their account, and the page must not save the old name back."""
    profile_id = _profile_id(params)
    _ledger().rename_account(profile_id, str(params.get("old") or ""),
                             str(params.get("new") or ""))
    return {**_ledger_state(profile_id),
            "settings": _settings_to_json(_ledger().settings(profile_id))}


def m_ledger_assign_account(params: JSON) -> JSON:
    """Put share trades that have no account yet into ``account``: all of them, or the
    engine trade ids in ``trade_ids``."""
    profile_id = _profile_id(params)
    ids = params.get("trade_ids")
    if ids is not None and (not isinstance(ids, list) or not all(isinstance(i, str) for i in ids)):
        raise RequestError("trade_ids must be a list of trade ids")
    _ledger().assign_account(profile_id, str(params.get("account") or ""), ids)
    return {**_ledger_state(profile_id),
            "settings": _settings_to_json(_ledger().settings(profile_id))}


def m_ledger_undo(params: JSON) -> JSON:
    batch_id = params.get("batch_id")
    if not isinstance(batch_id, int) or isinstance(batch_id, bool):
        raise RequestError("batch_id must be a number")
    profile_id = _profile_id(params)
    removed = _ledger().undo_batch(profile_id, batch_id)
    return {"removed": removed, **_ledger_state(profile_id)}


def m_compute(params: JSON) -> JSON:
    """The year's report. With a ``profile_id`` it also says whether the year was marked
    filed and lists every figure that changed since (task 4)."""
    report = report_to_json(_report(params))
    if params.get("profile_id") is not None:
        report["filing"] = _filing(_profile_id(params), int(params["year"]), report)
    return report


def _filing(profile_id: int, year: int, report: JSON) -> JSON | None:
    from engine.ledger.filing import changes

    filed = _ledger().filed(profile_id, year)
    if filed is None:
        return None
    return {"filed_at": filed.filed_at, "itr_form": filed.itr_form,
            "engine_version": filed.engine_version, "changes": changes(filed.report, report)}


def m_ledger_backup(_params: JSON) -> JSON:
    """The whole ledger as one file, for the user to save somewhere safe (task 5)."""
    from datetime import date as day

    data = _ledger().backup()
    return {"name": f"kosh-backup-{day.today().isoformat()}.kosh",
            "data_base64": base64.b64encode(data).decode()}


def m_ledger_restore(params: JSON) -> JSON:
    """Replace the ledger with a backup; the current file is kept as ``…before-restore``."""
    encoded = params.get("data_base64")
    if not isinstance(encoded, str) or not encoded:
        raise RequestError("data_base64 must be the backup file as base64 text")
    [(_, data)] = _files({"files": [{"name": "backup", "data_base64": encoded}]})
    before = _ledger().restore(data)
    return {"before": str(before), "profiles": _profiles()}


def m_ledger_mark_filed(params: JSON) -> JSON:
    """Mark the year as filed, keeping its figures as computed from these inputs."""
    from engine.ledger.filing import changes

    profile_id = _profile_id(params)
    report = report_to_json(_report(params))
    shown = params.get("shown")
    if shown is not None:
        try:
            moved = changes(shown, report)
        except (KeyError, TypeError, AttributeError, ArithmeticError):
            raise RequestError("the report shown is malformed") from None
        if moved:
            raise RequestError("the figures changed while you were looking at them; check the "
                               "updated figures and mark the year as filed again")
    form = params.get("itr_form")
    when = _ledger().mark_filed(profile_id, int(params["year"]), report,
                                str(form) if form else None)
    return {"filed_at": when}


def m_ledger_unmark_filed(params: JSON) -> JSON:
    _ledger().unmark_filed(_profile_id(params), int(params["year"]))
    return {}


def m_unclassified(params: JSON) -> JSON:
    trades = [trade_from_json(t) for t in params.get("trades", [])]
    classes = {isin: FundClass(v) for isin, v in params.get("fund_classes", {}).items()}
    return {"isins": unclassified_funds(trades, classes)}


def m_export_itr(params: JSON) -> JSON:
    from engine.export.itr import export_itr
    from engine.export.json_out import to_json

    report = _report(params)
    export = export_itr(report, form=params.get("form") or None, names=params.get("names") or {})
    return {
        "form": export.form, "valid": export.valid,
        "errors": [{"path": e.path, "message": e.message} for e in export.errors],
        "warnings": [{"code": n.code, "message": n.message, "question": n.question}
                     for n in export.warnings],
        "json": to_json(export.schedules),
    }


def m_export_pdf(params: JSON) -> JSON:
    from engine.export.pdf import render_summary

    pdf = render_summary(_report(params), names=params.get("names") or {})
    return {"pdf_base64": base64.b64encode(pdf).decode("ascii")}


METHODS: dict[str, Callable[[JSON], JSON]] = {
    "version": lambda _params: {"version": __version__},
    "import": m_import,
    "compute": m_compute,
    "unclassified_funds": m_unclassified,
    "export_itr": m_export_itr,
    "export_pdf": m_export_pdf,
    "ledger_profile": m_ledger_profile,
    "ledger_profiles": m_ledger_profiles,
    "ledger_save_settings": m_ledger_save_settings,
    "ledger_mark_filed": m_ledger_mark_filed,
    "ledger_backup": m_ledger_backup,
    "ledger_add_transfer": m_ledger_add_transfer,
    "ledger_remove_transfer": m_ledger_remove_transfer,
    "ledger_rename_account": m_ledger_rename_account,
    "ledger_assign_account": m_ledger_assign_account,
    "opening_template": m_opening_template,
    "ledger_add_opening": m_ledger_add_opening,
    "ledger_restore": m_ledger_restore,
    "ledger_unmark_filed": m_ledger_unmark_filed,
    "ledger_state": m_ledger_state,
    "ledger_import": m_ledger_import,
    "ledger_undo": m_ledger_undo,
}


def handle(request: Any) -> JSON:
    """Answer one request. Never raises: problems become an ``error`` member."""
    request_id = request.get("id") if isinstance(request, dict) else None
    try:
        if not isinstance(request, dict):
            raise RequestError("request must be a JSON object")
        method = METHODS.get(str(request.get("method")))
        if method is None:
            raise RequestError(f"unknown method {request.get('method')!r}")
        params = request.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise RequestError("params must be an object")
        return {"id": request_id, "result": method(params)}
    except Exception as error:  # every failure goes back to the UI as data
        return {"id": request_id,
                "error": {"type": type(error).__name__, "message": str(error)}}


def main() -> None:
    """Serve requests: one JSON object per line on stdin, one response per line on stdout.
    Both streams are UTF-8 whatever the platform's locale (Windows defaults to cp1252, which
    can't encode "₹")."""
    for stream in (sys.stdin, sys.stdout):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as error:
            response: JSON = {"id": None, "error": {"type": "JSONDecodeError",
                                                    "message": str(error)}}
        else:
            response = handle(request)
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":  # pragma: no cover - exercised through a subprocess in tests
    main()
