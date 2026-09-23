"""The version the SDK announces must be the version it is.

This was a literal in _client.py. It said 2.0.0 while the package was at
3.1.0, so every request made for two releases announced a version two majors
old, and nothing anywhere failed. The constant is now read from the installed
metadata, which cannot drift; these hold that shut and pin the value to
pyproject.toml so a release that forgets one of the two files is a red test
rather than a quiet lie in a header.
"""

import re
from importlib import metadata
from pathlib import Path

from themeparks._client import PACKAGE_VERSION, _default_user_agent

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def declared_version() -> str:
    for line in PYPROJECT.read_text().splitlines():
        match = re.fullmatch(r'version = "([^"]+)"', line.strip())
        if match:
            return match.group(1)
    raise AssertionError(f"no version line in {PYPROJECT}")


def test_pyproject_declares_a_version():
    # Guard the helper: a parser that silently found nothing would make every
    # other test here pass for the wrong reason.
    assert re.fullmatch(r"\d+\.\d+\.\d+", declared_version())


def test_announced_version_matches_pyproject():
    assert declared_version() == PACKAGE_VERSION


def test_announced_version_matches_installed_metadata():
    assert metadata.version("themeparks") == PACKAGE_VERSION


def test_user_agent_carries_it():
    assert _default_user_agent() == f"themeparks-sdk-py/{declared_version()}"


def test_the_version_is_not_a_placeholder():
    # The source-tree fallback must never reach a released artifact.
    assert PACKAGE_VERSION != "0+unknown"
