import io
import zipfile
from decimal import Decimal

import pytest

from engine.api import compute_tax_year
from engine.models import Side
from importers import xlsx
from importers.base import ImportFormatError
from importers.mapped import load_mapped_tradebook, mapped_profile
from importers.upstox import load_upstox_tradebooks
from importers.xlsx import read_xlsx
from importers.zerodha import PROFILE as ZERODHA
from importers.zerodha import load_zerodha_tradebook, load_zerodha_tradebooks
from tests.fixtures.tradebooks import UPSTOX_HEADER, upstox_row, write_csv
from tests.fixtures.xlsx import Bool, Inline, Num, encrypt, xlsx_bytes
from tests.fixtures.zerodha import HEADER, Row, tradebook_csv

PAN = "ABCDE1234F"  # the only PAN allowed in this repo (synthetic)
WRONG = "WRONG0000X"
ANY = "unused"


def test_cell_types_and_number_text() -> None:
    rows = read_xlsx(xlsx_bytes([
        ["text", Num("100"), Num("0.30000000000000004"), Num("1E-3"), Num("-2.5")],
        [Num("45778", 1), Num("45778.396527777775", 2), Num("1234.5", 3), Inline("in"),
         Bool(True)],
        [None, "gap", None, Num("0")],
        [],
        [Num("0.5", 1)],
    ]))
    assert rows[0] == ["text", "100", "0.3", "0.001", "-2.5"]
    assert rows[1] == ["2025-05-01", "2025-05-01 09:31:00", "1234.5", "in", "TRUE"]
    assert rows[2] == ["", "gap", "", "0"]
    assert rows[3] == []
    assert rows[4] == ["12:00:00"]


def test_date1904_and_sheet_selection() -> None:
    data = xlsx_bytes([[Num("44316", 1)]], date1904=True, extra_sheets=("Summary",),
                      absolute_target=True)
    assert read_xlsx(data) == [["2025-05-01"]]
    assert read_xlsx(data, sheet="Summary") == [["Summary"]]
    assert read_xlsx(data, sheet=1) == [["Summary"]]
    with pytest.raises(ImportFormatError, match="no sheet named"):
        read_xlsx(data, sheet="Nope")
    with pytest.raises(ImportFormatError, match="no sheet 3"):
        read_xlsx(data, sheet=2)


def test_password_protected_workbook() -> None:
    data = encrypt(xlsx_bytes([["secret", Num("1")]], pad=True), PAN)
    assert read_xlsx(data, password=PAN) == [["secret", "1"]]
    with pytest.raises(ImportFormatError, match=r"password-protected.*PAN"):
        read_xlsx(data)
    with pytest.raises(ImportFormatError, match="wrong password"):
        read_xlsx(data, password=WRONG)
    small = encrypt(xlsx_bytes([["tiny"]]), PAN)  # library limitation: clear error, no crash
    with pytest.raises(ImportFormatError, match="couldn't decrypt"):
        read_xlsx(small, password=PAN)


