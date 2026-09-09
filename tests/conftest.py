from __future__ import annotations

from pathlib import Path

import pytest

from pluto.core.demo import demo_portfolio
from pluto.core.model import Portfolio


@pytest.fixture(autouse=True)
def pluto_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "pluto-home"
    monkeypatch.setenv("PLUTO_HOME", str(home))
    return home


@pytest.fixture
def demo() -> Portfolio:
    return demo_portfolio()
