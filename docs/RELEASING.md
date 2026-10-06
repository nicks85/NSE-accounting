# Releasing and verifying Kosh

## Cutting a release (maintainers)

1. Make sure `main` is green in CI and `docs/OPEN_QUESTIONS.md` reflects the release.
2. Bump the version in `pyproject.toml`, `engine/__init__.py`, `app/package.json`,
   `app/src-tauri/Cargo.toml` and `app/src-tauri/tauri.conf.json`, commit, and tag: `git tag v0.1.0 && git push origin v0.1.0`.
3. The **Release** workflow builds the engine and installers on Linux, macOS and Windows,
   smoke-tests the bundled engine, and attaches everything to a **draft** GitHub release with
   `SHA256SUMS` and signed build-provenance attestations.
4. Review the draft (install it, run through import → gains → export) and publish it.

### Optional OS code signing

Signing for macOS Gatekeeper / notarisation and Windows SmartScreen needs certificates only the
maintainer can obtain. Add these repository secrets and the workflow uses them automatically:
`APPLE_CERTIFICATE`, `APPLE_CERTIFICATE_PASSWORD`, `APPLE_SIGNING_IDENTITY`, `APPLE_ID`,
`APPLE_PASSWORD`, `APPLE_TEAM_ID` (see the Tauri signing guide). Windows Authenticode signing is
not wired yet. Without them, installers are unsigned at the OS level but still carry signed
provenance (below).

## Verifying a download (anyone)

1. **Checksum:** `sha256sum -c SHA256SUMS --ignore-missing`.
2. **Provenance:** with the GitHub CLI,
   `gh attestation verify <file> --repo nicks85/NSE-accounting`. This proves the file was built
   by this repository's Release workflow from a specific commit (Sigstore-signed; no keys held by
   anyone).
3. **Rebuild the engine yourself:** check out the tagged commit and run
   `uv sync --locked --group build && uv run --group build python scripts/build_engine.py`.
   The engine build is reproducible on the same OS, architecture and toolchain (fixed hash seed,
   timestamps from the commit): compare the SHA-256 of
   `app/src-tauri/binaries/kosh-engine-<target>` with the release asset of the same name.
   Full bit-for-bit reproducibility of the platform installers (DMG/MSI/AppImage) is not yet
   verified; that is tracked as future work. `app/src-tauri/Cargo.lock` must be committed (it
   needs a machine with Rust: `cargo generate-lockfile` in `app/src-tauri`) so Rust
   dependencies are locked too.

Builds cover Linux x86_64, Windows x86_64 and macOS on Apple silicon (`macos-latest`); there is
no Intel-Mac build yet.

## What the app never does

No network calls, no telemetry, no update checks: the app's content-security policy allows no
HTTP, no network-capable Tauri plugin is installed, and `tests/test_offline_guard*.py` fail the
build if any of that changes. Runtime dependencies are audited in `docs/DEPENDENCIES.md`.
