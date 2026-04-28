from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def project_env_file() -> Path:
    return project_root() / ".env"


def legacy_app_root() -> Path:
    configured_root = os.environ.get("ARGON_LEGACY_APP_ROOT")
    candidates = []
    if configured_root:
        candidates.append(Path(configured_root))
    candidates.extend(
        [
            project_root() / "app",
            Path(r"D:\Argon_ai\app"),
        ]
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def ensure_legacy_import_paths() -> None:
    root = project_root()
    legacy_root = legacy_app_root()
    legacy_env_file = legacy_root / ".env"
    neon_env_file = project_env_file()

    for path in (root, legacy_root):
        path_str = str(path)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)

    if legacy_env_file.exists():
        load_dotenv(legacy_env_file, override=False)
    if neon_env_file.exists():
        load_dotenv(neon_env_file, override=True)

    db_url = os.environ.get("DB_URL")
    database_url = os.environ.get("DATABASE_URL")
    if db_url and not database_url:
        os.environ["DATABASE_URL"] = db_url
    elif database_url and not db_url:
        os.environ["DB_URL"] = database_url
