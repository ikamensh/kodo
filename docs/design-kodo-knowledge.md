# Kodo Knowledge: Design Document

## The Problem

You have a hard question — prove a theorem, analyze a policy, find the optimal strategy,
evaluate competing frameworks, or synthesize a research area. Today you'd either:
- Ask a single LLM and get a one-shot answer (fast but shallow)
- Manually iterate with follow-ups (slow, requires your attention)
- Hire experts (expensive, days/weeks)

**Kodo Knowledge** gives you a third option: throw compute and time at it. Walk away,
come back to a converged, deeply-reasoned answer with the work shown.

## Core Insight from Kodo (Code)

Kodo for code works because:
1. An orchestrator breaks work into stages
2. Workers execute independently
3. Verifiers independently check "is this actually done?"
4. The loop iterates until convergence

The key adaptation for knowledge work: **verification is no longer binary** (tests pass/fail).
Instead, convergence is reached when independent lines of reasoning agree, or when further
iteration stops producing new insights.

---

## Architecture

### Layer 1: The Question Intake

```
User provides:
  - Goal (natural language): "Prove or disprove that P = NP"
  - Effort level: quick (minutes) / standard (hour) / deep (hours) / exhaustive (overnight)
  - Domain hints (optional): "mathematics", "policy", "business strategy"
  - Constraints (optional): "use only published results", "consider regulatory context"
  - Output format (optional): "formal proof", "executive briefing", "research survey"
```

The intake phase is where the orchestrator earns its keep. Before dispatching any workers,
it does a **Question Analysis** step:

1. **Classification**: What kind of knowledge task is this?
   - Proof/disproof (convergence = valid proof found or impossibility shown)
   - Factual research (convergence = consistent facts from multiple sources)
   - Analysis/evaluation (convergence = stable ranking under perturbation)
   - Synthesis/creative (convergence = diminishing marginal improvements)
   - Decision support (convergence = robust recommendation under scenarios)

2. **Decomposition**: Can this be broken into independent sub-questions?
   - "Evaluate X" → [strengths, weaknesses, alternatives, context, edge cases]
   - "Prove theorem" → [known results, proof strategies, potential counterexamples]
   - Some questions are atomic; decomposition isn't always useful

3. **Strategy Selection**: Which execution pattern fits?

### Layer 2: Execution Patterns

Unlike code (which mostly follows plan→implement→test), knowledge work needs
**multiple distinct patterns** the orchestrator can select:

#### Pattern A: Adversarial Convergence
Best for: proofs, claims, controversial questions

```
┌─────────────┐     ┌─────────────┐
│  Advocate    │     │  Skeptic     │
│  (build the │────▶│  (attack the │
│   case)     │◀────│   case)      │
└─────────────┘     └─────────────┘
        │                   │
        └───────┬───────────┘
                ▼
         ┌──────────┐
         │  Judge    │
         │  (assess  │
         │  state)   │
         └──────────┘
                │
         converged? ──no──▶ next round (with judge's feedback)
                │
               yes
                │
         final synthesis
```

The Advocate builds the strongest case. The Skeptic tries to demolish it.
The Judge decides if the argument survives or needs refinement. This naturally
converges: either the Advocate's case becomes bulletproof, or the Skeptic finds
a fatal flaw. The Judge tracks whether new rounds are producing substantive
changes or just cosmetic rewording.

#### Pattern B: Parallel Exploration + Synthesis
Best for: research questions, "what are the options?", surveys

```
┌──────────┐  ┌──────────┐  ┌──────────┐
│ Explorer  │  │ Explorer  │  │ Explorer  │
│ (angle 1) │  │ (angle 2) │  │ (angle 3) │
└─────┬─────┘  └─────┬─────┘  └─────┬─────┘
      │              │              │
      └──────────────┼──────────────┘
                     ▼
              ┌──────────────┐
              │ Synthesizer  │
              │ (merge,      │
              │  reconcile,  │
              │  identify    │
              │  gaps)       │
              └──────┬───────┘
                     │
              gaps found? ──yes──▶ dispatch targeted explorers
                     │
                    no
                     │
              final document
```

Explorers work independently on different facets. The Synthesizer merges their
findings, identifies contradictions and gaps, and dispatches follow-up explorers
as needed. Converges when the Synthesizer finds no more gaps.

#### Pattern C: Iterative Deepening
Best for: proofs, optimization, "find the best X"

```
┌──────────┐
│ Worker    │──▶ draft v1
└──────────┘
      │
      ▼
┌──────────┐
│ Critic   │──▶ "gap in step 3, lemma X is wrong"
└──────────┘
      │
      ▼
┌──────────┐
│ Worker   │──▶ draft v2 (addressing critique)
└──────────┘
      │
      ▼
┌──────────┐
│ Critic   │──▶ "sound, but step 5 could be simpler"
└──────────┘
      │
      ...
      │
      ▼
┌──────────┐
│ Critic   │──▶ "no substantive issues remain"
└──────────┘
      │
      ▼
final answer
```

