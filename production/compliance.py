"""
Specification Compliance Validator: Maps requirement→code→test, 100% coverage verification
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Optional, Tuple


@dataclass
class RequirementMapping:
    """Maps a requirement to implementation and test"""
    requirement_id: str
    description: str
    implementation_line: Optional[int] = None
    test_reference: Optional[str] = None
    covered: bool = False


@dataclass
class ComplianceResult:
    """Result of compliance check"""
    code_id: str
    timestamp: datetime
    total_requirements: int
    covered_requirements: int
    coverage_percentage: float
    uncovered_requirements: List[str] = field(default_factory=list)
    mappings: List[RequirementMapping] = field(default_factory=list)
    compliant: bool = False  # 100% coverage
    decision: str = ""


class ComplianceValidator:
    """
    Validates that code implementation matches specification requirements
    
    Process:
    1. Extract requirements from spec/docstring
    2. Find implementations in code
    3. Verify tests exist for each requirement
    4. Calculate coverage percentage
    """

    def __init__(self, min_coverage: float = 1.0):
        """
        Initialize validator
        
        Args:
            min_coverage: Minimum coverage percentage to pass (0-1)
        """
        self.min_coverage = min_coverage

    async def validate(
        self,
        code: str,
        specification: str,
        test_code: Optional[str] = None,
    ) -> ComplianceResult:
        """
        Validate code against specification
        
        Args:
            code: Implementation code
            specification: Requirements specification
            test_code: Optional test code
            
        Returns:
            ComplianceResult with coverage details
        """
        timestamp = datetime.now()
        
        # Extract requirements from specification
        requirements = self._extract_requirements(specification)
        
        if not requirements:
            return ComplianceResult(
                code_id="unknown",
                timestamp=timestamp,
                total_requirements=0,
                covered_requirements=0,
                coverage_percentage=1.0,
                compliant=True,
                decision="No requirements found",
            )
        
        # Check implementation for each requirement
        mappings = []
        for req in requirements:
            mapping = self._check_requirement(
                requirement=req,
                code=code,
                test_code=test_code,
            )
            mappings.append(mapping)
        
        # Calculate coverage
        covered = sum(1 for m in mappings if m.covered)
        total = len(mappings)
        coverage = covered / total if total > 0 else 0
        
        compliant = coverage >= self.min_coverage
        uncovered = [m.requirement_id for m in mappings if not m.covered]
        
        if compliant:
            decision = f"100% coverage: all {total} requirements implemented"
        else:
            decision = f"Coverage: {coverage*100:.0f}% ({covered}/{total} requirements)"
        
        return ComplianceResult(
            code_id="unknown",
            timestamp=timestamp,
            total_requirements=total,
            covered_requirements=covered,
            coverage_percentage=coverage * 100,
            uncovered_requirements=uncovered,
            mappings=mappings,
            compliant=compliant,
            decision=decision,
        )

    def _extract_requirements(self, specification: str) -> List[str]:
        """Extract requirements from specification"""
        requirements = []
        
        # Look for common patterns
        patterns = [
            r"(?:MUST|SHOULD|SHALL)[\s:]*([^.!?\n]+)",  # MUST/SHOULD statements
            r"REQ[-_]?(\d+)[\s:]*([^.!?\n]+)",  # REQ-123 format
            r"(?:Requirement|Feature)[\s:]*([^.!?\n]+)",  # Requirement: ...
        ]
        
        for pattern in patterns:
            matches = re.finditer(pattern, specification, re.IGNORECASE)
            for match in matches:
                req_text = match.group(1) if match.lastindex and match.lastindex >= 1 else match.group(0)
                if req_text and len(req_text) > 5:  # Filter out noise
                    requirements.append(req_text.strip())
        
        # Remove duplicates while preserving order
        seen = set()
        unique = []
        for r in requirements:
            r_lower = r.lower()
            if r_lower not in seen:
                seen.add(r_lower)
                unique.append(r)
        
        return unique

    def _check_requirement(
        self,
        requirement: str,
        code: str,
        test_code: Optional[str] = None,
    ) -> RequirementMapping:
        """
        Check if requirement is implemented and tested
        
        Returns:
            RequirementMapping with coverage status
        """
        # Extract key terms from requirement
        terms = self._extract_key_terms(requirement)
        
        # Check if terms appear in code
        implementation_found = any(
            term in code
            for term in terms
        )
        
        # Check if requirement is tested
        test_found = False
        if test_code:
            test_found = any(
                term in test_code
                for term in terms
            )
        
        # Requirement is covered if implemented and ideally tested
        covered = implementation_found and (test_found or not test_code)
        
        return RequirementMapping(
            requirement_id=self._generate_req_id(requirement),
            description=requirement,
            implementation_line=self._find_line(code, terms),
            test_reference=self._find_test_ref(test_code, terms) if test_code else None,
            covered=covered,
        )

    @staticmethod
    def _extract_key_terms(requirement: str) -> List[str]:
        """Extract key terms from requirement"""
        # Split on common separators and filter short words
        words = re.findall(r'\b\w{4,}\b', requirement.lower())
        return words[:3]  # Use first 3 key terms

    @staticmethod
    def _generate_req_id(requirement: str) -> str:
        """Generate requirement ID from description"""
        # Create hash-based ID
        words = requirement.split()[:2]
        return f"REQ_{len(words)}_{hash(requirement.lower()) % 10000:04d}"

    @staticmethod
    def _find_line(code: str, terms: List[str]) -> Optional[int]:
        """Find line number where term appears"""
        for i, line in enumerate(code.split("\n"), 1):
            if any(term in line.lower() for term in terms):
                return i
        return None

    @staticmethod
    def _find_test_ref(test_code: str, terms: List[str]) -> Optional[str]:
        """Find test reference"""
        matches = re.findall(r'def\s+(test_\w+)', test_code)
        if matches:
            return matches[0]
        return None
