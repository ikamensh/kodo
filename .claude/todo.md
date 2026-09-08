# Kodo maintenance review

- [x] Audit current provider models, installed dependencies, and documented behavior.
- [x] Refresh verified model defaults while retaining explicit overrides.
- [x] Consolidate a focused area of duplicated implementation without changing workflows.
- [x] Run integration workflows and the regression suite; review the final diff.
- [x] Commit stable changes and report feature gaps and simplification opportunities.

## Scope

Keep kodo focused on local, user-triggered coding runs with unattended progress and independent verification. Preserve existing user files and historical benchmark results. Prefer a small, tested cleanup over a broad rewrite.

## Review

Consolidated backend/model metadata and reused the CLI orchestrator lifecycle for native Kimi. Removed the obsolete SDK adapter and compatibility shim. Fixed interrupted worktree and MCP shutdown cleanup. Independent review exposed and verified Kimi process-tree cancellation and exchange accounting regressions.

Full non-live suite: 1,739 passed, 1 skipped; focused final Kimi budget cases: 3 passed. Wheel/source builds and scoped lint pass. See `docs/maintenance-review-2026-09.md` for feature priorities and remaining warnings, and `docs/model-audit-2026-09-08.md` for sourced model choices.
