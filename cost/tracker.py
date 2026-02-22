"""
Token Tracker: Track API costs and token usage
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Any


class ModelType(str, Enum):
    """Types of models"""
    GPT_4 = "gpt-4"
    GPT_35 = "gpt-3.5-turbo"
    CLAUDE_OPUS = "claude-opus"
    CLAUDE_SONNET = "claude-sonnet"
    CLAUDE_HAIKU = "claude-haiku"
    LOCAL = "local"


@dataclass
class TokenUsage:
    """Token usage for a single API call"""
    model: ModelType
    input_tokens: int
    output_tokens: int
    total_tokens: int


# Pricing per 1M tokens (approximate as of 2024)
MODEL_PRICING = {
    ModelType.GPT_4: {
        "input": 30.0,  # $30 per 1M input tokens
        "output": 60.0,  # $60 per 1M output tokens
    },
    ModelType.GPT_35: {
        "input": 0.5,
        "output": 1.5,
    },
    ModelType.CLAUDE_OPUS: {
        "input": 15.0,
        "output": 75.0,
    },
    ModelType.CLAUDE_SONNET: {
        "input": 3.0,
        "output": 15.0,
    },
    ModelType.CLAUDE_HAIKU: {
        "input": 0.8,
        "output": 4.0,
    },
    ModelType.LOCAL: {
        "input": 0,
        "output": 0,
    },
}


@dataclass
class CostRecord:
    """Record of API usage and cost"""
    timestamp: datetime
    task_type: str  # "verification", "quality_check", "generation", etc.
    model: ModelType
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: float
    duration_seconds: float
    component: str  # Which kodo component


class TokenTracker:
    """Track API usage and costs"""

    def __init__(self):
        """Initialize tracker"""
        self.records: List[CostRecord] = []

    def record_usage(
        self,
        task_type: str,
        model: ModelType,
        input_tokens: int,
        output_tokens: int,
        duration_seconds: float = 0,
        component: str = "unknown",
    ) -> CostRecord:
        """Record API usage"""
        total_tokens = input_tokens + output_tokens
        cost = self._calculate_cost(model, input_tokens, output_tokens)
        
        record = CostRecord(
            timestamp=datetime.now(),
            task_type=task_type,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cost_usd=cost,
            duration_seconds=duration_seconds,
            component=component,
        )
        
        self.records.append(record)
        return record

    def get_total_cost(self) -> float:
        """Get total cost across all records"""
        return sum(r.cost_usd for r in self.records)

    def get_cost_by_component(self) -> Dict[str, float]:
        """Get cost breakdown by component"""
        costs = {}
        for record in self.records:
            if record.component not in costs:
                costs[record.component] = 0
            costs[record.component] += record.cost_usd
        return costs

    def get_cost_by_model(self) -> Dict[ModelType, float]:
        """Get cost breakdown by model"""
        costs = {}
        for record in self.records:
            if record.model not in costs:
                costs[record.model] = 0
            costs[record.model] += record.cost_usd
        return costs

    def get_cost_by_task(self) -> Dict[str, float]:
        """Get cost breakdown by task type"""
        costs = {}
        for record in self.records:
            if record.task_type not in costs:
                costs[record.task_type] = 0
            costs[record.task_type] += record.cost_usd
        return costs

    def get_tokens_by_component(self) -> Dict[str, int]:
        """Get token usage by component"""
        tokens = {}
        for record in self.records:
            if record.component not in tokens:
                tokens[record.component] = 0
            tokens[record.component] += record.total_tokens
        return tokens

    def get_statistics(self) -> Dict[str, Any]:
        """Get usage statistics"""
        if not self.records:
            return {
                "total_records": 0,
                "total_cost": 0,
                "total_tokens": 0,
                "average_cost_per_record": 0,
            }
        
        total_tokens = sum(r.total_tokens for r in self.records)
        total_cost = sum(r.cost_usd for r in self.records)
        avg_duration = sum(r.duration_seconds for r in self.records) / len(self.records)
        
        return {
            "total_records": len(self.records),
            "total_cost": total_cost,
            "total_tokens": total_tokens,
            "average_cost_per_record": total_cost / len(self.records),
            "average_tokens_per_record": total_tokens / len(self.records),
            "average_duration_seconds": avg_duration,
        }

    def export(self, filepath: Path) -> None:
        """Export cost records to JSON"""
        data = [
            {
                "timestamp": r.timestamp.isoformat(),
                "task_type": r.task_type,
                "model": r.model.value,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "total_tokens": r.total_tokens,
                "cost_usd": r.cost_usd,
                "duration_seconds": r.duration_seconds,
                "component": r.component,
            }
            for r in self.records
        ]
        
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)

    @staticmethod
    def _calculate_cost(
        model: ModelType,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        """Calculate cost for token usage"""
        if model not in MODEL_PRICING:
            return 0
        
        pricing = MODEL_PRICING[model]
        
        # Cost is per 1M tokens
        input_cost = (input_tokens / 1_000_000) * pricing["input"]
        output_cost = (output_tokens / 1_000_000) * pricing["output"]
        
        return input_cost + output_cost
