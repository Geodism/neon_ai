from __future__ import annotations

from dataclasses import dataclass
import importlib
import importlib.util
import json
import os
from typing import Any

from neon_ai.config import load_neon_env


DEFAULT_PROVIDER = "disabled"
DEFAULT_OPENAI_MODEL = "gpt-5-mini"
DEFAULT_OLLAMA_MODEL = "llama3"
ALLOWED_PROVIDERS = {"disabled", "openai", "ollama"}
LLM_PROVIDER_ENV = "NEON_LLM_PROVIDER"
LLM_SAFE_MODE_ENV = "NEON_LLM_SAFE_MODE"
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
OPENAI_MODEL_ENV = "OPENAI_MODEL"
OLLAMA_MODEL_ENV = "OLLAMA_MODEL"


@dataclass(frozen=True)
class LLMProviderConfig:
    provider: str
    model: str | None
    safe_mode: bool
    openai_key_present: bool
    ollama_model: str | None
    openai_model: str | None
    notes: str


@dataclass(frozen=True)
class LLMResult:
    success: bool
    provider: str
    model: str | None
    text: str | None
    json_data: dict[str, Any] | list[Any] | None
    input_tokens: int | None
    output_tokens: int | None
    estimated_cost: float | None
    error: str | None
    raw_text: str | None = None


@dataclass(frozen=True)
class LLMHealthCheckResult:
    success: bool
    provider: str
    model: str | None
    status: str
    safe_mode: bool
    openai_key_present: bool
    error: str | None


def get_llm_provider_config() -> LLMProviderConfig:
    load_neon_env()
    provider = str(os.getenv(LLM_PROVIDER_ENV, DEFAULT_PROVIDER)).strip().lower() or DEFAULT_PROVIDER
    if provider not in ALLOWED_PROVIDERS:
        provider = DEFAULT_PROVIDER
    safe_mode = _truthy_env(LLM_SAFE_MODE_ENV, default=True)
    openai_model = str(os.getenv(OPENAI_MODEL_ENV, DEFAULT_OPENAI_MODEL)).strip() or DEFAULT_OPENAI_MODEL
    ollama_model = str(os.getenv(OLLAMA_MODEL_ENV, DEFAULT_OLLAMA_MODEL)).strip() or DEFAULT_OLLAMA_MODEL
    openai_key_present = bool(str(os.getenv(OPENAI_API_KEY_ENV, "")).strip())
    selected_model = None
    if provider == "openai":
        selected_model = openai_model
    elif provider == "ollama":
        selected_model = ollama_model
    notes = _provider_notes(provider=provider, safe_mode=safe_mode, openai_key_present=openai_key_present)
    return LLMProviderConfig(
        provider=provider,
        model=selected_model,
        safe_mode=safe_mode,
        openai_key_present=openai_key_present,
        ollama_model=ollama_model,
        openai_model=openai_model,
        notes=notes,
    )


def get_llm_client(config: LLMProviderConfig | None = None):
    config = config or get_llm_provider_config()
    if config.provider == "disabled":
        return None
    if config.provider == "openai":
        if not config.openai_key_present:
            raise RuntimeError(f"{OPENAI_API_KEY_ENV} is required when {LLM_PROVIDER_ENV}=openai.")
        openai_module = _import_optional_module("openai")
        if openai_module is None:
            raise RuntimeError("OpenAI provider selected, but the 'openai' SDK is not installed in this environment.")
        client_class = getattr(openai_module, "OpenAI", None)
        if client_class is None:
            raise RuntimeError("OpenAI provider selected, but the installed 'openai' package does not expose OpenAI().")
        return client_class(api_key=os.getenv(OPENAI_API_KEY_ENV))
    if config.provider == "ollama":
        ollama_module = _import_optional_module("ollama")
        if ollama_module is None:
            raise RuntimeError("Ollama provider selected, but the 'ollama' package is not installed in this environment.")
        return ollama_module
    raise RuntimeError(f"Unsupported LLM provider '{config.provider}'.")


