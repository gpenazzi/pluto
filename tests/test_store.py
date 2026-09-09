from pathlib import Path

import pytest

from pluto.core.demo import demo_portfolio
from pluto.core.model import Portfolio
from pluto.store.paths import default_portfolio, list_portfolios, portfolio_dir
from pluto.store.versions import StoreError, VersionStore


def test_commit_load_history_and_revert(tmp_path: Path):
    store = VersionStore(tmp_path / "p")
    assert not store.exists()
    with pytest.raises(StoreError):
        store.head()
    p = demo_portfolio()
    v1 = store.commit(p, "initial")
    assert v1.version == 1 and store.head() == 1
    assert store.load() == p

    p2 = store.load()
    p2.remove_transaction(p2.transactions[-1].id)
    v2 = store.commit(p2, "remove last")
    assert v2.version == 2
    assert len(store.load().transactions) == len(p.transactions) - 1

    v3 = store.revert(1)
    assert v3.version == 3 and v3.reverted_from == 1
    assert store.load() == p
    assert store.load(2) == p2  # old versions untouched
    assert [i.version for i in store.history()] == [1, 2, 3]
    assert (tmp_path / "p" / "versions" / "000003.json").exists()


def test_portfolio_dir_uses_pluto_home(pluto_home: Path):
    store = VersionStore(portfolio_dir("x"))
    store.commit(Portfolio(name="x"), "init")
    assert list_portfolios() == ["x"]
    assert default_portfolio() == "x"
    assert str(pluto_home) in str(store.root)
