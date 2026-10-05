"""Minimal, dependency-light XLSX reader for broker reports.

Cell values are read as the text stored in the sheet XML and turned into strings for the
tabular importer, so numbers never pass through float (CLAUDE.md rule 2). Excel stores numbers
as binary doubles and may write noise such as 0.30000000000000004; values are rounded to 15
significant digits, which is what Excel itself displays. Dates are recognised from the cell's
number format and returned as ISO text.

Password-protected workbooks (Groww reports use the PAN) are decrypted locally with
msoffcrypto-tool. Nothing here touches the network.

Safety: archive size and member count are capped, and XML with a DTD or entity declarations is
refused (a spreadsheet never needs one), which blocks entity-expansion attacks.
"""

import io
import posixpath
import re
import zipfile
from datetime import date, datetime, timedelta
from decimal import Context, Decimal, InvalidOperation
from xml.etree import ElementTree

from importers.base import ImportFormatError

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ZIP_MAGIC = b"PK\x03\x04"
MAX_MEMBERS = 5000
MAX_UNCOMPRESSED = 200 * 1024 * 1024
DISPLAY_DIGITS = Context(prec=15)
BUILTIN_DATE_FORMATS = {14, 15, 16, 17, 18, 19, 20, 21, 22, 45, 46, 47}


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
    if password is None:
        raise ImportFormatError(
            "the workbook is password-protected; supply the password (Groww reports use your "
            "PAN in capital letters)"
        )
    import msoffcrypto  # local import: only needed for protected files
    from msoffcrypto.exceptions import DecryptionError, InvalidKeyError
    from msoffcrypto.format.ooxml import OOXMLFile

    try:
        office = msoffcrypto.OfficeFile(io.BytesIO(data))
    except Exception:
        raise ImportFormatError("not a readable Excel file") from None
    if not isinstance(office, OOXMLFile):
        raise ImportFormatError("old .xls files aren't supported; save as .xlsx or .csv")
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


class _Book:
    def __init__(self, archive: zipfile.ZipFile) -> None:
        self.archive = archive
        self.names = set(archive.namelist())

    def xml(self, path: str) -> ElementTree.Element | None:
        if path not in self.names:
            return None
        raw = self.archive.read(path)
        # OOXML parts are UTF-8; refusing UTF-16 keeps the DTD check below reliable.
        if raw[:2] in (b"\xff\xfe", b"\xfe\xff") or b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
            raise ImportFormatError(f"unexpected encoding or DTD in {path}; file refused")
        return ElementTree.fromstring(raw)  # noqa: S314 - DTDs/entities refused above


def _check_archive(archive: zipfile.ZipFile) -> None:
    infos = archive.infolist()
    if len(infos) > MAX_MEMBERS or sum(i.file_size for i in infos) > MAX_UNCOMPRESSED:
        raise ImportFormatError("workbook is too large to import")


def _sheet_path(book: _Book, sheet: str | int | None) -> str:
    workbook = book.xml("xl/workbook.xml")
    if workbook is None:
        raise ImportFormatError("not an Excel workbook (xl/workbook.xml missing)")
    sheets = [s for block in _children(workbook, "sheets") for s in _children(block, "sheet")]
    if not sheets:
        raise ImportFormatError("workbook has no sheets")
    if sheet is None:
        chosen = sheets[0]
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
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join("xl", target))


def _shared_strings(book: _Book) -> list[str]:
    root = book.xml("xl/sharedStrings.xml")
    if root is None:
        return []
    return ["".join(t.text or "" for t in item.iter() if _local(t.tag) == "t")
            for item in _children(root, "si")]


def _is_date_code(code: str) -> bool:
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
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return raw
    if not value.is_finite():
        return raw
    return format(value.normalize(DISPLAY_DIGITS), "f") if value else "0"


def _serial_to_text(raw: str, date1904: bool) -> str:
    serial = Decimal(raw)
    base = date(1904, 1, 1) if date1904 else date(1899, 12, 30)
    days = int(serial)
    seconds = int(((serial - days) * 86400).to_integral_value())
    moment = datetime.combine(base, datetime.min.time()) + timedelta(days=days, seconds=seconds)
    if days == 0 and not date1904 and serial < 1:
        return moment.time().isoformat()
    if seconds == 0:
        return moment.date().isoformat()
    return moment.isoformat(sep=" ")


def _column_index(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref.upper())
    if not letters:
        return -1
    index = 0
    for char in letters.group():
        index = index * 26 + ord(char) - 64
    return index - 1


def read_xlsx(data: bytes, *, password: str | None = None,
              sheet: str | int | None = None) -> list[list[str]]:
    """Rows of cell text from one sheet (the first by default)."""
    if data.startswith(OLE_MAGIC):
        data = _decrypt(data, password)
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ImportFormatError("not a readable Excel file") from None
    with archive:
        _check_archive(archive)
        book = _Book(archive)
        path = _sheet_path(book, sheet)
        root = book.xml(path)
        if root is None:
            raise ImportFormatError(f"sheet file {path} is missing")
        strings = _shared_strings(book)
        dated = _date_styles(book)
        date1904 = _date1904(book)

        rows: list[list[str]] = []
        for data_block in _children(root, "sheetData"):
            for row in _children(data_block, "row"):
                cells: list[str] = []
                for cell in _children(row, "c"):
                    position = _column_index(cell.get("r", "")) if cell.get("r") else len(cells)
                    if position < len(cells):
                        position = len(cells)
                    cells.extend([""] * (position - len(cells)))
                    cells.append(_cell_text(cell, strings, dated, date1904))
                row_number = int(row.get("r", len(rows) + 1))
                while len(rows) < row_number - 1:
                    rows.append([])
                rows.append(cells)
        return rows


def _cell_text(cell: ElementTree.Element, strings: list[str], dated: set[int],
               date1904: bool) -> str:
    kind = cell.get("t", "n")
    value_nodes = _children(cell, "v")
    raw = (value_nodes[0].text or "") if value_nodes else ""
    if kind == "s":
        index = int(raw) if raw.isdigit() else -1
        if not 0 <= index < len(strings):
            raise ImportFormatError("broken shared-string reference in workbook")
        return strings[index]
    if kind == "inlineStr":
        return "".join(t.text or "" for t in cell.iter() if _local(t.tag) == "t")
    if kind == "b":
        return "TRUE" if raw == "1" else "FALSE"
    if kind in {"str", "e", "d"}:
        return raw
    if raw == "":
        return ""
    if int(cell.get("s", "0")) in dated:
        return _serial_to_text(raw, date1904)
    return _number_text(raw)
