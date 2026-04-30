from __future__ import annotations

from html import escape

from .models import DocumentTemplateKind, DocumentTemplateVersion
from .repository import DocumentControlRepository
from .token_engine import render_tokens


_TEMPLATE_KIND_ORDER = {
    DocumentTemplateKind.HEADER: 10,
    DocumentTemplateKind.BODY: 20,
    DocumentTemplateKind.FOOTER: 30,
}


class DocumentRenderService:
    def __init__(self, repository: DocumentControlRepository) -> None:
        self._repository = repository

    def get_active_templates(self, document_type_code: str) -> list[DocumentTemplateVersion]:
        summaries = self._repository.list_templates(document_type_code=document_type_code)
        versions: list[DocumentTemplateVersion] = []
        for summary in summaries:
            if not summary.is_active or summary.active_version_id is None:
                continue
            version = self._repository.get_template_version(version_id=summary.active_version_id)
            if version is not None:
                versions.append(version)
        versions.sort(key=lambda item: _TEMPLATE_KIND_ORDER.get(item.kind, 999))
        return versions

    def render_preview_html(self, document_type_code: str, context: dict[str, object]) -> str:
        templates = self.get_active_templates(document_type_code)
        rendered_text = self.render_text(document_type_code=document_type_code, context=context, templates=templates)
        return self._wrap_preview_html(
            document_type_code=document_type_code,
            rendered_text=rendered_text,
            title=str(context.get("DocumentTitle") or f"{document_type_code} Preview"),
        )

    def render_text(
        self,
        document_type_code: str,
        context: dict[str, object],
        templates: list[DocumentTemplateVersion] | None = None,
    ) -> str:
        active_templates = templates if templates is not None else self.get_active_templates(document_type_code)
        combined_body = "\n\n".join(
            version.body_content.strip()
            for version in active_templates
            if version.body_content.strip()
        )
        return render_tokens(combined_body, context)

    def _wrap_preview_html(self, document_type_code: str, rendered_text: str, title: str) -> str:
        lowered = rendered_text.lower()
        if "<html" in lowered and "</html>" in lowered:
            return rendered_text

        safe_title = escape(title)
        if "<" in rendered_text and ">" in rendered_text:
            body_markup = rendered_text
        else:
            body_markup = f"<pre>{escape(rendered_text)}</pre>"
        return (
            "<!DOCTYPE html>\n"
            "<html>\n"
            "<head>\n"
            '  <meta charset="utf-8">\n'
            f"  <title>{safe_title} Preview</title>\n"
            "  <style>\n"
            "    body { font-family: Segoe UI, Arial, sans-serif; margin: 24px; }\n"
            "    pre { white-space: pre-wrap; word-break: break-word; }\n"
            "  </style>\n"
            "</head>\n"
            "<body>\n"
            f"  <h1>{safe_title} Preview</h1>\n"
            f"  {body_markup}\n"
            "</body>\n"
            "</html>\n"
        )
