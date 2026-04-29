from __future__ import annotations

from pathlib import Path

from neon_ai.config import load_neon_env


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def project_env_file() -> Path:
    return project_root() / ".env"


def ensure_neon_bootstrap() -> None:
    load_neon_env()
