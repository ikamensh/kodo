# Iteration 1: Stabilize 0.5.1 Release Surface

This iteration is release hygiene for `0.5.1`, not product expansion.

## Goal

Make package/runtime versioning consistent and restore the documented mocked, no-key verification workflows that developers rely on before publishing.

## Scope

- Reconcile `pyproject.toml` version `0.5.1` with `kodo.__version__`.
- Run packaging tests after the version fix.
- Fix stale supported smoke scripts instead of deleting them, especially `scripts/smoke_test_cli.py`, which patches removed `kodo.factory._build_team_mission`.
- Run the AGENTS-listed mocked scripts where practical.
- Run targeted pytest slices covering CLI smoke, packaging, and touched behavior.
- Keep `kodo/knowledge` dormant and experimental: no package entry point, no CLI command, and no broader release promise.
- Keep issue `#53` for `kodo doctor` out of this iteration; it belongs to the issue pipeline.

## Acceptance Signal

The iteration is ready when:

- Runtime version and package version both report `0.5.1`.
- Packaging tests pass.
- Supported mocked smoke workflows documented in AGENTS.md pass where practical, with any skipped workflow explicitly justified.
- Targeted pytest slices for CLI smoke, packaging, and touched behavior pass.
- A lightweight release validation checklist exists and captures version consistency, supported mocked smoke scripts, and targeted pytest commands.
- No product code for `kodo knowledge` exposure or `kodo doctor` is added as part of this iteration.
