import json
from datetime import date
from decimal import Decimal

import pytest

from engine.api import FundClass, compute_tax_year
from engine.classify.capital_gains import Bucket, Term
from engine.export import itr
from engine.export.itr import ExportError, choose_form, export_itr
from engine.export.json_out import to_json
from engine.export.schemas_registry import UnsupportedFormError, load_schema, validate
from engine.export.skeleton import SkeletonError, skeleton
from engine.matching.corporate_actions import Bonus
from tests.golden.helpers import DEBT_FUND, HYBRID_FUND, A, buy, d, day, fut, mf, sell

C = "INE000C01013"
CLASSES = {DEBT_FUND: FundClass.SPECIFIED, HYBRID_FUND: FundClass.OTHER}


def _mixed_report():  # type: ignore[no-untyped-def]
    return compute_tax_year(2025, [
        buy("2015-01-01", 1000, 500), sell("2025-06-01", 1000, 1500),
        buy("2024-01-01", 100, 1000, instrument="INE000B01012"),
        sell("2025-07-01", 100, 2000, instrument="INE000B01012"),
        buy("2025-04-10", 100, 1000, instrument=C), sell("2025-12-20", 100, 1300, instrument=C),
        mf("BUY", "2025-04-10", 1000, 10, DEBT_FUND), mf("SELL", "2025-06-10", 1000, 9, DEBT_FUND),
        mf("BUY", "2022-01-01", 1000, 100, HYBRID_FUND),
        mf("SELL", "2025-09-01", 1000, 150, HYBRID_FUND),
    ], fmv_2018={A: d(800)}, fund_classes=CLASSES)


def _written(export):  # type: ignore[no-untyped-def]
    return json.loads(to_json(export.schedules))


def test_mixed_export_is_valid_and_adds_up() -> None:
    """112A: BE row (cost 5L, FMV 8L, sale 15L → balance 7L) + consolidated AE row (balance 1L).
    CG: s.111A STCG 30,000 less slab-rate STCL 1,000 → 29,000; LTCG 8L (112A) + 50,000 (s.112)
    = 8,50,000; sum 8,79,000."""
    export = export_itr(_mixed_report(), names={A: "SYNTHETIC A LTD"})
    assert export.form == "ITR-2" and export.valid, export.errors
    out = _written(export)
    rows = out["Schedule112A"]["Schedule112ADtls"]
    assert [(r["ShareOnOrBefore"], r["ISINCode"], r["Balance"]) for r in rows] == [
        ("BE", A, 700000), ("AE", "INNOTREQUIRD", 100000)]
    assert rows[0]["CostAcqWithoutIndx"] == 800000 and rows[0]["AcquisitionCost"] == 500000
    assert "NumSharesUnits" not in rows[1]
    assert out["Schedule112A"]["Balance112A"] == 800000
    cg = out["ScheduleCGFor23"]
    assert cg["ShortTermCapGainFor23"]["TotalSTCG"] == 29000
    assert cg["LongTermCapGain23"]["TotalLTCG"] == 850000
    assert cg["SumOfCGIncm"] == 879000
    losses = cg["CurrYrLosses"]
    assert losses["InStcg20Per"]["StclSetoffAppRate"] == 1000
    assert losses["InStcg20Per"]["CurrYrCapGain"] == 29000
    accrual = cg["AccruOrRecOfCG"]
    assert accrual["ShortTermUnder20Per"]["DateRange"]["Up16Of12To15Of3"] == 29000
    assert accrual["LongTermUnder12_5Per"]["DateRange"] == {
        "Upto15Of6": 700000, "Upto15Of9": 150000, "Up16Of9To15Of12": 0,
        "Up16Of12To15Of3": 0, "Up16Of3To31Of3": 0}
    assert any(n.question == "Q-025" for n in export.warnings)


def test_itr3_chosen_for_trading_income_and_itr2_refused() -> None:
    report = compute_tax_year(2025, [
        buy("2025-05-01", 10, 100), sell("2025-06-01", 10, 120),
        fut("BUY", "2025-06-02", 50, 100), fut("SELL", "2025-06-05", 50, 110),
    ])
    assert choose_form(report) == "ITR-3"
    export = export_itr(report)
    assert export.form == "ITR-3" and export.valid, export.errors
    assert "TotalBalance112A" not in export.schedules["Schedule112A"]
    with pytest.raises(ExportError, match="use ITR-3"):
        export_itr(report, form="ITR-2")


