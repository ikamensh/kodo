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

Autonomous test generation. Discovers coverage gaps, writes tests in parallel, runs full regression.

![Test Mode](diagrams/test-mode.svg)
