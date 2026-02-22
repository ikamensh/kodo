"""
Quality Checker: 7-point checklist implementation
"""

import ast
import re
import subprocess
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import List, Optional


class CheckPoint(str, Enum):
    """The 7 quality checkpoints"""
    SYNTAX = "syntax_valid"
    TEST_REGRESSION = "test_regression"
    TEST_COVERAGE = "test_coverage"
    SECURITY = "security_check"
    LINT = "lint_standards"
    DOCUMENTATION = "documentation"
    API_COMPAT = "api_compatibility"


class Severity(str, Enum):
    """Severity of check failure"""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class CheckPointResult:
    """Result of a single checkpoint"""
    checkpoint: CheckPoint
    passed: bool
    message: str
    severity: Severity = Severity.HIGH


class QualityChecker:
    """Implements the 7-point quality checklist"""

    async def run_all_checks(
        self,
        code: str,
        test_code: Optional[str] = None,
        previous_code: Optional[str] = None,
    ) -> List[CheckPointResult]:
        """Run all 7 quality checks"""
        results = []

        # 1. Syntax validation
        syntax_result = self._check_syntax(code)
        results.append(syntax_result)

        # 2. Test regression (if test_code provided)
        if test_code:
            regression_result = await self._check_test_regression(code, test_code)
            results.append(regression_result)
        else:
            results.append(
                CheckPointResult(
                    checkpoint=CheckPoint.TEST_REGRESSION,
                    passed=False,
                    message="No test code provided",
                    severity=Severity.HIGH,
                )
            )

        # 3. Test coverage
        coverage_result = await self._check_test_coverage(code, test_code)
        results.append(coverage_result)

        # 4. Security check
        security_result = self._check_security(code)
        results.append(security_result)

        # 5. Lint standards
        lint_result = await self._check_lint(code)
        results.append(lint_result)

        # 6. Documentation
        doc_result = self._check_documentation(code)
        results.append(doc_result)

        # 7. API compatibility
        if previous_code:
            api_result = self._check_api_compatibility(previous_code, code)
            results.append(api_result)
        else:
            results.append(
                CheckPointResult(
                    checkpoint=CheckPoint.API_COMPAT,
                    passed=True,
                    message="No previous code provided (skipped)",
                    severity=Severity.LOW,
                )
            )

        return results

    def _check_syntax(self, code: str) -> CheckPointResult:
        """Check 1: Code syntax validation"""
        try:
            ast.parse(code)
            return CheckPointResult(
                checkpoint=CheckPoint.SYNTAX,
                passed=True,
                message="Code syntax valid ✓",
            )
        except SyntaxError as e:
            return CheckPointResult(
                checkpoint=CheckPoint.SYNTAX,
                passed=False,
                message=f"Syntax error: {str(e)}",
                severity=Severity.CRITICAL,
            )
        except Exception as e:
            return CheckPointResult(
                checkpoint=CheckPoint.SYNTAX,
                passed=False,
                message=f"Parse error: {str(e)}",
                severity=Severity.CRITICAL,
            )

    async def _check_test_regression(
        self,
        code: str,
        test_code: str,
    ) -> CheckPointResult:
        """Check 2: All tests pass (regression prevention)"""
        # Create temporary test file
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".py",
            delete=False,
        ) as f:
            f.write(f"{code}\n\n{test_code}")
            f.flush()
            temp_path = f.name

        try:
            result = subprocess.run(
                ["python", "-m", "pytest", temp_path, "-v"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            
            if result.returncode == 0:
                return CheckPointResult(
                    checkpoint=CheckPoint.TEST_REGRESSION,
                    passed=True,
                    message="All tests pass ✓",
                )
            else:
                # Count failures
                failures = result.stdout.count("FAILED")
                return CheckPointResult(
                    checkpoint=CheckPoint.TEST_REGRESSION,
                    passed=False,
                    message=f"Test failures: {failures}",
                    severity=Severity.CRITICAL,
                )
        except subprocess.TimeoutExpired:
            return CheckPointResult(
                checkpoint=CheckPoint.TEST_REGRESSION,
                passed=False,
                message="Tests timed out",
                severity=Severity.HIGH,
            )
        except Exception as e:
            return CheckPointResult(
                checkpoint=CheckPoint.TEST_REGRESSION,
                passed=False,
                message=f"Test execution error: {str(e)}",
                severity=Severity.HIGH,
            )
        finally:
            Path(temp_path).unlink(missing_ok=True)

    async def _check_test_coverage(
        self,
        code: str,
        test_code: Optional[str] = None,
    ) -> CheckPointResult:
        """Check 3: Test coverage >80%"""
        if not test_code:
            return CheckPointResult(
                checkpoint=CheckPoint.TEST_COVERAGE,
                passed=False,
                message="No tests provided for coverage check",
                severity=Severity.HIGH,
            )

        # Simple heuristic: count assertions/test functions
        try:
            tree = ast.parse(code)
            
            # Count functions/classes
            total_functions = sum(
                1 for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.ClassDef))
            )
            
            test_tree = ast.parse(test_code)
            test_functions = sum(
                1 for node in ast.walk(test_tree)
                if isinstance(node, ast.FunctionDef) and "test" in node.name.lower()
            )
            
            if total_functions == 0:
                coverage_rate = 1.0
            else:
                coverage_rate = min(test_functions / total_functions, 1.0)
            
            if coverage_rate >= 0.8:
                return CheckPointResult(
                    checkpoint=CheckPoint.TEST_COVERAGE,
                    passed=True,
                    message=f"Test coverage sufficient: {coverage_rate*100:.0f}% ✓",
                )
            else:
                return CheckPointResult(
                    checkpoint=CheckPoint.TEST_COVERAGE,
                    passed=False,
                    message=f"Coverage insufficient: {coverage_rate*100:.0f}% < 80%",
                    severity=Severity.HIGH,
                )
        except Exception as e:
            return CheckPointResult(
                checkpoint=CheckPoint.TEST_COVERAGE,
                passed=False,
                message=f"Coverage analysis error: {str(e)}",
                severity=Severity.MEDIUM,
            )

    def _check_security(self, code: str) -> CheckPointResult:
        """Check 4: Security vulnerabilities"""
        issues = []

        # Check for common security issues
        dangerous_patterns = [
            (r"eval\s*\(", "eval() usage"),
            (r"exec\s*\(", "exec() usage"),
            (r"__import__", "__import__() usage"),
            (r"pickle\.loads", "pickle.loads() without validation"),
            (r"subprocess\.call\s*\(\s*['\"].*['\"]", "subprocess.call with shell"),
            (r"os\.system", "os.system() usage"),
        ]

        for pattern, description in dangerous_patterns:
            if re.search(pattern, code):
                issues.append(description)

        if issues:
            return CheckPointResult(
                checkpoint=CheckPoint.SECURITY,
                passed=False,
                message=f"Security issues found: {', '.join(issues)}",
                severity=Severity.CRITICAL,
            )
        else:
            return CheckPointResult(
                checkpoint=CheckPoint.SECURITY,
                passed=True,
                message="No obvious security issues ✓",
            )

    async def _check_lint(self, code: str) -> CheckPointResult:
        """Check 5: Lint/style standards"""
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".py",
            delete=False,
        ) as f:
            f.write(code)
            f.flush()
            temp_path = f.name

        try:
            # Try flake8
            result = subprocess.run(
                ["flake8", temp_path, "--max-line-length=100"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            
            if result.returncode == 0:
                return CheckPointResult(
                    checkpoint=CheckPoint.LINT,
                    passed=True,
                    message="Lint standards met ✓",
                )
            else:
                issues = len(result.stdout.splitlines())
                return CheckPointResult(
                    checkpoint=CheckPoint.LINT,
                    passed=False,
                    message=f"Lint issues: {issues} violations",
                    severity=Severity.MEDIUM,
                )
        except FileNotFoundError:
            # flake8 not installed, do basic checks
            return self._basic_lint_check(code)
        except Exception:
            return self._basic_lint_check(code)
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def _basic_lint_check(self, code: str) -> CheckPointResult:
        """Basic lint check if flake8 not available"""
        issues = []
        
        # Check line length
        for i, line in enumerate(code.split("\n"), 1):
            if len(line) > 120:
                issues.append(f"Line {i} too long ({len(line)} > 120)")
        
        if issues:
            return CheckPointResult(
                checkpoint=CheckPoint.LINT,
                passed=False,
                message=f"Style issues: {len(issues)} violations",
                severity=Severity.LOW,
            )
        else:
            return CheckPointResult(
                checkpoint=CheckPoint.LINT,
                passed=True,
                message="Basic lint check passed ✓",
            )

    def _check_documentation(self, code: str) -> CheckPointResult:
        """Check 6: Documentation completeness"""
        try:
            tree = ast.parse(code)
            functions = [
                node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef)
            ]
            
            if not functions:
                return CheckPointResult(
                    checkpoint=CheckPoint.DOCUMENTATION,
                    passed=True,
                    message="No functions to document",
                )
            
            # Count functions with docstrings
            documented = sum(
                1 for f in functions
                if ast.get_docstring(f)
            )
            
            doc_rate = documented / len(functions)
            
            if doc_rate >= 0.8:
                return CheckPointResult(
                    checkpoint=CheckPoint.DOCUMENTATION,
                    passed=True,
                    message=f"Documentation: {doc_rate*100:.0f}% of functions documented ✓",
                )
            else:
                return CheckPointResult(
                    checkpoint=CheckPoint.DOCUMENTATION,
                    passed=False,
                    message=f"Insufficient docs: only {doc_rate*100:.0f}% documented",
                    severity=Severity.MEDIUM,
                )
        except Exception as e:
            return CheckPointResult(
                checkpoint=CheckPoint.DOCUMENTATION,
                passed=False,
                message=f"Documentation check error: {str(e)}",
                severity=Severity.LOW,
            )

    def _check_api_compatibility(
        self,
        previous_code: str,
        new_code: str,
    ) -> CheckPointResult:
        """Check 7: API compatibility"""
        try:
            prev_tree = ast.parse(previous_code)
            new_tree = ast.parse(new_code)
            
            # Extract public functions/classes
            prev_public = self._extract_public_api(prev_tree)
            new_public = self._extract_public_api(new_tree)
            
            # Check for removed public APIs
            removed = prev_public - new_public
            
            if removed:
                return CheckPointResult(
                    checkpoint=CheckPoint.API_COMPAT,
                    passed=False,
                    message=f"Breaking changes detected: {', '.join(removed)}",
                    severity=Severity.HIGH,
                )
            else:
                return CheckPointResult(
                    checkpoint=CheckPoint.API_COMPAT,
                    passed=True,
                    message="API compatible with previous version ✓",
                )
        except Exception as e:
            return CheckPointResult(
                checkpoint=CheckPoint.API_COMPAT,
                passed=False,
                message=f"API compatibility check error: {str(e)}",
                severity=Severity.MEDIUM,
            )

    @staticmethod
    def _extract_public_api(tree: ast.AST) -> set:
        """Extract public functions and classes from AST"""
        public = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                if not node.name.startswith("_"):
                    public.add(node.name)
        return public
