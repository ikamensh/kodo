# Iteration 2: Enforce Testability Policies and Document Stabilized Features

## Goal
Enforce testability policies and document newly stabilized features.

## Scope
- Update test configurations so developers running standard test suites are guaranteed they use mocked backends without accidental live API calls.
- Update benchmark configurations to write output to `.hive/benchmarks/`.
- Document the newly stabilized `kodo doctor` command so users can discover how to check machine readiness.

## Acceptance Signal
- Automated tests enforce mocked backend usage by default.
- Benchmark output correctly routes to the designated `.hive/benchmarks/` directory.
- The `kodo doctor` command is properly documented in the user-facing documentation.
