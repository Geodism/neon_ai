from __future__ import annotations

from collections.abc import Callable

import psycopg2
from psycopg2 import errorcodes
from psycopg2.extras import Json

from neon_ai.database.connection import get_connection

from .models import (
    DocumentOutputFormat,
    DocumentPathRule,
    DocumentTemplateDefault,
    DocumentTemplateKind,
    DocumentTemplateSummary,
    DocumentTemplateVersion,
    DocumentTypeDefinition,
    GeneratedDocumentRecord,
)


class DocumentControlRepository:
    def __init__(self, connection_factory: Callable[[], object] | None = None) -> None:
        self._connection_factory = connection_factory or get_connection

    def list_document_types(self) -> list[DocumentTypeDefinition]:
        query = """
            SELECT
                document_type_id,
                document_type_code,
                display_name,
                description,
                default_output_format,
                is_active,
                created_at
            FROM public.app_document_type
            ORDER BY display_name, document_type_code
        """
        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    cur.execute(query)
                    return [self._map_document_type(row) for row in cur.fetchall()]
        except psycopg2.Error as exc:
            if self._is_missing_schema_error(exc):
                return []
            raise

    def list_templates(self, document_type_code: str | None = None) -> list[DocumentTemplateSummary]:
        query = """
            SELECT
                t.template_id,
                t.document_type_code,
                t.template_kind,
                t.template_name,
                t.content_format,
                t.active_version_id,
                t.current_version_number AS active_version_number,
                t.is_active,
                t.notes,
                t.updated_at
            FROM public.app_document_template t
            WHERE (%s IS NULL OR t.document_type_code = %s)
            ORDER BY t.document_type_code, t.template_kind, t.template_name
        """
        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    cur.execute(query, (document_type_code, document_type_code))
                    return [self._map_template_summary(row) for row in cur.fetchall()]
        except psycopg2.Error as exc:
            if self._is_missing_schema_error(exc):
                return []
            raise

    def get_template_version(
        self,
        template_id: int | None = None,
        version_id: int | None = None,
    ) -> DocumentTemplateVersion | None:
        if template_id is None and version_id is None:
            raise ValueError("template_id or version_id is required")

        query = """
            SELECT
                v.template_version_id,
                v.template_id,
                t.document_type_code,
                t.template_kind,
                t.template_name,
                v.version_number,
                v.subject_line,
                v.body_content,
                v.content_format,
                v.output_format,
                v.token_schema,
                v.change_summary,
                v.notes,
                (t.active_version_id = v.template_version_id) AS is_active,
                v.created_at,
                v.created_by
            FROM public.app_document_template_version v
            JOIN public.app_document_template t
                ON t.template_id = v.template_id
            WHERE (%s IS NOT NULL AND v.template_version_id = %s)
               OR (%s IS NOT NULL AND t.template_id = %s)
            ORDER BY
                CASE
                    WHEN %s IS NOT NULL AND t.active_version_id = v.template_version_id THEN 0
                    ELSE 1
                END,
                v.version_number DESC
            LIMIT 1
        """
        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        query,
                        (version_id, version_id, template_id, template_id, template_id),
                    )
                    row = cur.fetchone()
                    return self._map_template_version(row) if row else None
        except psycopg2.Error as exc:
            if self._is_missing_schema_error(exc):
                return None
            raise

    def save_template_version(self, version: DocumentTemplateVersion) -> DocumentTemplateVersion:
        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    template_id = version.template_id
                    if template_id is None:
                        cur.execute(
                            """
                            INSERT INTO public.app_document_template (
                                document_type_code,
                                template_kind,
                                template_name,
                                content_format,
                                current_version_number,
                                notes,
                                is_active
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                            RETURNING template_id
                            """,
                            (
                                version.document_type_code,
                                version.kind.value,
                                version.template_name,
                                version.content_format,
                                0,
                                version.notes,
                                version.is_active,
                            ),
                        )
                        template_id = int(cur.fetchone()["template_id"])
                    else:
                        cur.execute(
                            """
                            UPDATE public.app_document_template
                            SET template_name = %s,
                                document_type_code = %s,
                                template_kind = %s,
                                content_format = %s,
                                notes = %s,
                                updated_at = now()
                            WHERE template_id = %s
                            """,
                            (
                                version.template_name,
                                version.document_type_code,
                                version.kind.value,
                                version.content_format,
                                version.notes,
                                template_id,
                            ),
                        )

                    if version.template_version_id is None:
                        cur.execute(
                            """
                            SELECT COALESCE(MAX(version_number), 0) + 1 AS next_version
                            FROM public.app_document_template_version
                            WHERE template_id = %s
                            """,
                            (template_id,),
                        )
                        next_version = int(cur.fetchone()["next_version"])
                        cur.execute(
                            """
                            INSERT INTO public.app_document_template_version (
                                template_id,
                                version_number,
                                subject_line,
                                body_content,
                                content_format,
                                output_format,
                                token_schema,
                                change_summary,
                                notes,
                                created_by
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            RETURNING template_version_id
                            """,
                            (
                                template_id,
                                next_version,
                                version.subject_line,
                                version.body_content,
                                version.content_format,
                                version.output_format.value,
                                Json(list(version.token_schema)),
                                version.change_summary,
                                version.notes,
                                version.created_by,
                            ),
                        )
                        template_version_id = int(cur.fetchone()["template_version_id"])
                    else:
                        template_version_id = version.template_version_id
                        cur.execute(
                            """
                            UPDATE public.app_document_template_version
                            SET subject_line = %s,
                                body_content = %s,
                                content_format = %s,
                                output_format = %s,
                                token_schema = %s,
                                change_summary = %s,
                                notes = %s,
                                created_by = COALESCE(%s, created_by)
                            WHERE template_version_id = %s
                            """,
                            (
                                version.subject_line,
                                version.body_content,
                                version.content_format,
                                version.output_format.value,
                                Json(list(version.token_schema)),
                                version.change_summary,
                                version.notes,
                                version.created_by,
                                template_version_id,
                            ),
                        )

                    if version.is_active:
                        self._activate_template_cursor(cur, template_id, template_version_id)

                    conn.commit()
                    return self.get_template_version(version_id=template_version_id) or DocumentTemplateVersion(
                        template_version_id=template_version_id,
                        template_id=template_id,
                        document_type_code=version.document_type_code,
                        kind=version.kind,
                        template_name=version.template_name,
                        version_number=version.version_number,
                        subject_line=version.subject_line,
                        body_content=version.body_content,
                        content_format=version.content_format,
                        output_format=version.output_format,
                        token_schema=version.token_schema,
                        change_summary=version.change_summary,
                        notes=version.notes,
                        is_active=version.is_active,
                        created_by=version.created_by,
                    )
        except psycopg2.Error as exc:
            if self._is_missing_schema_error(exc):
                raise RuntimeError("Document control schema is not available. Apply database_schema.sql first.") from exc
            raise

    def activate_template(self, template_id: int) -> None:
        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT template_version_id
                        FROM public.app_document_template_version
                        WHERE template_id = %s
                        ORDER BY version_number DESC, template_version_id DESC
                        LIMIT 1
                        """,
                        (template_id,),
                    )
                    version_row = cur.fetchone()
                    if not version_row or version_row.get("template_version_id") is None:
                        raise ValueError(f"Template #{template_id} does not have a saved version to activate.")

                    self._activate_template_cursor(cur, template_id, int(version_row["template_version_id"]))
                    conn.commit()
        except psycopg2.Error as exc:
            if self._is_missing_schema_error(exc):
                raise RuntimeError("Document control schema is not available. Apply database_schema.sql first.") from exc
            raise

    def list_template_defaults(
        self,
        document_type_code: str | None = None,
        usage_context: str | None = None,
    ) -> list[DocumentTemplateDefault]:
        query = """
            SELECT
                template_default_id,
                document_type_code,
                template_kind,
                usage_context,
                template_id,
                updated_at,
                updated_by
            FROM public.app_document_template_default
            WHERE (%s IS NULL OR document_type_code = %s)
              AND (%s IS NULL OR usage_context = %s)
            ORDER BY document_type_code, usage_context, template_kind
        """
        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        query,
                        (document_type_code, document_type_code, usage_context, usage_context),
                    )
                    return [self._map_template_default(row) for row in cur.fetchall()]
        except psycopg2.Error as exc:
            if self._is_missing_schema_error(exc):
                return []
            raise

    def get_template_default(
        self,
        document_type_code: str,
        template_kind: DocumentTemplateKind | str,
        usage_context: str,
    ) -> DocumentTemplateDefault | None:
        kind_value = (
            template_kind.value if isinstance(template_kind, DocumentTemplateKind) else str(template_kind)
        )
        query = """
            SELECT
                template_default_id,
                document_type_code,
                template_kind,
                usage_context,
                template_id,
                updated_at,
                updated_by
            FROM public.app_document_template_default
            WHERE document_type_code = %s
              AND template_kind = %s
              AND usage_context = %s
            LIMIT 1
        """
        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    cur.execute(query, (document_type_code, kind_value, usage_context))
                    row = cur.fetchone()
                    return self._map_template_default(row) if row else None
        except psycopg2.Error as exc:
            if self._is_missing_schema_error(exc):
                return None
            raise

    def set_template_default(
        self,
        document_type_code: str,
        template_kind: DocumentTemplateKind | str,
        usage_context: str,
        template_id: int,
        updated_by: str = "UI",
    ) -> DocumentTemplateDefault:
        kind_value = (
            template_kind.value if isinstance(template_kind, DocumentTemplateKind) else str(template_kind)
        )
        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT document_type_code, template_kind
                        FROM public.app_document_template
                        WHERE template_id = %s
                        """,
                        (template_id,),
                    )
                    template_row = cur.fetchone()
                    if not template_row:
                        raise ValueError(f"Template #{template_id} was not found.")

                    actual_document_type = str(template_row["document_type_code"])
                    actual_kind = str(template_row["template_kind"])
                    if actual_document_type != document_type_code:
                        raise ValueError(
                            f"Template #{template_id} belongs to {actual_document_type}, not {document_type_code}."
                        )
                    if actual_kind != kind_value:
                        raise ValueError(
                            f"Template #{template_id} has kind {actual_kind}, not {kind_value}."
                        )

                    cur.execute(
                        """
                        INSERT INTO public.app_document_template_default (
                            document_type_code,
                            template_kind,
                            usage_context,
                            template_id,
                            updated_at,
                            updated_by
                        )
                        VALUES (%s, %s, %s, %s, now(), %s)
                        ON CONFLICT (document_type_code, template_kind, usage_context)
                        DO UPDATE SET
                            template_id = EXCLUDED.template_id,
                            updated_at = now(),
                            updated_by = EXCLUDED.updated_by
                        RETURNING
                            template_default_id,
                            document_type_code,
                            template_kind,
                            usage_context,
                            template_id,
                            updated_at,
                            updated_by
                        """,
                        (
                            document_type_code,
                            kind_value,
                            usage_context,
                            template_id,
                            updated_by,
                        ),
                    )
                    row = cur.fetchone()
                    conn.commit()
                    if row:
                        return self._map_template_default(row)
                    return DocumentTemplateDefault(
                        template_default_id=None,
                        document_type_code=document_type_code,
                        kind=DocumentTemplateKind(kind_value),
                        usage_context=usage_context,
                        template_id=template_id,
                        updated_by=updated_by,
                    )
        except psycopg2.Error as exc:
            if self._is_missing_schema_error(exc):
                raise RuntimeError("Document control schema is not available. Apply database_schema.sql first.") from exc
            raise

    def list_path_rules(self, document_type_code: str | None = None) -> list[DocumentPathRule]:
        query = """
            SELECT
                path_rule_id,
                document_type_code,
                local_root,
                fallback_root,
                relative_pattern,
                filename_pattern,
                storage_bucket,
                output_format,
                rule_name,
                is_active,
                create_folder_if_missing,
                notes,
                created_at,
                created_by
            FROM public.app_document_path_rule
            WHERE (%s IS NULL OR document_type_code = %s)
            ORDER BY document_type_code, rule_name, path_rule_id
        """
        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    cur.execute(query, (document_type_code, document_type_code))
                    return [self._map_path_rule(row) for row in cur.fetchall()]
        except psycopg2.Error as exc:
            if self._is_missing_schema_error(exc):
                return []
            raise

    def get_active_path_rule(self, document_type_code: str) -> DocumentPathRule | None:
        query = """
            SELECT
                path_rule_id,
                document_type_code,
                local_root,
                fallback_root,
                relative_pattern,
                filename_pattern,
                storage_bucket,
                output_format,
                rule_name,
                is_active,
                create_folder_if_missing,
                notes,
                created_at,
                created_by
            FROM public.app_document_path_rule
            WHERE document_type_code = %s
              AND is_active = TRUE
            ORDER BY path_rule_id DESC
            LIMIT 1
        """
        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    cur.execute(query, (document_type_code,))
                    row = cur.fetchone()
                    return self._map_path_rule(row) if row else None
        except psycopg2.Error as exc:
            if self._is_missing_schema_error(exc):
                return None
            raise

    def save_path_rule(self, rule: DocumentPathRule) -> DocumentPathRule:
        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    if rule.path_rule_id is None:
                        cur.execute(
                            """
                            INSERT INTO public.app_document_path_rule (
                                document_type_code,
                                local_root,
                                fallback_root,
                                relative_pattern,
                                filename_pattern,
                                storage_bucket,
                                output_format,
                                rule_name,
                                is_active,
                                create_folder_if_missing,
                                notes,
                                created_by
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            RETURNING path_rule_id
                            """,
                            (
                                rule.document_type_code,
                                rule.local_root,
                                rule.fallback_root,
                                rule.relative_pattern,
                                rule.filename_pattern,
                                rule.storage_bucket,
                                rule.output_format.value,
                                rule.rule_name,
                                rule.is_active,
                                rule.create_folder_if_missing,
                                rule.notes,
                                rule.created_by,
                            ),
                        )
                        path_rule_id = int(cur.fetchone()["path_rule_id"])
                    else:
                        path_rule_id = rule.path_rule_id
                        cur.execute(
                            """
                            UPDATE public.app_document_path_rule
                            SET document_type_code = %s,
                                local_root = %s,
                                fallback_root = %s,
                                relative_pattern = %s,
                                filename_pattern = %s,
                                storage_bucket = %s,
                                output_format = %s,
                                rule_name = %s,
                                is_active = %s,
                                create_folder_if_missing = %s,
                                notes = %s,
                                created_by = COALESCE(%s, created_by)
                            WHERE path_rule_id = %s
                            """,
                            (
                                rule.document_type_code,
                                rule.local_root,
                                rule.fallback_root,
                                rule.relative_pattern,
                                rule.filename_pattern,
                                rule.storage_bucket,
                                rule.output_format.value,
                                rule.rule_name,
                                rule.is_active,
                                rule.create_folder_if_missing,
                                rule.notes,
                                rule.created_by,
                                path_rule_id,
                            ),
                        )

                    if rule.is_active:
                        self._activate_path_rule_cursor(cur, rule.document_type_code, path_rule_id)

                    conn.commit()
                    return self._get_path_rule_by_id(path_rule_id) or DocumentPathRule(
                        path_rule_id=path_rule_id,
                        document_type_code=rule.document_type_code,
                        local_root=rule.local_root,
                        fallback_root=rule.fallback_root,
                        relative_pattern=rule.relative_pattern,
                        filename_pattern=rule.filename_pattern,
                        storage_bucket=rule.storage_bucket,
                        output_format=rule.output_format,
                        rule_name=rule.rule_name,
                        is_active=rule.is_active,
                        create_folder_if_missing=rule.create_folder_if_missing,
                        notes=rule.notes,
                        created_by=rule.created_by,
                    )
        except psycopg2.Error as exc:
            if self._is_missing_schema_error(exc):
                raise RuntimeError("Document control schema is not available. Apply database_schema.sql first.") from exc
            raise

    def activate_path_rule(self, path_rule_id: int) -> None:
        rule = self._get_path_rule_by_id(path_rule_id)
        if rule is None:
            raise ValueError(f"Path rule #{path_rule_id} was not found.")

        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    self._activate_path_rule_cursor(cur, rule.document_type_code, path_rule_id)
                    conn.commit()
        except psycopg2.Error as exc:
            if self._is_missing_schema_error(exc):
                raise RuntimeError("Document control schema is not available. Apply database_schema.sql first.") from exc
            raise

    def list_generated_documents(
        self,
        document_type_code: str | None = None,
        source_record_id: str | None = None,
    ) -> list[GeneratedDocumentRecord]:
        query = """
            SELECT
                generated_document_id,
                document_type_code,
                output_format,
                relative_path,
                absolute_path,
                rendered_filename,
                template_version_ids,
                context_snapshot,
                source_record_type,
                source_record_id,
                created_at,
                created_by
            FROM public.app_generated_document
            WHERE (%s IS NULL OR document_type_code = %s)
              AND (%s IS NULL OR source_record_id = %s)
            ORDER BY created_at DESC, generated_document_id DESC
        """
        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        query,
                        (document_type_code, document_type_code, source_record_id, source_record_id),
                    )
                    return [self._map_generated_document(row) for row in cur.fetchall()]
        except psycopg2.Error as exc:
            if self._is_missing_schema_error(exc):
                return []
            raise

    def save_generated_document(self, record: GeneratedDocumentRecord) -> GeneratedDocumentRecord:
        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO public.app_generated_document (
                            document_type_code,
                            output_format,
                            relative_path,
                            absolute_path,
                            rendered_filename,
                            template_version_ids,
                            context_snapshot,
                            source_record_type,
                            source_record_id,
                            created_by
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING generated_document_id
                        """,
                        (
                            record.document_type_code,
                            record.output_format.value,
                            record.relative_path,
                            record.absolute_path,
                            record.rendered_filename,
                            Json(list(record.template_version_ids)),
                            Json(record.context_snapshot),
                            record.source_record_type,
                            record.source_record_id,
                            record.created_by,
                        ),
                    )
                    generated_document_id = int(cur.fetchone()["generated_document_id"])
                    conn.commit()
                    return self._get_generated_document_by_id(generated_document_id) or GeneratedDocumentRecord(
                        generated_document_id=generated_document_id,
                        document_type_code=record.document_type_code,
                        output_format=record.output_format,
                        relative_path=record.relative_path,
                        absolute_path=record.absolute_path,
                        rendered_filename=record.rendered_filename,
                        template_version_ids=record.template_version_ids,
                        context_snapshot=record.context_snapshot,
                        source_record_type=record.source_record_type,
                        source_record_id=record.source_record_id,
                        created_by=record.created_by,
                    )
        except psycopg2.Error as exc:
            if self._is_missing_schema_error(exc):
                raise RuntimeError("Document control schema is not available. Apply database_schema.sql first.") from exc
            raise

    def _activate_template_cursor(self, cur, template_id: int, template_version_id: int) -> None:
        cur.execute(
            """
            SELECT document_type_code, template_kind
            FROM public.app_document_template
            WHERE template_id = %s
            """,
            (template_id,),
        )
        template_row = cur.fetchone()
        if not template_row:
            raise ValueError(f"Template #{template_id} was not found.")
        cur.execute(
            """
            SELECT version_number
            FROM public.app_document_template_version
            WHERE template_version_id = %s
            """,
            (template_version_id,),
        )
        version_row = cur.fetchone()
        current_version_number = int(version_row["version_number"]) if version_row else 0
        cur.execute(
            """
            UPDATE public.app_document_template
            SET is_active = TRUE,
                active_version_id = %s,
                current_version_number = %s,
                updated_at = now()
            WHERE template_id = %s
            """,
            (template_version_id, current_version_number, template_id),
        )

    def _activate_path_rule_cursor(self, cur, document_type_code: str, path_rule_id: int) -> None:
        cur.execute(
            """
            UPDATE public.app_document_path_rule
            SET is_active = FALSE
            WHERE document_type_code = %s
            """,
            (document_type_code,),
        )
        cur.execute(
            """
            UPDATE public.app_document_path_rule
            SET is_active = TRUE
            WHERE path_rule_id = %s
            """,
            (path_rule_id,),
        )

    def _get_path_rule_by_id(self, path_rule_id: int) -> DocumentPathRule | None:
        query = """
            SELECT
                path_rule_id,
                document_type_code,
                local_root,
                fallback_root,
                relative_pattern,
                filename_pattern,
                storage_bucket,
                output_format,
                rule_name,
                is_active,
                create_folder_if_missing,
                notes,
                created_at,
                created_by
            FROM public.app_document_path_rule
            WHERE path_rule_id = %s
        """
        with self._connection_factory() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (path_rule_id,))
                row = cur.fetchone()
                return self._map_path_rule(row) if row else None

    def _get_generated_document_by_id(self, generated_document_id: int) -> GeneratedDocumentRecord | None:
        query = """
            SELECT
                generated_document_id,
                document_type_code,
                output_format,
                relative_path,
                absolute_path,
                rendered_filename,
                template_version_ids,
                context_snapshot,
                source_record_type,
                source_record_id,
                created_at,
                created_by
            FROM public.app_generated_document
            WHERE generated_document_id = %s
        """
        with self._connection_factory() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (generated_document_id,))
                row = cur.fetchone()
                return self._map_generated_document(row) if row else None

    def _map_document_type(self, row: dict) -> DocumentTypeDefinition:
        return DocumentTypeDefinition(
            document_type_id=int(row["document_type_id"]),
            document_type_code=str(row["document_type_code"]),
            display_name=str(row["display_name"]),
            description=row.get("description"),
            default_output_format=DocumentOutputFormat(str(row.get("default_output_format") or "html")),
            is_active=bool(row.get("is_active")),
            created_at=row.get("created_at"),
        )

    def _map_template_summary(self, row: dict) -> DocumentTemplateSummary:
        return DocumentTemplateSummary(
            template_id=int(row["template_id"]),
            document_type_code=str(row["document_type_code"]),
            kind=DocumentTemplateKind(str(row["template_kind"])),
            template_name=str(row["template_name"]),
            content_format=str(row.get("content_format") or "html"),
            active_version_id=(
                int(row["active_version_id"]) if row.get("active_version_id") is not None else None
            ),
            active_version_number=(
                int(row["active_version_number"]) if row.get("active_version_number") is not None else None
            ),
            is_active=bool(row.get("is_active")),
            notes=row.get("notes"),
            updated_at=row.get("updated_at"),
        )

    def _map_template_version(self, row: dict) -> DocumentTemplateVersion:
        return DocumentTemplateVersion(
            template_version_id=int(row["template_version_id"]),
            template_id=int(row["template_id"]),
            document_type_code=str(row["document_type_code"]),
            kind=DocumentTemplateKind(str(row["template_kind"])),
            template_name=str(row["template_name"]),
            version_number=int(row["version_number"]),
            subject_line=row.get("subject_line"),
            body_content=str(row.get("body_content") or ""),
            content_format=str(row.get("content_format") or "html"),
            output_format=DocumentOutputFormat(str(row.get("output_format") or "html")),
            token_schema=tuple(str(item) for item in (row.get("token_schema") or [])),
            change_summary=row.get("change_summary"),
            notes=row.get("notes"),
            is_active=bool(row.get("is_active")),
            created_at=row.get("created_at"),
            created_by=row.get("created_by"),
        )

    def _map_path_rule(self, row: dict) -> DocumentPathRule:
        return DocumentPathRule(
            path_rule_id=int(row["path_rule_id"]),
            document_type_code=str(row["document_type_code"]),
            local_root=row.get("local_root"),
            fallback_root=row.get("fallback_root"),
            relative_pattern=str(row.get("relative_pattern") or ""),
            filename_pattern=str(row.get("filename_pattern") or ""),
            storage_bucket=str(row.get("storage_bucket") or ""),
            output_format=DocumentOutputFormat(str(row.get("output_format") or "html")),
            rule_name=str(row.get("rule_name") or "Default"),
            is_active=bool(row.get("is_active")),
            create_folder_if_missing=bool(row.get("create_folder_if_missing", True)),
            notes=row.get("notes"),
            created_at=row.get("created_at"),
            created_by=row.get("created_by"),
        )

    def _map_template_default(self, row: dict) -> DocumentTemplateDefault:
        return DocumentTemplateDefault(
            template_default_id=(
                int(row["template_default_id"]) if row.get("template_default_id") is not None else None
            ),
            document_type_code=str(row["document_type_code"]),
            kind=DocumentTemplateKind(str(row["template_kind"])),
            usage_context=str(row["usage_context"]),
            template_id=int(row["template_id"]),
            updated_at=row.get("updated_at"),
            updated_by=row.get("updated_by"),
        )

    def _map_generated_document(self, row: dict) -> GeneratedDocumentRecord:
        template_version_ids = tuple(int(value) for value in (row.get("template_version_ids") or []))
        context_snapshot = dict(row.get("context_snapshot") or {})
        return GeneratedDocumentRecord(
            generated_document_id=int(row["generated_document_id"]),
            document_type_code=str(row["document_type_code"]),
            output_format=DocumentOutputFormat(str(row.get("output_format") or "html")),
            relative_path=str(row.get("relative_path") or ""),
            absolute_path=str(row.get("absolute_path") or ""),
            rendered_filename=str(row.get("rendered_filename") or ""),
            template_version_ids=template_version_ids,
            context_snapshot=context_snapshot,
            source_record_type=row.get("source_record_type"),
            source_record_id=row.get("source_record_id"),
            created_at=row.get("created_at"),
            created_by=row.get("created_by"),
        )

    def _is_missing_schema_error(self, exc: psycopg2.Error) -> bool:
        return exc.pgcode in {errorcodes.UNDEFINED_TABLE, errorcodes.UNDEFINED_COLUMN}