Simple but effective. The worker improves iteratively against specific feedback.
Convergence = the critic runs out of substantive objections.

#### Pattern D: Tournament
Best for: "which approach is best?", decisions with multiple options

```
┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────┐
│ Approach A│ │ Approach B│ │ Approach C│ │ Approach D│
│ (advocate)│ │ (advocate)│ │ (advocate)│ │ (advocate)│
└─────┬─────┘ └─────┬─────┘ └─────┬─────┘ └─────┬─────┘
      │              │              │              │
      └──────────────┴──────┬───────┴──────────────┘
                            ▼
                    ┌───────────────┐
                    │  Comparator   │
                    │  (evaluate on │
                    │   criteria)   │
                    └───────┬───────┘
                            │
                    ┌───────────────┐
                    │ Devil's       │
                    │ Advocate      │
                    │ (stress-test  │
                    │  top picks)   │
                    └───────┬───────┘
                            │
                    ranked recommendation
```

Each approach gets a dedicated advocate to make its best case. The Comparator
evaluates them on explicit criteria. The Devil's Advocate stress-tests the
top-ranked options to check robustness.

#### Pattern E: Calibrated Estimation
Best for: questions with quantitative answers, forecasting

```
┌───────────┐ ┌───────────┐ ┌───────────┐
│ Estimator │ │ Estimator │ │ Estimator │
│ (method 1)│ │ (method 2)│ │ (method 3)│
└─────┬─────┘ └─────┬─────┘ └─────┬─────┘
      │              │              │
      └──────────────┼──────────────┘
                     ▼
              ┌──────────────┐
              │ Aggregator   │
              │ (Delphi-style│
              │  reconcile)  │
              └──────┬───────┘
                     │
              spread too wide? ──yes──▶ share estimates, re-estimate
                     │
                    no
                     │
              calibrated answer with confidence interval
```

