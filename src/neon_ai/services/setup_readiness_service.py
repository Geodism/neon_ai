from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from neon_ai.config import load_neon_env
from neon_ai.database.connection import get_connection
from neon_ai.services.llm_provider_service import get_safe_model_config_summary


_SECRET_NAMES = {
    "OPENAI_API_KEY",
    "SMTP_PASSWORD",
    "SMTP_PASS",
    "GMAIL_APP_PASSWORD",
    "EMAIL_APP_PASSWORD",
    "SENDGRID_API_KEY",
}

_EMAIL_SENDER_ENV_NAMES = (
    "NEON_SENDER_EMAIL",
    "SMTP_FROM_EMAIL",
    "SMTP_USER",
    "GMAIL_USER",
    "EMAIL_ADDRESS",
    "PERSONAL_EMAIL",
)

_COMPANY_ENV_NAMES = ("NEON_COMPANY_NAME", "COMPANY_NAME", "BUSINESS_NAME")
_OPERATOR_ENV_NAMES = ("NEON_OPERATOR_NAME", "OPERATOR_NAME")
_REPLY_TO_ENV_NAMES = ("NEON_REPLY_TO_EMAIL", "REPLY_TO_EMAIL", "SMTP_REPLY_TO")
_SENDER_NAME_ENV_NAMES = ("NEON_SENDER_DISPLAY_NAME", "SENDER_DISPLAY_NAME", "SMTP_SENDER_NAME")
_SIGNATURE_ENV_NAMES = ("NEON_DEFAULT_SIGNATURE", "EMAIL_SIGNATURE", "DEFAULT_EMAIL_SIGNATURE")

_STORAGE_ROOT_ENV_NAMES = ("NEON_STORAGE_ROOT", "STORAGE_ROOT", "DOCUMENT_STORAGE_ROOT")
_STAGING_ROOT_ENV_NAMES = ("NEON_STAGING_ROOT", "STAGING_ROOT", "DOCUMENT_STAGING_ROOT")
_DOCUMENT_OUTPUT_ROOT_ENV_NAMES = (
    "NEON_DOCUMENT_OUTPUT_ROOT",
    "DOCUMENT_OUTPUT_ROOT",
    "GENERATED_DOCUMENTS_ROOT",
)


def get_setup_readiness_summary() -> dict[str, Any]:
    load_neon_env()
    warnings: list[str] = []

    company_operator = _check_company_operator()
    email = check_email_config()
    llm = check_llm_config()
    database = check_database_config()
    storage = check_storage_paths()
    safety = check_safety_mode()

    for section in (company_operator, email, llm, database, storage, safety):
        warnings.extend(str(item) for item in section.get("warnings", []) if item)

    ready_for_private_test = all(
        [
            bool(database.get("configured")),
            bool(database.get("connected")),
            bool(email.get("send_mode_enabled")),
            bool(llm.get("configured")),
            not bool(storage.get("obvious_missing_path")),
            bool(safety.get("gateway_disabled")),
        ]
    )
    if not ready_for_private_test:
        warnings.append(
            "Private-test readiness is not fully green yet. Review the missing or warning items before real runs."
        )

    return {
        "company_operator": company_operator,
        "email": email,
        "llm": llm,
        "database": database,
        "storage": storage,
        "safety": safety,
        "warnings": _dedupe_preserve_order(warnings),
        "ready_for_private_test": ready_for_private_test,
    }


