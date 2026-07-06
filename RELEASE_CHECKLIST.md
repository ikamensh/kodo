# Release Validation Checklist

Use this checklist before publishing the `0.5.1` release.

## Version Consistency

- [ ] `pyproject.toml` has `[project] version = "0.5.1"`.
- [ ] `kodo.__version__` in `kodo/__init__.py` is exactly `"0.5.1"`.
- [ ] Packaging metadata and runtime version agree; run the packaging target below.

## Targeted Pytest Checks

- [ ] Packaging and bundled assets:

```bash
uv run pytest tests/test_packaging.py
```

- [ ] CLI smoke coverage:

```bash
uv run pytest tests/cli/test_cli_smoke.py
```

- [ ] Mocked happy-path run, resume, goal-file, and improve flows:

```bash
uv run pytest tests/test_mocked_happy_path.py
```

## Supported Mocked Smoke Scripts

These scripts run with mocked AI backends and do not require API keys.

- [ ] Non-interactive CLI run:

```bash
uv run python scripts/smoke_test_cli.py
```

- [ ] Full mocked launch flow:

```bash
uv run python scripts/run_cli_mocked.py
```

- [ ] Resume interrupted run:

```bash
uv run python scripts/smoke_test_resume.py
```

- [ ] Improve mode smoke:

```bash
uv run python scripts/smoke_test_improve.py
```

- [ ] Interactive flow smoke:

```bash
uv run python scripts/smoke_test_interactive.py
```

- [ ] Full mocked improve run:

```bash
uv run python scripts/run_improve_mocked.py
```

## Optional Browser Viewer Check

Run this when release changes touch the log viewer or embedded run data.

```bash
uv run python scripts/verify_viewer_browser.py
```
