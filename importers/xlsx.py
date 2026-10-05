"""Minimal, dependency-light XLSX reader for broker reports.

Cell values are read as the text stored in the sheet XML and turned into strings for the
tabular importer, so numbers never pass through float (CLAUDE.md rule 2). Integers (IDs,
quantities) are kept exactly as stored. Excel stores other numbers as binary doubles and may
write noise such as 0.30000000000000004; those are rounded to 15 significant digits, which is
what Excel itself displays. Dates are recognised from the cell's number format and returned as
ISO text.

Password-protected workbooks (Groww reports use the PAN) are decrypted locally with
msoffcrypto-tool. Nothing here touches the network.

Safety: archive size, compression ratio, rows and columns are capped; the XML parser refuses
any DTD or entity declaration (a spreadsheet never needs one), which blocks entity-expansion
attacks whatever the encoding; every parse failure becomes an ``ImportFormatError``.
"""

import io
import posixpath
import re
import zipfile
import zlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Context, Decimal, InvalidOperation
from xml.etree import ElementTree
from xml.parsers import expat

from importers.base import ImportFormatError

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ZIP_MAGIC = b"PK\x03\x04"
MAX_MEMBERS = 5000
MAX_UNCOMPRESSED = 200 * 1024 * 1024
MAX_RATIO = 200
"""Largest compression ratio accepted for a member over 1 MB (zip-bomb guard)."""
MAX_ROWS = 200_000
MAX_COLUMNS = 1_000
DISPLAY_DIGITS = Context(prec=15)
BUILTIN_DATE_FORMATS = {14, 15, 16, 17, 18, 19, 20, 21, 22, 45, 46, 47}
INTEGER = re.compile(r"[+-]?\d+")


@dataclass(frozen=True, slots=True)
class Sheet:
    rows: list[list[str]]
    warnings: tuple[str, ...]