def health_check(*, allow_live_call: bool = False) -> LLMHealthCheckResult:
    config = get_llm_provider_config()
    if config.provider == "disabled":
        return LLMHealthCheckResult(
            success=True,
            provider=config.provider,
            model=config.model,
            status="disabled_mode_ready",
            safe_mode=config.safe_mode,
            openai_key_present=config.openai_key_present,
            error=None,
        )
    if config.provider == "openai":
        if not config.openai_key_present:
            return LLMHealthCheckResult(
                success=False,
                provider=config.provider,
                model=config.model,
                status="openai_missing_api_key",
                safe_mode=config.safe_mode,
                openai_key_present=False,
                error=f"{OPENAI_API_KEY_ENV} is not set.",
            )
        if _import_optional_module("openai") is None:
            return LLMHealthCheckResult(
                success=False,
                provider=config.provider,
                model=config.model,
                status="openai_sdk_missing",
                safe_mode=config.safe_mode,
                openai_key_present=True,
                error="The 'openai' SDK is not installed in this environment.",
            )
        if config.safe_mode and not allow_live_call:
            return LLMHealthCheckResult(
                success=True,
                provider=config.provider,
                model=config.model,
                status="openai_configured_no_live_probe_safe_mode",
                safe_mode=True,
                openai_key_present=True,
                error=None,
            )
        return LLMHealthCheckResult(
            success=True,
            provider=config.provider,
            model=config.model,
            status="openai_configured_no_live_probe",
            safe_mode=config.safe_mode,
            openai_key_present=True,
            error=None,
        )
    if config.provider == "ollama":
        if _import_optional_module("ollama") is None:
            return LLMHealthCheckResult(
                success=False,
                provider=config.provider,
                model=config.model,
                status="ollama_sdk_missing",
                safe_mode=config.safe_mode,
                openai_key_present=config.openai_key_present,
                error="The 'ollama' package is not installed in this environment.",
            )
        if config.safe_mode and not allow_live_call:
            return LLMHealthCheckResult(
                success=True,
                provider=config.provider,
                model=config.model,
                status="ollama_configured_no_live_probe_safe_mode",
                safe_mode=True,
                openai_key_present=config.openai_key_present,
                error=None,
            )
        return LLMHealthCheckResult(
            success=True,
            provider=config.provider,
            model=config.model,
            status="ollama_configured_no_live_probe",
            safe_mode=config.safe_mode,
            openai_key_present=config.openai_key_present,
            error=None,
        )
    return LLMHealthCheckResult(
        success=False,
        provider=config.provider,
        model=config.model,
        status="unsupported_provider",
        safe_mode=config.safe_mode,
        openai_key_present=config.openai_key_present,
        error=f"Unsupported provider '{config.provider}'.",
    )


