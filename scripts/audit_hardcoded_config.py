#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

EXCLUDED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "run",
    "logs",
    "market_radar_exports",
    "venv",
}

EXCLUDED_SUFFIXES = {
    ".db",
    ".csv",
    ".html",
    ".json",
    ".jsonl",
    ".gz",
    ".jpg",
    ".jpeg",
    ".log",
    ".png",
    ".pyc",
    ".sqlite",
    ".zip",
}

PATTERNS = {
    "secret_literal": re.compile(
        r"\b(API_KEY|APP_KEY|SECRET|PASSWORD|TOKEN|CHAT_ID|EMAIL)\s*=\s*['\"][^'\"]{8,}['\"]"
    ),
    "absolute_project_path": re.compile(r"['\"]/(Applications/stock_dashboard|Users/brainlee)[^'\"]*['\"]"),
    "localhost_url": re.compile(r"https?://(127\.0\.0\.1|localhost):\d+"),
    "fixed_year_or_date": re.compile(r"\b20(20|21|22|23|24|25|26)(0[1-9]|1[0-2])?([0-3][0-9])?\b"),
}


@dataclass
class Finding:
    category: str
    path: Path
    line_no: int
    preview: str


def should_scan(path: Path) -> bool:
    rel_parts = path.relative_to(PROJECT_ROOT).parts
    if any(part in EXCLUDED_DIRS for part in rel_parts):
        return False
    if path.suffix.lower() in EXCLUDED_SUFFIXES:
        return False
    return path.is_file()


def mask_preview(line: str) -> str:
    line = line.strip()
    line = re.sub(
        r"(API_KEY|APP_KEY|SECRET|PASSWORD|TOKEN|CHAT_ID|EMAIL)(\s*=\s*)['\"][^'\"]+['\"]",
        r"\1\2***",
        line,
    )
    return line[:180]


def scan_file(path: Path) -> list[Finding]:
    findings: list[Finding] = []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return findings
    for line_no, line in enumerate(text.splitlines(), 1):
        for category, pattern in PATTERNS.items():
            if pattern.search(line):
                findings.append(Finding(category, path.relative_to(PROJECT_ROOT), line_no, mask_preview(line)))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit hardcoded config/secrets/paths/date literals.")
    parser.add_argument("--category", choices=sorted(PATTERNS), help="Show only one category.")
    parser.add_argument("--limit", type=int, default=200, help="Maximum sample findings to print.")
    args = parser.parse_args()

    findings: list[Finding] = []
    for path in PROJECT_ROOT.rglob("*"):
        if should_scan(path):
            findings.extend(scan_file(path))

    if args.category:
        findings = [f for f in findings if f.category == args.category]

    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.category] = counts.get(finding.category, 0) + 1

    print("Hardcoded config audit")
    print("======================")
    for category in sorted(PATTERNS):
        print(f"{category}: {counts.get(category, 0)}")

    print("\nSamples")
    print("-------")
    for finding in findings[: args.limit]:
        print(f"{finding.category}\t{finding.path}:{finding.line_no}\t{finding.preview}")

    return 1 if counts.get("secret_literal", 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