def check_email_config() -> dict[str, Any]:
    load_neon_env()
    warnings: list[str] = []

    smtp_host = _env_first(("SMTP_HOST", "SMTP_SERVER", "MAIL_HOST"))
    smtp_port = _env_first(("SMTP_PORT", "MAIL_PORT"))
    smtp_user = _env_first(("SMTP_USER", "GMAIL_USER", "EMAIL_USER"))
    smtp_password_present = any(_env_bool_present(name) for name in _SECRET_NAMES if "SMTP" in name or "EMAIL" in name or "GMAIL" in name)
    sender_email = _env_first(_EMAIL_SENDER_ENV_NAMES)
    allowlist = _parse_email_list(os.environ.get("NEON_EMAIL_ALLOWLIST"))
    test_mode_enabled = _truthy(os.environ.get("NEON_EMAIL_TEST_MODE"))
    private_operator_send_mode_enabled = _truthy(os.environ.get("NEON_PRIVATE_OPERATOR_SEND_MODE"))
    send_mode_enabled = bool(private_operator_send_mode_enabled or test_mode_enabled)
    send_mode = (
        "private_operator"
        if private_operator_send_mode_enabled
        else "test"
        if test_mode_enabled
        else "disabled"
    )

    smtp_configured = bool((smtp_host and smtp_port and smtp_user and smtp_password_present) or (smtp_user and smtp_password_present))
    if not smtp_configured:
        warnings.append("SMTP/Gmail outbox configuration is incomplete or source unknown.")
    if not sender_email:
        warnings.append("Sender email is not configured or source unknown.")
    if not send_mode_enabled:
        warnings.append("Level 3 send mode is disabled. Enable NEON_EMAIL_TEST_MODE=1 or NEON_PRIVATE_OPERATOR_SEND_MODE=1.")
    if test_mode_enabled and not private_operator_send_mode_enabled and not allowlist:
        warnings.append("NEON_EMAIL_ALLOWLIST is empty. Test mode requires an allowlisted recipient.")

    return {
        "smtp_configured": smtp_configured,
        "smtp_host_configured": bool(smtp_host),
        "smtp_port_configured": bool(smtp_port),
        "smtp_user_configured": bool(smtp_user),
        "smtp_password_configured": bool(smtp_password_present),
        "sender_email_configured": bool(sender_email),
        "sender_email_masked": mask_email(sender_email) if sender_email else "Not configured",
        "test_mode_enabled": test_mode_enabled,
        "test_mode": "On" if test_mode_enabled else "Off",
        "private_operator_send_mode_enabled": private_operator_send_mode_enabled,
        "private_operator_send_mode": "On" if private_operator_send_mode_enabled else "Off",
        "send_mode": send_mode,
        "send_mode_enabled": send_mode_enabled,
        "allowlist_required": bool(test_mode_enabled and not private_operator_send_mode_enabled),
        "allowlist_optional": bool(private_operator_send_mode_enabled),
        "allowlist_count": len(allowlist),
        "allowlist_masked": mask_email_list(allowlist),
        "allowlist_source": "NEON_EMAIL_ALLOWLIST in .env or process environment; required in test mode, optional in private operator mode",
        "last_send_smoke_result": "Not recorded / source unknown",
        "warnings": warnings,
    }


def check_llm_config() -> dict[str, Any]:
    load_neon_env()
    warnings: list[str] = []
    try:
        summary = get_safe_model_config_summary()
    except Exception as exc:
        return {
            "configured": False,
            "summary": {},
            "warnings": [f"LLM configuration could not be loaded: {exc}"],
        }

    lanes = {
        lane_name: dict(summary.get(lane_name) or {})
        for lane_name in ("local", "remote_fast", "remote_strong")
    }
    configured = any(
        bool(lane.get("provider")) and str(lane.get("provider")).lower() != "disabled" and bool(lane.get("model"))
        for lane in lanes.values()
    )
    if not configured:
        warnings.append("No local or remote LLM lane appears configured.")

    return {
        "configured": configured,
        "safe_mode": bool(summary.get("safe_mode")),
        "remote_escalation_only": bool(summary.get("remote_escalation_only")),
        "max_remote_calls_per_run": summary.get("max_remote_calls_per_run"),
        "max_remote_strong_calls_per_run": summary.get("max_remote_strong_calls_per_run"),
        "max_remote_input_chars": summary.get("max_remote_input_chars"),
        "lanes": lanes,
        "warnings": warnings,
    }


