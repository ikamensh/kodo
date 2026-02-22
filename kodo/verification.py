"""Enhanced verification checklist for architect and tester agents.

Provides structured verification prompts with categorized checks,
severity levels, and result parsing for tracking bug detection rates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence


class CheckCategory(Enum):
    """Categories of verification checks."""

    SYNTAX = "syntax"
    TESTS = "tests"
    WARNINGS = "warnings"
    ARCHITECTURE = "architecture"
    SECURITY = "security"
    PERFORMANCE = "performance"
    CORRECTNESS = "correctness"


class Severity(Enum):
    """Issue severity levels."""

    BLOCKER = "blocker"  # must fix before merge
    MAJOR = "major"  # should fix but not blocking
    MINOR = "minor"  # cosmetic or style issues
    INFO = "info"  # informational notes


@dataclass
class VerificationCheck:
    """A single verification check item."""

    category: CheckCategory
    description: str
    prompt_instruction: str

    def to_checklist_item(self) -> str:
        """Format as a checklist instruction for the agent."""
        return f"- [ ] **{self.category.value.upper()}**: {self.description}"


@dataclass
class VerificationIssue:
    """An issue found during verification."""

    category: CheckCategory
    severity: Severity
    description: str
    file_path: str | None = None
    line_number: int | None = None

    @property
    def location(self) -> str:
        if self.file_path and self.line_number:
            return f"{self.file_path}:{self.line_number}"
        if self.file_path:
            return self.file_path
        return "(no location)"


@dataclass
class VerificationReport:
    """Parsed result of a verification run."""

    issues: list[VerificationIssue] = field(default_factory=list)
    checks_passed: list[str] = field(default_factory=list)
    raw_text: str = ""

    @property
    def blocker_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == Severity.BLOCKER)

    @property
    def major_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == Severity.MAJOR)

    @property
    def minor_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == Severity.MINOR)

    @property
    def total_issues(self) -> int:
        return len(self.issues)

    @property
    def is_clean(self) -> bool:
        """True if no blockers or major issues found."""
        return self.blocker_count == 0 and self.major_count == 0

    @property
    def issues_by_category(self) -> dict[CheckCategory, list[VerificationIssue]]:
        result: dict[CheckCategory, list[VerificationIssue]] = {}
        for issue in self.issues:
            result.setdefault(issue.category, []).append(issue)
        return result

    def summary(self) -> str:
        """One-line summary of the verification result."""
        if not self.issues:
            return "ALL CHECKS PASS — no issues found"
        parts = []
        if self.blocker_count:
            parts.append(f"{self.blocker_count} blocker(s)")
        if self.major_count:
            parts.append(f"{self.major_count} major")
        if self.minor_count:
            parts.append(f"{self.minor_count} minor")
        return f"ISSUES FOUND: {', '.join(parts)}"


# ---------------------------------------------------------------------------
# Standard verification checklists
# ---------------------------------------------------------------------------

ARCHITECT_CHECKLIST: list[VerificationCheck] = [
    VerificationCheck(
        CheckCategory.SYNTAX,
        "Code syntax valid — no parse errors",
        "Check for syntax errors in all modified files. "
        "Try importing the modules or running a linter.",
    ),
    VerificationCheck(
        CheckCategory.TESTS,
        "Tests pass — run the test suite and verify green",
        "Run `pytest` (or the project's test command) and verify all tests pass. "
        "Report any failures with the test name and error message.",
    ),
    VerificationCheck(
        CheckCategory.WARNINGS,
        "No new warnings — linter/type-checker clean",
        "Check for new warnings from linters or type checkers. "
        "New deprecation warnings or type errors should be flagged.",
    ),
    VerificationCheck(
        CheckCategory.ARCHITECTURE,
        "Architecture consistent — follows existing patterns",
        "Verify new code follows existing architectural patterns. "
        "Check: proper separation of concerns, consistent naming, "
        "no circular imports, appropriate abstraction level.",
    ),
    VerificationCheck(
        CheckCategory.SECURITY,
        "Security — no hardcoded secrets or injection points",
        "Check for: hardcoded API keys/passwords, SQL injection, "
        "path traversal, unsafe deserialization, missing input validation. "
        "Reject any code with hardcoded credentials.",
    ),
    VerificationCheck(
        CheckCategory.PERFORMANCE,
        "Performance — no O(n²) loops or unbounded memory",
        "Look for: nested loops over large collections, missing pagination, "
        "unbounded list growth, missing connection pooling, N+1 queries.",
    ),
    VerificationCheck(
        CheckCategory.CORRECTNESS,
        "Correctness — code does what it claims to do",
        "Verify the implementation matches the stated goal. "
        "Check edge cases, error handling, and boundary conditions.",
    ),
]


def build_verification_prompt(
    goal: str,
    summary: str,
    *,
    checklist: Sequence[VerificationCheck] | None = None,
    role: str = "architect",
) -> str:
    """Build a structured verification prompt with checklist.

    Parameters
    ----------
    goal : str
        The original goal being verified.
    summary : str
        What the orchestrator claims was accomplished.
    checklist : list[VerificationCheck], optional
        Custom checklist. Defaults to ARCHITECT_CHECKLIST.
    role : str
        Role label (architect, tester, etc.).

    Returns
    -------
    str
        The verification prompt to send to the agent.
    """
    checks = checklist or ARCHITECT_CHECKLIST

    items = "\n".join(c.to_checklist_item() for c in checks)
    instructions = "\n".join(
        f"  {i+1}. {c.prompt_instruction}" for i, c in enumerate(checks)
    )

    return f"""\
