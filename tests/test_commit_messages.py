"""Tests for Conventional Commit validation."""

import pytest

from scripts.check_commit_messages import is_conventional_subject


@pytest.mark.parametrize(
    ("subject", "valid"),
    [
        ("fix: handle timeout", True),
        ("chore(deps): update actions", True),
        ("feat!: remove legacy support", True),
        ("docs(readme): clarify usage", True),
        ("deps: update a runtime dependency", True),
        ("chore(master): release 0.1.7", True),
        ("release: publish a preview", False),
        ("Fix timeout", False),
        ("fix: ", False),
        ("fix(scope: missing delimiter", False),
    ],
)
def test_subject(subject, valid):
    assert is_conventional_subject(subject) is valid