def check_database_config() -> dict[str, Any]:
    load_neon_env()
    db_url = os.environ.get("DB_URL") or os.environ.get("DATABASE_URL")
    redacted = redact_database_url(db_url)
    warnings: list[str] = []
    connected = False
    error: str | None = None
    if not db_url:
        warnings.append("DB_URL / DATABASE_URL is not configured.")
    else:
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1 AS ok")
                    row = cur.fetchone()
                    connected = bool(row and int(row.get("ok") or 0) == 1)
        except Exception as exc:
            error = _safe_error(exc)
            warnings.append(f"Database connection check failed: {error}")

    return {
        "configured": bool(db_url),
        "connected": connected,
        "target_type": _database_target_type(db_url),
        "project_ref": _detect_supabase_project_ref(db_url),
        "redacted_url": redacted,
        "error": error,
        "warnings": warnings,
    }


def check_storage_paths() -> dict[str, Any]:
    load_neon_env()
    warnings: list[str] = []
    env_paths = [
        _path_record("storage_root", _env_first(_STORAGE_ROOT_ENV_NAMES), "environment"),
        _path_record("staging_root", _env_first(_STAGING_ROOT_ENV_NAMES), "environment"),
        _path_record("document_output_root", _env_first(_DOCUMENT_OUTPUT_ROOT_ENV_NAMES), "environment"),
    ]
    env_paths = [record for record in env_paths if record.get("path")]

    path_rule_records, path_rule_warnings = _load_document_path_rule_records()
    warnings.extend(path_rule_warnings)
    if not env_paths and not path_rule_records:
        warnings.append("No storage/document path roots were found in env or document path rules.")

    all_records = env_paths + path_rule_records
    obvious_missing_path = any(record.get("path") and not record.get("exists") and not record.get("parent_exists") for record in all_records)

    return {
        "env_paths": env_paths,
        "document_path_rules": path_rule_records,
        "obvious_missing_path": obvious_missing_path,
        "warnings": warnings,
    }


def check_safety_mode() -> dict[str, Any]:
    load_neon_env()
    gateway_disabled = _truthy(os.environ.get("NEON_DISABLE_GATEWAY"))
    private_brain_disabled = _truthy(os.environ.get("NEON_DISABLE_PRIVATE_BRAIN"))
    automation_enabled = _truthy(os.environ.get("NEON_AUTOMATION_ENABLED"))
    email_test_mode = _truthy(os.environ.get("NEON_EMAIL_TEST_MODE"))
    private_operator_send_mode = _truthy(os.environ.get("NEON_PRIVATE_OPERATOR_SEND_MODE"))
    legacy_direct_send_enabled = _truthy(os.environ.get("NEON_ENABLE_LEGACY_DIRECT_SEND"))
    warnings: list[str] = []
    if not gateway_disabled:
        warnings.append("NEON_DISABLE_GATEWAY is not enabled. Private testing usually starts with gateway disabled.")
    if automation_enabled:
        warnings.append("NEON_AUTOMATION_ENABLED is on. Confirm this is intentional before private-test runs.")
    if not email_test_mode and not private_operator_send_mode:
        warnings.append("No Level 3 send mode is enabled. Sends are blocked until test mode or private operator mode is on.")
    if legacy_direct_send_enabled:
        warnings.append(
            "NEON_ENABLE_LEGACY_DIRECT_SEND is on. Legacy direct SMTP paths can bypass the Level 3 approved-send wrapper."
        )

    return {
        "NEON_DISABLE_GATEWAY": _mode_label(gateway_disabled, os.environ.get("NEON_DISABLE_GATEWAY")),
        "NEON_DISABLE_PRIVATE_BRAIN": _mode_label(private_brain_disabled, os.environ.get("NEON_DISABLE_PRIVATE_BRAIN")),
        "NEON_AUTOMATION_ENABLED": _mode_label(automation_enabled, os.environ.get("NEON_AUTOMATION_ENABLED")),
        "NEON_EMAIL_TEST_MODE": _mode_label(email_test_mode, os.environ.get("NEON_EMAIL_TEST_MODE")),
        "NEON_PRIVATE_OPERATOR_SEND_MODE": _mode_label(
            private_operator_send_mode,
            os.environ.get("NEON_PRIVATE_OPERATOR_SEND_MODE"),
        ),
        "NEON_ENABLE_LEGACY_DIRECT_SEND": _mode_label(
            legacy_direct_send_enabled,
            os.environ.get("NEON_ENABLE_LEGACY_DIRECT_SEND"),
        ),
        "REMOTE_ESCALATION_ONLY": _mode_label(_truthy(os.environ.get("REMOTE_ESCALATION_ONLY")), os.environ.get("REMOTE_ESCALATION_ONLY")),
        "ALLOW_LIVE_LOCAL_LLM": _mode_label(_truthy(os.environ.get("ALLOW_LIVE_LOCAL_LLM")), os.environ.get("ALLOW_LIVE_LOCAL_LLM")),
        "ALLOW_LIVE_REMOTE_FAST_LLM": _mode_label(
            _truthy(os.environ.get("ALLOW_LIVE_REMOTE_FAST_LLM")),
            os.environ.get("ALLOW_LIVE_REMOTE_FAST_LLM"),
        ),
        "ALLOW_LIVE_REMOTE_STRONG_LLM": _mode_label(
            _truthy(os.environ.get("ALLOW_LIVE_REMOTE_STRONG_LLM")),
            os.environ.get("ALLOW_LIVE_REMOTE_STRONG_LLM"),
        ),
        "allowlist_mode": "Configured" if _parse_email_list(os.environ.get("NEON_EMAIL_ALLOWLIST")) else "Not configured",
        "level_3_send_mode": "private_operator" if private_operator_send_mode else "test" if email_test_mode else "disabled",
        "gateway_disabled": gateway_disabled,
        "private_brain_disabled": private_brain_disabled,
        "automation_enabled": automation_enabled,
        "legacy_direct_send_enabled": legacy_direct_send_enabled,
        "warnings": warnings,
    }


