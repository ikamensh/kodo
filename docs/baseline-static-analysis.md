# Baseline & Static Analysis Report

**Generated:** 2025-02-23  
**Scope:** `kodo/cli.py`, `kodo/orchestrators/`  
**Coverage:** 77% total (cli: 62%, orchestrators/base: 92%, api: 85%, claude_code: 31%)

---

## Coverage Summary

| Module | Stmts | Miss | Cover |
|--------|-------|------|-------|
| kodo/cli.py | 832 | 312 | 62% |
| kodo/orchestrators/base.py | 451 | 37 | 92% |
| kodo/orchestrators/api.py | 120 | 18 | 85% |
| kodo/orchestrators/claude_code.py | 78 | 54 | 31% |
| kodo/orchestrators/__init__.py | 10 | 7 | 30% |

**Low-coverage areas:** Interactive CLI paths (select_params, _offer_intake, _load_or_select_params), resume flow, improve post-run, ClaudeCodeOrchestrator (MCP-based).

---

## Error Handling

### Missing or narrow exception handling

| File:Line | Issue |
|-----------|-------|
| **cli.py:991** | `_load_or_select_params`: Only catches `json.JSONDecodeError`. `cfg_path.read_text()` can raise `OSError` (e.g. `PermissionError`) on unreadable config files. |
| **cli.py:680** | `_read_intake_output`: Non-staged branch has no try/except. `output_file.read_text()` can raise `OSError`. Staged branch catches only `JSONDecodeError`, not `OSError` from `read_text()`. |
| **cli.py:666** | `_read_intake_output`: Staged branch — `output_file.read_text()` before `json.loads` can raise `OSError`; only `JSONDecodeError` is caught. |
| **orchestrators/api.py:357** | `_summarize`: `summary_result = summarizer_agent.run_sync(...)` has no try/except. `ModelHTTPError`, `UsageLimitExceeded`, or network errors propagate and can crash the cycle. |
| **summarizer.py:60,82** | `_summarize_ollama`, `_summarize_gemini`: `urllib.request.urlopen` raises `urllib.error.HTTPError` on 4xx/5xx. Not explicitly caught; summarizer's `_do_summarize` catches `Exception` at line 142, so failures are swallowed silently. |

### Broad exception handling (acceptable per project rules)

| File:Line | Notes |
|-----------|-------|
| **cli.py:1287** | `except Exception` in `main()` — intentional; JSON mode prints error, else re-raises. |
| **orchestrators/base.py:160,242,423,452,501,1063,1193,1220** | `except Exception` around agent runs and verification — appropriate; failures are logged and converted to user-facing messages. |
| **summarizer.py:35,142** | `except Exception` in probe and `_do_summarize` — intentional; summaries are best-effort, probe is expected to fail when backend unavailable. |

---

## Security

### Secrets

| File:Line | Finding |
|-----------|---------|
| **summarizer.py:67-69** | API key passed in URL query string: `?key={api_key}`. If URL is ever logged (e.g. in debug), key could leak. Prefer `Authorization` header or body. |
| **factory.py:77-83** | API keys read from `os.environ`; not hardcoded. OK. |
| **sessions/claude.py:131-159** | `ANTHROPIC_API_KEY` stripped from env when `use_api_key=False`; restored after. OK. |

### Subprocess calls

| File:Line | Finding |
|-----------|---------|
| **sessions/base.py:105** | `subprocess.Popen(cmd, ...)` — uses list form, no `shell=True`. Safe. |
| **sessions/cursor.py:51-66** | `cmd` built from `prompt`, `project_dir`, `model` — passed as separate argv elements. No injection risk. |
| **sessions/codex.py:58-81** | Same pattern; `prompt` and `project_dir` in argv. Safe. |
| **sessions/gemini_cli.py:56-65** | Same pattern. Safe. |
| **orchestrators/base.py:578-583,592-610** | `subprocess.run` with fixed `git` commands. `label` in `create_worktree` is `f"stage-{stage.index}"` (integer); no user input. `worktree_dir` and `branch_name` from `tempfile.mkdtemp` and `uuid`. Safe. |

**Verdict:** No command injection risks. Subprocess calls use list argv and avoid `shell=True`.

---

## Unused Imports

**Ruff F401:** No unused imports in `kodo/cli.py` or `kodo/orchestrators/`.

---

## Other Findings

| File:Line | Issue |
|-----------|-------|
| **orchestrators/api.py:302-305** | On 529, fallback agent is created but the loop does not retry the same request; next iteration uses new agent. Retry logic is correct. |
| **orchestrators/base.py:1220** | `except Exception` in worktree cleanup — appropriate; logs and continues to avoid leaving temp dirs. |
| **cli.py:132-136** | `_detect_project_type`: Convoluted `has_cli` logic with `any(... for _ in [1] if ...)`. Could be simplified. |

---

## Recommendations

1. **cli.py**: Add `OSError` to exception handling for config and intake output file reads.
2. **summarizer.py**: Consider `Authorization` header instead of query param for API key; add explicit handling for `urllib.error.HTTPError` if you want to log or surface summarization failures.
3. **orchestrators/api.py**: Consider try/except around `_summarize` if cycle summary failures should be handled gracefully (e.g. fallback to truncation).