def is_xlsx(data: bytes) -> bool:
    return data.startswith(ZIP_MAGIC) or data.startswith(OLE_MAGIC)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(element: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    return [child for child in element if _local(child.tag) == name]


def _attr(element: ElementTree.Element, name: str) -> str | None:
    for key, value in element.attrib.items():
        if _local(key) == name:
            return value
    return None


def _decrypt(data: bytes, password: str | None) -> bytes:
    import msoffcrypto  # local import: only needed for protected files
    from msoffcrypto.exceptions import DecryptionError, InvalidKeyError
    from msoffcrypto.format.ooxml import OOXMLFile

    try:
        office = msoffcrypto.OfficeFile(io.BytesIO(data))
    except Exception:
        raise ImportFormatError("not a readable Excel file") from None
    if not isinstance(office, OOXMLFile):
        raise ImportFormatError("old .xls files aren't supported; save as .xlsx or .csv")
    if password is None:
        raise ImportFormatError(
            "the workbook is password-protected; supply the password (Groww reports use your "
            "PAN in capital letters)"
        )
    try:
        office.load_key(password=password)
        out = io.BytesIO()
        office.decrypt(out)
    except (InvalidKeyError, DecryptionError):
        raise ImportFormatError("wrong password for the workbook") from None
    except ValueError:
        raise ImportFormatError(
            "couldn't decrypt the workbook; open it in Excel and save a copy without a password"
        ) from None
    return out.getvalue()


def _refuse_dtd(*_: object) -> None:
    raise ImportFormatError("the workbook contains a DTD or entity declaration; file refused")


def _parse_xml(raw: bytes) -> ElementTree.Element:
    """Parse with expat directly so DTDs/entities can be refused in the parser itself."""
    builder = ElementTree.TreeBuilder()
    parser = expat.ParserCreate(namespace_separator="}")
    parser.StartElementHandler = builder.start
    parser.EndElementHandler = builder.end
    parser.CharacterDataHandler = builder.data
    parser.StartDoctypeDeclHandler = _refuse_dtd
    parser.EntityDeclHandler = _refuse_dtd
    parser.Parse(raw, True)
    return builder.close()


class _Book:
    def __init__(self, archive: zipfile.ZipFile) -> None:
        self.archive = archive
        self.names = set(archive.namelist())

    def xml(self, path: str) -> ElementTree.Element | None:
        if path not in self.names:
            return None
        return _parse_xml(self.archive.read(path))


def _check_archive(archive: zipfile.ZipFile) -> None:
    infos = archive.infolist()
    if len(infos) > MAX_MEMBERS or sum(i.file_size for i in infos) > MAX_UNCOMPRESSED:
        raise ImportFormatError("workbook is too large to import")
    for info in infos:
        if info.file_size > 1024 * 1024 and info.file_size > MAX_RATIO * max(info.compress_size, 1):
            raise ImportFormatError("workbook is suspiciously compressed; file refused")


def _sheet_path(book: _Book, sheet: str | int | None) -> tuple[str, list[str]]:
    workbook = book.xml("xl/workbook.xml")
    if workbook is None:
        raise ImportFormatError("not an Excel workbook (xl/workbook.xml missing)")
    sheets = [s for block in _children(workbook, "sheets") for s in _children(block, "sheet")]
    if not sheets:
        raise ImportFormatError("workbook has no sheets")
    warnings: list[str] = []
    if sheet is None:
        visible = [s for s in sheets if s.get("state", "visible") == "visible"]
        chosen = (visible or sheets)[0]
        if chosen is not sheets[0]:
            warnings.append(f"hidden sheet(s) skipped; reading sheet {chosen.get('name')!r}")
    elif isinstance(sheet, int):
        if not 0 <= sheet < len(sheets):
            raise ImportFormatError(f"workbook has {len(sheets)} sheet(s); no sheet {sheet + 1}")
        chosen = sheets[sheet]
    else:
        match = [s for s in sheets if s.get("name") == sheet]
        if not match:
            raise ImportFormatError(f"no sheet named {sheet!r}")
        chosen = match[0]
    rel_id = _attr(chosen, "id")
    rels = book.xml("xl/_rels/workbook.xml.rels")
    target = None
    if rels is not None:
        target = next((r.get("Target") for r in rels if r.get("Id") == rel_id), None)
    if not target:
        raise ImportFormatError("can't locate the sheet inside the workbook")
    if target.startswith("/"):
        return target.lstrip("/"), warnings
    return posixpath.normpath(posixpath.join("xl", target)), warnings


def _string_text(item: ElementTree.Element) -> str:
    """Text of a shared/inline string: direct <t> plus rich-text runs <r><t>, not phonetic
    guides (<rPh>)."""
    parts = [t.text or "" for t in _children(item, "t")]
    for run in _children(item, "r"):
        parts += [t.text or "" for t in _children(run, "t")]
    return "".join(parts)


def _shared_strings(book: _Book) -> list[str]:
    root = book.xml("xl/sharedStrings.xml")
    if root is None:
        return []
    return [_string_text(item) for item in _children(root, "si")]


def _is_date_code(code: str) -> bool:
    if re.search(r"\[(h+|m+|s+)\]", code, re.IGNORECASE):
        return False  # elapsed-time formats ([h]:mm) are durations, not dates
    stripped = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', "", code)
    return bool(re.search(r"[dmyhs]", stripped, re.IGNORECASE))


def _date_styles(book: _Book) -> set[int]:
    root = book.xml("xl/styles.xml")
    if root is None:
        return set()
    custom = {
        int(fmt.get("numFmtId", "-1")): fmt.get("formatCode", "")
        for block in _children(root, "numFmts") for fmt in _children(block, "numFmt")
    }
    dated = set()
    for block in _children(root, "cellXfs"):
        for index, xf in enumerate(_children(block, "xf")):
            fmt_id = int(xf.get("numFmtId", "0"))
            if fmt_id in BUILTIN_DATE_FORMATS or _is_date_code(custom.get(fmt_id, "")):
                dated.add(index)
    return dated


def _date1904(book: _Book) -> bool:
    workbook = book.xml("xl/workbook.xml")
    if workbook is None:  # pragma: no cover - checked earlier by _sheet_path
        return False
    props = [p for p in workbook if _local(p.tag) == "workbookPr"]
    return bool(props) and props[0].get("date1904") in {"1", "true"}


def _number_text(raw: str) -> str:
    if INTEGER.fullmatch(raw):
        return raw.lstrip("+")  # IDs and quantities: keep every digit
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return raw
    if not value.is_finite():
        return raw
    return format(value.normalize(DISPLAY_DIGITS), "f") if value else "0"


def _serial_to_text(raw: str, date1904: bool) -> str:
    serial = Decimal(raw)
    if serial < 0:
        raise ImportFormatError(f"negative date serial {raw}")
    total_seconds = int((serial * 86400).to_integral_value())
    days, seconds = divmod(total_seconds, 86400)
    if date1904:
        base = date(1904, 1, 1)
    elif days < 60:
        base = date(1899, 12, 31)  # before Excel's fictitious 29-Feb-1900
    elif days == 60:
        raise ImportFormatError("date serial 60 is Excel's non-existent 29-Feb-1900")
    else:
        base = date(1899, 12, 30)
    moment = datetime.combine(base, datetime.min.time()) + timedelta(days=days, seconds=seconds)
    if days == 0 and not date1904:
        return moment.time().isoformat()
    if seconds == 0:
        return moment.date().isoformat()
    return moment.isoformat(sep=" ")


def _column_index(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref.upper())
    if not letters:
        raise ImportFormatError(f"bad cell reference {ref!r}")
    index = 0
    for char in letters.group():
        index = index * 26 + ord(char) - 64
        if index > MAX_COLUMNS:
            raise ImportFormatError(f"cell {ref} is beyond column {MAX_COLUMNS}; file refused")
    return index - 1


def _cell_text(cell: ElementTree.Element, strings: list[str], dated: set[int],
               date1904: bool, notes: dict[str, int]) -> str:
    kind = cell.get("t", "n")
    value_nodes = _children(cell, "v")
    raw = (value_nodes[0].text or "") if value_nodes else ""
    if _children(cell, "f") and not value_nodes:
        notes["formula"] += 1
    if kind == "s":
        index = int(raw) if raw.isdigit() else -1
        if not 0 <= index < len(strings):
            raise ImportFormatError("broken shared-string reference in workbook")
        return strings[index]
    if kind == "inlineStr":
        return "".join(_string_text(item) for item in _children(cell, "is"))
    if kind == "b":
        return "TRUE" if raw == "1" else "FALSE"
    if kind == "e":
        notes["error"] += 1
        return raw
    if kind in {"str", "d"}:
        return raw
    if raw == "":
        return ""
    if int(cell.get("s", "0")) in dated:
        return _serial_to_text(raw, date1904)
    return _number_text(raw)


def _read_sheet(book: _Book, sheet: str | int | None) -> Sheet:
    path, warnings = _sheet_path(book, sheet)
    root = book.xml(path)
    if root is None:
        raise ImportFormatError(f"sheet file {path} is missing")
    strings = _shared_strings(book)
    dated = _date_styles(book)
    date1904 = _date1904(book)
    notes = {"formula": 0, "error": 0}

    rows: list[list[str]] = []
    for data_block in _children(root, "sheetData"):
        for row in _children(data_block, "row"):
            row_number = int(row.get("r", len(rows) + 1))
            if row_number <= len(rows):
                raise ImportFormatError(f"row {row_number} is out of order or repeated")
            if row_number > MAX_ROWS:
                raise ImportFormatError(f"more than {MAX_ROWS} rows; file refused")
            cells: list[str] = []
            for cell in _children(row, "c"):
                ref = cell.get("r")
                position = _column_index(ref) if ref else len(cells)
                if position < len(cells):
                    raise ImportFormatError(f"cell {ref} in row {row_number} is out of order")
                cells.extend([""] * (position - len(cells)))
                cells.append(_cell_text(cell, strings, dated, date1904, notes))
            rows.extend([] for _ in range(row_number - 1 - len(rows)))
            rows.append(cells)
    if notes["formula"]:
        warnings.append(f"{notes['formula']} formula cell(s) have no saved value and were read "
                        "as blank; open and re-save the file in Excel (Q-018)")
    if notes["error"]:
        warnings.append(f"{notes['error']} cell(s) contain Excel errors such as #N/A (Q-018)")
    return Sheet(rows, tuple(warnings))


def read_xlsx_sheet(data: bytes, *, password: str | None = None,
                    sheet: str | int | None = None) -> Sheet:
    """Rows of cell text from one sheet (the first visible one by default), plus warnings."""
    if data.startswith(OLE_MAGIC):
        data = _decrypt(data, password)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            _check_archive(archive)
            return _read_sheet(_Book(archive), sheet)
    except ImportFormatError:
        raise
    except (zipfile.BadZipFile, zlib.error, EOFError, expat.ExpatError, ValueError,
            KeyError) as error:
        raise ImportFormatError(f"not a readable Excel file ({type(error).__name__})") from None


def read_xlsx(data: bytes, *, password: str | None = None,
              sheet: str | int | None = None) -> list[list[str]]:
    """Rows of cell text only (see ``read_xlsx_sheet`` for warnings)."""
    return read_xlsx_sheet(data, password=password, sheet=sheet).rows
