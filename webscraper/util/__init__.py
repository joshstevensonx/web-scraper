"""Shared helpers (text folding, URL handling, country data)."""

import importlib.util


def module_available(name: str) -> bool:
    """True if an optional dependency (e.g. ``pyarrow``, ``playwright``) can be imported."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False
