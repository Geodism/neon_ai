from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from neon_ai.bootstrap import build_container  # noqa: E402


def main() -> int:
    container = build_container()

    assert hasattr(container, "document_repository")
    assert hasattr(container, "document_catalog_service")
    assert hasattr(container, "document_render_service")
    assert hasattr(container, "document_path_rule_service")
    assert hasattr(container, "generated_document_service")

    token_help = container.document_catalog_service.get_token_help()
    assert isinstance(token_help, dict)
    assert "CustomerName" in token_help

    print("app_container_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