def redact_secret(value: Any) -> str:
    if value is None or str(value).strip() == "":
        return "Not configured"
    return "<redacted>"


def mask_email_list(values: list[str] | tuple[str, ...] | str | None) -> list[str]:
    if isinstance(values, str):
        parsed = _parse_email_list(values)
    else:
        parsed = [str(value).strip() for value in (values or []) if str(value).strip()]
    return [mask_email(value) for value in parsed]


def mask_email(value: Any) -> str:
    raw = str(value or "").strip()
    if "@" not in raw:
        return "Not configured" if not raw else "<masked>"
    local, domain = raw.rsplit("@", 1)
    if not local or not domain:
        return "<masked>"
    return f"{local[:1]}***@{domain.lower()}"


def redact_database_url(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "Not configured"
    try:
        parsed = urlparse(raw)
        host = parsed.hostname or "unknown-host"
        port = f":{parsed.port}" if parsed.port else ""
        database = parsed.path or ""
        return f"{parsed.scheme or 'db'}://<redacted>@{host}{port}{database}"
    except Exception:
        return "<redacted database url>"


def _check_company_operator() -> dict[str, Any]:
    company_name = _env_first(_COMPANY_ENV_NAMES)
    operator_name = _env_first(_OPERATOR_ENV_NAMES)
    reply_to = _env_first(_REPLY_TO_ENV_NAMES)
    sender_display_name = _env_first(_SENDER_NAME_ENV_NAMES)
    signature_present = bool(_env_first(_SIGNATURE_ENV_NAMES))
    warnings: list[str] = []
    if not company_name:
        warnings.append("Company name is not configured or source unknown.")
    if not operator_name:
        warnings.append("Operator name is not configured or source unknown.")

    return {
        "company_name": company_name or "Not configured / source unknown",
        "operator_name": operator_name or "Not configured / source unknown",
        "reply_to_email": mask_email(reply_to) if reply_to else "Not configured / source unknown",
        "sender_display_name": sender_display_name or "Not configured / source unknown",
        "default_signature_source": "Configured" if signature_present else "Not configured / source unknown",
        "warnings": warnings,
    }


def _load_document_path_rule_records() -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    warnings: list[str] = []
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        rule_name,
                        document_type_code,
                        local_root,
                        fallback_root,
                        is_active,
                        create_folder_if_missing
                    FROM public.app_document_path_rule
                    ORDER BY document_type_code, is_active DESC, rule_name
                    """
                )
                rows = cur.fetchall() or []
    except Exception as exc:
        warnings.append(f"Document path rules could not be checked: {_safe_error(exc)}")
        return records, warnings

    for row in rows:
        rule_label = f"{row.get('document_type_code') or 'UNKNOWN'} / {row.get('rule_name') or 'Unnamed rule'}"
        for field_name in ("local_root", "fallback_root"):
            value = row.get(field_name)
            if not value:
                continue
            record = _path_record(
                label=f"{rule_label} {field_name}",
                raw_path=str(value),
                source="app_document_path_rule",
            )
            record["is_active"] = bool(row.get("is_active"))
            record["create_folder_if_missing"] = bool(row.get("create_folder_if_missing"))
            records.append(record)
    return records, warnings


def _path_record(label: str, raw_path: str | None, source: str) -> dict[str, Any]:
    value = str(raw_path or "").strip()
    if not value:
        return {
            "label": label,
            "path": "",
            "source": source,
            "exists": False,
            "parent_exists": False,
            "can_create": False,
        }
    expanded = Path(os.path.expandvars(os.path.expanduser(value)))
    parent = expanded if expanded.exists() and expanded.is_dir() else expanded.parent
    return {
        "label": label,
        "path": str(expanded),
        "source": source,
        "exists": expanded.exists(),
        "parent_exists": parent.exists(),
        "can_create": parent.exists() and os.access(parent, os.W_OK),
    }


def _env_first(names: tuple[str, ...]) -> str:
    for name in names:
        value = str(os.environ.get(name, "")).strip()
        if value:
            return value
    return ""


def _env_bool_present(name: str) -> bool:
    return bool(str(os.environ.get(name, "")).strip())


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _mode_label(is_on: bool, raw_value: str | None) -> str:
    if raw_value is None or str(raw_value).strip() == "":
        return "Not configured"
    return "On" if is_on else "Off"


def _parse_email_list(value: str | None) -> list[str]:
    raw = str(value or "")
    parts = re.split(r"[,;\n]+", raw)
    return [part.strip() for part in parts if part.strip()]


def _database_target_type(db_url: str | None) -> str:
    raw = str(db_url or "").lower()
    if not raw:
        return "Not configured"
    if "supabase" in raw:
        return "Supabase / remote dev"
    if "localhost" in raw or "127.0.0.1" in raw:
        return "Local database"
    return "Configured database"


def _detect_supabase_project_ref(db_url: str | None) -> str:
    raw = str(db_url or "")
    if not raw:
        return "Not configured"
    if "cmhgktqjdxuzrcjbmjzk" in raw:
        return "cmhgktqjdxuzrcjbmjzk"
    match = re.search(r"db\.([a-z0-9]{20})\.supabase\.co", raw, flags=re.IGNORECASE)
    if match:
        return match.group(1)
    user_match = re.search(r"user=postgres\.([a-z0-9]{20})", raw, flags=re.IGNORECASE)
    if user_match:
        return user_match.group(1)
    return "Unknown / not Supabase"


def _safe_error(exc: Exception) -> str:
    message = str(exc)
    for name in _SECRET_NAMES:
        secret = str(os.environ.get(name, "")).strip()
        if secret:
            message = message.replace(secret, "<redacted>")
    db_url = os.environ.get("DB_URL") or os.environ.get("DATABASE_URL")
    if db_url:
        message = message.replace(str(db_url), redact_database_url(str(db_url)))
    return message


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output
