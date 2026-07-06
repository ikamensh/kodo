# Decisions

## Current Mission

- **Decision:** Kodo's durable mission is to build an unattended, multi-agent coding orchestrator.
- **Provenance:** Assistant synthesized this from repository docs, tests, scripts, and recent commits; user accepted the framing in the July 6, 2026 intake.

## Iteration 1 Direction

- **Decision:** Prioritize release hygiene now by stabilizing the `0.5.1` release/test surface.
- **Provenance:** Assistant proposed stabilizing `0.5.1`; user approved it as iteration 1.

## Supported Verification Surface

- **Decision:** Both pytest and `scripts/` smoke workflows are supported primary no-key verification paths.
- **Provenance:** User answered that scripts under `scripts/` stay supported alongside pytest.

## Stale Smoke Scripts

- **Decision:** Fix stale smoke scripts instead of deleting them.
- **Provenance:** User explicitly instructed to fix the stale script surface, including `scripts/smoke_test_cli.py`.

## Version Consistency

- **Decision:** `pyproject.toml` version `0.5.1` and `kodo.__version__` must agree for the release.
- **Provenance:** Assistant found packaging tests failing because runtime reported `0.5.0` while package metadata reported `0.5.1`; user approved making this part of iteration 1.

## Kodo Knowledge

- **Decision:** `kodo knowledge` remains a dormant experiment for now.
- **Provenance:** User stated it is not the next direction and should remain unshipped/experimental.

- **Decision:** Do not add a package entry point, CLI command, or broader release promise for `kodo/knowledge` in iteration 1.
- **Provenance:** User explicitly answered that `kodo/knowledge` stays private/experimental.

## Kodo Doctor

- **Decision:** `kodo doctor` is product-surface relevant but out of scope for iteration 1.
- **Provenance:** User noted issue `#53` was filed through Hive separately and should be handled through the issue pipeline.

## Release Checklist

- **Decision:** Add or update a lightweight release validation checklist during iteration 1.
- **Provenance:** Assistant proposed capturing version consistency, supported mocked smoke scripts, and targeted pytest commands; user approved the iteration brief.

## Commit Policy

- **Decision:** Do not commit or push until explicitly requested.
- **Provenance:** The intake recorded no commit or push until explicitly requested; user then explicitly requested writing spec files, committing, and pushing them.
