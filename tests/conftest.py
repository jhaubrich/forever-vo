"""Fixtures shared across the tests."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from tools.config import CONFIG_DIR, load_config


@pytest.fixture
def config_copy(tmp_path: Path) -> Iterator[Path]:
    """A copy of the repository's configs/ to edit."""
    copy = tmp_path / "configs"
    shutil.copytree(CONFIG_DIR, copy)
    yield copy
    load_config.cache_clear()
