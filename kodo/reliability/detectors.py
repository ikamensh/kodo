"""
Error Detectors: Identify failure patterns for self-healing
"""

import ast
import re
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple


class ErrorType(str, Enum):
    """Types of errors that can be detected"""
    SYNTAX_ERROR = "syntax_error"
    TYPE_ERROR = "type_error"
    IMPORT_ERROR = "import_error"
    NAME_ERROR = "name_error"
    ATTRIBUTE_ERROR = "attribute_error"
    INDENTATION_ERROR = "indentation_error"
    SECURITY_ISSUE = "security_issue"
    LINT_VIOLATION = "lint_violation"
    TEST_FAILURE = "test_failure"
    PERFORMANCE_ISSUE = "performance_issue"


@dataclass
class ErrorDetection:
    """Detected error with location and severity"""
    error_type: ErrorType
    message: str
    line: int
    column: int
    severity: str  # "critical", "high", "medium", "low"
    suggestion: Optional[str] = None


class ErrorDetector:
    """Detects various error types in code"""

    def detect_all(self, code: str, error_output: Optional[str] = None) -> List[ErrorDetection]:
        """
        Detect all types of errors in code
        
        Args:
            code: Source code
            error_output: Optional error/test output
            
        Returns:
            List of detected errors
        """
        errors = []

        # Syntax errors
        syntax_errors = self._detect_syntax_errors(code)
        errors.extend(syntax_errors)

        # Type hints and type errors
        type_errors = self._detect_type_errors(code)
        errors.extend(type_errors)

        # Import errors
        import_errors = self._detect_import_errors(code)
        errors.extend(import_errors)

        # Name errors (undefined variables)
        name_errors = self._detect_name_errors(code)
        errors.extend(name_errors)

        # Security issues
        security_errors = self._detect_security_issues(code)
        errors.extend(security_errors)

        # Lint violations
        lint_errors = self._detect_lint_violations(code)
        errors.extend(lint_errors)

        # Test failures (from error output)
        if error_output:
            test_errors = self._detect_test_failures(error_output, code)
            errors.extend(test_errors)

        return sorted(errors, key=lambda e: (e.line, e.column))

    def _detect_syntax_errors(self, code: str) -> List[ErrorDetection]:
        """Detect syntax errors"""
        errors = []
        try:
            ast.parse(code)
        except SyntaxError as e:
            errors.append(
                ErrorDetection(
                    error_type=ErrorType.SYNTAX_ERROR,
                    message=f"Syntax error: {e.msg}",
                    line=e.lineno or 1,
                    column=e.offset or 0,
                    severity="critical",
                    suggestion="Fix syntax error in code",
                )
            )
        except IndentationError as e:
            errors.append(
                ErrorDetection(
                    error_type=ErrorType.INDENTATION_ERROR,
                    message=f"Indentation error: {e.msg}",
                    line=e.lineno or 1,
                    column=e.offset or 0,
                    severity="critical",
                    suggestion="Fix indentation (use consistent spacing)",
                )
            )
        return errors

    def _detect_type_errors(self, code: str) -> List[ErrorDetection]:
        """Detect type-related issues"""
        errors = []
        
        try:
            tree = ast.parse(code)
            
            # Check for missing type hints in function definitions
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    # Check if return type hint exists
                    if node.returns is None and node.name not in ['__init__', '__main__']:
                        errors.append(
                            ErrorDetection(
                                error_type=ErrorType.TYPE_ERROR,
                                message=f"Missing return type hint in {node.name}",
                                line=node.lineno,
                                column=0,
                                severity="low",
                                suggestion=f"Add return type hint: def {node.name}(...) -> Type:",
                            )
                        )
        except:
            pass
        
        return errors

    def _detect_import_errors(self, code: str) -> List[ErrorDetection]:
        """Detect missing imports"""
        errors = []
        
        try:
            tree = ast.parse(code)
            
            # Get imported modules
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imported.add(alias.name.split('.')[0])
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        imported.add(node.module.split('.')[0])
            
            # Check for usage of common modules
            code_lower = code.lower()
            common_modules = {
                'pandas': ('pd.', 'dataframe'),
                'numpy': ('np.', 'ndarray'),
                'requests': ('requests.', 'get(', 'post('),
                'json': ('json.', 'loads', 'dumps'),
                're': ('re.', 'match(', 'search('),
            }
            
            for module, patterns in common_modules.items():
                if module not in imported:
                    for pattern in patterns:
                        if pattern in code_lower:
                            errors.append(
                                ErrorDetection(
                                    error_type=ErrorType.IMPORT_ERROR,
                                    message=f"Module '{module}' used but not imported",
                                    line=1,
                                    column=0,
                                    severity="high",
                                    suggestion=f"Add: import {module}",
                                )
                            )
                            break
        except:
            pass
        
        return errors

    def _detect_name_errors(self, code: str) -> List[ErrorDetection]:
        """Detect undefined variables"""
        errors = []
        
        try:
            tree = ast.parse(code)
            
            # Collect defined names
            defined = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                    defined.add(node.name)
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            defined.add(target.id)
            
            # Check for undefined references
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                    if node.id not in defined and not self._is_builtin(node.id):
                        errors.append(
                            ErrorDetection(
                                error_type=ErrorType.NAME_ERROR,
                                message=f"Undefined name: {node.id}",
                                line=node.lineno,
                                column=node.col_offset,
                                severity="high",
                                suggestion=f"Define {node.id} before use",
                            )
                        )
        except:
            pass
        
        return errors

    def _detect_security_issues(self, code: str) -> List[ErrorDetection]:
        """Detect security vulnerabilities"""
        errors = []
        
        dangerous_patterns = [
            (r'eval\s*\(', "eval() is dangerous", ErrorType.SECURITY_ISSUE, "critical"),
            (r'exec\s*\(', "exec() is dangerous", ErrorType.SECURITY_ISSUE, "critical"),
            (r'shell\s*=\s*True', "shell=True in subprocess is unsafe", ErrorType.SECURITY_ISSUE, "high"),
            (r'pickle\.loads', "pickle.loads() without validation", ErrorType.SECURITY_ISSUE, "high"),
        ]
        
        for pattern, msg, error_type, severity in dangerous_patterns:
            for match in re.finditer(pattern, code):
                # Find line number
                line = code[:match.start()].count('\n') + 1
                errors.append(
                    ErrorDetection(
                        error_type=error_type,
                        message=msg,
                        line=line,
                        column=match.start(),
                        severity=severity,
                        suggestion=f"Replace with safer alternative",
                    )
                )
        
        return errors

    def _detect_lint_violations(self, code: str) -> List[ErrorDetection]:
        """Detect lint violations"""
        errors = []
        
        for i, line in enumerate(code.split('\n'), 1):
            # Long lines
            if len(line) > 120:
                errors.append(
                    ErrorDetection(
                        error_type=ErrorType.LINT_VIOLATION,
                        message=f"Line too long ({len(line)} > 120)",
                        line=i,
                        column=120,
                        severity="low",
                        suggestion="Break long line into multiple lines",
                    )
                )
            
            # Trailing whitespace
            if line != line.rstrip():
                errors.append(
                    ErrorDetection(
                        error_type=ErrorType.LINT_VIOLATION,
                        message="Trailing whitespace",
                        line=i,
                        column=len(line.rstrip()),
                        severity="low",
                        suggestion="Remove trailing whitespace",
                    )
                )
        
        return errors

    def _detect_test_failures(self, error_output: str, code: str) -> List[ErrorDetection]:
        """Detect test failures from error output"""
        errors = []
        
        # Parse pytest output
        failure_pattern = r'(\w+\.py):(\d+).*FAILED'
        for match in re.finditer(failure_pattern, error_output):
            line_num = int(match.group(2))
            errors.append(
                ErrorDetection(
                    error_type=ErrorType.TEST_FAILURE,
                    message=f"Test failure at line {line_num}",
                    line=line_num,
                    column=0,
                    severity="high",
                    suggestion="Review test failure and fix code",
                )
            )
        
        return errors

    @staticmethod
    def _is_builtin(name: str) -> bool:
        """Check if name is a Python builtin"""
        builtins = {
            'print', 'len', 'range', 'str', 'int', 'float', 'list', 'dict', 'set',
            'True', 'False', 'None', 'Exception', 'type', 'object', 'super',
            'all', 'any', 'enumerate', 'map', 'filter', 'zip', 'sorted',
            'min', 'max', 'sum', 'abs', 'round', 'pow', 'divmod',
            '__name__', '__main__', '__file__', '__doc__',
        }
        return name in builtins
