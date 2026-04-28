from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


_ENV_LOADED = False


def package_root() -> Path:
    return Path(__file__).resolve().parent


def project_root() -> Path:
    return package_root().parents[1]


def neon_env_file() -> Path:
    return project_root() / ".env"


def _normalize_database_env_names() -> None:
    db_url = os.environ.get("DB_URL")
    database_url = os.environ.get("DATABASE_URL")
    if db_url and not database_url:
        os.environ["DATABASE_URL"] = db_url
    elif database_url and not db_url:
        os.environ["DB_URL"] = database_url


def load_neon_env() -> None:
    global _ENV_LOADED
    if _ENV_LOADED:
        _normalize_database_env_names()
        return

    env_file = neon_env_file()
    if env_file.exists():
        load_dotenv(env_file, override=True)

    _normalize_database_env_names()
    _ENV_LOADED = True


def get_required_env(name: str) -> str:
    load_neon_env()
    value = os.environ.get(name)
    if value is None or value == "":
        raise RuntimeError(f"Required environment variable '{name}' is not set.")
    return value
