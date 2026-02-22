"""
KODO 2.0 Orchestrator: Unified pipeline orchestration

Coordinates all 10 pillars:
1. Verification Engine
2. Quality Gate
3. Compliance Validator
4. Production Readiness
5. Failure Self-Healing
6. Audit Trail
7. Cost Optimization
8. Feedback Loop
9. Trust Scoring
10. Autonomous Improvement
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Any, Optional, List
from pathlib import Path

from kodo.verification import VerificationEngine
from kodo.quality import QualityGate
from kodo.production import ComplianceValidator, ProductionReadinessScorer
from kodo.reliability import FailureHealer
from kodo.transparency import AuditTrail, DecisionLogger, DecisionType, DecisionOutcome
from kodo.cost import CostOptimizer, TokenTracker, ModelType
from kodo.learning import FeedbackCollector, TrustScorer, AutomatedImprovement


@dataclass
class OrchestrationResult:
    """Result of full orchestration pipeline"""
    code_id: str
    timestamp: datetime
    
    # Pillar results
    verified: bool
    verification_score: float
    
    quality_passed: bool
    quality_score: float
    
    specification_compliance: float
    production_ready: bool
    production_score: float
    
    healed: bool
    errors_fixed: int
    
    trust_score: float
    trust_level: str
    
    # Final decision
    auto_action: str  # "deploy", "review", "reject"
    confidence: float
    reason: str


class Kodo2Orchestrator:
    """
    Master orchestrator for autonomous development
    
    Pipeline:
    1. Code input
    2. Self-heal any errors
    3. Verify code quality
    4. Run quality gate
    5. Check spec compliance
    6. Score production readiness
    7. Calculate trust score
    8. Make autonomous decision
    9. Log decision with audit trail
    10. Collect feedback and improve
    """

    def __init__(self):
        """Initialize orchestrator with all 10 pillars"""
        # Pillar 1: Verification Engine
        self.verifier = VerificationEngine(min_pass_score=90.0)
        
        # Pillar 2: Quality Gate
        self.quality = QualityGate(auto_merge_threshold=1.0)
        
        # Pillar 3 & 4: Compliance & Readiness
        self.compliance = ComplianceValidator(min_coverage=1.0)
        self.readiness = ProductionReadinessScorer()
        
        # Pillar 5: Self-Healing
        self.healer = FailureHealer()
        
        # Pillar 6: Audit Trail
        self.audit = AuditTrail()
        self.decision_logger = DecisionLogger(self.audit)
        
        # Pillar 7: Cost Optimization
        self.cost_tracker = TokenTracker()
        self.cost_optimizer = CostOptimizer(self.cost_tracker)
        
        # Pillar 8: Feedback Loop
        self.feedback = FeedbackCollector()
        
        # Pillar 9: Trust Scoring
        self.trust_scorer = TrustScorer(
            verification_engine=self.verifier,
            quality_gate=self.quality,
            feedback_collector=self.feedback,
        )
        
        # Pillar 10: Autonomous Improvement
        self.improvement = AutomatedImprovement()

    async def process_code(
        self,
        code: str,
        code_id: str = "unknown",
        test_code: Optional[str] = None,
        specification: Optional[str] = None,
    ) -> OrchestrationResult:
        """
        Process code through complete autonomous pipeline
        
        Args:
            code: Source code to process
            code_id: Unique identifier
            test_code: Optional test code
            specification: Optional specification
            
        Returns:
            OrchestrationResult with final decision
        """
        timestamp = datetime.now()
        healed_code = code
        errors_fixed = 0
        
        try:
            # Step 1: Self-heal any errors (Pillar 5)
            healing_result = await self.healer.heal(code, code_id)
            if healing_result.success or healing_result.errors_fixed > 0:
                healed_code = healing_result.healed_code
                errors_fixed = healing_result.errors_fixed
            
            # Step 2: Verify code (Pillar 1)
            verification = await self.verifier.verify(
                code=healed_code,
                code_id=code_id,
                test_code=test_code,
            )
            
            # Step 3: Quality gate (Pillar 2)
            quality = await self.quality.evaluate(
                code=healed_code,
                code_id=code_id,
                test_code=test_code,
            )
            
            # Step 4: Spec compliance (Pillar 3)
            compliance = await self.compliance.validate(
                code=healed_code,
                specification=specification or "",
                test_code=test_code,
            )
            
            # Step 5: Production readiness (Pillar 4)
            readiness = await self.readiness.score(
                code=healed_code,
                code_id=code_id,
                verification_score=verification.correctness_score,
                quality_gate_pass=(quality.auto_action == "merge"),
                compliance_coverage=compliance.coverage_percentage / 100,
            )
            
            # Step 6: Trust scoring (Pillar 9)
            trust = await self.trust_scorer.calculate_trust(
                code_id=code_id,
                verification_score=verification.correctness_score,
                quality_passed=(quality.auto_action == "merge"),
            )
            
            # Step 7: Make autonomous decision
            decision = self._make_decision(
                verification=verification,
                quality=quality,
                readiness=readiness,
                trust=trust,
            )
            
            # Step 8: Log decision (Pillar 6)
            decision_id = self._log_decision(
                code_id=code_id,
                decision=decision,
                verification=verification,
                quality=quality,
                readiness=readiness,
            )
            
            # Step 9: Track cost (Pillar 7)
            self.cost_tracker.record_usage(
                task_type="code_processing",
                model=ModelType.CLAUDE_HAIKU,
                input_tokens=len(code) // 4,  # Approximate
                output_tokens=len(healed_code) // 4,
                component="orchestrator",
            )
            
            return OrchestrationResult(
                code_id=code_id,
                timestamp=timestamp,
                verified=verification.status.value == "passed",
                verification_score=verification.correctness_score,
                quality_passed=(quality.auto_action == "merge"),
                quality_score=quality.overall_pass_rate * 100,
                specification_compliance=compliance.coverage_percentage,
                production_ready=(readiness.readiness_level.value == "production_ready"),
                production_score=readiness.overall_score,
                healed=(errors_fixed > 0),
                errors_fixed=errors_fixed,
                trust_score=trust.trust_score,
                trust_level=trust.trust_level.value,
                auto_action=decision["action"],
                confidence=decision["confidence"],
                reason=decision["reason"],
            )
            
        except Exception as e:
            # Handle orchestration errors
            return OrchestrationResult(
                code_id=code_id,
                timestamp=timestamp,
                verified=False,
                verification_score=0,
                quality_passed=False,
                quality_score=0,
                specification_compliance=0,
                production_ready=False,
                production_score=0,
                healed=False,
                errors_fixed=0,
                trust_score=0,
                trust_level="very_low",
                auto_action="reject",
                confidence=0.05,
                reason=f"Orchestration error: {str(e)}",
            )

    def _make_decision(
        self,
        verification,
        quality,
        readiness,
        trust,
    ) -> Dict[str, Any]:
        """Make autonomous decision based on all factors"""
        
        # Scoring logic
        if (
            verification.correctness_score >= 90
            and quality.auto_action == "merge"
            and trust.trust_score >= 85
            and readiness.overall_score >= 85
        ):
            return {
                "action": "deploy",
                "confidence": min(
                    verification.confidence_level / 100,
                    trust.trust_score / 100,
                ),
                "reason": "All systems green - production ready with high confidence",
            }
        elif (
            verification.correctness_score >= 75
            and quality.auto_action != "reject"
            and trust.trust_score >= 70
        ):
            return {
                "action": "review",
                "confidence": 0.7,
                "reason": "Code ready for staging - recommend human review",
            }
        else:
            return {
                "action": "reject",
                "confidence": 0.95,
                "reason": "Code does not meet deployment criteria",
            }

    def _log_decision(
        self,
        code_id: str,
        decision: Dict[str, Any],
        verification,
        quality,
        readiness,
    ) -> str:
        """Log decision with full audit trail"""
        decision_id = self.decision_logger.log_validation(
            context=f"Autonomous decision for {code_id}",
            checks_performed=[
                f"Verification: {verification.correctness_score:.0f}%",
                f"Quality: {quality.overall_pass_rate*100:.0f}%",
                f"Readiness: {readiness.overall_score:.0f}%",
            ],
            passed=(decision["action"] != "reject"),
            confidence=decision["confidence"],
        )
        
        return decision_id

    def get_full_report(self, code_id: str) -> Dict[str, Any]:
        """Get comprehensive report for a code"""
        report = {
            "code_id": code_id,
            "timestamp": datetime.now().isoformat(),
            
            "verification": {
                "history": len(self.verifier.get_history()),
                "stats": self.verifier.get_statistics(),
            },
            
            "quality": {
                "history": len(self.quality.get_history()),
                "stats": self.quality.get_statistics(),
            },
            
            "audit": {
                "decisions": len(self.audit.records),
                "stats": self.audit.get_statistics(),
            },
            
            "cost": {
                "total_cost": self.cost_tracker.get_total_cost(),
                "stats": self.cost_tracker.get_statistics(),
            },
            
            "feedback": {
                "records": len(self.feedback.records),
                "patterns": self.feedback.analyze_patterns(),
            },
            
            "trust": {
                "assessments": len(self.trust_scorer.get_assessment_history()),
                "stats": self.trust_scorer.get_statistics(),
            },
        }
        
        return report
