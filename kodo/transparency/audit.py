"""
Audit Trail: Log every decision with reasoning and trade-offs
"""

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import List, Dict, Any, Optional


class DecisionType(str, Enum):
    """Type of decision"""
    CODE_GENERATION = "code_generation"
    TEST_GENERATION = "test_generation"
    REFACTORING = "refactoring"
    OPTIMIZATION = "optimization"
    BUG_FIX = "bug_fix"
    FEATURE_ADD = "feature_add"
    VALIDATION = "validation"
    QUALITY_CHECK = "quality_check"
    AUTO_ACCEPT = "auto_accept"
    AUTO_REJECT = "auto_reject"
    AUTO_HEAL = "auto_heal"


class DecisionOutcome(str, Enum):
    """Outcome of a decision"""
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    PENDING = "pending"
    ESCALATED = "escalated"


@dataclass
class Alternative:
    """Alternative option considered"""
    name: str
    pros: List[str]
    cons: List[str]
    score: float  # 0-100


@dataclass
class DecisionRecord:
    """Record of a single decision"""
    decision_id: str
    timestamp: datetime
    decision_type: DecisionType
    context: str  # What was the task/issue
    reasoning: str  # Why this decision was made
    alternatives: List[Alternative] = field(default_factory=list)
    selected_alternative: Optional[str] = None
    outcome: DecisionOutcome = DecisionOutcome.PENDING
    confidence: float = 0.5  # 0-1
    metrics: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "decision_id": self.decision_id,
            "timestamp": self.timestamp.isoformat(),
            "decision_type": self.decision_type.value,
            "context": self.context,
            "reasoning": self.reasoning,
            "alternatives": [
                {
                    "name": a.name,
                    "pros": a.pros,
                    "cons": a.cons,
                    "score": a.score,
                }
                for a in self.alternatives
            ],
            "selected_alternative": self.selected_alternative,
            "outcome": self.outcome.value,
            "confidence": self.confidence,
            "metrics": self.metrics,
        }


class AuditTrail:
    """
    Audit trail for all autonomous decisions
    
    Records:
    - What decision was made
    - Why (reasoning)
    - Alternatives considered
    - Confidence level
    - Outcome
    """

    def __init__(self):
        """Initialize audit trail"""
        self.records: List[DecisionRecord] = []
        self._decision_counter = 0

    def record_decision(
        self,
        decision_type: DecisionType,
        context: str,
        reasoning: str,
        alternatives: Optional[List[Alternative]] = None,
        selected: Optional[str] = None,
        confidence: float = 0.5,
    ) -> str:
        """
        Record a decision
        
        Args:
            decision_type: Type of decision
            context: Context/task description
            reasoning: Why this decision was made
            alternatives: Alternative options considered
            selected: Which alternative was selected
            confidence: Confidence in decision (0-1)
            
        Returns:
            Decision ID
        """
        self._decision_counter += 1
        decision_id = f"DEC_{self._decision_counter:06d}"
        
        record = DecisionRecord(
            decision_id=decision_id,
            timestamp=datetime.now(),
            decision_type=decision_type,
            context=context,
            reasoning=reasoning,
            alternatives=alternatives or [],
            selected_alternative=selected,
            confidence=confidence,
        )
        
        self.records.append(record)
        return decision_id

    def mark_outcome(
        self,
        decision_id: str,
        outcome: DecisionOutcome,
        metrics: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Mark outcome of a decision"""
        for record in self.records:
            if record.decision_id == decision_id:
                record.outcome = outcome
                if metrics:
                    record.metrics.update(metrics)
                return True
        return False

    def get_decision(self, decision_id: str) -> Optional[DecisionRecord]:
        """Get a specific decision"""
        for record in self.records:
            if record.decision_id == decision_id:
                return record
        return None

    def get_by_type(self, decision_type: DecisionType) -> List[DecisionRecord]:
        """Get all decisions of a type"""
        return [r for r in self.records if r.decision_type == decision_type]

    def get_by_outcome(self, outcome: DecisionOutcome) -> List[DecisionRecord]:
        """Get all decisions with specific outcome"""
        return [r for r in self.records if r.outcome == outcome]

    def export(self, filepath: Path) -> None:
        """Export audit trail to JSON"""
        data = [r.to_dict() for r in self.records]
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)

    def get_statistics(self) -> Dict[str, Any]:
        """Get audit trail statistics"""
        if not self.records:
            return {
                "total_decisions": 0,
                "by_type": {},
                "by_outcome": {},
                "average_confidence": 0,
            }
        
        by_type = {}
        for dec_type in DecisionType:
            count = len(self.get_by_type(dec_type))
            if count > 0:
                by_type[dec_type.value] = count
        
        by_outcome = {}
        for outcome in DecisionOutcome:
            count = len(self.get_by_outcome(outcome))
            if count > 0:
                by_outcome[outcome.value] = count
        
        avg_confidence = sum(r.confidence for r in self.records) / len(self.records)
        
        return {
            "total_decisions": len(self.records),
            "by_type": by_type,
            "by_outcome": by_outcome,
            "average_confidence": avg_confidence,
        }

    def get_decision_timeline(self) -> List[Dict[str, Any]]:
        """Get decisions in chronological order"""
        sorted_records = sorted(self.records, key=lambda r: r.timestamp)
        return [
            {
                "id": r.decision_id,
                "time": r.timestamp.isoformat(),
                "type": r.decision_type.value,
                "outcome": r.outcome.value,
                "confidence": r.confidence,
            }
            for r in sorted_records
        ]
