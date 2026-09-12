"""Pluto: a local, AI-first portfolio manager."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("pluto")
except PackageNotFoundError:  # running from a checkout without an installed distribution
    __version__ = "0.1.0"
