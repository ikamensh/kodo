"""Prompt optimization — reduce token usage without losing meaning.

Provides tools to compress, deduplicate, and measure prompt token costs.
Used across agent descriptions, system prompts, and verification prompts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


def estimate_tokens(text: str) -> int:
    """Estimate token count using the ~4 chars/token heuristic.

    This is a rough approximation. For Claude models, 1 token ≈ 4 characters
    on average for English text. Exact counts require the tokenizer.
    """
    return max(1, len(text) // 4)


@dataclass
class PromptMetrics:
    """Before/after metrics for a prompt optimization."""

    original_chars: int = 0
    optimized_chars: int = 0
    original_tokens_est: int = 0
    optimized_tokens_est: int = 0

    @property
    def chars_saved(self) -> int:
        return self.original_chars - self.optimized_chars

    @property
    def tokens_saved_est(self) -> int:
        return self.original_tokens_est - self.optimized_tokens_est

    @property
    def savings_pct(self) -> float:
        if self.original_tokens_est == 0:
            return 0.0
        return (self.tokens_saved_est / self.original_tokens_est) * 100


@dataclass
class OptimizationResult:
    """Result of optimizing a prompt."""

    original: str
    optimized: str
    metrics: PromptMetrics


class PromptOptimizer:
    """Compresses and deduplicates prompt text to reduce token usage.

    Strategies:
    1. Remove redundant whitespace and empty lines
    2. Collapse repeated instructions (across agent descriptions)
    3. Replace verbose patterns with concise equivalents
    4. Strip filler words and hedging language
    """

    # Verbose → Concise replacement pairs
    _COMPRESSION_RULES: list[tuple[str, str]] = [
        # Verbose hedging
        (r"\bplease note that\b", "Note:"),
        (r"\bit is important to note that\b", "Note:"),
        (r"\bmake sure to\b", ""),
        (r"\bensure that you\b", ""),
        (r"\bin order to\b", "to"),
        (r"\bfor the purpose of\b", "for"),
        (r"\bat this point in time\b", "now"),
        (r"\bdue to the fact that\b", "because"),
        (r"\bin the event that\b", "if"),
        (r"\bwith regard to\b", "regarding"),
        (r"\bwith respect to\b", "regarding"),
        (r"\btake into account\b", "consider"),
        (r"\ba large number of\b", "many"),
        (r"\bin spite of the fact that\b", "although"),
        # Common verbose patterns
        (r"\bis able to\b", "can"),
        (r"\bare able to\b", "can"),
        (r"\bhas the ability to\b", "can"),
        (r"\bin a.*?manner\b", ""),
        # Reduce emphasis bloat
        (r"\bvery\s+", ""),
        (r"\breally\s+", ""),
        (r"\bextremely\s+", ""),
        (r"\babsolutely\s+", ""),
    ]

    def __init__(self, *, aggressive: bool = False):
        """Create optimizer.

        *aggressive*: when True, applies more aggressive compression rules
        that may slightly change nuance but save more tokens.
        """
        self.aggressive = aggressive
        self._seen_sentences: set[str] = set()

    def optimize(self, text: str) -> OptimizationResult:
        """Optimize a single prompt text.

        Returns the optimized text plus before/after metrics.
        """
        original = text
        result = text

        # 1. Normalize whitespace
        result = self._normalize_whitespace(result)

        # 2. Apply compression rules
        result = self._apply_compression_rules(result)

        # 3. Remove duplicate sentences (within this text)
        result = self._remove_internal_duplicates(result)

        # 4. Final whitespace cleanup
        result = self._normalize_whitespace(result)

        metrics = PromptMetrics(
            original_chars=len(original),
            optimized_chars=len(result),
            original_tokens_est=estimate_tokens(original),
            optimized_tokens_est=estimate_tokens(result),
        )

        return OptimizationResult(
            original=original,
            optimized=result,
            metrics=metrics,
        )

    def optimize_batch(
        self, prompts: dict[str, str]
    ) -> dict[str, OptimizationResult]:
        """Optimize multiple prompts, deduplicating across them.

        Parameters
        ----------
        prompts : dict
            ``{name: prompt_text}`` mapping.

        Returns
        -------
        dict
            ``{name: OptimizationResult}`` mapping.
        """
        self._seen_sentences.clear()
        results: dict[str, OptimizationResult] = {}

        for name, text in prompts.items():
            result = self.optimize(text)

            # Cross-prompt deduplication: remove sentences we've already seen
            if self.aggressive:
                optimized = self._deduplicate_cross_prompt(result.optimized)
                result = OptimizationResult(
                    original=result.original,
                    optimized=optimized,
                    metrics=PromptMetrics(
                        original_chars=result.metrics.original_chars,
                        optimized_chars=len(optimized),
                        original_tokens_est=result.metrics.original_tokens_est,
                        optimized_tokens_est=estimate_tokens(optimized),
                    ),
                )

            results[name] = result

        return results

    def total_savings(
        self, results: dict[str, OptimizationResult]
    ) -> PromptMetrics:
        """Compute aggregate savings across all optimized prompts."""
        total = PromptMetrics()
        for r in results.values():
            total.original_chars += r.metrics.original_chars
            total.optimized_chars += r.metrics.optimized_chars
            total.original_tokens_est += r.metrics.original_tokens_est
            total.optimized_tokens_est += r.metrics.optimized_tokens_est
        return total

    # ── Internal helpers ──────────────────────────────────────────────

    def _normalize_whitespace(self, text: str) -> str:
        """Remove redundant whitespace while preserving structure."""
        # Collapse multiple blank lines into one
        text = re.sub(r"\n{3,}", "\n\n", text)
        # Remove trailing whitespace from lines
        text = re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE)
        # Collapse multiple spaces into one
        text = re.sub(r"  +", " ", text)
        return text.strip()

    def _apply_compression_rules(self, text: str) -> str:
        """Apply pattern-based compression rules."""
        for pattern, replacement in self._COMPRESSION_RULES:
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
        return text

    def _remove_internal_duplicates(self, text: str) -> str:
        """Remove duplicate sentences within the same text."""
        lines = text.split("\n")
        seen: set[str] = set()
        result: list[str] = []

        for line in lines:
            normalized = line.strip().lower()
            if not normalized:
                result.append(line)  # preserve blank lines
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            result.append(line)

        return "\n".join(result)

    def _deduplicate_cross_prompt(self, text: str) -> str:
        """Remove sentences already seen in previously optimized prompts."""
        sentences = re.split(r"(?<=[.!?])\s+", text)
        result: list[str] = []

        for sentence in sentences:
            normalized = sentence.strip().lower()
            if normalized in self._seen_sentences:
                continue
            self._seen_sentences.add(normalized)
            result.append(sentence)

        return " ".join(result)


def audit_prompts() -> dict[str, PromptMetrics]:
    """Audit all Kodo prompts and return potential savings.

    Analyzes the orchestrator system prompt, agent descriptions,
    and verification prompts for optimization opportunities.
    """
    from kodo import (
        TESTER_PROMPT,
        TESTER_BROWSER_PROMPT,
        ARCHITECT_PROMPT,
        DESIGNER_PROMPT,
        DESIGNER_BROWSER_PROMPT,
    )
    from kodo.orchestrators.base import ORCHESTRATOR_SYSTEM_PROMPT

    prompts = {
        "orchestrator_system": ORCHESTRATOR_SYSTEM_PROMPT,
        "tester": TESTER_PROMPT,
        "tester_browser": TESTER_BROWSER_PROMPT,
        "architect": ARCHITECT_PROMPT,
        "designer": DESIGNER_PROMPT,
        "designer_browser": DESIGNER_BROWSER_PROMPT,
    }

    optimizer = PromptOptimizer()
    results = optimizer.optimize_batch(prompts)

    metrics: dict[str, PromptMetrics] = {}
    for name, result in results.items():
        metrics[name] = result.metrics

    metrics["TOTAL"] = optimizer.total_savings(results)
    return metrics
