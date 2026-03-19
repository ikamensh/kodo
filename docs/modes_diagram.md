# What Kodo does

## Overview

![Modes overview](diagrams/overview.svg)

## Goal Mode (`--goal`)

The primary mode: give kodo a goal, it plans and executes with autonomous agents.

![Goal Mode](diagrams/goal-mode.svg)

## Improve Mode (`--improve`)

Autonomous code review and refactoring. Parallel read-only analysis feeds into sequential triage + fix.

![Improve Mode](diagrams/improve-mode.svg)

## Test Mode (`--test`)

User-experience-first testing. Installs the software, exercises every feature end-to-end, probes edge cases, then writes regression tests for confirmed bugs.

![Test Mode](diagrams/test-mode.svg)
