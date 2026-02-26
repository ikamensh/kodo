"""Methodology library for --improve discovery stage."""

import shutil


def _detect_docker() -> bool:
    """Check whether docker is available on the host."""
    return shutil.which("docker") is not None


METHODOLOGY_LIBRARY = """\
## Recommended Methodologies

These are starting points — adapt, combine, or invent approaches that fit \
the project. You are not limited to this list.

### Static Analysis & Baseline
- **Lint & Type Check**: Run the project's configured linters and type checkers \
(mypy, pyright, eslint, tsc --noEmit, clippy, golangci-lint, etc.)
- **Dependency Audit**: Check for known vulnerabilities \
(pip-audit, npm audit, cargo audit, govulncheck, bundler-audit)
- **Dead Code / Unused Deps**: Find unused imports, unreachable code, \
dependencies in manifests that nothing imports

### Functional Testing
- **Happy Path Integration**: Run 3-5 core user scenarios end-to-end with \
realistic inputs. Mock or stub external services.
- **Adversarial / Edge Cases**: Empty inputs, None/null, zero, huge values, \
unicode, invalid configs, missing dependencies, wrong permissions
- **Property-Based Testing**: Generate random inputs to find invariant \
violations. Tools: Hypothesis (Python), fast-check (JS/TS), proptest (Rust), \
gopter (Go), jqwik (Java/Kotlin). Write properties for pure functions and \
data transformations.
- **Concurrency Testing**: Race conditions, deadlocks, thread safety. Relevant \
when the project uses async, threading, multiprocessing, or concurrent data \
structures.
- **Recent-Change Focus**: Use `git diff main...HEAD` or recent commits to \
identify recently changed code and concentrate testing effort there.

### Library / SDK-Specific
- **API Surface Audit**: Naming consistency, type annotations, docstring \
accuracy vs actual signatures, error/exception types
- **Consumer Project Testing**: Install as a dependency in a temp dir, exercise \
from a consumer's perspective. Can a developer start from the README alone?
- **API Misuse Testing**: Wrong types, missing args, wrong call order, edge \
values. Grade each error message: does it say what went wrong and how to fix it?

### Security
- **Input Validation**: SQL injection, path traversal, command injection, XSS \
at system boundaries (user input, external APIs, file uploads)
- **Secret Scanning**: Hardcoded credentials, API keys, tokens in source or \
config files
- **Permission / Auth Boundaries**: Verify access controls, privilege \
escalation paths (relevant for web apps, APIs with auth)

### Performance & Resources
- **Resource Leak Detection**: Unclosed files, DB connections, HTTP clients \
without context managers / defer / try-with-resources
- **Hot Path Profiling**: N+1 queries, unbounded loops, quadratic algorithms \
in hot paths

### Isolated Environment Testing
- **Docker-Based Testing**: Build and run the project inside a container to \
test in a clean environment — catches missing dependencies, implicit host \
assumptions, and install/build issues. Especially useful for projects with \
a Dockerfile or docker-compose setup.

### Architecture & Simplification
- **Unnecessary Complexity**: Code that could be simpler without losing \
functionality. Abstractions that don't pay for themselves, indirection \
that obscures rather than clarifies, dead code paths.
- **Structural Issues**: Poor module boundaries, circular dependencies, \
responsibilities in the wrong place.

### Infrastructure
- **Dockerfile Review**: Multi-stage builds, security (running as root, \
secrets in layers), layer optimization
- **CI/CD Config Audit**: Pipeline correctness, missing steps, caching, \
flaky test handling
"""
