"""
Production Feedback Loop: Collect metrics, analyze patterns
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Any, Optional


class FeedbackType(str, Enum):
    """Type of feedback"""
    USER_REVIEW = "user_review"
    PERFORMANCE_METRIC = "performance_metric"
    ERROR_REPORT = "error_report"
    USAGE_METRIC = "usage_metric"
    QUALITY_SCORE = "quality_score"


class FeedbackSentiment(str, Enum):
    """Sentiment of feedback"""
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"


@dataclass
class FeedbackRecord:
    """Record of feedback from production"""
    feedback_id: str
    timestamp: datetime
    feedback_type: FeedbackType
    code_id: str
    sentiment: FeedbackSentiment
    message: str
    metrics: Dict[str, Any] = field(default_factory=dict)
    severity: str = "normal"  # critical, high, normal, low


class FeedbackCollector:
    """
    Collects feedback from production usage
    
    Feedback sources:
    - User reviews/ratings
    - Performance metrics (latency, memory)
    - Error reports
    - Usage patterns
    - Quality scores
    """

    def __init__(self):
        """Initialize collector"""
        self.records: List[FeedbackRecord] = []
        self._feedback_counter = 0

    def record_feedback(
        self,
        feedback_type: FeedbackType,
        code_id: str,
        message: str,
        sentiment: Optional[FeedbackSentiment] = None,
        metrics: Optional[Dict[str, Any]] = None,
        severity: str = "normal",
    ) -> str:
        """Record feedback"""
        self._feedback_counter += 1
        feedback_id = f"FB_{self._feedback_counter:06d}"
        
        if sentiment is None:
            sentiment = self._infer_sentiment(message)
        
        record = FeedbackRecord(
            feedback_id=feedback_id,
            timestamp=datetime.now(),
            feedback_type=feedback_type,
            code_id=code_id,
            sentiment=sentiment,
            message=message,
            metrics=metrics or {},
            severity=severity,
        )
        
        self.records.append(record)
        return feedback_id

    def record_performance(
        self,
        code_id: str,
        latency_ms: float,
        memory_mb: float,
        throughput: float = 0,
    ) -> str:
        """Record performance metrics"""
        metrics = {
            "latency_ms": latency_ms,
            "memory_mb": memory_mb,
            "throughput": throughput,
        }
        
        # Determine sentiment based on performance
        if latency_ms > 5000 or memory_mb > 500:
            sentiment = FeedbackSentiment.NEGATIVE
            severity = "high"
        elif latency_ms > 1000:
            sentiment = FeedbackSentiment.NEUTRAL
            severity = "normal"
        else:
            sentiment = FeedbackSentiment.POSITIVE
            severity = "normal"
        
        message = f"Performance: {latency_ms:.0f}ms latency, {memory_mb:.0f}MB memory"
        
        return self.record_feedback(
            feedback_type=FeedbackType.PERFORMANCE_METRIC,
            code_id=code_id,
            message=message,
            sentiment=sentiment,
            metrics=metrics,
            severity=severity,
        )

    def record_error(
        self,
        code_id: str,
        error_type: str,
        error_message: str,
    ) -> str:
        """Record error"""
        return self.record_feedback(
            feedback_type=FeedbackType.ERROR_REPORT,
            code_id=code_id,
            message=f"Error: {error_type} - {error_message}",
            sentiment=FeedbackSentiment.NEGATIVE,
            severity="high",
        )

    def record_quality_score(
        self,
        code_id: str,
        score: float,  # 0-100
    ) -> str:
        """Record quality score"""
        sentiment = (
            FeedbackSentiment.POSITIVE if score >= 80
            else FeedbackSentiment.NEUTRAL if score >= 60
            else FeedbackSentiment.NEGATIVE
        )
        
        return self.record_feedback(
            feedback_type=FeedbackType.QUALITY_SCORE,
            code_id=code_id,
            message=f"Quality score: {score:.0f}%",
            sentiment=sentiment,
            metrics={"quality_score": score},
        )

    def get_feedback_by_code(self, code_id: str) -> List[FeedbackRecord]:
        """Get all feedback for a code"""
        return [r for r in self.records if r.code_id == code_id]

    def get_feedback_by_type(self, feedback_type: FeedbackType) -> List[FeedbackRecord]:
        """Get all feedback of a type"""
        return [r for r in self.records if r.feedback_type == feedback_type]

    def get_feedback_by_sentiment(self, sentiment: FeedbackSentiment) -> List[FeedbackRecord]:
        """Get feedback by sentiment"""
        return [r for r in self.records if r.sentiment == sentiment]

    def analyze_patterns(self) -> Dict[str, Any]:
        """Analyze feedback patterns"""
        if not self.records:
            return {
                "total_feedback": 0,
                "sentiment_distribution": {},
                "common_issues": [],
                "performance_summary": {},
            }
        
        # Sentiment distribution
        sentiments = {}
        for sentiment in FeedbackSentiment:
            count = len(self.get_feedback_by_sentiment(sentiment))
            if count > 0:
                sentiments[sentiment.value] = count
        
        # Common issues
        negative_feedback = self.get_feedback_by_sentiment(FeedbackSentiment.NEGATIVE)
        issues = {}
        for feedback in negative_feedback:
            # Extract issue types from message
            if "Error" in feedback.message:
                issues["errors"] = issues.get("errors", 0) + 1
            if "latency" in feedback.message.lower():
                issues["performance"] = issues.get("performance", 0) + 1
            if "quality" in feedback.message.lower():
                issues["quality"] = issues.get("quality", 0) + 1
        
        # Performance summary
        perf_feedback = self.get_feedback_by_type(FeedbackType.PERFORMANCE_METRIC)
        if perf_feedback:
            latencies = [r.metrics.get("latency_ms", 0) for r in perf_feedback if "latency_ms" in r.metrics]
            memories = [r.metrics.get("memory_mb", 0) for r in perf_feedback if "memory_mb" in r.metrics]
            
            perf_summary = {}
            if latencies:
                perf_summary["avg_latency_ms"] = sum(latencies) / len(latencies)
                perf_summary["max_latency_ms"] = max(latencies)
            if memories:
                perf_summary["avg_memory_mb"] = sum(memories) / len(memories)
                perf_summary["max_memory_mb"] = max(memories)
        else:
            perf_summary = {}
        
        return {
            "total_feedback": len(self.records),
            "sentiment_distribution": sentiments,
            "common_issues": sorted(issues.items(), key=lambda x: x[1], reverse=True),
            "performance_summary": perf_summary,
        }

    def export(self, filepath: Path) -> None:
        """Export feedback to JSON"""
        data = [
            {
                "feedback_id": r.feedback_id,
                "timestamp": r.timestamp.isoformat(),
                "type": r.feedback_type.value,
                "code_id": r.code_id,
                "sentiment": r.sentiment.value,
                "message": r.message,
                "severity": r.severity,
                "metrics": r.metrics,
            }
            for r in self.records
        ]
        
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)

    @staticmethod
    def _infer_sentiment(message: str) -> FeedbackSentiment:
        """Infer sentiment from message"""
        message_lower = message.lower()
        
        positive_words = {"good", "great", "excellent", "perfect", "works", "success"}
        negative_words = {"bad", "fail", "error", "issue", "problem", "slow", "crash"}
        
        pos_count = sum(1 for word in positive_words if word in message_lower)
        neg_count = sum(1 for word in negative_words if word in message_lower)
        
        if neg_count > pos_count:
            return FeedbackSentiment.NEGATIVE
        elif pos_count > neg_count:
            return FeedbackSentiment.POSITIVE
        else:
            return FeedbackSentiment.NEUTRAL
