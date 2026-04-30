from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from neon_ai.document_control import (  # noqa: E402
    DocumentCatalogService,
    DocumentControlRepository,
    DocumentPathRuleService,
    DocumentRenderService,
    GeneratedDocumentService,
    seed_document_control_defaults,
)


def main() -> int:
    repository = DocumentControlRepository()
    catalog_service = DocumentCatalogService(repository)
    render_service = DocumentRenderService(repository)
    path_rule_service = DocumentPathRuleService(repository)
    generated_document_service = GeneratedDocumentService(
        repository=repository,
        render_service=render_service,
        path_rule_service=path_rule_service,
    )

    seed_document_control_defaults()
    print("document_control_seed_ok")

    context = catalog_service.build_sample_context(
        "CUSTOMER_INVOICE",
        seed={
            "CustomerName": "ABC Property Management",
            "WorkOrderID": "WO-1042",
            "InvoiceNumber": "INV-2026-0031",
            "InvoiceDate": "2026-04-30",
            "TotalAmount": "$4,812.50",
            "DocumentTitle": "Customer Invoice Smoke Test",
        },
    )

    preview_html = render_service.render_preview_html("CUSTOMER_INVOICE", context)
    if "ABC Property Management" not in preview_html or "INV-2026-0031" not in preview_html:
        raise RuntimeError("Preview HTML did not render the seeded invoice tokens correctly.")
    print("document_control_preview_ok")

    record = generated_document_service.generate_preview_file(
        document_type_code="CUSTOMER_INVOICE",
        context=context,
        source_record_type="SMOKE",
        source_record_id="CUSTOMER_INVOICE_SMOKE",
        create_folders=True,
        created_by="smoke",
    )
    output_path = Path(record.absolute_path)
    if not output_path.exists():
        raise RuntimeError("Generated document record was saved, but the output HTML file was not found.")

    print("document_control_generated_ok")
    print(f"output path: {record.absolute_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
