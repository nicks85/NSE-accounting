# Runtime dependencies and the offline guarantee

Kosh must make zero network calls at runtime (CLAUDE.md rule 1). Every runtime dependency is
listed here with how that is ensured. `tests/test_offline_guard.py` fails the build if runtime
code imports a networking module, or if importing the engine/importers (and running a CAS
parse) loads `socket`, `ssl`, `urllib.request`, `http.client` or similar.

| Package | Used for | Network code? | How it's kept offline |
|---|---|---|---|
| msoffcrypto-tool (+ cryptography, olefile) | Decrypting password-protected XLSX (Groww) | None found | Imported lazily only for protected files |
| casparser (+ pypdfium2, pydantic, rich, click, dateutil) | Parsing CAMS/KFintech CAS PDFs | None in the parsing path | Imported lazily by `importers/cas.py` |
| casparser-isin (+ rapidfuzz) | Local ISIN / AMFI database (SQLite, bundled) | **Yes**: `casparser_isin/cli.py` downloads database updates via `urllib.request` | Never imported by Kosh (guard test checks `casparser_isin.cli` is not loaded). **Packaging (Phase 5): strip `casparser_isin/cli.py` from the bundled app.** |

Dev-only tools (pytest, ruff, mypy) are not shipped.
