"""Synthetic XLSX writer for importer tests. Writes the minimal OOXML parts by hand so tests
control exactly what is stored (raw number text, styles, inline strings). Synthetic data only."""

import io
import random
import zipfile
from dataclasses import dataclass
from xml.sax.saxutils import escape


@dataclass(frozen=True)
class Num:
    raw: str
    style: int = 0
    """0 general, 1 date (builtin 14), 2 date-time (custom), 3 currency (custom, not a date)."""


@dataclass(frozen=True)
class Inline:
    text: str


@dataclass(frozen=True)
class Bool:
    value: bool


Cell = str | Num | Inline | Bool | None

STYLES = """<?xml version="1.0" encoding="UTF-8"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<numFmts count="2"><numFmt numFmtId="164" formatCode="yyyy\\-mm\\-dd hh:mm:ss"/>
<numFmt numFmtId="165" formatCode="&quot;Rs&quot; #,##0.00;[Red]\\-#,##0.00"/></numFmts>
<cellXfs count="4"><xf numFmtId="0"/><xf numFmtId="14"/><xf numFmtId="164"/>
<xf numFmtId="165"/></cellXfs></styleSheet>"""


def _col(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def xlsx_bytes(rows: list[list[Cell]], *, sheet_name: str = "Trades", date1904: bool = False,
               extra_sheets: tuple[str, ...] = (), absolute_target: bool = False,
               pad: bool = False) -> bytes:
    """``pad`` adds an 8 KB incompressible dummy part so the package is big enough to encrypt
    (see ``encrypt``)."""
    strings: list[str] = []
    xml_rows = []
    for r, row in enumerate(rows, start=1):
        cells = []
        for c, value in enumerate(row):
            ref = f"{_col(c)}{r}"
            if value is None:
                continue
            if isinstance(value, str):
                strings.append(value)
                cells.append(f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>')
            elif isinstance(value, Num):
                cells.append(f'<c r="{ref}" s="{value.style}"><v>{value.raw}</v></c>')
            elif isinstance(value, Inline):
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(value.text)}</t></is></c>')
            else:
                cells.append(f'<c r="{ref}" t="b"><v>{int(value.value)}</v></c>')
        if cells:
            xml_rows.append(f'<row r="{r}">{"".join(cells)}</row>')
    main = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rel_ns = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    names = (sheet_name, *extra_sheets)
    sheets = "".join(f'<sheet name="{n}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
                     for i, n in enumerate(names))
    prefix = "/xl/" if absolute_target else ""
    rels = "".join(
        f'<Relationship Id="rId{i + 1}" Type="worksheet" '
        f'Target="{prefix}worksheets/sheet{i + 1}.xml"/>'
        for i in range(len(names)))
    shared = "".join(f"<si><t>{escape(s)}</t></si>" for s in strings)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/'
                   'package/2006/content-types"/>')
        z.writestr("xl/workbook.xml",
                   f'<workbook {main} {rel_ns}><workbookPr date1904="{int(date1904)}"/>'
                   f"<sheets>{sheets}</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                   f'relationships">{rels}</Relationships>')
        z.writestr("xl/sharedStrings.xml", f"<sst {main}>{shared}</sst>")
        z.writestr("xl/styles.xml", STYLES)
        z.writestr("xl/worksheets/sheet1.xml",
                   f"<worksheet {main}><sheetData>{''.join(xml_rows)}</sheetData></worksheet>")
        if pad:
            z.writestr("docProps/padding.bin", random.Random(0).randbytes(8192))
        for i in range(1, len(names)):
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml",
                       f'<worksheet {main}><sheetData><row r="1"><c r="A1" t="inlineStr">'
                       f"<is><t>{names[i]}</t></is></c></row></sheetData></worksheet>")
    return out.getvalue()


def encrypt(data: bytes, password: str) -> bytes:
    """Agile-encrypt a workbook. msoffcrypto's own encrypt → decrypt round trip fails for
    packages under ~4 KB (OLE mini-stream), so build small fixtures with ``pad=True``."""
    from msoffcrypto.format.ooxml import OOXMLFile

    out = io.BytesIO()
    OOXMLFile(io.BytesIO(data)).encrypt(password, out)
    return out.getvalue()
