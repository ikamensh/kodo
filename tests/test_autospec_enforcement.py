"""Meta-test: ensure mocks use autospec for signature enforcement.

Always use create_autospec or autospec=True when mocking. This catches bugs where
tests pass invalid parameters that the real function doesn't accept.
"""

import ast
from pathlib import Path

import pytest

WORKSPACE_ROOT = Path(__file__).parent.parent
TESTS_DIR = WORKSPACE_ROOT / "tests"


class MockPatternVisitor(ast.NodeVisitor):
    """AST visitor that finds patch() calls and checks for autospec usage.

    Tracks variable assignments to detect patterns like:
        mock = create_autospec(some_func)
        with patch("module.some_func", mock):
    """

    # Targets that replace values/constants rather than functions — autospec N/A
    _VALUE_TARGETS = frozenset(
        {
            "sys.argv",
            "sys.stdout",
            "sys.stderr",
        }
    )

    # Suffixes that indicate value replacement (module-level constants, dicts, etc.)
    _VALUE_SUFFIXES = (
        "_MAP",
        "_URL",
        "_TOKEN",
        "_CREDENTIALS",
        "TEAMS",
        "_original_stdout",
        "available_backends",
        "_allocator",
    )

    # External classes replaced with fake factories — autospec on constructors
    # of external deps is fragile and low-value. Enforce via integration tests.
    _CLASS_REPLACEMENT_TARGETS = frozenset(
        {
            "kodo.sessions.base.subprocess.Popen",
            "claude_agent_sdk.ClaudeSDKClient",
        }
    )

    def __init__(self, filepath: Path, source_lines: list[str]):
        self.filepath = filepath
        self.source_lines = source_lines
        self.violations: list[tuple[int, str, str]] = []
        self.autospec_vars: set[str] = set()

    def visit_FunctionDef(self, node: ast.FunctionDef):
        """Reset autospec_vars for each function scope."""
        old_vars = self.autospec_vars
        self.autospec_vars = set()
        self.generic_visit(node)
        self.autospec_vars = old_vars

    def visit_Assign(self, node: ast.Assign):
        """Track variables assigned via create_autospec()."""
        if isinstance(node.value, ast.Call):
            func = node.value.func
            is_create_autospec = (
                isinstance(func, ast.Name) and func.id == "create_autospec"
            ) or (isinstance(func, ast.Attribute) and func.attr == "create_autospec")
            if is_create_autospec:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self.autospec_vars.add(target.id)
        self.generic_visit(node)

    def _is_value_target(self, target: str) -> bool:
        """True if the patch target replaces a value/constant, not a callable."""
        if target in self._VALUE_TARGETS:
            return True
        leaf = target.rsplit(".", 1)[-1]
        return any(leaf.endswith(s) or leaf == s for s in self._VALUE_SUFFIXES)

    def _has_noqa(self, lineno: int) -> bool:
        """Check if line has a # noqa: autospec comment."""
        if 0 < lineno <= len(self.source_lines):
            return "noqa: autospec" in self.source_lines[lineno - 1]
        return False

    def visit_Call(self, node: ast.Call):
        """Check patch() calls for autospec usage."""
        patch_target = self._get_patch_target(node)
        if patch_target is None:
            self.generic_visit(node)
            return

        if self._has_noqa(node.lineno):
            self.generic_visit(node)
            return

        if (
            self._is_value_target(patch_target)
            or patch_target in self._CLASS_REPLACEMENT_TARGETS
        ):
            self.generic_visit(node)
            return

        if not self._has_autospec(node):
            self.violations.append(
                (
                    node.lineno,
                    patch_target,
                    f"patch('{patch_target}') should use autospec=True or create_autospec()",
                )
            )

        self.generic_visit(node)

    def _get_patch_target(self, node: ast.Call) -> str | None:
        """Extract the patch target string if this is a patch() call."""
        func = node.func

        if isinstance(func, ast.Attribute) and func.attr == "patch":
            if node.args and isinstance(node.args[0], ast.Constant):
                return node.args[0].value

        if isinstance(func, ast.Name) and func.id == "patch":
            if node.args and isinstance(node.args[0], ast.Constant):
                return node.args[0].value

        return None

    def _has_autospec(self, node: ast.Call) -> bool:
        """Check if the patch call uses autospec (directly or via tracked variable)."""
        # Check autospec=True keyword
        for kw in node.keywords:
            if kw.arg == "autospec" and isinstance(kw.value, ast.Constant):
                if kw.value.value is True:
                    return True

        if len(node.args) >= 2:
            second_arg = node.args[1]

            # Direct create_autospec() call
            if isinstance(second_arg, ast.Call):
                func = second_arg.func
                if isinstance(func, ast.Name) and func.id == "create_autospec":
                    return True
                if isinstance(func, ast.Attribute) and func.attr == "create_autospec":
                    return True

            # Variable previously assigned via create_autospec
            if isinstance(second_arg, ast.Name):
                if second_arg.id in self.autospec_vars:
                    return True

        return False


