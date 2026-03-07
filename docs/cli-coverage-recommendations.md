# CLI Test Coverage Recommendations

**Date:** 2026-03-07
**Current Project Coverage:** 76%
**Current CLI Coverage:** 81.7%

---

## Executive Summary

The CLI module has achieved **excellent test coverage** with 4 of 8 files at 80%+ coverage:

| File | Coverage | Status | Priority |
|------|----------|--------|----------|
| `__init__.py` | 100% | ✅ Perfect | - |
| `_params.py` | 99% | ✅ Excellent | - |
| `_subcommands.py` | 94% | ✅ Excellent | - |
| `_ui.py` | 83% | ✅ Good | - |
| **`_main.py`** | **80%** | ⚠️ **Just below target** | **Quick win** |
| `_intake.py` | 76% | ⚠️ Below target | Medium |
| `_improve.py` | 67% | ❌ Needs work | Medium |
| `_launch.py` | 64% | ❌ Needs work | High |

**Overall CLI Coverage:** 81.7% (1,424/1,742 lines)

---

## Priority 1: Quick Win - `_main.py` (80% → 85%)

**Current:** 79.7% (224/281 lines covered, 57 missing)
**Target:** 85% (+15 lines = +5.3pp)
**Effort:** Low (5 tests, ~2 hours)

### Missing Coverage Analysis

**57 missing lines breakdown:**
- **15 lines** - Low priority (error re-raise, already validated elsewhere)
- **25 lines** - Medium priority (interactive prompts)
- **17 lines** - High priority (core logic branches)

### Recommended Tests (to reach 85%)

#### Test 1: `--debug` flag sets skip_intake
**Line:** 265
```python
def test_debug_mode_sets_skip_intake(tmp_path):
    """--debug flag should set skip_intake=True."""
    # Mock launch_run to capture params
    # Assert args.skip_intake is True when --debug is set
```

#### Test 2: `--improve --focus` output formatting
**Lines:** 369, 371
```python
def test_improve_with_focus_prints_message(tmp_path, capsys):
    """--improve --focus should print focus message in non-JSON mode."""
    # Mock improve discovery
    # Verify "Focus: <area>" appears in output
```

#### Test 3: Interactive resume cancellation
**Lines:** 287-290
```python
def test_resume_user_cancels_interactive(tmp_path):
    """User can cancel resume at confirmation prompt."""
    # Mock incomplete runs found
    # Mock input() → 'n'
    # Verify sys.exit(0)
```

#### Test 4: Interactive goal.md rejection
**Lines:** 332-340, 342
```python
def test_interactive_goal_md_exists_user_rejects(tmp_path):
    """User can reject existing goal.md and enter new goal."""
    # Create goal.md file
    # Mock input() → 'n' (reject existing)
    # Mock get_goal() return
    # Verify get_goal() was called
```

#### Test 5: Interactive plan rejection
**Lines:** 396-405
```python
def test_interactive_existing_plan_user_rejects(tmp_path):
    """User can reject existing goal plan."""
    # Mock existing plan in run_dir
    # Mock input() → 'n'
    # Verify plan is not used (plan remains None)
```

**Impact:** +15 lines = 85% coverage (up from 80%)

---

## Priority 2: `_launch.py` (64% → 80%)

**Current:** 64.1% (202/315 lines covered, 113 missing)
**Target:** 80% (+50 lines = +15.9pp)
**Effort:** High (~15 tests, ~8 hours)

### Missing Coverage Hotspots

1. **Lines 47-74:** `--json` mode error handling and output formatting
2. **Lines 94-97, 115-119:** Resume log parsing edge cases
3. **Lines 144-145, 287-309:** Agent initialization and team setup
4. **Lines 326-327, 352-364:** Orchestrator setup edge cases
5. **Lines 378-381, 413, 425-436:** Session management error paths
6. **Lines 449-460, 490-497:** Result formatting and summary generation
7. **Lines 532-546, 552-553, 562-563:** Cleanup and teardown paths

### Recommended Test Categories

**Category A: JSON Mode (10 tests)**
- JSON error formatting for different exception types
- JSON output structure validation
- Stdout/stderr redirection edge cases

**Category B: Resume Flow (5 tests)**
- Resume with corrupted log file
- Resume with missing team members
- Resume with changed backend availability

**Category C: Launch Flow (5 tests)**
- Agent spawn failures
- Orchestrator initialization errors
- Session timeout handling

**Impact:** +50 lines = 80% coverage (up from 64%)

---

## Priority 3: `_improve.py` (67% → 80%)

**Current:** 67.0% (71/106 lines covered, 35 missing)
**Target:** 80% (+14 lines = +13pp)
**Effort:** Medium (~8 tests, ~4 hours)