def generate_text(
    prompt: str,
    *,
    system_prompt: str | None = None,
    temperature: float = 0.0,
    max_output_tokens: int | None = None,
    allow_live_call: bool = False,
) -> LLMResult:
    config = get_llm_provider_config()
    if config.provider == "disabled":
        return _disabled_result(config, error="LLM provider is disabled.")
    if config.safe_mode and not allow_live_call:
        return _disabled_result(config, error="LLM safe mode is enabled. Live provider calls are blocked.")
    if config.provider == "openai":
        return _generate_text_openai(
            config=config,
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
    if config.provider == "ollama":
        return _generate_text_ollama(
            config=config,
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=temperature,
        )
    return _disabled_result(config, error=f"Unsupported provider '{config.provider}'.")


def extract_json(
    prompt: str,
    *,
    system_prompt: str | None = None,
    allow_live_call: bool = False,
) -> LLMResult:
    full_prompt = (
        f"{prompt.strip()}\n\n"
        "Respond only with valid JSON. Do not add markdown, backticks, or commentary."
    )
    result = generate_text(
        full_prompt,
        system_prompt=system_prompt,
        allow_live_call=allow_live_call,
    )
    if not result.success:
        return result
    raw_text = str(result.text or "").strip()
    try:
        parsed = json.loads(raw_text)
        return LLMResult(
            success=True,
            provider=result.provider,
            model=result.model,
            text=result.text,
            json_data=parsed,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            estimated_cost=result.estimated_cost,
            error=None,
            raw_text=raw_text,
        )
    except Exception as exc:
        return LLMResult(
            success=False,
            provider=result.provider,
            model=result.model,
            text=result.text,
            json_data=None,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            estimated_cost=result.estimated_cost,
            error=f"Invalid JSON response: {exc}",
            raw_text=raw_text,
        )


def classify_text(
    text: str,
    labels: list[str] | tuple[str, ...],
    *,
    system_prompt: str | None = None,
    allow_live_call: bool = False,
) -> LLMResult:
    label_list = [str(label).strip() for label in labels if str(label).strip()]
    prompt = (
        "Classify the following text into exactly one of these labels: "
        f"{', '.join(label_list)}.\n\n"
        "Return only the label.\n\n"
        f"Text:\n{text}"
    )
    return generate_text(
        prompt,
        system_prompt=system_prompt,
        allow_live_call=allow_live_call,
    )


def _generate_text_openai(
    *,
    config: LLMProviderConfig,
    prompt: str,
    system_prompt: str | None,
    temperature: float,
    max_output_tokens: int | None,
) -> LLMResult:
    try:
        client = get_llm_client(config)
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        response = client.chat.completions.create(
            model=config.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_output_tokens,
        )
        choice = response.choices[0] if getattr(response, "choices", None) else None
        text = ""
        if choice is not None and getattr(choice, "message", None) is not None:
            text = str(choice.message.content or "").strip()
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", None) if usage else None
        output_tokens = getattr(usage, "completion_tokens", None) if usage else None
        return LLMResult(
            success=True,
            provider=config.provider,
            model=config.model,
            text=text,
            json_data=None,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=None,
            error=None,
            raw_text=text,
        )
    except Exception as exc:
        return _error_result(config, str(exc))


def _generate_text_ollama(
    *,
    config: LLMProviderConfig,
    prompt: str,
    system_prompt: str | None,
    temperature: float,
) -> LLMResult:
    try:
        ollama_module = get_llm_client(config)
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        response = ollama_module.chat(
            model=config.model or DEFAULT_OLLAMA_MODEL,
            messages=messages,
            options={"temperature": temperature},
        )
        text = str(((response or {}).get("message") or {}).get("content") or "").strip()
        prompt_eval_count = (response or {}).get("prompt_eval_count")
        eval_count = (response or {}).get("eval_count")
        return LLMResult(
            success=True,
            provider=config.provider,
            model=config.model,
            text=text,
            json_data=None,
            input_tokens=int(prompt_eval_count) if prompt_eval_count is not None else None,
            output_tokens=int(eval_count) if eval_count is not None else None,
            estimated_cost=None,
            error=None,
            raw_text=text,
        )
    except Exception as exc:
        return _error_result(config, f"Ollama provider not wired yet or unavailable: {exc}")


def _disabled_result(config: LLMProviderConfig, *, error: str) -> LLMResult:
    return LLMResult(
        success=False,
        provider=config.provider,
        model=config.model,
        text=None,
        json_data=None,
        input_tokens=None,
        output_tokens=None,
        estimated_cost=None,
        error=error,
        raw_text=None,
    )


def _error_result(config: LLMProviderConfig, error: str) -> LLMResult:
    return LLMResult(
        success=False,
        provider=config.provider,
        model=config.model,
        text=None,
        json_data=None,
        input_tokens=None,
        output_tokens=None,
        estimated_cost=None,
        error=error,
        raw_text=None,
    )


def _import_optional_module(module_name: str):
    if importlib.util.find_spec(module_name) is None:
        return None
    try:
        return importlib.import_module(module_name)
    except Exception:
        return None


def _provider_notes(*, provider: str, safe_mode: bool, openai_key_present: bool) -> str:
    if provider == "disabled":
        return "Disabled mode is the default. No model calls are made."
    if provider == "openai":
        if not openai_key_present:
            return "OpenAI provider selected, but the API key is missing."
        if safe_mode:
            return "OpenAI provider is configured, but safe mode blocks live calls unless explicitly allowed."
        return "OpenAI provider is configured for future controlled use."
    if provider == "ollama":
        if safe_mode:
            return "Ollama provider is configured, but safe mode blocks live calls unless explicitly allowed."
        return "Ollama provider is configured for future controlled use."
    return "Unsupported provider configuration."


def _truthy_env(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}
