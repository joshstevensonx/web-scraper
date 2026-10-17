"""Application paths. Everything lives under ``~/WebScraper`` unless ``WEBSCRAPER_HOME`` is set."""

from __future__ import annotations

import os


def home_dir() -> str:
    path = os.path.expanduser(os.environ.get("WEBSCRAPER_HOME") or "~/WebScraper")
    os.makedirs(path, exist_ok=True)
    return path


def output_dir() -> str:
    path = os.path.join(home_dir(), "output")
    os.makedirs(path, exist_ok=True)
    return path


def logs_dir() -> str:
    path = os.path.join(home_dir(), "logs")
    os.makedirs(path, exist_ok=True)
    return path


def jobs_dir() -> str:
    path = os.path.join(home_dir(), "jobs")
    os.makedirs(path, exist_ok=True)
    return path


def database_path() -> str:
    return os.path.join(home_dir(), "webscraper.db")
