# Mission

Kodo is an unattended, multi-agent coding orchestrator. A user gives `kodo` a goal, test request, or improvement request; the system delegates to coding agents, verifies independently, resumes interrupted work, logs progress, and produces reviewed code.

## Operating Principles

- Release surfaces should stay coherent: runtime behavior, package metadata, documented workflows, and smoke scripts should agree before publishing.
- Supported no-key workflows matter. Mocked scripts under `scripts/` are primary developer verification paths alongside pytest and should be fixed when they drift.
- Verification should be independent where practical: orchestrated coding work is not complete until another path has checked it.
- Interrupted work should be resumable, inspectable, and explainable from persisted run state and logs.
- Product surface should grow deliberately. Experimental subsystems should not become commands, entry points, or package promises until they are explicitly promoted.
- Keep release hygiene distinct from product expansion. New product ideas should enter through the issue pipeline unless an iteration explicitly adopts them.