The orchestrator claims the following goal is complete:

# Goal
{goal}

# Orchestrator's summary
{summary}

# Verification Checklist

Go through each check below. For each one, mark it PASS or FAIL with details.

{items}

## Detailed Instructions
{instructions}

## Reporting Format
For each issue found, report:
- **Category**: (SYNTAX/TESTS/WARNINGS/ARCHITECTURE/SECURITY/PERFORMANCE/CORRECTNESS)
- **Severity**: (BLOCKER/MAJOR/MINOR)
- **Location**: file:line (if applicable)
- **Description**: what's wrong and how to fix it

If all checks pass, say 'ALL CHECKS PASS'.
If only cosmetic issues fixed, say 'MINOR ISSUES FIXED'.
"""


def parse_verification_report(text: str) -> VerificationReport:
    """Parse an agent's verification response into structured issues.

    Uses pattern matching to extract categorized issues from free-text
    verification reports.
    """
    report = VerificationReport(raw_text=text)

    # Check for pass signals
    upper = text.upper()
    if "ALL CHECKS PASS" in upper:
        return report
    if "MINOR ISSUES FIXED" in upper:
        return report

    # Parse structured issue reports
    # Pattern: **Category**: SEVERITY - description
    issue_pattern = re.compile(
        r"\*\*(?:Category)?:?\s*"
        r"(SYNTAX|TESTS|WARNINGS|ARCHITECTURE|SECURITY|PERFORMANCE|CORRECTNESS)"
        r"\*\*:?\s*"
        r"(?:\*\*)?(?:Severity)?:?\s*"
        r"(BLOCKER|MAJOR|MINOR|INFO)?"
        r"(?:\*\*)?:?\s*"
        r"(?:.*?)"
        r"(?:Location)?:?\s*"
        r"(?:(`[^`]+`|[\w/.]+:\d+))?\s*"
        r"[-–—]?\s*"
        r"(.*)",
        re.IGNORECASE,
    )

    for match in issue_pattern.finditer(text):
        category_str = match.group(1).upper()
        severity_str = (match.group(2) or "MAJOR").upper()
        location = match.group(3) or ""
        description = match.group(4) or ""

        try:
            category = CheckCategory(category_str.lower())
        except ValueError:
            category = CheckCategory.CORRECTNESS

        try:
            severity = Severity(severity_str.lower())
        except ValueError:
            severity = Severity.MAJOR

        # Parse file:line from location
        file_path = None
        line_number = None
        if location:
            location = location.strip("`")
            parts = location.split(":")
            file_path = parts[0]
            if len(parts) > 1:
                try:
                    line_number = int(parts[1])
                except ValueError:
                    pass

        report.issues.append(
            VerificationIssue(
                category=category,
                severity=severity,
                description=description.strip(),
                file_path=file_path,
                line_number=line_number,
            )
        )

    # Also look for simpler patterns like "FAIL:" or "BLOCKER:"
    simple_pattern = re.compile(
        r"(BLOCKER|MAJOR|MINOR|FAIL):\s*(.*)", re.IGNORECASE
    )
    seen_descriptions = {i.description for i in report.issues}
    for match in simple_pattern.finditer(text):
        severity_str = match.group(1).upper()
        description = match.group(2).strip()
        if description in seen_descriptions:
            continue
        seen_descriptions.add(description)

        severity = {
            "BLOCKER": Severity.BLOCKER,
            "MAJOR": Severity.MAJOR,
            "MINOR": Severity.MINOR,
            "FAIL": Severity.MAJOR,
        }.get(severity_str, Severity.MAJOR)

        report.issues.append(
            VerificationIssue(
                category=CheckCategory.CORRECTNESS,
                severity=severity,
                description=description,
            )
        )

    # Extract passed checks
    pass_pattern = re.compile(r"\[x\]\s*\*\*(\w+)\*\*", re.IGNORECASE)
    for match in pass_pattern.finditer(text):
        report.checks_passed.append(match.group(1))

    return report


@dataclass
class VerificationMetrics:
    """Tracks verification effectiveness across multiple runs."""

    total_verifications: int = 0
    total_issues_found: int = 0
    blockers_caught: int = 0
    security_issues_caught: int = 0
    false_passes: int = 0  # issues found after verification "passed"

    @property
    def detection_rate(self) -> float:
        """Proportion of verifications that found at least one issue."""
        if self.total_verifications == 0:
            return 0.0
        findings = self.total_verifications - self.false_passes
        return findings / self.total_verifications

    @property
    def bug_escape_rate(self) -> float:
        """Proportion of bugs that escaped verification."""
        total_bugs = self.total_issues_found + self.false_passes
        if total_bugs == 0:
            return 0.0
        return self.false_passes / total_bugs

    def record_verification(self, report: VerificationReport) -> None:
        """Record a completed verification."""
        self.total_verifications += 1
        self.total_issues_found += report.total_issues
        self.blockers_caught += report.blocker_count
        self.security_issues_caught += sum(
            1
            for i in report.issues
            if i.category == CheckCategory.SECURITY
        )

    def record_escaped_bug(self) -> None:
        """Record a bug found after verification passed."""
        self.false_passes += 1