### Missing Coverage Areas

1. **Lines 37-89:** `_collect_prior_needs_decision()` - Parses existing improve reports
2. **Lines 454, 460-471:** Report extraction edge cases
3. **Lines 476-477:** File I/O error handling

### Recommended Tests

1. Test `_collect_prior_needs_decision()` with various report formats
2. Test `_extract_section()` with malformed markdown
3. Test `_build_fallback_plan()` structure
4. Test `run_improve_discovery()` backend failures
5. Test report file permissions errors

**Impact:** +14 lines = 80% coverage (up from 67%)

---

## Priority 4: `_intake.py` (76% → 85%)

**Current:** 76.0% (222/292 lines covered, 70 missing)
**Target:** 85% (+26 lines = +9pp)
**Effort:** Medium (~10 tests, ~5 hours)

### Missing Coverage Areas

1. **Lines 44-45, 48-49:** Error handling in goal input
2. **Lines 82-88, 97-116:** Interactive intake wizard edge cases
3. **Lines 161-162, 205, 276:** Plan generation failures
4. **Lines 318, 337-338, 358-359:** Stage refinement loops
5. **Lines 385-386, 404-406, 410-411, 415:** Validation and confirmation flows

### Recommended Tests

Focus on interactive wizard cancellation paths and plan generation error handling.

**Impact:** +26 lines = 85% coverage (up from 76%)

---

## Projected Impact on Project Coverage

### Current State
- **Project:** 76.0% (4,780/6,282 lines)
- **CLI Module:** 81.7% (1,424/1,742 lines)

### If All Priorities Completed

| Priority | Lines Added | CLI Coverage | Project Coverage |
|----------|-------------|--------------|------------------|
| Current | - | 81.7% | 76.0% |
| Priority 1 (_main.py → 85%) | +15 | 82.6% | 76.2% |
| Priority 2 (_launch.py → 80%) | +50 | 85.5% | 77.0% |
| Priority 3 (_improve.py → 80%) | +14 | 86.3% | 77.2% |
| Priority 4 (_intake.py → 85%) | +26 | 87.8% | 77.6% |
| **TOTAL** | **+105** | **87.8%** | **77.6%** |

### Effort Summary

| Priority | Effort | Est. Time | ROI |
|----------|--------|-----------|-----|
| Priority 1 (_main.py) | Low | 2 hours | ⭐⭐⭐ High |
| Priority 2 (_launch.py) | High | 8 hours | ⭐⭐ Medium |
| Priority 3 (_improve.py) | Medium | 4 hours | ⭐⭐ Medium |
| Priority 4 (_intake.py) | Medium | 5 hours | ⭐ Low |
| **TOTAL** | - | **19 hours** | - |

---

## Recommendation

### Option A: Quick Win Only (Recommended)
**Focus:** Priority 1 only (_main.py → 85%)
- **Effort:** 2 hours (5 tests)
- **Impact:** CLI 82.6%, Project 76.2%
- **Rationale:** Minimal effort, crosses 85% threshold for _main.py

### Option B: Comprehensive CLI (If pursuing 80% project coverage)
**Focus:** Priorities 1-3 (all CLI files to 80%+)
- **Effort:** 14 hours (33 tests)
- **Impact:** CLI 86.3%, Project 77.2%
- **Rationale:** Brings entire CLI module to 80%+ coverage

### Option C: Maximum Coverage (Not Recommended)
**Focus:** All priorities including _intake.py
- **Effort:** 19 hours (43 tests)
- **Impact:** CLI 87.8%, Project 77.6%
- **Rationale:** Diminishing returns, _intake.py already at 76%

---

## Current Achievement Summary

### Stage 2 Accomplishments (Completed)
✅ `_params.py`: 40% → 99% (+59pp, 20 tests)
✅ `_subcommands.py`: 28% → 94% (+66pp, 39 tests)
✅ Combined: 96.5% CLI coverage for these critical files
✅ Zero bugs discovered (validates code quality)
✅ 803 total tests passing

### Remaining Work (Optional)
- Only **4 CLI files** remain below 80%
- **1 file** (`_main.py`) is at 79.7% - trivial to fix
- **3 files** require moderate effort (64-76% coverage)

---

## Conclusion

**Current CLI coverage (81.7%) is already strong** for a complex orchestration system. The CLI modules handling parameter selection and team management (`_params.py`, `_subcommands.py`) have achieved industry-leading coverage (96.5%).

**Recommended action:** Priority 1 only (add 5 tests to `_main.py` for 85% coverage). This provides the best ROI with minimal effort.

Further testing of `_launch.py`, `_improve.py`, and `_intake.py` should only be pursued if there's a specific goal to reach 78%+ project coverage or if bugs are discovered in production usage of these modules.