def test_unsupported_year() -> None:
    report = compute_tax_year(2024, [buy("2024-05-01", 1, 1), sell("2024-06-01", 1, 2)])
    with pytest.raises(UnsupportedFormError, match="FY 2024-25"):
        export_itr(report)


def test_missing_name_and_manual_lines_are_noted() -> None:
    report = compute_tax_year(2025, [buy("2016-01-01", 10, 100), sell("2025-06-01", 10, 200)],
                              fmv_2018={A: d(150)})
    export = export_itr(report)
    assert any("no name for INE000A01011" in n.message for n in export.warnings)
    assert export.schedules["Schedule112A"]["Schedule112ADtls"][0]["ShareUnitName"] == A
    manual = compute_tax_year(2025, [
        buy("2025-04-01", 1, 1), sell("2025-05-01", 1, 2)])
    object.__setattr__(manual.capital_gains[0], "manual", True)
    noted = export_itr(manual)
    assert any(n.question == "Q-021" for n in noted.warnings)


def test_bonus_stripped_loss_goes_to_section_94_column() -> None:
    """Loss 50,000 ignored under bonus stripping: balance -50,000, disallowed 50,000, STCG 0."""
    report = compute_tax_year(2025, [buy("2025-05-01", 100, 1000), sell("2025-07-01", 100, 500)],
                              actions=[Bonus(A, day("2025-06-15"), held=1, bonus=1)])
    export = export_itr(report)
    assert export.valid, export.errors
    block = export.schedules["ScheduleCGFor23"]["ShortTermCapGainFor23"]["EquityMFonSTT"][0][
        "EquityMFonSTTDtls"]
    assert (block["BalanceCG"], block["LossSec94of7Or94of8"], block["CapgainonAssets"]) == (
        -50000, 50000, 0)


def test_set_off_order_and_accrual_split_across_periods() -> None:
    """STCG 20%: 30,000 (May) + 10,000 (Oct); slab STCL 8,000 → 32,000 left, split 3:1 by
    period gains → 24,000 / 8,000. LTCL 5,000 (12.5%) stays within its column."""
    report = compute_tax_year(2025, [
        buy("2025-04-10", 100, 1000), sell("2025-05-10", 100, 1300),
        buy("2025-04-10", 100, 1000, instrument=C), sell("2025-10-10", 100, 1100, instrument=C),
        mf("BUY", "2025-04-10", 1000, 10, DEBT_FUND), mf("SELL", "2025-06-10", 1000, 2, DEBT_FUND),
        buy("2023-01-01", 10, 1000, instrument="INE000B01012"),
        sell("2025-06-10", 10, 500, instrument="INE000B01012"),
    ], fund_classes=CLASSES)
    export = export_itr(report)
    assert export.valid, export.errors
    cg = export.schedules["ScheduleCGFor23"]
    assert cg["CurrYrLosses"]["InStcg20Per"]["CurrYrCapGain"] == 32000
    assert cg["CurrYrLosses"]["LossRemainSetOff"]["LtclSetOff12_5Per"] == 5000
    split = cg["AccruOrRecOfCG"]["ShortTermUnder20Per"]["DateRange"]
    assert (split["Upto15Of6"], split["Up16Of9To15Of12"]) == (24000, 8000)


@pytest.mark.parametrize(("day_", "period"), [
    (date(2025, 4, 1), "Upto15Of6"), (date(2025, 6, 15), "Upto15Of6"),
    (date(2025, 6, 16), "Upto15Of9"), (date(2025, 9, 16), "Up16Of9To15Of12"),
    (date(2025, 12, 16), "Up16Of12To15Of3"), (date(2026, 3, 15), "Up16Of12To15Of3"),
    (date(2026, 3, 16), "Up16Of3To31Of3"), (date(2026, 1, 5), "Up16Of12To15Of3"),
])
def test_advance_tax_periods(day_: date, period: str) -> None:
    assert itr._period(day_) == period


def test_bucket_without_a_column() -> None:
    with pytest.raises(ExportError, match="no column"):
        itr._category(Bucket(Term.SHORT, Decimal("0.15")))


def test_validation_is_exact_on_decimals() -> None:
    row = {"ShareOnOrBefore": "AE", "ISINCode": "INNOTREQUIRD", "ShareUnitName": "X",
           "NumSharesUnits": Decimal("1.1"), "TotSaleValue": 1, "CostAcqWithoutIndx": 0,
           "AcquisitionCost": Decimal("0"), "LTCGBeforelowerB1B2": 0,
           "FairMktValuePerShareunit": Decimal("0"), "TotFairMktValueCapAst": 0,
           "ExpExclCnctTransfer": Decimal("0"), "TotalDeductions": 0, "Balance": 1}
    assert validate(row, "ITR-2", 2025, "Schedule112A115ADType") == []
    bad = validate({**row, "NumSharesUnits": Decimal("1.00001")}, "ITR-2", 2025,
                   "Schedule112A115ADType")
    assert bad and "multiple of" in bad[0].message


