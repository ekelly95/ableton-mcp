"""pyproject.toml version must match control_surface.config.VERSION."""

import tomllib
from pathlib import Path

from control_surface.config import VERSION


def test_versions_match():
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    with open(pyproject, "rb") as f:
        data = tomllib.load(f)
    assert data["project"]["version"] == VERSION