def _zip(files: dict[str, str]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name, body in files.items():
            z.writestr(name, body)
    return out.getvalue()


@pytest.mark.parametrize(("data", "message"), [
    (b"PK\x03\x04garbage", "not a readable Excel file"),
    (xlsx.OLE_MAGIC + b"garbage", "not a readable Excel file"),
    (_zip({"a.txt": "x"}), "xl/workbook.xml missing"),
    (_zip({"xl/workbook.xml": '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><w/>'}),
     "DTD"),
    (_zip({"xl/workbook.xml": "<workbook><sheets/></workbook>"}), "no sheets"),
    (_zip({"xl/workbook.xml": '<workbook><sheets><sheet name="a" id="r1"/></sheets></workbook>'}),
     "can't locate the sheet"),
])
def test_broken_files(data: bytes, message: str) -> None:
    with pytest.raises(ImportFormatError, match=message):
        read_xlsx(data, password=ANY)


def test_missing_sheet_part_and_bad_shared_string() -> None:
    workbook = ('<workbook><sheets><sheet name="a" xmlns:r="urn:r" r:id="r1"/></sheets>'
                "</workbook>")
    rels = '<Relationships><Relationship Id="r1" Target="worksheets/s.xml"/></Relationships>'
    base = {"xl/workbook.xml": workbook, "xl/_rels/workbook.xml.rels": rels}
    with pytest.raises(ImportFormatError, match=r"sheet file xl/worksheets/s\.xml is missing"):
        read_xlsx(_zip(base))
    sheet = '<worksheet><sheetData><row><c t="s"><v>7</v></c></row></sheetData></worksheet>'
    with pytest.raises(ImportFormatError, match="shared-string"):
        read_xlsx(_zip({**base, "xl/worksheets/s.xml": sheet}))
    plain = ('<worksheet><sheetData><row><c><v>abc</v></c><c t="str"><v>f</v></c><c/></row>'
             "</sheetData></worksheet>")
    assert read_xlsx(_zip({**base, "xl/worksheets/s.xml": plain})) == [["abc", "f", ""]]
    bad_ref = '<worksheet><sheetData><row><c r="?"/></row></sheetData></worksheet>'
    with pytest.raises(ImportFormatError, match="bad cell reference"):
        read_xlsx(_zip({**base, "xl/worksheets/s.xml": bad_ref}))


def test_size_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(xlsx, "MAX_MEMBERS", 2)
    with pytest.raises(ImportFormatError, match="too large"):
        read_xlsx(xlsx_bytes([["a"]]))


def test_old_xls_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    import msoffcrypto

    monkeypatch.setattr(msoffcrypto, "OfficeFile", lambda _: object())
    with pytest.raises(ImportFormatError, match=r"old \.xls"):
        read_xlsx(xlsx.OLE_MAGIC, password=ANY)


def test_zerodha_xlsx_with_title_rows_round_trip() -> None:
    """Zerodha tradebook saved as XLSX with title rows: buy 100 @1,000, sell @1,200 →
    STCG 20,000 → tax 4,000. Dates stored as Excel serials."""
    title = [["Tradebook"], ["Client ID: SYNTHETIC"], []]
    body = [
        ["SYNTHA", "INE000A01011", Num("45778", 1), "NSE", "EQ", "EQ", "buy", Bool(False),
         Num("100"), Num("1000"), "1", "9", Num("45778.39583333333", 2)],
        ["SYNTHA", "INE000A01011", Num("45931", 1), "NSE", "EQ", "EQ", "sell", Bool(False),
         Num("100"), Num("1200"), "2", "9", Num("45931.5", 2)],
    ]
    result = load_zerodha_tradebook(xlsx_bytes([*title, list(HEADER), *body]), name="z.xlsx")
    assert [t.side for t in result.trades] == [Side.BUY, Side.SELL]
    assert result.trades[0].executed_at is not None
    assert compute_tax_year(2025, result.trades).special_rate_tax_rounded == Decimal(4000)


def test_groww_style_encrypted_xlsx_with_mapping() -> None:
    profile = mapped_profile({"trade_date": "Date", "side": "Type", "quantity": "Qty",
                              "price": "Price", "trade_id": "Order Id", "isin": "ISIN",
                              "exchange": "Exchange"}, source="Groww (mapped)", key="GROWW")
    data = encrypt(xlsx_bytes([
        ["Date", "Exchange", "ISIN", "Type", "Qty", "Price", "Order Id"],
        [Num("45778", 1), "NSE", "INE000A01011", "BUY", Num("10"), Num("100.5"), "G1"],
    ], pad=True), PAN)
    [trade] = load_mapped_tradebook(data, profile, password=PAN).trades
    assert (trade.price, trade.trade_id) == (Decimal("100.5"), "GROWW:NSE:2025-05-01:G1")


def test_csv_bytes_and_encodings() -> None:
    csv_text = tradebook_csv([Row("SYNTHA", "2025-05-01", "buy", "1", "1")])
    assert len(load_zerodha_tradebook(csv_text.encode("utf-8-sig")).trades) == 1
    latin = csv_text.replace("SYNTHA", "SYNTHÄ").encode("cp1252")
    result = load_zerodha_tradebook(latin, name="old.csv")
    assert any("Windows-1252" in w for w in result.warnings)
    with pytest.raises(ImportFormatError, match=r"bad\.xlsx: not a readable Excel file"):
        load_zerodha_tradebook(b"PK\x03\x04junk", name="bad.xlsx")
    assert ZERODHA.key == "ZERODHA"


def test_multi_file_loaders_mix_csv_and_xlsx() -> None:
    csv_file = tradebook_csv([Row("SYNTHA", "2025-05-01", "buy", "1", "1")]).encode()
    merged = load_zerodha_tradebooks([("a.csv", csv_file), ("b.csv", csv_file)])
    assert len(merged.trades) == 1
    upstox = write_csv(UPSTOX_HEADER, [upstox_row("2025-05-02", "BUY", 1, "1")]).encode()
    assert len(load_upstox_tradebooks([("u.csv", upstox)]).trades) == 1


def test_remaining_edge_paths() -> None:
    from importers.upstox import load_upstox_tradebook

    upstox = write_csv(UPSTOX_HEADER, [upstox_row("2025-05-02", "BUY", 1, "1")]).encode()
    assert len(load_upstox_tradebook(upstox).trades) == 1
    no_id = _zip({"xl/workbook.xml": '<workbook><sheets><sheet name="a"/></sheets></workbook>'})
    with pytest.raises(ImportFormatError, match="can't locate the sheet"):
        read_xlsx(no_id)
    workbook = ('<workbook><sheets><sheet name="a" xmlns:r="urn:r" r:id="r1"/></sheets>'
                "</workbook>")
    rels = '<Relationships><Relationship Id="r1" Target="worksheets/s.xml"/></Relationships>'
    sheet = "<worksheet><sheetData><row><c><v>INF</v></c></row></sheetData></worksheet>"
    data = _zip({"xl/workbook.xml": workbook, "xl/_rels/workbook.xml.rels": rels,
                 "xl/worksheets/s.xml": sheet})
    assert read_xlsx(data) == [["INF"]]



WORKBOOK = ('<workbook><sheets><sheet name="a" xmlns:r="urn:r" r:id="r1"/></sheets></workbook>')
RELS = '<Relationships><Relationship Id="r1" Target="worksheets/s.xml"/></Relationships>'


def _book(sheet_xml: str | bytes, **extra: str) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("xl/workbook.xml", extra.pop("workbook", WORKBOOK))
        z.writestr("xl/_rels/workbook.xml.rels", RELS)
        z.writestr("xl/worksheets/s.xml", sheet_xml)
        for name, body in extra.items():
            z.writestr(name.replace("__", "/"), body)
    return out.getvalue()


def _sheet(cells: str) -> str:
    return f"<worksheet><sheetData>{cells}</sheetData></worksheet>"


def test_long_integer_ids_kept_exactly() -> None:
    rows = read_xlsx(_book(_sheet(
        "<row><c><v>1300000012345678</v></c><c><v>1300000012345679</v></c>"
        "<c><v>1234567890123456789</v></c><c><v>+7</v></c></row>")))
    assert rows == [["1300000012345678", "1300000012345679", "1234567890123456789", "7"]]


def test_phonetic_text_not_included() -> None:
    shared = ('<sst><si><t>東京</t><rPh sb="0" eb="2"><t>トウキョウ</t></rPh></si>'
              "<si><r><t>Rich </t></r><r><t>text</t></r></si></sst>")
    inline = ('<c t="inlineStr"><is><t>A</t><rPh><t>x</t></rPh></is></c>')
    rows = read_xlsx(_book(_sheet(f'<row><c t="s"><v>0</v></c><c t="s"><v>1</v></c>{inline}'
                                  "</row>"), **{"xl__sharedStrings.xml": shared}))
    assert rows == [["東京", "Rich text", "A"]]


@pytest.mark.parametrize("sheet_xml", [
    '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE x [<!ENTITY a "b">]><w>&a;</w>'
    .encode("utf-16-le"),
    b'<?xml version="1.0"?><!doctype x><w/>',
    b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><w>&a;</w>',
])
def test_dtd_refused_in_any_encoding(sheet_xml: bytes) -> None:
    with pytest.raises(ImportFormatError, match=r"DTD|not a readable"):
        read_xlsx(_book(sheet_xml))


@pytest.mark.parametrize(("cells", "message"), [
    ('<row><c r="ZZZZ1"><v>1</v></c></row>', "beyond column"),
    ('<row r="3000000"><c><v>1</v></c></row>', "more than 200000 rows"),
    ('<row><c r="C1"><v>3</v></c><c r="A1"><v>1</v></c></row>', "out of order"),
    ('<row r="3"><c><v>1</v></c></row><row r="1"><c><v>1</v></c></row>', "out of order"),
    ('<row r="2"/><row r="2"/>', "repeated"),
    ('<row r="x"/>', "not a readable Excel file"),
    ('<row><c s="x"><v>1</v></c></row>', "not a readable Excel file"),
    ("<row><c><v>1</v></row>", "not a readable Excel file"),
])
def test_malformed_sheets(cells: str, message: str) -> None:
    with pytest.raises(ImportFormatError, match=message):
        read_xlsx(_book(_sheet(cells)))


def test_hidden_first_sheet_errors_and_formulas_warn() -> None:
    workbook = ('<workbook><sheets><sheet name="h" state="hidden" xmlns:r="urn:r" r:id="r9"/>'
                '<sheet name="a" xmlns:r="urn:r" r:id="r1"/></sheets></workbook>')
    sheet = _sheet('<row><c t="e"><v>#N/A</v></c><c><f>A1+1</f></c>'
                   '<c t="d"><v>2025-05-01</v></c></row>')
    result = xlsx.read_xlsx_sheet(_book(sheet, workbook=workbook))
    assert result.rows == [["#N/A", "", "2025-05-01"]]
    assert any("hidden sheet" in w for w in result.warnings)
    assert any("formula" in w for w in result.warnings)
    assert any("#N/A" in w for w in result.warnings)


def test_date_edge_cases() -> None:
    styles = ('<styleSheet><numFmts><numFmt numFmtId="170" formatCode="[h]:mm"/></numFmts>'
              '<cellXfs><xf numFmtId="0"/><xf numFmtId="14"/><xf numFmtId="170"/>'
              '<xf numFmtId="22"/></cellXfs></styleSheet>')
    rows = read_xlsx(_book(_sheet(
        '<row><c s="1"><v>1</v></c><c s="1"><v>59</v></c><c s="1"><v>61</v></c>'
        '<c s="2"><v>1.5</v></c><c s="3"><v>45777.99999999</v></c></row>'),
        **{"xl__styles.xml": styles}))
    assert rows == [["1900-01-01", "1900-02-28", "1900-03-01", "1.5", "2025-05-01"]]
    for bad, message in (("60", "29-Feb-1900"), ("-0.5", "negative date serial")):
        with pytest.raises(ImportFormatError, match=message):
            read_xlsx(_book(_sheet(f'<row><c s="1"><v>{bad}</v></c></row>'),
                            **{"xl__styles.xml": styles}))


def test_corrupt_zip_and_compression_bomb(monkeypatch: pytest.MonkeyPatch) -> None:
    good = _book(_sheet("<row><c><v>1</v></c></row>"))
    corrupt = bytearray(good)
    start = corrupt.index(b"<row>")
    corrupt[start:start + 5] = b"<rox>"
    with pytest.raises(ImportFormatError, match="not a readable Excel file"):
        read_xlsx(bytes(corrupt))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/workbook.xml", WORKBOOK)
        z.writestr("big.bin", b"0" * (3 * 1024 * 1024))
    with pytest.raises(ImportFormatError, match="suspiciously compressed"):
        read_xlsx(out.getvalue())


def test_plain_xls_says_unsupported_not_password(monkeypatch: pytest.MonkeyPatch) -> None:
    import msoffcrypto

    monkeypatch.setattr(msoffcrypto, "OfficeFile", lambda _: object())
    with pytest.raises(ImportFormatError, match=r"old \.xls"):
        read_xlsx(xlsx.OLE_MAGIC)


def test_utf16_and_binary_csv() -> None:
    csv_text = tradebook_csv([Row("SYNTHA", "2025-05-01", "buy", "1", "1")])
    assert len(load_zerodha_tradebook(csv_text.encode("utf-16")).trades) == 1
    with pytest.raises(ImportFormatError, match="binary data"):
        load_zerodha_tradebook(b"abc\x00def")
    with pytest.raises(ImportFormatError, match=r"weird\.csv: not a readable CSV"):
        load_zerodha_tradebook(b'"' + b"x" * 200_000 + b'"\n', name="weird.csv")


def test_xlsx_warnings_reach_the_import_result(monkeypatch: pytest.MonkeyPatch) -> None:
    from importers import tabular

    row = ["SYNTHA", "INE000A01011", "2025-05-01", "NSE", "EQ", "EQ", "buy", "false", "1",
           "1", "1", "9", ""]
    monkeypatch.setattr(tabular, "read_xlsx_sheet",
                        lambda *_, **__: xlsx.Sheet([list(HEADER), row], ("1 formula cell",)))
    result = load_zerodha_tradebook(xlsx_bytes([["x"]]), name="z.xlsx")
    assert len(result.trades) == 1
    assert "z.xlsx: 1 formula cell" in result.warnings
