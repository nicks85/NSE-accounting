# Runtime dependencies and the offline guarantee

Kosh must make zero network calls at runtime (CLAUDE.md rule 1). Every runtime dependency is
listed here with how that is ensured. `tests/test_offline_guard.py` fails the build if runtime
code imports a networking module, or if importing the engine/importers (and running a CAS
parse) loads `socket`, `ssl`, `urllib.request`, `http.client` or similar.

| Package | Used for | Network code? | How it's kept offline |
|---|---|---|---|
| msoffcrypto-tool (+ cryptography, olefile) | Decrypting password-protected XLSX (Groww) | None found | Imported lazily only for protected files |
| casparser (+ pypdfium2, pydantic, rich, click, dateutil) | Parsing CAMS/KFintech CAS PDFs | None in the parsing path | Imported lazily by `importers/cas.py` |
| jsonschema (+ referencing, rpds-py, attrs, jsonschema-specifications) | Validating ITR JSON against the official CBDT schemas | **Yes, unused**: the deprecated `RefResolver` and remote `$ref` retrieval can fetch over HTTP | Kosh validates with an empty `referencing.Registry` and no retrieve function, so remote refs raise instead of being fetched (test: `test_remote_ref_is_never_fetched`); the guard runs a validation under tripwires |
| casparser-isin (+ rapidfuzz) | Local ISIN / AMFI database (SQLite, bundled) | **Yes**: `casparser_isin/cli.py` downloads database updates via `urllib.request` | Never imported by Kosh (guard test checks `casparser_isin.cli` is not loaded). **Packaging (Phase 5): strip `casparser_isin/cli.py` from the bundled app.** |

casparser-isin reads its database from the bundled file unless the environment variable
`CASPARSER_ISIN_DB` points elsewhere; the packaged app must not set it.

Dev-only tools (pytest, ruff, mypy) are not shipped.