Multiple independent estimates using different methodologies. If they agree,
high confidence. If they disagree, iterative reconciliation (each estimator
sees others' estimates and reasoning, updates their own). Converges when
spread narrows below threshold.

---

### Layer 3: The Convergence Engine

This is the novel piece. In code-kodo, convergence = "tests pass and reviewer approves."
In knowledge-kodo, convergence is a spectrum:

```python
class ConvergenceState:
    confidence: float          # 0.0 - 1.0
    stability: float           # how much did last iteration change the answer?
    agreement: float           # do independent lines of reasoning agree?
    completeness: float        # are there known gaps remaining?
    diminishing_returns: bool  # is each iteration adding less?

    @property
    def converged(self) -> bool:
        """Convergence is multi-dimensional."""
        if self.confidence > 0.95 and self.stability > 0.9:
            return True  # High confidence, stable answer
        if self.diminishing_returns and self.confidence > 0.7:
            return True  # Good enough, no point continuing
        return False

    @property
    def verdict_type(self) -> str:
        """What kind of conclusion did we reach?"""
        if self.confidence > 0.9:
            return "strong_conclusion"     # "The answer is X because..."
        if self.confidence > 0.6:
            return "qualified_conclusion"  # "Most likely X, but Y is possible if..."
        return "open_question"             # "The key tension is between X and Y..."
```

The orchestrator tracks convergence across iterations by asking the Judge/Critic/Synthesizer
to explicitly score these dimensions. The meta-prompt includes:

> "Compare this iteration's conclusion to the previous iteration's.
>  Rate on a 1-10 scale: How much did the substance change?
>  Rate: How confident are you in the current answer?
>  List: What gaps or uncertainties remain?"

**Key principle**: The system should be honest about what it achieved. Not every question
has a clean answer. The output should clearly communicate:
- "This is definitively X" (strong convergence)
- "This is most likely X, with caveats Y and Z" (partial convergence)
- "I could not resolve this — the crux is [specific tension]" (honest non-convergence)
- "After exhaustive analysis, the question is ill-posed because..." (reframing)

---

### Layer 4: Agent Mixture

Unlike code-kodo where all agents are essentially "coding agents with different tools,"
knowledge-kodo benefits from **heterogeneous agents** with different strengths:

```
Agent Pool:
┌─────────────────────────────────────────────────────┐
│                                                     │
│  Reasoning Agents (for logic, proofs, analysis)     │
│  ├─ Claude Opus    — deep, careful reasoning        │
│  ├─ o3/o4          — chain-of-thought specialists   │
│  ├─ Gemini 2.5 Pro — long context, broad knowledge  │
│  └─ DeepSeek R1    — math/formal reasoning          │
│                                                     │
│  Research Agents (for facts, sources, data)          │
│  ├─ Perplexity API — grounded web search            │
│  ├─ Claude + search tools — search + reasoning      │
│  └─ Gemini + Google Search — real-time grounding     │
│                                                     │
│  Computation Agents (for formal verification)        │
│  ├─ Code interpreter — run calculations, simulations│
│  ├─ Lean/Coq agent — formal proof checking          │
│  └─ Wolfram Alpha — mathematical computation        │
│                                                     │
│  Specialized Agents                                  │
│  ├─ Citation checker — verify claims against sources│
│  ├─ Bias detector — flag reasoning biases           │
│  └─ Steelman agent — make opposing cases stronger   │
│                                                     │
└─────────────────────────────────────────────────────┘
```

**Why mix models?** Different models have different failure modes. If Claude and o3
independently reach the same conclusion via different reasoning paths, that's much
stronger than either alone. Model diversity is a feature, not a compromise.

**Role Assignment Strategy:**

| Role | Best Fit | Why |
|------|----------|-----|
| Advocate/Worker | Claude Opus | Careful, thorough, good at nuance |
| Skeptic/Critic | o3/o4 | Systematic, finds logical gaps |
| Judge | Different model from advocate/skeptic | Independence matters |
| Researcher | Perplexity / search-augmented | Grounded in real sources |
| Synthesizer | Claude Opus / Gemini Pro | Long context, good at structure |
| Calculator | Code interpreter | Exact computation |

The orchestrator itself should be **cheap and fast** (Gemini Flash, Claude Haiku) —
same as code-kodo. It routes, it doesn't reason deeply.

---

### Layer 5: The Artifact System

Code-kodo produces code files. Knowledge-kodo produces **structured documents**:

```
Working Artifact (evolves across iterations):
├── answer.md           # The current best answer
├── reasoning_trace.md  # How we got here (chain of reasoning)
├── evidence.md         # Facts, sources, data supporting the answer
├── counterarguments.md # Known objections and responses
├── confidence.md       # What we're sure about vs. uncertain
├── open_questions.md   # What we couldn't resolve and why
└── iteration_log.md    # What changed in each iteration
```

Agents read and write to this shared artifact space. The orchestrator manages
which agents see which artifacts (e.g., the Skeptic sees answer.md but writes
to counterarguments.md; the Worker then sees counterarguments.md and updates
answer.md).

This is analogous to code files — agents have a shared workspace, but with
structured knowledge artifacts instead of source code.

---

### Layer 6: Tools for Knowledge Work

```
Knowledge Agent Toolbox:
├── web_search(query)           — find information
├── fetch_page(url)             — read a specific source
├── compute(code)               — run Python for calculations
├── write_artifact(name, content) — update a working document
├── read_artifact(name)         — read current state of a document
├── check_citation(claim, source) — verify a claim against a source
├── formal_verify(proof)        — check a formal proof (Lean/Coq/etc.)
├── query_database(query)       — structured data lookup
└── ask_expert(question, domain) — delegate to domain-specialized agent
```

Compared to code-kodo's tools (file edit, terminal, browser), knowledge tools
are oriented around **information gathering** and **document construction** rather
than **system manipulation**.

---

## Execution Flow: End to End

```
User: "Is it possible to solve the Collatz conjecture
       using methods from ergodic theory?"

1. INTAKE
   Orchestrator classifies: proof-exploration question
   Decomposes into:
     a) What has been tried with ergodic theory approaches?
     b) What are the main barriers?
     c) Are there partial results?
     d) What would a proof strategy look like?
   Selects: Pattern B (Parallel Exploration) → Pattern A (Adversarial) on synthesis

2. EXPLORATION PHASE (parallel)
   Explorer 1 (Claude + search): Survey ergodic theory approaches to Collatz
   Explorer 2 (Gemini + search): Survey known barriers and impossibility results
   Explorer 3 (o3): Analyze what an ergodic proof would require formally
   Explorer 4 (Perplexity): Find most recent papers and results

3. SYNTHESIS
   Synthesizer (Claude Opus): Merge explorer reports
   → Identifies: Terrence Tao's 2019 result (almost all orbits),
     known density results, transfer operator approaches,
     gap: ergodic methods give "almost all" but not "all"

4. ADVERSARIAL PHASE
   Advocate (Claude Opus): "Ergodic methods are promising because..."
   Skeptic (o3): "But the fundamental barrier is... measure-zero
     exceptions could contain counterexamples..."
   Judge (Gemini Pro): "The skeptic raises a valid structural issue.
     The advocate should address whether ergodic methods can be
     strengthened to cover all cases, not just almost all."

5. ITERATION (2 more rounds)
   ...advocate refines, skeptic finds diminishing objections...

6. CONVERGENCE
   Stability: 0.92 (last round changed little)
   Confidence: 0.78 (qualified conclusion)
   Agreement: 0.85 (advocate and skeptic agree on key points)

   → Verdict type: qualified_conclusion

7. OUTPUT
   "Ergodic theory methods have produced the strongest partial results
    on the Collatz conjecture (Tao 2019), but face a fundamental
    structural barrier: they characterize typical behavior but cannot
    rule out exceptional orbits. A pure ergodic proof appears insufficient,
    but ergodic methods combined with [specific techniques] remain the
    most promising direction. Key open question: [specific gap]."

   + Full reasoning trace, evidence, counterarguments, confidence breakdown
```

---

## Effort Levels for Knowledge Work

| Level | Time Budget | Agents | Iterations | Output |
|-------|-------------|--------|------------|--------|
| **quick** | 2-5 min | 1-2 | 1 | Short answer + reasoning |
| **standard** | 15-30 min | 3-4 | 2-3 | Structured analysis |
| **deep** | 1-3 hours | 5-8 | 5-10 | Full report with evidence |
| **exhaustive** | overnight | 8-15 | until convergence | Comprehensive document |

At **quick**, you're basically getting a better single-LLM answer (one worker + one critic).
At **exhaustive**, you're getting something that would take a research assistant days.

---

## What Changes vs Code-Kodo, What Stays the Same

### Stays the Same
- Orchestrator architecture (cheap model routing to expensive workers)
- Cycle-based execution with convergence checking
- Effort levels controlling depth
- Multi-agent coordination through shared workspace
- Session abstraction (different backends pluggable)
- Unattended execution as the core value prop
- Cost tracking and monitoring

### Changes
- **Verification** → **Convergence** (spectrum, not binary)
- **Code files** → **Knowledge artifacts** (structured documents)
- **Homogeneous agents** → **Heterogeneous model mixture** (different models for different roles)
- **Test runner** → **Fact checker / Logic verifier** (different verification primitives)
- **Git integration** → **Version-tracked documents** (iteration history)
- **Acceptance criteria** → **Convergence criteria** (stability, agreement, confidence)
- **Execution patterns**: 5 distinct patterns vs code's plan→implement→verify
- **Tools**: search/compute/cite vs file-edit/terminal/browser

### New Concepts
- **Adversarial convergence** — built-in devil's advocate
- **Model diversity as a feature** — disagreement between models is informative
- **Confidence-calibrated output** — honest about what it knows vs. doesn't
- **Diminishing returns detection** — know when to stop
- **Pattern selection** — orchestrator picks the right execution shape for the question type

---

## Implementation Roadmap

### Phase 1: Core Loop (reuse from code-kodo)
- Adapt OrchestratorBase for knowledge work
- Implement knowledge artifact system (read/write structured documents)
- Add API sessions for o3, Gemini, Perplexity, DeepSeek
- Implement Pattern C (Iterative Deepening) — simplest pattern
- Basic convergence detection (stability + confidence scoring)

### Phase 2: Multi-Pattern Execution
- Implement Pattern A (Adversarial Convergence)
- Implement Pattern B (Parallel Exploration + Synthesis)
- Orchestrator question classification → pattern selection
- Web search + citation tools

### Phase 3: Advanced Convergence
- Multi-dimensional convergence tracking
- Diminishing returns detection
- Confidence calibration across models
- Formal verification tools (code interpreter, optional Lean/Coq)

### Phase 4: Tournament + Estimation
- Pattern D (Tournament)
- Pattern E (Calibrated Estimation)
- Model diversity scoring (tracking which models agree/disagree)
- Rich output formatting (evidence maps, reasoning traces)

---

## Open Questions

1. **How much orchestrator intelligence is needed for pattern selection?**
   Could start simple: user hints + keyword matching. Or let the orchestrator
   reason about it (costs one cheap model call).

2. **Should agents see each other's work during a round, or only between rounds?**
   Independence within a round gives diversity. Sharing between rounds enables building.
   Probably: independent within, shared between (like code-kodo's cycles).

3. **How to handle hallucinated "facts"?**
   Research agents with web search are grounded. Reasoning agents without search
   should be flagged as "unverified reasoning" vs "sourced claims." The citation
   checker agent is critical here.

4. **What's the right convergence threshold?**
   Probably domain-dependent. Math proofs need higher certainty than business strategy.
   Could be user-configurable or auto-calibrated per question type.

5. **Can we reuse code-kodo sessions directly?**
   ClaudeSession and the API orchestrator can likely be reused with minimal changes.
   The main adaptation is in tools, prompts, and convergence logic — not session management.
