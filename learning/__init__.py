"""
KODO 2.0 Pillars 8-10: Learning & Improvement

Pillar 8: Production Feedback Loop - Collect metrics, analyze patterns
Pillar 9: Human Trust Score - Calculate 0-100% confidence
Pillar 10: Autonomous Improvement - Post-project analysis, pattern extraction
"""

from .feedback import FeedbackCollector, FeedbackRecord, FeedbackType, FeedbackSentiment
from .trust import TrustScorer, TrustAssessment
from .improvement import AutomatedImprovement, ImprovementSuggestion

# Cycle tracking for autonomous improvement
from dataclasses import dataclass, field
from typing import Dict, Any, List
from datetime import datetime

@dataclass
class CycleRecord:
    """Records metrics from a single improvement cycle"""
    cycle_num: int
    timestamp: datetime
    goal: str
    improvements_made: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    duration_seconds: float = 0.0
    test_pass_rate: float = 0.0
    commits_made: int = 0
    success: bool = True
    error_message: str = ""

class CycleLearner:
    """Learns from improvement cycles to optimize future cycles"""
    def __init__(self):
        self.cycles: List[CycleRecord] = []
        self.patterns: Dict[str, Any] = {}
    
    def record_cycle(self, cycle: CycleRecord) -> None:
        """Record a completed cycle"""
        self.cycles.append(cycle)
    
    def analyze_patterns(self) -> Dict[str, Any]:
        """Analyze patterns across cycles"""
        if not self.cycles:
            return {}
        
        patterns = {
            "total_cycles": len(self.cycles),
            "success_rate": sum(1 for c in self.cycles if c.success) / len(self.cycles),
            "avg_duration": sum(c.duration_seconds for c in self.cycles) / len(self.cycles),
            "avg_test_pass_rate": sum(c.test_pass_rate for c in self.cycles) / len(self.cycles),
            "total_commits": sum(c.commits_made for c in self.cycles),
        }
        self.patterns = patterns
        return patterns
    
    def get_improvements_for_next_cycle(self) -> List[str]:
        """Suggest improvements based on learned patterns"""
        if not self.cycles:
            return []
        
        # Analyze which improvements were most successful
        improvements = {}
        for cycle in self.cycles:
            for imp in cycle.improvements_made:
                improvements[imp] = improvements.get(imp, 0) + 1
        
        # Return top improvements
        return sorted(improvements.items(), key=lambda x: x[1], reverse=True)[:5]

__all__ = [
    "FeedbackCollector",
    "FeedbackRecord",
    "FeedbackType",
    "FeedbackSentiment",
    "TrustScorer",
    "TrustAssessment",
    "AutomatedImprovement",
    "ImprovementSuggestion",
    "CycleRecord",
    "CycleLearner",
]
