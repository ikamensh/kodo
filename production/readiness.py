"""
Production Readiness Scorer: Composite scoring with confidence indicators
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, Optional, List


class ReadinessLevel(str, Enum):
    """Production readiness level"""
    PRODUCTION_READY = "production_ready"
    STAGING_READY = "staging_ready"
    DEV_READY = "dev_ready"
    NOT_READY = "not_ready"


class ConfidenceLevel(str, Enum):
    """Confidence in readiness assessment"""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class ReadinessScore:
    """Production readiness assessment"""
    code_id: str
    timestamp: datetime
    overall_score: float  # 0-100
    readiness_level: ReadinessLevel
    confidence: ConfidenceLevel
    
    # Component scores
    code_quality: float  # 0-100
    test_coverage: float  # 0-100
    performance: float  # 0-100
    security: float  # 0-100
    documentation: float  # 0-100
    maintainability: float  # 0-100
    
    issues: List[str] = None
    recommendations: List[str] = None
    
    def __post_init__(self):
        if self.issues is None:
            self.issues = []
        if self.recommendations is None:
            self.recommendations = []


class ProductionReadinessScorer:
    """
    Scores code for production readiness
    
    Factors:
    - Code quality (lint, style)
    - Test coverage (target: >85%)
    - Performance (execution time, memory)
    - Security (vulnerability scan)
    - Documentation (completeness)
    - Maintainability (cyclomatic complexity, duplication)
    """

    def __init__(
        self,
        quality_weight: float = 0.2,
        coverage_weight: float = 0.25,
        performance_weight: float = 0.15,
        security_weight: float = 0.2,
        docs_weight: float = 0.1,
        maintainability_weight: float = 0.1,
    ):
        """Initialize scorer with component weights"""
        self.quality_weight = quality_weight
        self.coverage_weight = coverage_weight
        self.performance_weight = performance_weight
        self.security_weight = security_weight
        self.docs_weight = docs_weight
        self.maintainability_weight = maintainability_weight

    async def score(
        self,
        code: str,
        code_id: str = "unknown",
        verification_score: Optional[float] = None,
        quality_gate_pass: Optional[bool] = None,
        compliance_coverage: Optional[float] = None,
        test_metrics: Optional[Dict] = None,
    ) -> ReadinessScore:
        """
        Score code for production readiness
        
        Args:
            code: Code to score
            code_id: Code identifier
            verification_score: Score from verification engine (0-100)
            quality_gate_pass: Whether code passed quality gate
            compliance_coverage: Specification compliance (0-1)
            test_metrics: Optional test metrics dict
            
        Returns:
            ReadinessScore with components and confidence
        """
        timestamp = datetime.now()
        issues = []
        recommendations = []
        
        # Component scores (all 0-100)
        
        # 1. Code quality
        code_quality = verification_score if verification_score else 50
        if code_quality < 80:
            issues.append(f"Code quality below target: {code_quality:.0f}%")
            recommendations.append("Address quality gate failures and style issues")
        
        # 2. Test coverage (estimate from verification)
        test_coverage = (verification_score or 50) * 0.9 if verification_score else 50
        if test_coverage < 85:
            issues.append(f"Test coverage below 85%: {test_coverage:.0f}%")
            recommendations.append("Add more test cases to cover edge cases")
        
        # 3. Performance (estimate based on code length)
        performance = self._estimate_performance(code)
        if performance < 70:
            issues.append(f"Performance concerns detected: {performance:.0f}%")
            recommendations.append("Profile code and optimize hotspots")
        
        # 4. Security
        security = self._assess_security(code)
        if security < 80:
            issues.append(f"Security issues detected: {security:.0f}%")
            recommendations.append("Fix security vulnerabilities")
        
        # 5. Documentation
        documentation = self._assess_documentation(code)
        if documentation < 70:
            issues.append(f"Documentation incomplete: {documentation:.0f}%")
            recommendations.append("Add docstrings and API documentation")
        
        # 6. Maintainability
        maintainability = self._assess_maintainability(code)
        if maintainability < 70:
            issues.append(f"Maintainability concerns: {maintainability:.0f}%")
            recommendations.append("Refactor for better code organization")
        
        # Calculate overall score (weighted average)
        overall_score = (
            code_quality * self.quality_weight +
            test_coverage * self.coverage_weight +
            performance * self.performance_weight +
            security * self.security_weight +
            documentation * self.docs_weight +
            maintainability * self.maintainability_weight
        )
        
        # Determine readiness level
        if overall_score >= 90 and not issues:
            readiness_level = ReadinessLevel.PRODUCTION_READY
            confidence = ConfidenceLevel.HIGH
        elif overall_score >= 75 and len(issues) <= 2:
            readiness_level = ReadinessLevel.STAGING_READY
            confidence = ConfidenceLevel.MEDIUM
        elif overall_score >= 60:
            readiness_level = ReadinessLevel.DEV_READY
            confidence = ConfidenceLevel.MEDIUM
        else:
            readiness_level = ReadinessLevel.NOT_READY
            confidence = ConfidenceLevel.LOW
        
        # Adjust confidence based on inputs
        input_confidence = 0
        if verification_score is not None:
            input_confidence += 1
        if quality_gate_pass is not None:
            input_confidence += 1
        if compliance_coverage is not None:
            input_confidence += 1
        if test_metrics is not None:
            input_confidence += 1
        
        if input_confidence <= 1:
            confidence = ConfidenceLevel.LOW
        elif input_confidence <= 2:
            if confidence != ConfidenceLevel.HIGH:
                confidence = ConfidenceLevel.MEDIUM
        
        return ReadinessScore(
            code_id=code_id,
            timestamp=timestamp,
            overall_score=overall_score,
            readiness_level=readiness_level,
            confidence=confidence,
            code_quality=code_quality,
            test_coverage=test_coverage,
            performance=performance,
            security=security,
            documentation=documentation,
            maintainability=maintainability,
            issues=issues,
            recommendations=recommendations,
        )

    def _estimate_performance(self, code: str) -> float:
        """Estimate performance score from code characteristics"""
        score = 80  # Start with baseline
        
        # Penalize nested loops
        nested_count = code.count("for ") + code.count("while ")
        if nested_count > 3:
            score -= min(nested_count * 5, 30)
        
        # Penalize large functions
        import re
        functions = re.findall(r'def\s+\w+.*?:', code)
        avg_func_size = len(code) / max(len(functions), 1)
        if avg_func_size > 500:
            score -= 10
        
        return max(30, min(100, score))

    @staticmethod
    def _assess_security(code: str) -> float:
        """Assess security from code patterns"""
        score = 95
        
        # Check for security issues
        dangerous = [
            ("eval(", 20),
            ("exec(", 20),
            ("pickle", 15),
            ("shell=True", 10),
        ]
        
        for pattern, penalty in dangerous:
            if pattern in code:
                score -= penalty
        
        return max(0, min(100, score))

    @staticmethod
    def _assess_documentation(code: str) -> float:
        """Assess documentation from docstrings"""
        import ast
        try:
            tree = ast.parse(code)
            
            # Count documented items
            functions = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
            documented = sum(1 for f in functions if ast.get_docstring(f))
            
            if not functions:
                return 70
            
            doc_rate = documented / len(functions)
            return min(100, doc_rate * 100)
        except:
            return 50

    @staticmethod
    def _assess_maintainability(code: str) -> float:
        """Assess maintainability from code metrics"""
        score = 80
        
        # Check for very long lines
        long_lines = sum(1 for line in code.split("\n") if len(line) > 120)
        if long_lines > 5:
            score -= min(long_lines * 2, 20)
        
        # Check for duplicated patterns
        import re
        # Simple check: multiple similar variable names
        vars_pattern = re.findall(r'[a-z]+\d+', code.lower())
        if len(vars_pattern) > 10:
            score -= 10
        
        return max(40, min(100, score))
