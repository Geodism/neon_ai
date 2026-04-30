from __future__ import annotations

import os
import re
from pathlib import Path

from .models import DocumentOutputFormat, DocumentPathRule
from .repository import DocumentControlRepository
from .token_engine import render_tokens, sanitize_filename_part


_PATH_SPLIT_PATTERN = re.compile(r"[\\/]+")
_OUTPUT_SUFFIXES = {
    DocumentOutputFormat.HTML: ".html",
    DocumentOutputFormat.TEXT: ".txt",
    DocumentOutputFormat.DOCX: ".docx",
    DocumentOutputFormat.PDF: ".pdf",
}


class DocumentPathRuleService:
    def __init__(self, repository: DocumentControlRepository) -> None:
        self._repository = repository

    def list_path_rules(self, document_type_code: str | None = None) -> list[DocumentPathRule]:
        return self._repository.list_path_rules(document_type_code=document_type_code)

    def load_active_path_rule(self, document_type_code: str) -> DocumentPathRule | None:
        return self._repository.get_active_path_rule(document_type_code)

    def resolve_output_path(
        self,
        document_type_code: str,
        context: dict[str, object],
        output_format: DocumentOutputFormat | None = None,
        create_folders: bool = False,
        base_directory: Path | str | None = None,
    ) -> Path:
        rule = self.load_active_path_rule(document_type_code)
        if rule is None:
            raise ValueError(f"No active path rule is configured for document type '{document_type_code}'.")

        return self.resolve_rule_path(
            rule=rule,
            context=context,
            output_format=output_format,
            create_folders=create_folders,
            base_directory=base_directory,
        )

    def resolve_rule_path(
        self,
        rule: DocumentPathRule,
        context: dict[str, object],
        output_format: DocumentOutputFormat | None = None,
        create_folders: bool = False,
        base_directory: Path | str | None = None,
    ) -> Path:
        if rule is None:
            raise ValueError("A document path rule is required.")

        resolved_output_format = output_format or rule.output_format
        rendered_relative = render_tokens(rule.relative_pattern, context)
        rendered_filename = render_tokens(rule.filename_pattern, context)

        relative_parts = [
            part
            for part in (
                sanitize_filename_part(segment)
                for segment in _PATH_SPLIT_PATTERN.split(rendered_relative)
            )
            if part
        ]
        filename = sanitize_filename_part(rendered_filename) or sanitize_filename_part(document_type_code) or "document"
        suffix = _OUTPUT_SUFFIXES[resolved_output_format]
        if Path(filename).suffix.lower() != suffix:
            filename = f"{filename}{suffix}"

        target_path = Path(*relative_parts, filename) if relative_parts else Path(filename)
        root_path = self._resolve_base_directory(rule, base_directory)
        if root_path is not None:
            target_path = root_path / target_path

        if create_folders:
            self._ensure_target_folder(rule, target_path.parent)

        return target_path

    def _ensure_target_folder(self, rule: DocumentPathRule, target_folder: Path) -> None:
        if rule.create_folder_if_missing:
            target_folder.mkdir(parents=True, exist_ok=True)
            return

        if target_folder.exists():
            return

        rule_name = (rule.rule_name or "Unnamed Rule").strip() or "Unnamed Rule"
        raise ValueError(
            "Target folder does not exist and this path rule is configured not to create it automatically.\n\n"
            f"Rule: {rule_name}\n"
            f"Document Type: {rule.document_type_code}\n"
            f"Target Folder: {target_folder}"
        )

    def _resolve_base_directory(
        self,
        rule: DocumentPathRule,
        base_directory: Path | str | None,
    ) -> Path | None:
        if base_directory is not None:
            return Path(base_directory)

        preferred_root = (rule.local_root or "").strip()
        fallback_root = (rule.fallback_root or "").strip()

        if preferred_root:
            preferred_path = Path(preferred_root)
            drive = os.path.splitdrive(str(preferred_path))[0]
            if not drive or Path(f"{drive}\\").exists():
                return preferred_path

        if fallback_root:
            return Path(fallback_root)

        return None
