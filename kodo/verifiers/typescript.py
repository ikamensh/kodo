"""TypeScript and build verification for Kodo agents.

Ensures code modifications don't break compilation, linting, or tests.
"""

from __future__ import annotations

import subprocess
import json
from pathlib import Path
from typing import NamedTuple


class VerifyResult(NamedTuple):
    """Result of code verification."""
    success: bool
    error_message: str = ""
    details: str = ""
    files_with_errors: list[str] = []


def verify_typescript_build(project_dir: Path) -> VerifyResult:
    """Run TypeScript compiler to verify no syntax errors.
    
    Args:
        project_dir: Root of project (where tsconfig.json/package.json is)
        
    Returns:
        VerifyResult with success status and error details
    """
    try:
        result = subprocess.run(
            ["npx", "tsc", "--noEmit"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=60,
        )
        
        if result.returncode == 0:
            return VerifyResult(success=True)
        
        # Parse TypeScript errors
        errors = result.stderr or result.stdout
        files_with_errors = parse_typescript_errors(errors)
        
        return VerifyResult(
            success=False,
            error_message="TypeScript compilation failed",
            details=errors[:1000],  # First 1000 chars of error
            files_with_errors=files_with_errors,
        )
    except subprocess.TimeoutExpired:
        return VerifyResult(
            success=False,
            error_message="TypeScript check timed out (>60s)"
        )
    except Exception as e:
        return VerifyResult(
            success=False,
            error_message=f"TypeScript verification failed: {str(e)}"
        )


def verify_eslint(project_dir: Path, files: list[str] | None = None) -> VerifyResult:
    """Run ESLint to verify code style.
    
    Args:
        project_dir: Root of project
        files: Specific files to lint. If None, lint all.
        
    Returns:
        VerifyResult with success status and error details
    """
    try:
        cmd = ["npx", "eslint", "--format", "json"]
        if files:
            cmd.extend(files)
        else:
            cmd.append("src/")  # Default to src directory
        
        result = subprocess.run(
            cmd,
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=60,
        )
        
        if result.returncode == 0:
            return VerifyResult(success=True)
        
        # Parse ESLint output
        try:
            lint_errors = json.loads(result.stdout)
            files_with_errors = [err["filePath"] for err in lint_errors if err.get("messages")]
            error_summary = format_eslint_errors(lint_errors)
            
            return VerifyResult(
                success=False,
                error_message="ESLint found violations",
                details=error_summary[:1000],
                files_with_errors=files_with_errors,
            )
        except json.JSONDecodeError:
            return VerifyResult(
                success=False,
                error_message="ESLint check failed",
                details=result.stdout[:1000] or result.stderr[:1000],
            )
    except subprocess.TimeoutExpired:
        return VerifyResult(
            success=False,
            error_message="ESLint check timed out (>60s)"
        )
    except Exception as e:
        return VerifyResult(
            success=False,
            error_message=f"ESLint verification failed: {str(e)}"
        )


def verify_build(project_dir: Path) -> VerifyResult:
    """Run production build to verify everything compiles together.
    
    Args:
        project_dir: Root of project
        
    Returns:
        VerifyResult with success status and error details
    """
    try:
        result = subprocess.run(
            ["npm", "run", "build"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=120,
        )
        
        if result.returncode == 0:
            return VerifyResult(success=True, details="Build succeeded")
        
        # Parse build error
        errors = result.stderr or result.stdout
        
        return VerifyResult(
            success=False,
            error_message="Build failed",
            details=errors[:2000],  # More context for build errors
        )
    except subprocess.TimeoutExpired:
        return VerifyResult(
            success=False,
            error_message="Build timed out (>120s)"
        )
    except Exception as e:
        return VerifyResult(
            success=False,
            error_message=f"Build verification failed: {str(e)}"
        )


def parse_typescript_errors(error_output: str) -> list[str]:
    """Extract file paths from TypeScript error output."""
    files = set()
    for line in error_output.split("\n"):
        if "error TS" in line:
            # Format: "path/to/file.ts(line,col): error TSxxxx: ..."
            if ":" in line:
                file_part = line.split(":")[0].strip()
                if file_part and not file_part.startswith(" "):
                    files.add(file_part)
    return list(files)


def format_eslint_errors(lint_errors: list[dict]) -> str:
    """Format ESLint errors into readable summary."""
    summary_lines = []
    for err in lint_errors[:5]:  # First 5 files
        file_path = err.get("filePath", "?")
        messages = err.get("messages", [])
        if messages:
            summary_lines.append(f"{file_path}: {len(messages)} issues")
            for msg in messages[:3]:  # First 3 issues per file
                summary_lines.append(
                    f"  Line {msg.get('line')}: {msg.get('message')}"
                )
    return "\n".join(summary_lines)


def verify_after_changes(
    project_dir: Path,
    modified_files: list[str],
    full_build: bool = False,
) -> VerifyResult:
    """Comprehensive verification after code changes.
    
    Args:
        project_dir: Root of project
        modified_files: Files that were modified
        full_build: If True, run full build. If False, just TypeScript check.
        
    Returns:
        VerifyResult from the most critical check (build > ts > eslint)
    """
    # Always run TypeScript first (fastest, most critical)
    ts_result = verify_typescript_build(project_dir)
    if not ts_result.success:
        return ts_result
    
    # Run ESLint on modified files
    eslint_result = verify_eslint(project_dir, modified_files)
    if not eslint_result.success:
        return eslint_result
    
    # Optionally run full build
    if full_build:
        build_result = verify_build(project_dir)
        if not build_result.success:
            return build_result
    
    return VerifyResult(success=True, details="All checks passed")
