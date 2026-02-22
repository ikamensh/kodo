"""
Human Trust Score: Calculate 0-100% confidence level
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, Any, Optional, List

from ..verification import VerificationEngine
from ..quality import QualityGate
from .feedback import FeedbackCollector, FeedbackSentiment


class TrustLevel(str, Enum):
    """Trust level indicators"""
    VERY_HIGH = "very_high"  # 85-100
    HIGH = "high"  # 70-84
    MEDIUM = "medium"  # 50-69
    LOW = "low"  # 30-49
    VERY_LOW = "very_low"  # 0-29


class TrustColor(str, Enum):
    """Color indicators for trust"""
    GREEN = "green"  # Fully trusted
    YELLOW = "yellow"  # Caution needed
    RED = "red"  # Not trusted


@dataclass
class TrustAssessment:
    """Trust score assessment"""
    timestamp: datetime
    code_id: str
    trust_score: float  # 0-100
    trust_level: TrustLevel
    color_indicator: TrustColor
    
    # Component scores
    verification_trust: float
    quality_trust: float
    feedback_trust: float
    consistency_trust: float
    
    details: Dict[str, Any] = None
    recommendations: List[str] = None
    
    def __post_init__(self):
        if self.details is None:
            self.details = {}
        if self.recommendations is None:
            self.recommendations = []


class TrustScorer:
    """
    Calculate human trust score for autonomous decisions
    
    Trust factors:
    - Verification score (40%) - How well tests passed
    - Quality gate pass (30%) - Passed all quality checks
    - Feedback sentiment (20%) - What users/metrics say
    - Consistency (10%) - How consistent with past decisions
    """

    def __init__(
        self,
        verification_engine: Optional[VerificationEngine] = None,
        quality_gate: Optional[QualityGate] = None,
        feedback_collector: Optional[FeedbackCollector] = None,
    ):
        """Initialize trust scorer"""
        self.verification = verification_engine
        self.quality = quality_gate
        self.feedback = feedback_collector
        self.assessments: List[TrustAssessment] = []

    async def calculate_trust(
        self,
        code_id: str,
        verification_score: Optional[float] = None,
        quality_passed: Optional[bool] = None,
        recent_feedback: Optional[List[Any]] = None,
    ) -> TrustAssessment:
        """
        Calculate trust score for code
        
        Args:
            code_id: Code identifier
            verification_score: Score from verification (0-100)
            quality_passed: Whether quality gate passed
            recent_feedback: Recent feedback records
            
        Returns:
            TrustAssessment with trust score and details
        """
        timestamp = datetime.now()
        
        # Component 1: Verification trust (40%)
        if verification_score is not None:
            verification_trust = verification_score
        else:
            # Try to get from verification engine
            if self.verification and self.verification.verification_history:
                recent = self.verification.verification_history[-1]
                verification_trust = recent.correctness_score
            else:
                verification_trust = 50  # Default if no data
        
        # Component 2: Quality trust (30%)
        if quality_passed is not None:
            quality_trust = 100 if quality_passed else 30
        else:
            # Try to get from quality gate
            if self.quality and self.quality.check_history:
                recent = self.quality.check_history[-1]
                quality_trust = 100 if recent.auto_action == "merge" else 40
            else:
                quality_trust = 50
        
        # Component 3: Feedback trust (20%)
        if recent_feedback:
            positive = sum(1 for f in recent_feedback if getattr(f, 'sentiment', None) == FeedbackSentiment.POSITIVE)
            negative = sum(1 for f in recent_feedback if getattr(f, 'sentiment', None) == FeedbackSentiment.NEGATIVE)
            
            if len(recent_feedback) > 0:
                feedback_trust = (positive / len(recent_feedback)) * 100
                feedback_trust -= negative * 10  # Penalty for negative feedback
                feedback_trust = max(0, min(100, feedback_trust))
            else:
                feedback_trust = 50
        else:
            if self.feedback:
                code_feedback = self.feedback.get_feedback_by_code(code_id)
                if code_feedback:
                    positive = sum(1 for f in code_feedback if f.sentiment == FeedbackSentiment.POSITIVE)
                    negative = sum(1 for f in code_feedback if f.sentiment == FeedbackSentiment.NEGATIVE)
                    feedback_trust = (positive / len(code_feedback)) * 100 if code_feedback else 50
                    feedback_trust -= negative * 10
                    feedback_trust = max(0, min(100, feedback_trust))
                else:
                    feedback_trust = 50
            else:
                feedback_trust = 50
        
        # Component 4: Consistency trust (10%)
        consistency_trust = self._calculate_consistency(code_id)
        
        # Weighted average
        trust_score = (
            verification_trust * 0.40 +
            quality_trust * 0.30 +
            feedback_trust * 0.20 +
            consistency_trust * 0.10
        )
        
        # Determine trust level and color
        trust_level, color = self._get_trust_level(trust_score)
        
        # Generate recommendations
        recommendations = self._get_recommendations(
            trust_score,
            verification_trust,
            quality_trust,
            feedback_trust,
        )
        
        assessment = TrustAssessment(
            timestamp=timestamp,
            code_id=code_id,
            trust_score=trust_score,
            trust_level=trust_level,
            color_indicator=color,
            verification_trust=verification_trust,
            quality_trust=quality_trust,
            feedback_trust=feedback_trust,
            consistency_trust=consistency_trust,
            details={
                "components": {
                    "verification": verification_trust,
                    "quality": quality_trust,
                    "feedback": feedback_trust,
                    "consistency": consistency_trust,
                },
            },
            recommendations=recommendations,
        )
        
        self.assessments.append(assessment)
        return assessment

    def _calculate_consistency(self, code_id: str) -> float:
        """Calculate consistency score based on past assessments"""
        if len(self.assessments) < 3:
            return 75  # Default for new codes
        
        # Look at last 3 assessments for this code
        recent = [a for a in self.assessments[-10:] if a.code_id == code_id]
        
        if len(recent) < 2:
            return 75
        
        # Calculate variance in scores
        scores = [a.trust_score for a in recent]
        avg_score = sum(scores) / len(scores)
        variance = sum((s - avg_score) ** 2 for s in scores) / len(scores)
        std_dev = variance ** 0.5
        
        # Low variance = high consistency
        if std_dev < 5:
            return 95
        elif std_dev < 15:
            return 75
        else:
            return 50

    @staticmethod
    def _get_trust_level(score: float) -> tuple:
        """Get trust level and color for score"""
        if score >= 85:
            return TrustLevel.VERY_HIGH, TrustColor.GREEN
        elif score >= 70:
            return TrustLevel.HIGH, TrustColor.GREEN
        elif score >= 50:
            return TrustLevel.MEDIUM, TrustColor.YELLOW
        elif score >= 30:
            return TrustLevel.LOW, TrustColor.YELLOW
        else:
            return TrustLevel.VERY_LOW, TrustColor.RED

    @staticmethod
    def _get_recommendations(
        trust_score: float,
        verification: float,
        quality: float,
        feedback: float,
    ) -> List[str]:
        """Generate recommendations based on trust factors"""
        recommendations = []
        
        if verification < 80:
            recommendations.append("Improve test coverage and verification score")
        
        if quality < 80:
            recommendations.append("Address quality gate failures")
        
        if feedback < 70:
            recommendations.append("Monitor production feedback and fix reported issues")
        
        if trust_score < 50:
            recommendations.append("Require human review before deployment")
        
        if trust_score >= 85:
            recommendations.append("Code can be auto-deployed with confidence")
        
        return recommendations

    def get_assessment_history(self) -> List[TrustAssessment]:
        """Get all assessments"""
        return self.assessments.copy()

    def get_statistics(self) -> Dict[str, Any]:
        """Get trust statistics"""
        if not self.assessments:
            return {
                "total_assessments": 0,
                "average_trust": 0,
                "trust_distribution": {},
            }
        
        trust_levels = {}
        for level in TrustLevel:
            count = sum(1 for a in self.assessments if a.trust_level == level)
            if count > 0:
                trust_levels[level.value] = count
        
        avg_trust = sum(a.trust_score for a in self.assessments) / len(self.assessments)
        
        return {
            "total_assessments": len(self.assessments),
            "average_trust": avg_trust,
            "trust_distribution": trust_levels,
        }
