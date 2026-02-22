"""
Autonomous Improvement: Post-project analysis, pattern extraction, template evolution
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Any, Optional
from pathlib import Path


@dataclass
class ImprovementSuggestion:
    """Suggested improvement based on patterns"""
    category: str  # "code_quality", "testing", "security", "performance"
    suggestion: str
    priority: str  # "critical", "high", "medium", "low"
    estimated_impact: float  # 0-1 how much this could improve things
    examples: List[str] = field(default_factory=list)


class AutomatedImprovement:
    """
    Analyzes past projects and suggests improvements
    
    Uses:
    - Failure patterns from reliability
    - Quality trends
    - Cost analysis
    - Feedback patterns
    """

    def __init__(self):
        """Initialize improvement analyzer"""
        self.project_history: List[Dict[str, Any]] = []
        self.learned_patterns: Dict[str, Any] = {}

    def record_project(
        self,
        project_id: str,
        verification_scores: List[float],
        quality_results: List[bool],
        test_counts: List[int],
        cost: float,
        feedback: List[Any],
        issues: List[str],
    ) -> None:
        """Record project for learning"""
        self.project_history.append({
            "project_id": project_id,
            "timestamp": datetime.now(),
            "verification_scores": verification_scores,
            "quality_results": quality_results,
            "test_counts": test_counts,
            "cost": cost,
            "feedback": feedback,
            "issues": issues,
        })

    def analyze_patterns(self) -> Dict[str, Any]:
        """Analyze patterns across projects"""
        if not self.project_history:
            return {}
        
        patterns = {
            "common_issues": {},
            "verification_trend": None,
            "quality_pass_rate": None,
            "average_test_count": None,
            "cost_per_project": None,
        }
        
        # Analyze common issues
        all_issues = []
        for project in self.project_history:
            all_issues.extend(project["issues"])
        
        issue_counts = {}
        for issue in all_issues:
            issue_counts[issue] = issue_counts.get(issue, 0) + 1
        
        patterns["common_issues"] = sorted(
            issue_counts.items(),
            key=lambda x: x[1],
            reverse=True
        )[:5]
        
        # Verification trend
        all_scores = []
        for project in self.project_history:
            all_scores.extend(project["verification_scores"])
        
        if all_scores:
            patterns["verification_trend"] = {
                "average": sum(all_scores) / len(all_scores),
                "min": min(all_scores),
                "max": max(all_scores),
            }
        
        # Quality pass rate
        all_results = []
        for project in self.project_history:
            all_results.extend(project["quality_results"])
        
        if all_results:
            passes = sum(1 for r in all_results if r)
            patterns["quality_pass_rate"] = passes / len(all_results)
        
        # Average test count
        all_tests = []
        for project in self.project_history:
            all_tests.extend(project["test_counts"])
        
        if all_tests:
            patterns["average_test_count"] = sum(all_tests) / len(all_tests)
        
        # Cost analysis
        total_cost = sum(p["cost"] for p in self.project_history)
        patterns["cost_per_project"] = total_cost / len(self.project_history)
        
        self.learned_patterns = patterns
        return patterns

    def get_improvement_suggestions(self) -> List[ImprovementSuggestion]:
        """Get improvement suggestions based on analysis"""
        if not self.learned_patterns:
            self.analyze_patterns()
        
        suggestions = []
        
        # Check verification scores
        if self.learned_patterns.get("verification_trend"):
            trend = self.learned_patterns["verification_trend"]
            if trend["average"] < 80:
                suggestions.append(
                    ImprovementSuggestion(
                        category="code_quality",
                        suggestion="Improve verification score (currently {}%) - add more comprehensive tests".format(
                            int(trend["average"])
                        ),
                        priority="high",
                        estimated_impact=0.3,
                        examples=[
                            "Add edge case tests",
                            "Improve test coverage",
                            "Test error conditions",
                        ],
                    )
                )
        
        # Check quality pass rate
        if self.learned_patterns.get("quality_pass_rate"):
            pass_rate = self.learned_patterns["quality_pass_rate"]
            if pass_rate < 0.9:
                suggestions.append(
                    ImprovementSuggestion(
                        category="code_quality",
                        suggestion="Quality gate pass rate is {}% - address common failures".format(
                            int(pass_rate * 100)
                        ),
                        priority="high",
                        estimated_impact=0.25,
                        examples=[
                            "Fix lint violations",
                            "Improve documentation",
                            "Add type hints",
                        ],
                    )
                )
        
        # Check test coverage
        if self.learned_patterns.get("average_test_count"):
            avg_tests = self.learned_patterns["average_test_count"]
            if avg_tests < 10:
                suggestions.append(
                    ImprovementSuggestion(
                        category="testing",
                        suggestion="Average test count is low ({}) - increase test coverage".format(
                            int(avg_tests)
                        ),
                        priority="medium",
                        estimated_impact=0.2,
                        examples=[
                            "Add unit tests for all functions",
                            "Add integration tests",
                            "Add performance tests",
                        ],
                    )
                )
        
        # Check cost efficiency
        if self.learned_patterns.get("cost_per_project"):
            cost = self.learned_patterns["cost_per_project"]
            suggestions.append(
                ImprovementSuggestion(
                    category="cost_optimization",
                    suggestion="Average cost per project: ${:.2f} - consider using cheaper models for non-critical tasks".format(
                        cost
                    ),
                    priority="medium",
                    estimated_impact=0.15,
                    examples=[
                        "Use Claude Haiku for verification",
                        "Use Claude Sonnet for generation",
                        "Batch similar tasks",
                    ],
                )
            )
        
        # Check common issues
        if self.learned_patterns.get("common_issues"):
            common = self.learned_patterns["common_issues"][0]
            suggestions.append(
                ImprovementSuggestion(
                    category="reliability",
                    suggestion="Most common issue: {} - implement targeted fix".format(
                        common[0]
                    ),
                    priority="high",
                    estimated_impact=0.2,
                    examples=[
                        "Add error handling",
                        "Improve validation",
                        "Add logging",
                    ],
                )
            )
        
        return sorted(suggestions, key=lambda s: s.priority == "critical", reverse=True)

    def generate_improvement_report(self) -> str:
        """Generate human-readable improvement report"""
        patterns = self.analyze_patterns()
        suggestions = self.get_improvement_suggestions()
        
        report = "=== KODO Autonomous Improvement Report ===\n\n"
        
        report += "## Analysis Summary\n"
        report += f"Projects Analyzed: {len(self.project_history)}\n"
        
        if patterns.get("verification_trend"):
            trend = patterns["verification_trend"]
            report += f"Verification Score: {trend['average']:.1f}% (range: {trend['min']:.0f}%-{trend['max']:.0f}%)\n"
        
        if patterns.get("quality_pass_rate"):
            report += f"Quality Pass Rate: {patterns['quality_pass_rate']*100:.1f}%\n"
        
        if patterns.get("average_test_count"):
            report += f"Average Tests per Project: {patterns['average_test_count']:.0f}\n"
        
        if patterns.get("cost_per_project"):
            report += f"Average Cost per Project: ${patterns['cost_per_project']:.2f}\n"
        
        report += "\n## Improvement Opportunities\n"
        for i, suggestion in enumerate(suggestions, 1):
            report += f"\n{i}. {suggestion.suggestion}\n"
            report += f"   Priority: {suggestion.priority.upper()}\n"
            report += f"   Estimated Impact: {suggestion.estimated_impact*100:.0f}%\n"
            report += f"   Examples:\n"
            for example in suggestion.examples:
                report += f"   - {example}\n"
        
        return report

    def export_analysis(self, filepath: Path) -> None:
        """Export analysis to JSON"""
        patterns = self.analyze_patterns()
        suggestions = self.get_improvement_suggestions()
        
        data = {
            "timestamp": datetime.now().isoformat(),
            "projects_analyzed": len(self.project_history),
            "patterns": {
                k: (
                    v if not isinstance(v, dict)
                    else {
                        kk: str(vv) if not isinstance(vv, (int, float))
                        else vv
                        for kk, vv in v.items()
                    }
                ) if not isinstance(v, list)
                else [str(item) for item in v]
                for k, v in patterns.items()
            },
            "suggestions": [
                {
                    "category": s.category,
                    "suggestion": s.suggestion,
                    "priority": s.priority,
                    "estimated_impact": s.estimated_impact,
                    "examples": s.examples,
                }
                for s in suggestions
            ],
        }
        
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)
