"""Kodo Autonomous Self-Improvement System.

This module implements continuous self-improvement for Kodo:
- Monitors health in real-time
- Analyzes codebase for improvements
- Executes improvements autonomously
- Learns what works best
- Runs 24/7 without human intervention
"""

from kodo.autonomous.monitor import RealTimeMonitor, HealthMetrics
from kodo.autonomous.executor import AutoImprovementExecutor, Improvement, ExecutionResult
from kodo.autonomous.continuous_loop import ContinuousImprovementSystem, create_system

__all__ = [
    "RealTimeMonitor",
    "HealthMetrics",
    "AutoImprovementExecutor",
    "Improvement",
    "ExecutionResult",
    "ContinuousImprovementSystem",
    "create_system",
]
