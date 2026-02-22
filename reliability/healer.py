"""
Failure Self-Healer: Auto-detect and fix errors
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple

from .detectors import ErrorDetector, ErrorDetection, ErrorType


@dataclass
class HealingResult:
    """Result of healing attempt"""
    code_id: str
    timestamp: datetime
    original_code: str
    healed_code: str
    errors_detected: int
    errors_fixed: int
    remaining_errors: List[ErrorDetection] = field(default_factory=list)
    applied_fixes: List[str] = field(default_factory=list)
    success: bool = False  # All critical errors fixed
    confidence: float = 0.0  # 0-1, how confident in the fixes


class FailureHealer:
    """
    Auto-detect and fix:
    - Type errors
    - Lint violations
    - Security issues
    - Test failures
    """

    def __init__(self, detector: Optional[ErrorDetector] = None):
        """Initialize healer"""
        self.detector = detector or ErrorDetector()
        self.healing_history: List[HealingResult] = []

    async def heal(
        self,
        code: str,
        code_id: str = "unknown",
        error_output: Optional[str] = None,
    ) -> HealingResult:
        """
        Detect and fix errors in code
        
        Args:
            code: Source code
            code_id: Code identifier
            error_output: Optional error output for context
            
        Returns:
            HealingResult with fixed code and metrics
        """
        timestamp = datetime.now()
        
        # Detect errors
        errors = self.detector.detect_all(code, error_output)
        initial_error_count = len(errors)
        
        # Try to fix errors
        healed_code = code
        fixes = []
        
        for error in errors:
            fixed_code, fix_desc = self._fix_error(healed_code, error)
            if fixed_code != healed_code:
                healed_code = fixed_code
                fixes.append(fix_desc)
        
        # Re-detect errors in healed code
        remaining_errors = self.detector.detect_all(healed_code)
        remaining_critical = [e for e in remaining_errors if e.severity == "critical"]
        
        success = len(remaining_critical) == 0
        fixed_count = initial_error_count - len(remaining_errors)
        
        # Calculate confidence
        if success:
            confidence = 0.95
        elif fixed_count > 0:
            confidence = 0.5 + (fixed_count / max(initial_error_count, 1)) * 0.4
        else:
            confidence = 0.1
        
        result = HealingResult(
            code_id=code_id,
            timestamp=timestamp,
            original_code=code,
            healed_code=healed_code,
            errors_detected=initial_error_count,
            errors_fixed=fixed_count,
            remaining_errors=remaining_errors,
            applied_fixes=fixes,
            success=success,
            confidence=confidence,
        )
        
        self.healing_history.append(result)
        return result

    def _fix_error(self, code: str, error: ErrorDetection) -> Tuple[str, str]:
        """
        Fix a single error
        
        Returns:
            Tuple of (fixed_code, description)
        """
        lines = code.split('\n')
        line_idx = error.line - 1
        
        if line_idx < 0 or line_idx >= len(lines):
            return code, ""
        
        line = lines[line_idx]
        original_line = line
        
        # Syntax/Indentation fixes
        if error.error_type == ErrorType.INDENTATION_ERROR:
            # Try to auto-indent
            line = "    " + line.lstrip()
        
        # Trailing whitespace fix
        elif error.error_type == ErrorType.LINT_VIOLATION:
            if "Trailing whitespace" in error.message:
                line = line.rstrip()
            elif "Line too long" in error.message:
                # Can't auto-fix line length, just note it
                return code, ""
        
        # Type errors (add type hints)
        elif error.error_type == ErrorType.TYPE_ERROR:
            if "return type hint" in error.message:
                # Try to add return type
                if "def " in line:
                    line = self._add_return_type_hint(line)
        
        # Import errors
        elif error.error_type == ErrorType.IMPORT_ERROR:
            if "not imported" in error.message:
                # Add import at the beginning
                module = self._extract_module_name(error.message)
                if module:
                    return self._add_import(code, module), f"Added import for {module}"
        
        # Name errors
        elif error.error_type == ErrorType.NAME_ERROR:
            # Can't auto-fix without more context
            return code, ""
        
        # Security issues
        elif error.error_type == ErrorType.SECURITY_ISSUE:
            if "eval(" in error.message:
                line = line.replace("eval(", "ast.literal_eval(")
            elif "exec(" in error.message:
                line = line.replace("exec(", "# UNSAFE: exec(")
        
        # If we made a change, apply it
        if line != original_line:
            lines[line_idx] = line
            fixed_code = '\n'.join(lines)
            
            fix_desc = f"Fixed {error.error_type.value} on line {error.line}"
            return fixed_code, fix_desc
        
        return code, ""

    @staticmethod
    def _add_return_type_hint(line: str) -> str:
        """Add return type hint to function definition"""
        # Simple case: def func_name(args):
        if "def " in line and ":" in line and "->" not in line:
            # Find the colon and insert before it
            colon_idx = line.rfind(":")
            if colon_idx > 0:
                # Insert generic return type
                return line[:colon_idx] + " -> Any" + line[colon_idx:]
        return line

    @staticmethod
    def _extract_module_name(message: str) -> Optional[str]:
        """Extract module name from error message"""
        # Message like: "Module 'pandas' used but not imported"
        match = re.search(r"'(\w+)'", message)
        if match:
            return match.group(1)
        return None

    @staticmethod
    def _add_import(code: str, module: str) -> str:
        """Add import statement at beginning of code"""
        lines = code.split('\n')
        
        # Find where to insert (after any existing imports)
        insert_idx = 0
        for i, line in enumerate(lines):
            if line.startswith("import ") or line.startswith("from "):
                insert_idx = i + 1
        
        # Create import line
        import_line = f"import {module}"
        lines.insert(insert_idx, import_line)
        
        return '\n'.join(lines)

    def get_history(self) -> List[HealingResult]:
        """Get healing history"""
        return self.healing_history.copy()

    def get_statistics(self):
        """Get statistics"""
        if not self.healing_history:
            return {
                "total_attempts": 0,
                "successful": 0,
                "total_errors_detected": 0,
                "total_errors_fixed": 0,
            }
        
        successful = sum(1 for r in self.healing_history if r.success)
        total_detected = sum(r.errors_detected for r in self.healing_history)
        total_fixed = sum(r.errors_fixed for r in self.healing_history)
        
        return {
            "total_attempts": len(self.healing_history),
            "successful": successful,
            "success_rate": successful / len(self.healing_history),
            "total_errors_detected": total_detected,
            "total_errors_fixed": total_fixed,
            "average_confidence": sum(r.confidence for r in self.healing_history) / len(self.healing_history),
        }
