from __future__ import annotations

import re


_TOKEN_PATTERN = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_INVALID_FILENAME_CHARS = re.compile(r'[\\/*?:"<>|]+')
_WHITESPACE_PATTERN = re.compile(r"\s+")


def extract_tokens(content: str) -> set[str]:
    return {match.group(1) for match in _TOKEN_PATTERN.finditer(content or "")}


def render_tokens(content: str, context: dict) -> str:
    def _replace(match: re.Match[str]) -> str:
        token_name = match.group(1)
        value = context.get(token_name)
        if value is None:
            return ""
        return str(value)

    return _TOKEN_PATTERN.sub(_replace, content or "")


def sanitize_filename_part(value: object) -> str:
    text = str(value or "").strip()
    text = _INVALID_FILENAME_CHARS.sub("", text)
    text = _WHITESPACE_PATTERN.sub("_", text)
    text = text.strip(" .")
    return text
