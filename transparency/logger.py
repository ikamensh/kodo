"""
Decision Logger: Simple logging interface for decisions
"""

import logging
from typing import Optional, Dict, Any, List

from .audit import AuditTrail, DecisionType, DecisionOutcome, Alternative


class DecisionLogger:
    """Logger interface for recording autonomous decisions"""

    def __init__(self, audit_trail: Optional[AuditTrail] = None):
        """Initialize logger"""
        self.audit = audit_trail or AuditTrail()
        self.logger = logging.getLogger("kodo.decisions")

    def log_code_generation(
        self,
        context: str,
        reasoning: str,
        alternatives: Optional[List[Alternative]] = None,
        confidence: float = 0.5,
    ) -> str:
        """Log code generation decision"""
        decision_id = self.audit.record_decision(
            decision_type=DecisionType.CODE_GENERATION,
            context=context,
            reasoning=reasoning,
            alternatives=alternatives,
            confidence=confidence,
        )
        
        self.logger.info(
            f"Code generation [{decision_id}]: {context[:50]}... "
            f"(confidence: {confidence*100:.0f}%)"
        )
        
        return decision_id

    def log_validation(
        self,
        context: str,
        checks_performed: List[str],
        passed: bool,
        confidence: float = 0.9,
    ) -> str:
        """Log validation decision"""
        reasoning = f"Validation: {', '.join(checks_performed)}"
        outcome = "All checks passed" if passed else "Some checks failed"
        
        decision_id = self.audit.record_decision(
            decision_type=DecisionType.VALIDATION,
            context=context,
            reasoning=f"{reasoning}. {outcome}",
            confidence=confidence,
        )
        
        self.logger.info(
            f"Validation [{decision_id}]: {context[:50]}... "
            f"(passed: {passed}, confidence: {confidence*100:.0f}%)"
        )
        
        return decision_id

    def log_quality_check(
        self,
        code_id: str,
        passed: bool,
        score: float,
        issues: List[str] = None,
    ) -> str:
        """Log quality check decision"""
        decision_id = self.audit.record_decision(
            decision_type=DecisionType.QUALITY_CHECK,
            context=f"Quality check for {code_id}",
            reasoning=f"Score: {score:.1f}%, Passed: {passed}",
            confidence=score / 100.0,
            selected="pass" if passed else "fail",
        )
        
        self.logger.info(
            f"Quality check [{decision_id}]: {code_id} - "
            f"Score {score:.1f}%, Passed: {passed}"
        )
        
        return decision_id

    def log_auto_accept(
        self,
        code_id: str,
        reason: str,
        metrics: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Log auto-accept decision"""
        decision_id = self.audit.record_decision(
            decision_type=DecisionType.AUTO_ACCEPT,
            context=f"Auto-accept decision for {code_id}",
            reasoning=reason,
            confidence=0.95,
        )
        
        self.audit.mark_outcome(decision_id, DecisionOutcome.ACCEPTED, metrics)
        
        self.logger.info(f"Auto-accept [{decision_id}]: {code_id} - {reason}")
        
        return decision_id

    def log_auto_reject(
        self,
        code_id: str,
        reason: str,
        metrics: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Log auto-reject decision"""
        decision_id = self.audit.record_decision(
            decision_type=DecisionType.AUTO_REJECT,
            context=f"Auto-reject decision for {code_id}",
            reasoning=reason,
            confidence=0.95,
        )
        
        self.audit.mark_outcome(decision_id, DecisionOutcome.REJECTED, metrics)
        
        self.logger.warning(f"Auto-reject [{decision_id}]: {code_id} - {reason}")
        
        return decision_id

    def log_auto_heal(
        self,
        code_id: str,
        errors_fixed: int,
        fixes: List[str],
        success: bool,
    ) -> str:
        """Log auto-heal decision"""
        decision_id = self.audit.record_decision(
            decision_type=DecisionType.AUTO_HEAL,
            context=f"Auto-heal for {code_id}",
            reasoning=f"Fixed {errors_fixed} errors: {', '.join(fixes[:3])}",
            confidence=0.8 if success else 0.3,
        )
        
        self.logger.info(
            f"Auto-heal [{decision_id}]: {code_id} - "
            f"Fixed {errors_fixed} errors, Success: {success}"
        )
        
        return decision_id

    def log_escalation(
        self,
        code_id: str,
        reason: str,
        severity: str = "medium",
    ) -> str:
        """Log escalation (requires human review)"""
        decision_id = self.audit.record_decision(
            decision_type=DecisionType.VALIDATION,
            context=f"Escalation for {code_id}",
            reasoning=reason,
            confidence=0.5,
        )
        
        self.audit.mark_outcome(decision_id, DecisionOutcome.ESCALATED)
        
        self.logger.warning(
            f"Escalation [{decision_id}]: {code_id} - "
            f"Severity: {severity}, Reason: {reason}"
        )
        
        return decision_id

    def get_audit_trail(self) -> AuditTrail:
        """Get the underlying audit trail"""
        return self.audit
