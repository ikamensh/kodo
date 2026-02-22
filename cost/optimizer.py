"""
Cost Optimizer: Suggest models, optimize spending
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .tracker import ModelType, MODEL_PRICING, TokenTracker


@dataclass
class CostMetrics:
    """Cost optimization metrics"""
    current_cost: float
    optimized_cost: float
    potential_savings: float
    savings_percentage: float
    recommended_models: List[str]
    efficiency_score: float  # 0-100


class CostOptimizer:
    """Optimize API costs by suggesting better models"""

    def __init__(self, tracker: Optional[TokenTracker] = None):
        """Initialize optimizer"""
        self.tracker = tracker or TokenTracker()

    def suggest_model(
        self,
        task_type: str,
        required_capability: str = "general",
        budget_constraint: Optional[float] = None,
    ) -> Tuple[ModelType, str]:
        """
        Suggest best model for a task
        
        Args:
            task_type: Type of task (verification, generation, etc.)
            required_capability: Required capability (fast, smart, cheap)
            budget_constraint: Max cost allowed
            
        Returns:
            Tuple of (ModelType, reasoning)
        """
        # Map task types to capability requirements
        capability_map = {
            "verification": "fast",
            "test_generation": "smart",
            "code_generation": "smart",
            "quality_check": "fast",
            "documentation": "general",
            "optimization": "smart",
        }
        
        capability = capability_map.get(task_type, required_capability)
        
        # Recommend models based on capability and cost
        if capability == "smart":
            # Need best model for complex reasoning
            candidates = [
                (ModelType.CLAUDE_OPUS, "Best reasoning"),
                (ModelType.CLAUDE_SONNET, "Good reasoning, cheaper"),
                (ModelType.GPT_4, "GPT-4 alternative"),
            ]
        elif capability == "fast":
            # Need speed for quick analysis
            candidates = [
                (ModelType.CLAUDE_HAIKU, "Fastest, cheapest"),
                (ModelType.CLAUDE_SONNET, "Faster, good quality"),
                (ModelType.GPT_35, "GPT alternative"),
            ]
        else:
            # General purpose
            candidates = [
                (ModelType.CLAUDE_SONNET, "Balanced performance/cost"),
                (ModelType.CLAUDE_HAIKU, "Cost-effective"),
                (ModelType.GPT_35, "Alternative"),
            ]
        
        # Filter by budget
        if budget_constraint:
            candidates = [
                (m, r) for m, r in candidates
                if MODEL_PRICING[m]["input"] < budget_constraint
            ]
        
        # Return best candidate
        if candidates:
            return candidates[0]
        else:
            return ModelType.CLAUDE_HAIKU, "Budget constraint: cheapest option"

    def optimize_project_costs(self) -> CostMetrics:
        """
        Analyze project costs and suggest optimizations
        
        Returns:
            CostMetrics with savings potential
        """
        if not self.tracker.records:
            return CostMetrics(
                current_cost=0,
                optimized_cost=0,
                potential_savings=0,
                savings_percentage=0,
                recommended_models=[],
                efficiency_score=50,
            )
        
        current_cost = self.tracker.get_total_cost()
        
        # Analyze current usage
        by_task = self.tracker.get_cost_by_task()
        by_component = self.tracker.get_cost_by_component()
        
        # Calculate optimized cost by suggesting better models
        optimized_cost = current_cost
        recommendations = []
        
        # Check if tasks could use cheaper models
        for task_type, cost in by_task.items():
            # For most tasks, could potentially use cheaper model
            if "verification" in task_type.lower():
                savings = cost * 0.6  # 60% savings with Haiku vs Claude Opus
                optimized_cost -= savings
                recommendations.append(f"Use Claude Haiku for {task_type}")
            elif "quality" in task_type.lower():
                savings = cost * 0.5
                optimized_cost -= savings
                recommendations.append(f"Use Haiku for {task_type}")
        
        # Calculate metrics
        optimized_cost = max(current_cost * 0.3, optimized_cost)  # At least 30% of current
        savings = current_cost - optimized_cost
        savings_pct = (savings / current_cost * 100) if current_cost > 0 else 0
        
        # Efficiency score (0-100, where 100 = already optimal)
        efficiency = max(0, 100 - savings_pct)
        
        return CostMetrics(
            current_cost=current_cost,
            optimized_cost=optimized_cost,
            potential_savings=savings,
            savings_percentage=savings_pct,
            recommended_models=recommendations,
            efficiency_score=efficiency,
        )

    def get_cost_report(self) -> str:
        """Generate human-readable cost report"""
        stats = self.tracker.get_statistics()
        by_component = self.tracker.get_cost_by_component()
        by_task = self.tracker.get_cost_by_task()
        
        report = "=== KODO Cost Report ===\n"
        report += f"Total Cost: ${stats['total_cost']:.2f}\n"
        report += f"Total Tokens: {stats['total_tokens']:,}\n"
        report += f"Records: {stats['total_records']}\n"
        report += f"Avg Cost per Record: ${stats['average_cost_per_record']:.2f}\n\n"
        
        report += "--- By Component ---\n"
        for component, cost in sorted(by_component.items(), key=lambda x: x[1], reverse=True):
            pct = (cost / stats['total_cost'] * 100) if stats['total_cost'] > 0 else 0
            report += f"  {component}: ${cost:.2f} ({pct:.1f}%)\n"
        
        report += "\n--- By Task ---\n"
        for task, cost in sorted(by_task.items(), key=lambda x: x[1], reverse=True):
            pct = (cost / stats['total_cost'] * 100) if stats['total_cost'] > 0 else 0
            report += f"  {task}: ${cost:.2f} ({pct:.1f}%)\n"
        
        return report