def test_multiple_of_ignores_non_numbers() -> None:
    errors = validate({"NumSharesUnits": "lots"}, "ITR-2", 2025, "Schedule112A115ADType")
    assert not any("multiple of" in e.message for e in errors)
    assert any("is not of type 'number'" in e.message for e in errors)


def test_skeleton_handles_untyped_nodes() -> None:
    schema = load_schema("ITR-2", 2025)
    schema["definitions"]["_KoshShape"] = {
        "required": ["obj", "arr"],
        "properties": {"obj": {"required": ["n"], "properties": {"n": {"type": "integer"}}},
                       "arr": {"items": {"type": "integer"}}},
    }
    try:
        assert skeleton("ITR-2", 2025, "_KoshShape") == {"obj": {"n": 0}, "arr": []}
    finally:
        del schema["definitions"]["_KoshShape"]


def test_skeleton_refuses_required_free_text() -> None:
    with pytest.raises(SkeletonError, match="no neutral default"):
        skeleton("ITR-2", 2025, "Form_ITR2")
    schema = load_schema("ITR-2", 2025)
    schema["definitions"]["_KoshTest"] = {"type": "object", "required": ["x"],
                                          "properties": {"x": {"type": "array", "minItems": 1}}}
    try:
        with pytest.raises(SkeletonError, match="non-empty array"):
            skeleton("ITR-2", 2025, "_KoshTest")
    finally:
        del schema["definitions"]["_KoshTest"]


def test_json_writer() -> None:
    text = to_json({"a": Decimal("1234.5600"), "b": [], "c": {}, "d": [1, "x", None, True]})
    assert '"a": 1234.5600' in text and json.loads(text)["d"] == [1, "x", None, True]
    with pytest.raises(ValueError, match="non-finite"):
        to_json(Decimal("NaN"))
    with pytest.raises(TypeError, match="cannot serialise"):
        to_json(1.5)


