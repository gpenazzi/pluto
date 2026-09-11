"""Where Pluto keeps user data: $PLUTO_HOME or ~/.pluto."""

from __future__ import annotations

import json
import os
from pathlib import Path


def home() -> Path:
    return Path(os.environ.get("PLUTO_HOME", "~/.pluto")).expanduser()


def portfolio_dir(name: str) -> Path:
    return home() / "portfolios" / name


def list_portfolios() -> list[str]:
    root = home() / "portfolios"
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if (p / "HEAD").exists())


def config_path() -> Path:
    return home() / "config.json"


def read_config() -> dict[str, object]:
    p = config_path()
    if not p.exists():
        return {}
    return json.loads(p.read_text())


def write_config(cfg: dict[str, object]) -> None:
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2))


def default_portfolio() -> str:
    name = read_config().get("portfolio")
    if isinstance(name, str) and name:
        return name
    names = list_portfolios()
    return names[0] if names else "main"


def trash_dir() -> Path:
    return home() / "trash"


def delete_portfolio(name: str) -> Path:
    """Move a portfolio out of the way instead of erasing it. Returns the trash location.
    Refuses to remove the last remaining portfolio."""
    from datetime import UTC, datetime
    from shutil import move

    names = list_portfolios()
    if name not in names:
        raise FileNotFoundError(f"no portfolio named {name!r}")
    if len(names) == 1:
        raise ValueError("cannot delete the last portfolio; create another one first")
    src = portfolio_dir(name)
    dest = trash_dir() / f"{name}-{datetime.now(UTC):%Y%m%d-%H%M%S}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    move(str(src), str(dest))
    cfg = read_config()
    if cfg.get("portfolio") == name:
        remaining = list_portfolios()
        cfg["portfolio"] = remaining[0]
        write_config(cfg)
    return dest
