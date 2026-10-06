# Contributing to Kosh

Thank you for helping. Kosh computes tax figures people may file, so correctness and honesty
about uncertainty matter more than speed. Please read `README.md` and `CLAUDE.md` first.

## Ground rules (non-negotiable)

1. **Offline.** No network calls at runtime: no analytics, CDN assets or update checks. Don't add
   network-capable dependencies, Tauri plugins or capabilities. `tests/test_offline_guard*.py`
   enforce this.
2. **Money is `Decimal`.** Never `float` for amounts, in Python or in the UI (the UI keeps
   amounts as decimal strings and only formats them for display). Round only when reporting.
3. **Never invent tax rules.** Every rule cites the Income-tax Act 1961 section and the
   Income-tax Act 2025 section (where applicable) plus a source URL (official texts are in
   `docs/sources/`). If unsure, implement it behind `unverified=True`, make it surface a warning,
   and add an entry to `docs/OPEN_QUESTIONS.md`.
4. **Synthetic data only.** Test fixtures are generated; no real statements or personal data.
   The only PAN allowed anywhere is `ABCDE1234F`.
5. **Rule packs are versioned by tax year.** A Budget change is a new file in `engine/rules/`,
   not an edit to an old one.
6. **Never fabricate a broker format.** Base importers on public documentation or anonymised
   real samples, and keep `FORMAT_CONFIRMED = False` until confirmed.

## Setup

```bash
uv sync                          # Python engine + importers
pnpm --dir app install           # UI
uv run pytest -q                 # engine/importer unit, golden and offline-guard tests
uv run pytest --cov=engine --cov=importers   # coverage (kept at 100%)
uv run ruff check . && uv run mypy engine importers
pnpm --dir app test              # UI unit tests
pnpm --dir app build && pnpm --dir app exec playwright test   # E2E through the real engine
pnpm --dir app dev               # browser UI at http://localhost:1420 (talks to the local engine)
uv run --group build python scripts/build_engine.py   # bundled engine (needed by tauri dev/build)
KOSH_ENGINE_PYTHON="$(uv run which python)" pnpm --dir app tauri dev   # desktop app (needs Rust)
```

`tauri dev` and `tauri build` need the bundled engine in `app/src-tauri/binaries/`, so build it
first. In debug builds `KOSH_ENGINE_PYTHON` makes the app run `python -m engine.rpc` from your
checkout instead (on Windows use the path of `.venv\Scripts\python.exe`); release builds only
ever run the bundled engine.

```bash
uv run --group build python scripts/check_engine_binary.py   # smoke-test the bundled engine
```

## Workflow

- One feature per branch (`feat/...`, `fix/...`), small conventional commits, tests passing
  before every commit (`set -o pipefail` when piping test output).
- **UI changes are click-tested in the running app** (built web UI or desktop) before
  committing — not only unit-tested — and covered by a Playwright test.
- Every PR-sized change gets a QA review (see `.claude/agents/qa.md` for the checklist).

## Common contributions

- **A rule or correction:** change the relevant rule pack or `engine/rules/common.py`, cite both
  Acts and the source, add golden cases in `tests/golden/` with the hand computation in the
  docstring, and resolve or add the `docs/OPEN_QUESTIONS.md` entry.
- **A golden case:** synthetic trades, the expected figures, and step-by-step working a CA can
  check.
- **An importer:** a `BrokerProfile` in `importers/` (or a new parser), a synthetic fixture
  generator in `tests/fixtures/`, round-trip tests to tax, and an open question for anything
  unconfirmed.
- **Verifying an open question:** the most valuable contribution. Add the citation or evidence,
  remove the `unverified` flag, and update the tests.

## Disclaimer

Kosh is a calculation aid, not tax, legal or financial advice.