def test_remote_ref_is_never_fetched(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    from referencing import Registry
    from referencing.exceptions import Unresolvable

    from engine.export.schemas_registry import _Validator

    def trip(*_: object, **__: object) -> None:
        raise AssertionError("network used")

    monkeypatch.setattr(socket, "getaddrinfo", trip)
    validator = _Validator({"$ref": "https://example.invalid/schema.json"}, registry=Registry())
    with pytest.raises(Unresolvable):
        validator.validate(1)



def _check_against_engine(report, export) -> None:  # type: ignore[no-untyped-def]
    """Per column: accrual periods add up to the gain left after set-off."""
    cg = export.schedules["ScheduleCGFor23"]
    for column, key in itr.ACCRUAL_FIELD.items():
        left = cg["CurrYrLosses"][itr._row_key(column)]["CurrYrCapGain"]
        assert sum(cg["AccruOrRecOfCG"][key]["DateRange"].values()) == left


def test_itr3_with_112a_and_business_income() -> None:
    """QA case: ITR-3 names column 9 LTCGBeforelower6and11."""
    report = compute_tax_year(2025, [
        buy("2015-01-01", 10, 100), sell("2025-06-01", 10, 300),
        fut("BUY", "2025-06-02", 50, 100), fut("SELL", "2025-06-05", 50, 110),
    ], fmv_2018={A: d(150)})
    export = export_itr(report, names={A: "SYNTHETIC A LTD"})
    assert export.form == "ITR-3" and export.valid, export.errors
    row = export.schedules["Schedule112A"]["Schedule112ADtls"][0]
    assert row["LTCGBeforelower6and11"] == 1500 and "LTCGBeforelowerB1B2" not in row
    _check_against_engine(report, export)


def test_all_loss_year_is_valid() -> None:
    """QA case: STCL 500 + LTCL 500 → sums floored at 0; losses shown in the set-off table."""
    report = compute_tax_year(2025, [
        buy("2025-05-01", 10, 100), sell("2025-06-01", 10, 50),
        buy("2023-01-01", 10, 100, instrument=C), sell("2025-06-01", 10, 50, instrument=C),
    ])
    export = export_itr(report)
    assert export.valid, export.errors
    cg = export.schedules["ScheduleCGFor23"]
    assert (cg["SumOfCGIncm"], cg["TotScheduleCGFor23"]) == (0, 0)
    assert cg["CurrYrLosses"]["LossRemainSetOff"]["StclSetoff20Per"] == 500
    assert cg["CurrYrLosses"]["LossRemainSetOff"]["LtclSetOff12_5Per"] == 500
    assert cg["LongTermCapGain23"]["SaleOfEquityShareUs112A"]["BalanceCG"] == -500


def test_mixed_fmv_lots_get_consistent_rows() -> None:
    """QA case: lots at 100 and 200, FMV 150, sold at 300 (10 each). Per-lot rows:
    lot 1 cost max(1000, min(3000, 1500)) = 1500; lot 2 cost max(2000, 1500) = 2000."""
    report = compute_tax_year(2025, [
        buy("2015-01-01", 10, 100), buy("2016-01-01", 10, 200), sell("2025-06-01", 20, 300),
    ], fmv_2018={A: d(150)})
    export = export_itr(report, names={A: "SYNTHETIC A LTD"})
    assert export.valid, export.errors
    rows = export.schedules["Schedule112A"]["Schedule112ADtls"]
    for row in rows:
        lower = min(row["TotSaleValue"], row["TotFairMktValueCapAst"])
        assert row["LTCGBeforelowerB1B2"] == lower
        assert row["CostAcqWithoutIndx"] == max(int(row["AcquisitionCost"]), lower)
    assert [r["CostAcqWithoutIndx"] for r in rows] == [1500, 2000]
    assert sum(r["Balance"] for r in rows) == sum(ln.gain for ln in report.capital_gains)
    _check_against_engine(report, export)


def test_per_share_precision_and_split_adjusted_fmv() -> None:
    """3 shares at 333.33: sale 999.99 → Rs 1,000 in the schedule, so the per-share price is
    1000/3 = 333.3333 (4 dp, consistent with column 6). A 1:3 split after 2018 divides the FMV:
    FMV 150/share before split → 50/share now."""
    from engine.matching.corporate_actions import Split

    report = compute_tax_year(2025, [buy("2015-01-01", 1, 100), sell("2025-06-01", 3, "333.33")],
                              actions=[Split(A, day("2020-01-01"), old=1, new=3)],
                              fmv_2018={A: d(150)})
    export = export_itr(report, names={A: "SYNTHETIC A LTD"})
    assert export.valid, export.errors
    row = export.schedules["Schedule112A"]["Schedule112ADtls"][0]
    assert row["SalePricePerShareUnit"] == Decimal("333.3333")
    assert row["FairMktValuePerShareunit"] == Decimal("50.0000")


def test_missing_fmv_and_non_isin_are_flagged() -> None:
    report = compute_tax_year(2025, [buy("2015-01-01", 10, 100), sell("2025-06-01", 10, 300)])
    export = export_itr(report, names={A: "SYNTHETIC A LTD"})
    assert any("has no 31-Jan-2018 FMV" in n.message for n in export.warnings)
    odd = compute_tax_year(2025, [buy("2023-01-01", 1, 1, instrument="RELIANCE"),
                                  sell("2025-06-01", 1, 2, instrument="RELIANCE")])
    with pytest.raises(ExportError, match="needs an ISIN"):
        export_itr(odd)


def test_empty_report_and_slab_loss_absorbed_across_columns() -> None:
    """Empty year exports validly. Slab-rate STCL 3,000 vs STCG 20% 1,000 then LTCG 12.5%
    5,000 → 0 and 3,000 left."""
    assert export_itr(compute_tax_year(2025, [])).valid
    report = compute_tax_year(2025, [
        mf("BUY", "2025-04-10", 1000, 10, DEBT_FUND), mf("SELL", "2025-06-10", 1000, 7, DEBT_FUND),
        buy("2025-04-10", 10, 100), sell("2025-06-10", 10, 200),
        buy("2023-01-01", 10, 100, instrument=C), sell("2025-06-10", 10, 600, instrument=C),
    ], fund_classes=CLASSES)
    export = export_itr(report)
    assert export.valid, export.errors
    losses = export.schedules["ScheduleCGFor23"]["CurrYrLosses"]
    assert losses["InStcg20Per"]["CurrYrCapGain"] == 0
    assert losses["InLtcg12_5Per"]["StclSetoffAppRate"] == 2000
    assert losses["InLtcg12_5Per"]["CurrYrCapGain"] == 3000
    _check_against_engine(report, export)
