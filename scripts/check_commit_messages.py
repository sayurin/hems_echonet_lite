"""Validate Conventional Commit subjects in a pull request."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

_SUBJECT_PATTERN = re.compile(
    r"^(?P<type>[a-z][a-z0-9-]*)(?:\((?P<scope>[^()\r\n]+)\))?!?: (?P<description>\S.*)$"
)
_ALLOWED_TYPES = frozenset(
    {
        "feat",
        "fix",
        "perf",
        "deps",
        "revert",
        "docs",
        "style",
        "chore",
        "refactor",
        "test",
        "build",
        "ci",
    }
)


def is_conventional_subject(subject: str) -> bool:
    """Return whether a subject uses an allowed type and Conventional Commit format."""
    match = _SUBJECT_PATTERN.fullmatch(subject)
    return match is not None and match.group("type") in _ALLOWED_TYPES


def main() -> int:
    """Validate commit subjects and an optional pull request title."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base")
    parser.add_argument("head")
    parser.add_argument("--title", help="pull request title to validate")
    args = parser.parse_args()

    subjects = subprocess.run(
        ["git", "log", "--format=%s", "--no-merges", f"{args.base}..{args.head}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()

    invalid_subjects = [
        subject for subject in subjects if not is_conventional_subject(subject)
    ]
    if args.title is not None and not is_conventional_subject(args.title):
        invalid_subjects.append(f"PR title: {args.title}")

    if invalid_subjects:
        for subject in invalid_subjects:
            sys.stderr.write(
                f"Invalid Conventional Commit subject or unsupported type: {subject}\n"
            )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
