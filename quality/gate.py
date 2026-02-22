"""
Autonomous Quality Gate: 7-point checklist, auto-merge/reject
"""

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import List, Dict, Any, Optional

from .checks import QualityChecker, CheckPoint, CheckPointResult


class QualityGateStatus(str, Enum):
    """Status of quality gate"""
    PASSED = "passed"
    FAILED = "failed"
    REVIEW_REQUIRED = "review_required"


@dataclass
class QualityCheckResult:
    """Result of quality check"""
    timestamp: datetime
    code_id: str
    check_results: List[CheckPointResult]
    passed: bool  # All 7 points passed
    failed_points: List[str] = field(default_factory=list)
    passed_points: List[str] = field(default_factory=list)
    overall_pass_rate: float = 0.0  # 0-1
    decision: str = ""
    auto_action: str = ""  # "merge", "reject", or "review"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "timestamp": self.timestamp.isoformat(),
            "code_id": self.code_id,
            "passed": self.passed,
            "check_results": [
                {
                    "checkpoint": r.checkpoint.value,
                    "passed": r.passed,
                    "message": r.message,
                    "severity": r.severity.value,
                }
                for r in self.check_results
            ],
            "overall_pass_rate": self.overall_pass_rate,
            "auto_action": self.auto_action,
            "decision": self.decision,
            "failed_points": self.failed_points,
            "passed_points": self.passed_points,
        }


class QualityGate:
    """
    7-Point Quality Gate Checklist:
    1. Code compiles/parses without syntax errors
    2. All existing tests pass (regression prevention)
    3. New code has test coverage (>80%)
    4. No security vulnerabilities detected
    5. Meets style/lint standards
    6. Documentation complete (docstrings, comments)
    7. No breaking changes to public API
    """

    def __init__(
        self,
        auto_merge_threshold: float = 1.0,  # All 7/7 must pass
        checker: Optional[QualityChecker] = None,
    ):
        """
        Initialize quality gate
        
        Args:
            auto_merge_threshold: Fraction of checks that must pass (0-1)
                0.0 = all can fail, 1.0 = all must pass
            checker: Custom quality checker
        """
        self.auto_merge_threshold = auto_merge_threshold
        self.checker = checker or QualityChecker()
        self.check_history: List[QualityCheckResult] = []

    async def evaluate(
        self,
        code: str,
        code_id: str,
        test_code: Optional[str] = None,
        previous_code: Optional[str] = None,
    ) -> QualityCheckResult:
        """
        Evaluate code against quality gate
        
        Args:
            code: Code to evaluate
            code_id: Unique identifier
            test_code: Optional test code
            previous_code: Optional previous version for API compatibility check
            
        Returns:
            QualityCheckResult with decision
        """
        timestamp = datetime.now()
        
        # Run all 7 checks
        check_results = await self.checker.run_all_checks(
            code=code,
            test_code=test_code,
            previous_code=previous_code,
        )
        
        # Calculate results
        passed_count = sum(1 for r in check_results if r.passed)
        total_count = len(check_results)
        overall_pass_rate = passed_count / total_count if total_count > 0 else 0
        
        passed = passed_count == total_count  # All must pass
        failed_points = [r.checkpoint.value for r in check_results if not r.passed]
        passed_points = [r.checkpoint.value for r in check_results if r.passed]
        
        # Make decision
        if passed:
            decision = "All 7 checkpoints passed ✓"
            auto_action = "merge"
        elif overall_pass_rate >= self.auto_merge_threshold:
            decision = f"Passed {passed_count}/{total_count} checks"
            auto_action = "merge" if passed_count >= total_count - 1 else "review"
        else:
            decision = f"Failed {len(failed_points)} checkpoints: {', '.join(failed_points)}"
            auto_action = "reject"
        
        result = QualityCheckResult(
            timestamp=timestamp,
            code_id=code_id,
            check_results=check_results,
            passed=passed,
            failed_points=failed_points,
            passed_points=passed_points,
            overall_pass_rate=overall_pass_rate,
            decision=decision,
            auto_action=auto_action,
        )
        
        self.check_history.append(result)
        return result

    def get_history(self) -> List[QualityCheckResult]:
        """Get quality gate history"""
        return self.check_history.copy()

    def export_results(self, filepath: Path) -> None:
        """Export quality check results"""
        results = [r.to_dict() for r in self.check_history]
        with open(filepath, "w") as f:
            json.dump(results, f, indent=2)

    def get_statistics(self) -> Dict[str, Any]:
        """Get statistics from history"""
        if not self.check_history:
            return {
                "total_checks": 0,
                "passed": 0,
                "failed": 0,
                "pass_rate": 0,
            }
        
        passed = sum(1 for r in self.check_history if r.auto_action == "merge")
        failed = sum(1 for r in self.check_history if r.auto_action == "reject")
        
        return {
            "total_checks": len(self.check_history),
            "passed": passed,
            "failed": failed,
            "pass_rate": passed / len(self.check_history),
        }
