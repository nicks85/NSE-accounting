# CLAUDE.md — Kosh (offline Indian share-market tax app)

You are the lead engineer building Kosh, described in README.md. Read README.md first.
Work autonomously through the phases below, delegating to subagents, and keep tests green.

## Non-negotiable rules

1. **Offline.** The app must make zero network calls at runtime. No analytics, no CDN assets,
   no update checks. The Tauri allowlist must deny network. Add a test that fails if any
   runtime module imports `requests`, `httpx`, `urllib.request` or `socket`.
2. **Money is `Decimal`.** Never use float for amounts. Round only at the final reporting step,
   per Income Tax rounding rules.
3. **Never invent tax rules.** Every rule in `engine/rules/` must carry a docstring with the
   section of the Income-tax Act 1961 *and* the Income-tax Act 2025 (where applicable), plus
   a source URL (incometax.gov.in / CBDT notification). If you are unsure of a rule, implement
   it behind `UNVERIFIED = True`, surface a warning in the output, and add it to
   `docs/OPEN_QUESTIONS.md`. Do not guess silently.
4. **Synthetic data only.** Test fixtures must be generated, never real personal data.
   No PAN numbers except the obviously fake `ABCDE1234F`.
5. **Rule packs are versioned by tax year.** A Budget change = a new rule pack file, not edits
   to an old one. FY 2025-26 and earlier use 1961 Act; Tax Year 2026-27 onward uses 2025 Act.
6. **Commit small.** One logical change per commit, conventional commit messages,
   tests passing before every commit.

## Commands

```bash
uv sync                        # install engine deps
uv run pytest -q               # engine unit + golden tests
uv run pytest --cov=engine     # coverage (target ≥ 90% on engine/)
uv run ruff check . && uv run mypy engine
pnpm --dir app install
pnpm --dir app test            # UI unit tests (Vitest)
pnpm --dir app build           # web build used for E2E
pnpm --dir app exec playwright test   # E2E against the built UI served on localhost
```

## Subagents

Define these in `.claude/agents/` (create the files in Phase 0). Run independent ones in parallel.

| Agent | Owns | Must not touch |
|---|---|---|
| `tax-rules` | `engine/rules/`, `docs/rules/`, `docs/OPEN_QUESTIONS.md` | importers, UI |
| `importer` | `importers/`, `tests/fixtures/` generators | rules |
| `engine` | `engine/matching/`, `engine/classify/`, `engine/export/` | UI |
| `ui` | `app/` | engine internals (use the public API only) |
| `qa` | `tests/golden/`, E2E tests, offline-guard test; reviews every PR-sized change | production code (report issues instead) |

The lead (you) owns the public engine API (`engine/api.py`), integration, and this file.

## Phases

**Phase 0 — Scaffold.** Repo layout from README, `pyproject.toml` (uv), ruff, mypy strict,
pytest, Tauri + React app, GitHub Actions CI (lint, test, build on Linux/macOS/Windows),
subagent definition files. Exit: CI green on an empty skeleton.

**Phase 1 — Engine core.** Lot model, FIFO matcher, corporate actions, classification
(delivery / intraday / F&O), equity STCG/LTCG with grandfathering and the 23-Jul-2024 cutover,
₹1.25 lakh exemption, set-off and carry-forward ledger. Exit: ≥ 30 golden cases passing.

**Phase 2 — Importers.** Zerodha first, then Groww, Upstox, Angel One, then CAS PDF.
Each importer normalises into the same `Trade` schema and has a synthetic fixture generator.
Exit: each broker round-trips fixture → trades → expected gains.

**Phase 3 — Export.** Download the official CBDT ITR-2/ITR-3 JSON schemas into
`engine/export/schemas/` (one-time, at dev time — never at runtime) and validate output against
them. PDF summary report. Exit: exported JSON validates against schema.

**Phase 4 — UI.** Import wizard, holdings/lots view, gains summary with "why?" drill-down
showing the rule and section, loss carry-forward screen, export screen.
Exit: Playwright E2E covers import → compute → export.

**Phase 5 — Hardening.** Offline guard, reproducible build, signed release workflow,
`CONTRIBUTING.md`, disclaimer screen on first launch.

## Definition of done for any task

- Tests added and passing; coverage not reduced.
- Lint and type checks clean.
- Any tax assumption cited or logged in `docs/OPEN_QUESTIONS.md`.
- `qa` agent has reviewed it.

## Stop and ask the human when

- A tax rule is ambiguous or sources conflict.
- A change would add any network capability.
- You need real broker sample files (ask for anonymised ones; never fabricate their format —
  check public broker docs or ask).
- Before starting any new feature or changing a user-facing workflow: write a short
  decision brief in docs/decisions/NNNN-<topic>.md (problem, options, trade-offs,
  your recommendation, open tax questions), then STOP and wait for my approval.
  Do not write production code until I approve the brief.
