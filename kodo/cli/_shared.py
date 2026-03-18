"""Shared helpers for improve and test modes."""

import re
import shutil


def slugify(name: str) -> str:
    """Turn a stage name into a filesystem-safe slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "stage"


def extract_section(text: str, heading: str) -> str:
    """Extract the body of a markdown section (## heading) from *text*."""
    pattern = rf"^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)"
    m = re.search(pattern, text, re.MULTILINE | re.DOTALL)
    return m.group(1) if m else ""


def detect_docker() -> bool:
    """Check whether docker is available on the host."""
    return shutil.which("docker") is not None


def build_environment_section() -> str:
    """Build the host environment section for discovery prompts."""
    if detect_docker():
        return "- **Docker**: available."
    return "- **Docker**: not available."


# Maximum number of prior runs to scan for context.
_MAX_PRIOR_RUNS = 10


def collect_prior_report_items(
    current_run_id: str,
    report_glob: str,
    sections: dict[str, str],
) -> str:
    """Scan prior run reports and extract items from named sections.

    *report_glob* — glob pattern relative to runs root (e.g. ``"*/test-report.md"``).
    *sections* — maps section heading → prompt fragment template.
      Each template gets ``{items}`` replaced with the extracted bullet lines.
      Only included in output if items were found.

    Returns a prompt fragment string (empty if nothing found).
    """
    from kodo.log import _runs_root

    runs_dir = _runs_root()
    if not runs_dir.exists():
        return ""

    collected: dict[str, list[str]] = {heading: [] for heading in sections}

    reports = sorted(runs_dir.glob(report_glob), reverse=True)
    scanned = 0

    for report_file in reports:
        run_id = report_file.parent.name
        if run_id == current_run_id:
            continue
        if scanned >= _MAX_PRIOR_RUNS:
            break
        scanned += 1
        try:
            content = report_file.read_text(encoding="utf-8")
        except OSError:
            continue

        for heading in sections:
            section_text = extract_section(content, heading)
            for line in section_text.strip().splitlines():
                line = line.strip()
                if line.startswith("- "):
                    collected[heading].append(line)

    parts: list[str] = []
    for heading, template in sections.items():
        items = collected[heading]
        if items:
            block = "\n".join(items)
            parts.append(template.format(items=block))
    return "".join(parts)
