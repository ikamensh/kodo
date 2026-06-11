#!/usr/bin/env python3
"""Build, verify, and upload a kodo release."""

from __future__ import annotations

import argparse
import glob
import shutil
import subprocess
import sys


def run(cmd: list[str], label: str) -> None:
    print(label)
    subprocess.run(cmd, check=True)


def dist_files() -> list[str]:
    files = sorted(glob.glob("dist/*"))
    if not files:
        raise RuntimeError("No dist files found in dist/. Build did not produce artifacts.")
    return files


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run release checks, build artifacts, verify them, and upload to PyPI."
    )
    parser.add_argument(
        "-t",
        "--test-pypi",
        action="store_true",
        help="Upload to TestPyPI instead of PyPI.",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Skip the PyPI confirmation prompt.",
    )
    args = parser.parse_args()

    py = sys.executable
    run([py, "-m", "ruff", "check", "."], "Running ruff...")
    run([py, "-m", "pytest"], "Running pytest...")

    shutil.rmtree("dist", ignore_errors=True)
    run([py, "-m", "build"], "Building artifacts...")
    files = dist_files()
    run([py, "-m", "twine", "check", *files], "Checking artifacts...")

    if args.test_pypi:
        run(
            [py, "-m", "twine", "upload", "--repository", "testpypi", *files],
            "Uploading to TestPyPI...",
        )
        return 0

    if not args.yes:
        confirm = input("Upload to PyPI (https://pypi.org)? Type 'yes' to continue: ")
        if confirm.strip() != "yes":
            print("Aborted.")
            return 1

    run([py, "-m", "twine", "upload", *files], "Uploading to PyPI...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