class BareMockArgVisitor(ast.NodeVisitor):
    """AST visitor that flags bare MagicMock()/Mock() passed as keyword args.

    Bare mocks accept any attribute, hiding interface mismatches with the
    real class. Use create_autospec(Cls, instance=True), MagicMock(spec=Cls),
    or a typed helper from conftest.py.

    Skips mock-infrastructure calls (patch, MagicMock, etc.) and
    mock-configuration keywords (return_value, side_effect, etc.).
    """

    # Keywords that configure mock behavior, not real function params
    _MOCK_CONFIG_KW = frozenset(
        {
            "return_value",
            "side_effect",
            "spec",
            "spec_set",
            "wraps",
            "new",
            "new_callable",
            "autospec",
        }
    )

    # Functions that are mock infrastructure, not production code
    _MOCK_INFRA_FN = frozenset(
        {
            "patch",
            "MagicMock",
            "Mock",
            "PropertyMock",
            "AsyncMock",
            "create_autospec",
        }
    )

    def __init__(self, filepath: Path):
        self.filepath = filepath
        self.violations: list[tuple[int, str]] = []
        self.bare_mock_vars: set[str] = set()

    def visit_FunctionDef(self, node: ast.FunctionDef):
        old_vars = self.bare_mock_vars
        self.bare_mock_vars = set()
        self.generic_visit(node)
        self.bare_mock_vars = old_vars

    def visit_Assign(self, node: ast.Assign):
        """Track variables assigned via bare MagicMock()/Mock()."""
        if self._is_bare_mock(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.bare_mock_vars.add(target.id)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        """Check keyword args for bare mocks passed to non-infrastructure calls."""
        if self._is_mock_infra_call(node):
            self.generic_visit(node)
            return

        for kw in node.keywords:
            if kw.arg is None or kw.arg in self._MOCK_CONFIG_KW:
                continue
            if self._is_bare_mock(kw.value):
                self.violations.append(
                    (
                        node.lineno,
                        f"{kw.arg}=MagicMock() — use create_autospec() or MagicMock(spec=X)",
                    )
                )
            elif isinstance(kw.value, ast.Name) and kw.value.id in self.bare_mock_vars:
                self.violations.append(
                    (
                        node.lineno,
                        f"{kw.arg}={kw.value.id} (bare MagicMock) — use create_autospec() or MagicMock(spec=X)",
                    )
                )
        self.generic_visit(node)

    def _is_mock_infra_call(self, node: ast.Call) -> bool:
        func = node.func
        if isinstance(func, ast.Name) and func.id in self._MOCK_INFRA_FN:
            return True
        if isinstance(func, ast.Attribute) and func.attr in self._MOCK_INFRA_FN:
            return True
        return False

    @staticmethod
    def _is_bare_mock(node: ast.expr) -> bool:
        """True if node is MagicMock()/Mock() with no spec/spec_set."""
        if not isinstance(node, ast.Call):
            return False
        func = node.func
        if not (isinstance(func, ast.Name) and func.id in ("MagicMock", "Mock")):
            return False
        return not any(kw.arg in ("spec", "spec_set") for kw in node.keywords)


def _scan_test_files():
    """Return cached list of (path, source, tree) for all test files."""
    results = []
    for test_file in TESTS_DIR.rglob("test_*.py"):
        try:
            rel_path = test_file.relative_to(WORKSPACE_ROOT)
            if "archive" in rel_path.parts:
                continue
            if test_file.name == "test_autospec_enforcement.py":
                continue
        except ValueError:
            continue
        try:
            source = test_file.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except SyntaxError:
            continue
        results.append((test_file, source, tree))
    return results


def _find_all_violations():
    """Single pass: scan all files for both autospec and bare-mock violations."""
    autospec_violations = []
    bare_mock_violations = []
    for test_file, source, tree in _scan_test_files():
        v1 = MockPatternVisitor(test_file, source.splitlines())
        v1.visit(tree)
        for line, target, issue in v1.violations:
            autospec_violations.append((test_file, line, target, issue))
        v2 = BareMockArgVisitor(test_file)
        v2.visit(tree)
        for line, issue in v2.violations:
            bare_mock_violations.append((test_file, line, issue))
    return autospec_violations, bare_mock_violations


# Cache the scan so both tests share one pass over the files.
_cached_violations = None


def _get_violations():
    global _cached_violations
    if _cached_violations is None:
        _cached_violations = _find_all_violations()
    return _cached_violations


def test_all_mocks_use_autospec():
    """Verify all mocks use autospec for signature enforcement."""
    violations, _ = _get_violations()
    if violations:
        messages = [
            f"  {fp.relative_to(WORKSPACE_ROOT)}:{ln} - {issue}"
            for fp, ln, _tgt, issue in violations
        ]
        pytest.fail(
            f"Found {len(violations)} mock(s) without autospec:\n"
            + "\n".join(messages)
            + "\n\nUse create_autospec() or autospec=True to enforce API signatures."
        )


def test_no_bare_mock_as_function_arg():
    """Verify mocks passed as function args use spec enforcement."""
    _, violations = _get_violations()
    if violations:
        messages = [
            f"  {fp.relative_to(WORKSPACE_ROOT)}:{ln} - {issue}"
            for fp, ln, issue in violations
        ]
        pytest.fail(
            f"Found {len(violations)} bare mock(s) passed as function args:\n"
            + "\n".join(messages)
            + "\n\nUse MagicMock(spec=X) or create_autospec(X, instance=True)."
        )
