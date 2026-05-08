from __future__ import annotations

from dataclasses import dataclass
import importlib
import importlib.util
import json
import os
from typing import Any

from neon_ai.config import load_neon_env


DEFAULT_PROVIDER = "disabled"
DEFAULT_LOCAL_PROVIDER = "ollama"
DEFAULT_LOCAL_MODEL = "llama3"
DEFAULT_REMOTE_FAST_PROVIDER = "openai"
DEFAULT_REMOTE_FAST_MODEL = "gpt-5.4-mini"
DEFAULT_REMOTE_STRONG_PROVIDER = "openai"
DEFAULT_REMOTE_STRONG_MODEL = "gpt-5.4"
DEFAULT_REMOTE_ESCALATION_ONLY = True
DEFAULT_MAX_REMOTE_CALLS_PER_RUN = 10
DEFAULT_MAX_REMOTE_STRONG_CALLS_PER_RUN = 3
DEFAULT_MAX_REMOTE_INPUT_CHARS = 12000

ALLOWED_PROVIDERS = {"disabled", "openai", "ollama"}
LANE_NAMES = {"disabled", "local", "remote_fast", "remote_strong"}

LLM_PROVIDER_ENV = "NEON_LLM_PROVIDER"
LLM_SAFE_MODE_ENV = "NEON_LLM_SAFE_MODE"
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
OPENAI_MODEL_ENV = "OPENAI_MODEL"
OLLAMA_MODEL_ENV = "OLLAMA_MODEL"

LOCAL_LLM_PROVIDER_ENV = "LOCAL_LLM_PROVIDER"
LOCAL_LLM_MODEL_ENV = "LOCAL_LLM_MODEL"
REMOTE_FAST_LLM_PROVIDER_ENV = "REMOTE_FAST_LLM_PROVIDER"
REMOTE_FAST_LLM_MODEL_ENV = "REMOTE_FAST_LLM_MODEL"
REMOTE_STRONG_LLM_PROVIDER_ENV = "REMOTE_STRONG_LLM_PROVIDER"
REMOTE_STRONG_LLM_MODEL_ENV = "REMOTE_STRONG_LLM_MODEL"
REMOTE_ESCALATION_ONLY_ENV = "REMOTE_ESCALATION_ONLY"
ALLOW_LIVE_LOCAL_LLM_ENV = "ALLOW_LIVE_LOCAL_LLM"
ALLOW_LIVE_REMOTE_FAST_LLM_ENV = "ALLOW_LIVE_REMOTE_FAST_LLM"
ALLOW_LIVE_REMOTE_STRONG_LLM_ENV = "ALLOW_LIVE_REMOTE_STRONG_LLM"
MAX_REMOTE_LLM_CALLS_PER_RUN_ENV = "MAX_REMOTE_LLM_CALLS_PER_RUN"
MAX_REMOTE_STRONG_CALLS_PER_RUN_ENV = "MAX_REMOTE_STRONG_CALLS_PER_RUN"
MAX_REMOTE_INPUT_CHARS_ENV = "MAX_REMOTE_INPUT_CHARS"

# Backward-compatible aliases kept so older control-plane code continues to work.
LLM_LOCAL_PROVIDER_ENV = LOCAL_LLM_PROVIDER_ENV
LLM_REMOTE_PROVIDER_ENV = REMOTE_FAST_LLM_PROVIDER_ENV
LLM_LOCAL_MODEL_ENV = LOCAL_LLM_MODEL_ENV
LLM_REMOTE_MODEL_ENV = REMOTE_FAST_LLM_MODEL_ENV
LLM_ALLOW_LIVE_LOCAL_ENV = ALLOW_LIVE_LOCAL_LLM_ENV
LLM_ALLOW_LIVE_REMOTE_ENV = ALLOW_LIVE_REMOTE_FAST_LLM_ENV


@dataclass(frozen=True)
class ModelLaneConfig:
    lane: str
    provider: str
    model: str | None
    allow_live: bool
    key_present: bool
    available: bool
    max_calls_per_run: int | None
    max_input_chars: int | None
    notes: str


@dataclass(frozen=True)
class ModelConfig:
    safe_mode: bool
    remote_escalation_only: bool
    max_remote_calls_per_run: int
    max_remote_strong_calls_per_run: int
    max_remote_input_chars: int
    local: ModelLaneConfig
    remote_fast: ModelLaneConfig
    remote_strong: ModelLaneConfig
    disabled: ModelLaneConfig


@dataclass(frozen=True)
class LLMProviderConfig:
    provider: str
    model: str | None
    safe_mode: bool
    primary_local_provider: str
    primary_remote_provider: str
    primary_remote_fast_provider: str
    primary_remote_strong_provider: str
    local_model: str | None
    remote_model: str | None
    remote_fast_model: str | None
    remote_strong_model: str | None
    allow_live_local: bool
    allow_live_remote: bool
    allow_live_remote_fast: bool
    allow_live_remote_strong: bool
    remote_escalation_only: bool
    max_remote_calls_per_run: int
    max_remote_strong_calls_per_run: int
    max_remote_input_chars: int
    openai_key_present: bool
    ollama_model: str | None
    openai_model: str | None
    notes: str
    lanes: dict[str, ModelLaneConfig]


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
    lane: str = "disabled"


@dataclass(frozen=True)
class LLMHealthCheckResult:
    success: bool
    provider: str
    model: str | None
    status: str
    safe_mode: bool
    openai_key_present: bool
    error: str | None
    lane: str = "disabled"


def get_model_config() -> ModelConfig:
    load_neon_env()
    safe_mode = _truthy_env(LLM_SAFE_MODE_ENV, default=True)
    openai_key_present = bool(str(os.getenv(OPENAI_API_KEY_ENV, "")).strip())
    remote_escalation_only = _truthy_env(REMOTE_ESCALATION_ONLY_ENV, default=DEFAULT_REMOTE_ESCALATION_ONLY)
    max_remote_calls = _int_env(MAX_REMOTE_LLM_CALLS_PER_RUN_ENV, default=DEFAULT_MAX_REMOTE_CALLS_PER_RUN)
    max_remote_strong_calls = _int_env(
        MAX_REMOTE_STRONG_CALLS_PER_RUN_ENV,
        default=DEFAULT_MAX_REMOTE_STRONG_CALLS_PER_RUN,
    )
    max_remote_input_chars = _int_env(MAX_REMOTE_INPUT_CHARS_ENV, default=DEFAULT_MAX_REMOTE_INPUT_CHARS)

    local_provider = _normalize_provider(
        os.getenv(LOCAL_LLM_PROVIDER_ENV) or os.getenv(LLM_LOCAL_PROVIDER_ENV),
        default=DEFAULT_LOCAL_PROVIDER,
    )
    local_model = _get_lane_model(
        env_name=LOCAL_LLM_MODEL_ENV,
        default=DEFAULT_LOCAL_MODEL,
        fallback=os.getenv(OLLAMA_MODEL_ENV),
    )
    remote_fast_provider = _normalize_provider(
        os.getenv(REMOTE_FAST_LLM_PROVIDER_ENV) or os.getenv(LLM_REMOTE_PROVIDER_ENV),
        default=DEFAULT_REMOTE_FAST_PROVIDER,
    )
    remote_fast_model = _get_lane_model(
        env_name=REMOTE_FAST_LLM_MODEL_ENV,
        default=DEFAULT_REMOTE_FAST_MODEL,
        fallback=os.getenv(OPENAI_MODEL_ENV),
    )
    remote_strong_provider = _normalize_provider(
        os.getenv(REMOTE_STRONG_LLM_PROVIDER_ENV),
        default=DEFAULT_REMOTE_STRONG_PROVIDER,
    )
    remote_strong_model = _get_lane_model(
        env_name=REMOTE_STRONG_LLM_MODEL_ENV,
        default=DEFAULT_REMOTE_STRONG_MODEL,
        fallback=os.getenv(OPENAI_MODEL_ENV),
    )

    local = _build_lane_config(
        lane="local",
        provider=local_provider,
        model=local_model,
        allow_live=_truthy_env(ALLOW_LIVE_LOCAL_LLM_ENV, default=False),
        openai_key_present=openai_key_present,
        max_calls_per_run=None,
        max_input_chars=max_remote_input_chars,
    )
    remote_fast = _build_lane_config(
        lane="remote_fast",
        provider=remote_fast_provider,
        model=remote_fast_model,
        allow_live=_truthy_env(ALLOW_LIVE_REMOTE_FAST_LLM_ENV, default=False),
        openai_key_present=openai_key_present,
        max_calls_per_run=max_remote_calls,
        max_input_chars=max_remote_input_chars,
    )
    remote_strong = _build_lane_config(
        lane="remote_strong",
        provider=remote_strong_provider,
        model=remote_strong_model,
        allow_live=_truthy_env(ALLOW_LIVE_REMOTE_STRONG_LLM_ENV, default=False),
        openai_key_present=openai_key_present,
        max_calls_per_run=max_remote_strong_calls,
        max_input_chars=max_remote_input_chars,
    )
    disabled = ModelLaneConfig(
        lane="disabled",
        provider="disabled",
        model=None,
        allow_live=False,
        key_present=False,
        available=True,
        max_calls_per_run=None,
        max_input_chars=None,
        notes="Disabled lane makes no model calls.",
    )
    return ModelConfig(
        safe_mode=safe_mode,
        remote_escalation_only=remote_escalation_only,
        max_remote_calls_per_run=max_remote_calls,
        max_remote_strong_calls_per_run=max_remote_strong_calls,
        max_remote_input_chars=max_remote_input_chars,
        local=local,
        remote_fast=remote_fast,
        remote_strong=remote_strong,
        disabled=disabled,
    )


def get_llm_provider_config() -> LLMProviderConfig:
    model_config = get_model_config()
    provider = _normalize_provider(os.getenv(LLM_PROVIDER_ENV), default=DEFAULT_PROVIDER)
    selected_lane = _selected_lane_from_provider(provider, model_config)
    selected_config = get_model_config_for_lane(selected_lane, model_config=model_config)
    notes = _provider_notes(provider=provider, model_config=model_config)
    return LLMProviderConfig(
        provider=selected_config.provider,
        model=selected_config.model,
        safe_mode=model_config.safe_mode,
        primary_local_provider=model_config.local.provider,
        primary_remote_provider=model_config.remote_fast.provider,
        primary_remote_fast_provider=model_config.remote_fast.provider,
        primary_remote_strong_provider=model_config.remote_strong.provider,
        local_model=model_config.local.model,
        remote_model=model_config.remote_fast.model,
        remote_fast_model=model_config.remote_fast.model,
        remote_strong_model=model_config.remote_strong.model,
        allow_live_local=model_config.local.allow_live,
        allow_live_remote=model_config.remote_fast.allow_live,
        allow_live_remote_fast=model_config.remote_fast.allow_live,
        allow_live_remote_strong=model_config.remote_strong.allow_live,
        remote_escalation_only=model_config.remote_escalation_only,
        max_remote_calls_per_run=model_config.max_remote_calls_per_run,
        max_remote_strong_calls_per_run=model_config.max_remote_strong_calls_per_run,
        max_remote_input_chars=model_config.max_remote_input_chars,
        openai_key_present=model_config.remote_fast.key_present or model_config.remote_strong.key_present,
        ollama_model=model_config.local.model if model_config.local.provider == "ollama" else None,
        openai_model=model_config.remote_fast.model if model_config.remote_fast.provider == "openai" else None,
        notes=notes,
        lanes={
            "local": model_config.local,
            "remote_fast": model_config.remote_fast,
            "remote_strong": model_config.remote_strong,
            "disabled": model_config.disabled,
        },
    )


def resolve_model_lane(lane: str | None) -> str:
    lane_norm = str(lane or "disabled").strip().lower() or "disabled"
    if lane_norm in LANE_NAMES:
        return lane_norm
    return "disabled"


def get_model_config_for_lane(lane: str, *, model_config: ModelConfig | None = None) -> ModelLaneConfig:
    model_config = model_config or get_model_config()
    lane_norm = resolve_model_lane(lane)
    if lane_norm == "local":
        return model_config.local
    if lane_norm == "remote_fast":
        return model_config.remote_fast
    if lane_norm == "remote_strong":
        return model_config.remote_strong
    return model_config.disabled


def get_model_name_for_lane(lane: str) -> str | None:
    return get_model_config_for_lane(lane).model


def get_provider_for_lane(lane: str) -> str:
    return get_model_config_for_lane(lane).provider


def get_call_budget_for_lane(lane: str) -> int | None:
    return get_model_config_for_lane(lane).max_calls_per_run


def get_local_model_name() -> str | None:
    return get_model_name_for_lane("local")


def get_remote_fast_model_name() -> str | None:
    return get_model_name_for_lane("remote_fast")


def get_remote_strong_model_name() -> str | None:
    return get_model_name_for_lane("remote_strong")


def local_provider_available() -> bool:
    return get_model_config_for_lane("local").available


def remote_fast_provider_available() -> bool:
    return get_model_config_for_lane("remote_fast").available


def remote_strong_provider_available() -> bool:
    return get_model_config_for_lane("remote_strong").available


def get_model_lane_status() -> dict[str, Any]:
    return get_safe_model_config_summary()


def get_provider_status() -> dict[str, Any]:
    return get_safe_model_config_summary()


def get_safe_model_config_summary() -> dict[str, Any]:
    model_config = get_model_config()
    return {
        "safe_mode": model_config.safe_mode,
        "remote_escalation_only": model_config.remote_escalation_only,
        "max_remote_calls_per_run": model_config.max_remote_calls_per_run,
        "max_remote_strong_calls_per_run": model_config.max_remote_strong_calls_per_run,
        "max_remote_input_chars": model_config.max_remote_input_chars,
        "local": _lane_summary(model_config.local),
        "remote_fast": _lane_summary(model_config.remote_fast),
        "remote_strong": _lane_summary(model_config.remote_strong),
    }


def redact_provider_details_safely() -> dict[str, Any]:
    return get_safe_model_config_summary()


def get_llm_client(
    config: LLMProviderConfig | None = None,
    *,
    lane: str | None = None,
    model_config: ModelConfig | None = None,
):
    if lane is not None:
        lane_config = get_model_config_for_lane(lane, model_config=model_config)
        return _get_llm_client_for_lane(lane_config)
    config = config or get_llm_provider_config()
    lane_config = _selected_lane_from_provider(config.provider, get_model_config())
    return _get_llm_client_for_lane(get_model_config_for_lane(lane_config))


def health_check(*, allow_live_call: bool = False, lane: str | None = None) -> LLMHealthCheckResult:
    model_config = get_model_config()
    if lane is not None:
        lane_config = get_model_config_for_lane(lane, model_config=model_config)
        return _health_check_lane(lane_config=lane_config, safe_mode=model_config.safe_mode, allow_live_call=allow_live_call)
    config = get_llm_provider_config()
    lane_name = _selected_lane_from_provider(config.provider, model_config)
    lane_config = get_model_config_for_lane(lane_name, model_config=model_config)
    return _health_check_lane(lane_config=lane_config, safe_mode=model_config.safe_mode, allow_live_call=allow_live_call)


def generate_text(
    prompt: str,
    *,
    system_prompt: str | None = None,
    temperature: float = 0.0,
    max_output_tokens: int | None = None,
    allow_live_call: bool = False,
    lane: str | None = None,
) -> LLMResult:
    model_config = get_model_config()
    lane_name = resolve_model_lane(lane) if lane is not None else _selected_lane_from_provider(get_llm_provider_config().provider, model_config)
    lane_config = get_model_config_for_lane(lane_name, model_config=model_config)
    if lane_config.provider == "disabled":
        return _disabled_result(lane_config, error="LLM provider is disabled.")
    if model_config.safe_mode and not allow_live_call:
        return _disabled_result(lane_config, error="LLM safe mode is enabled. Live provider calls are blocked.")
    if lane_config.provider == "openai":
        return _generate_text_openai(
            lane_config=lane_config,
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
    if lane_config.provider == "ollama":
        return _generate_text_ollama(
            lane_config=lane_config,
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=temperature,
        )
    return _disabled_result(lane_config, error=f"Unsupported provider '{lane_config.provider}'.")


def extract_json(
    prompt: str,
    *,
    system_prompt: str | None = None,
    max_output_tokens: int | None = None,
    allow_live_call: bool = False,
    lane: str | None = None,
) -> LLMResult:
    full_prompt = (
        f"{prompt.strip()}\n\n"
        "Respond only with valid JSON. Do not add markdown, backticks, or commentary."
    )
    result = generate_text(
        full_prompt,
        system_prompt=system_prompt,
        max_output_tokens=max_output_tokens,
        allow_live_call=allow_live_call,
        lane=lane,
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
            lane=result.lane,
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
            lane=result.lane,
        )


def classify_text(
    text: str,
    labels: list[str] | tuple[str, ...],
    *,
    system_prompt: str | None = None,
    allow_live_call: bool = False,
    lane: str | None = None,
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
        lane=lane,
    )


def _generate_text_openai(
    *,
    lane_config: ModelLaneConfig,
    prompt: str,
    system_prompt: str | None,
    temperature: float,
    max_output_tokens: int | None,
) -> LLMResult:
    try:
        client = _get_llm_client_for_lane(lane_config)
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        request_kwargs = build_openai_request_kwargs(
            model=lane_config.model,
            messages=messages,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        response = client.chat.completions.create(**request_kwargs)
        choice = response.choices[0] if getattr(response, "choices", None) else None
        text = ""
        if choice is not None and getattr(choice, "message", None) is not None:
            text = str(choice.message.content or "").strip()
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", None) if usage else None
        output_tokens = getattr(usage, "completion_tokens", None) if usage else None
        return LLMResult(
            success=True,
            provider=lane_config.provider,
            model=lane_config.model,
            text=text,
            json_data=None,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=None,
            error=None,
            raw_text=text,
            lane=lane_config.lane,
        )
    except Exception as exc:
        return _error_result(lane_config, str(exc))


def build_openai_request_kwargs(
    *,
    model: str | None,
    messages: list[dict[str, str]],
    temperature: float,
    max_output_tokens: int | None = None,
    api_style: str = "chat_completions",
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    token_limit = _positive_int_or_none(max_output_tokens)
    if token_limit is not None:
        if api_style == "responses":
            kwargs["max_output_tokens"] = token_limit
        else:
            kwargs["max_completion_tokens"] = token_limit
    return {key: value for key, value in kwargs.items() if value is not None}


def _positive_int_or_none(value: int | None) -> int | None:
    if value is None:
        return None
    try:
        integer_value = int(value)
    except (TypeError, ValueError):
        return None
    if integer_value <= 0:
        return None
    return integer_value


def _generate_text_ollama(
    *,
    lane_config: ModelLaneConfig,
    prompt: str,
    system_prompt: str | None,
    temperature: float,
) -> LLMResult:
    try:
        ollama_module = _get_llm_client_for_lane(lane_config)
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        response = ollama_module.chat(
            model=lane_config.model or DEFAULT_LOCAL_MODEL,
            messages=messages,
            options={"temperature": temperature},
        )
        text = str(((response or {}).get("message") or {}).get("content") or "").strip()
        prompt_eval_count = (response or {}).get("prompt_eval_count")
        eval_count = (response or {}).get("eval_count")
        return LLMResult(
            success=True,
            provider=lane_config.provider,
            model=lane_config.model,
            text=text,
            json_data=None,
            input_tokens=int(prompt_eval_count) if prompt_eval_count is not None else None,
            output_tokens=int(eval_count) if eval_count is not None else None,
            estimated_cost=None,
            error=None,
            raw_text=text,
            lane=lane_config.lane,
        )
    except Exception as exc:
        return _error_result(lane_config, f"Ollama provider not wired yet or unavailable: {exc}")


def _disabled_result(lane_config: ModelLaneConfig, *, error: str) -> LLMResult:
    return LLMResult(
        success=False,
        provider=lane_config.provider,
        model=lane_config.model,
        text=None,
        json_data=None,
        input_tokens=None,
        output_tokens=None,
        estimated_cost=None,
        error=error,
        raw_text=None,
        lane=lane_config.lane,
    )


def _error_result(lane_config: ModelLaneConfig, error: str) -> LLMResult:
    return LLMResult(
        success=False,
        provider=lane_config.provider,
        model=lane_config.model,
        text=None,
        json_data=None,
        input_tokens=None,
        output_tokens=None,
        estimated_cost=None,
        error=error,
        raw_text=None,
        lane=lane_config.lane,
    )


def _get_llm_client_for_lane(lane_config: ModelLaneConfig):
    if lane_config.provider == "disabled":
        return None
    if lane_config.provider == "openai":
        if not lane_config.key_present:
            raise RuntimeError(f"{OPENAI_API_KEY_ENV} is required when provider is openai.")
        openai_module = _import_optional_module("openai")
        if openai_module is None:
            raise RuntimeError("OpenAI provider selected, but the 'openai' SDK is not installed in this environment.")
        client_class = getattr(openai_module, "OpenAI", None)
        if client_class is None:
            raise RuntimeError("OpenAI provider selected, but the installed 'openai' package does not expose OpenAI().")
        return client_class(api_key=os.getenv(OPENAI_API_KEY_ENV))
    if lane_config.provider == "ollama":
        ollama_module = _import_optional_module("ollama")
        if ollama_module is None:
            raise RuntimeError("Ollama provider selected, but the 'ollama' package is not installed in this environment.")
        return ollama_module
    raise RuntimeError(f"Unsupported LLM provider '{lane_config.provider}'.")


def _health_check_lane(
    *,
    lane_config: ModelLaneConfig,
    safe_mode: bool,
    allow_live_call: bool,
) -> LLMHealthCheckResult:
    if lane_config.provider == "disabled":
        return LLMHealthCheckResult(
            success=True,
            provider="disabled",
            model=None,
            status="disabled_mode_ready",
            safe_mode=safe_mode,
            openai_key_present=False,
            error=None,
            lane=lane_config.lane,
        )
    if lane_config.provider == "openai":
        if not lane_config.key_present:
            return LLMHealthCheckResult(
                success=False,
                provider=lane_config.provider,
                model=lane_config.model,
                status="openai_missing_api_key",
                safe_mode=safe_mode,
                openai_key_present=False,
                error=f"{OPENAI_API_KEY_ENV} is not set.",
                lane=lane_config.lane,
            )
        if _import_optional_module("openai") is None:
            return LLMHealthCheckResult(
                success=False,
                provider=lane_config.provider,
                model=lane_config.model,
                status="openai_sdk_missing",
                safe_mode=safe_mode,
                openai_key_present=True,
                error="The 'openai' SDK is not installed in this environment.",
                lane=lane_config.lane,
            )
        if safe_mode and not allow_live_call:
            return LLMHealthCheckResult(
                success=True,
                provider=lane_config.provider,
                model=lane_config.model,
                status="openai_configured_no_live_probe_safe_mode",
                safe_mode=True,
                openai_key_present=True,
                error=None,
                lane=lane_config.lane,
            )
        return LLMHealthCheckResult(
            success=True,
            provider=lane_config.provider,
            model=lane_config.model,
            status="openai_configured_no_live_probe",
            safe_mode=safe_mode,
            openai_key_present=True,
            error=None,
            lane=lane_config.lane,
        )
    if lane_config.provider == "ollama":
        if _import_optional_module("ollama") is None:
            return LLMHealthCheckResult(
                success=False,
                provider=lane_config.provider,
                model=lane_config.model,
                status="ollama_sdk_missing",
                safe_mode=safe_mode,
                openai_key_present=False,
                error="The 'ollama' package is not installed in this environment.",
                lane=lane_config.lane,
            )
        if safe_mode and not allow_live_call:
            return LLMHealthCheckResult(
                success=True,
                provider=lane_config.provider,
                model=lane_config.model,
                status="ollama_configured_no_live_probe_safe_mode",
                safe_mode=True,
                openai_key_present=False,
                error=None,
                lane=lane_config.lane,
            )
        return LLMHealthCheckResult(
            success=True,
            provider=lane_config.provider,
            model=lane_config.model,
            status="ollama_configured_no_live_probe",
            safe_mode=safe_mode,
            openai_key_present=False,
            error=None,
            lane=lane_config.lane,
        )
    return LLMHealthCheckResult(
        success=False,
        provider=lane_config.provider,
        model=lane_config.model,
        status="unsupported_provider",
        safe_mode=safe_mode,
        openai_key_present=lane_config.key_present,
        error=f"Unsupported provider '{lane_config.provider}'.",
        lane=lane_config.lane,
    )


def _build_lane_config(
    *,
    lane: str,
    provider: str,
    model: str | None,
    allow_live: bool,
    openai_key_present: bool,
    max_calls_per_run: int | None,
    max_input_chars: int | None,
) -> ModelLaneConfig:
    key_present = openai_key_present if provider == "openai" else False
    module_available = _provider_module_available(provider)
    available = provider == "disabled" or (provider == "openai" and module_available and key_present) or (provider == "ollama" and module_available)
    notes = _lane_notes(
        lane=lane,
        provider=provider,
        available=available,
        key_present=key_present,
        allow_live=allow_live,
    )
    return ModelLaneConfig(
        lane=lane,
        provider=provider,
        model=model,
        allow_live=allow_live,
        key_present=key_present,
        available=available,
        max_calls_per_run=max_calls_per_run,
        max_input_chars=max_input_chars,
        notes=notes,
    )


def _lane_summary(lane_config: ModelLaneConfig) -> dict[str, Any]:
    summary = {
        "provider": lane_config.provider,
        "model": lane_config.model,
        "allow_live": lane_config.allow_live,
        "available": lane_config.available,
    }
    if lane_config.provider == "openai":
        summary["key_present"] = lane_config.key_present
    return summary


def _lane_notes(*, lane: str, provider: str, available: bool, key_present: bool, allow_live: bool) -> str:
    if provider == "disabled":
        return f"{lane} lane is disabled."
    if provider == "openai" and not key_present:
        return f"{lane} lane is configured for OpenAI, but no API key is present."
    if not available:
        return f"{lane} lane provider is configured but not currently available."
    if not allow_live:
        return f"{lane} lane is configured but live calls are disabled by default."
    return f"{lane} lane is configured for controlled live use."


def _provider_notes(*, provider: str, model_config: ModelConfig) -> str:
    return (
        f"Selected provider: {provider}. "
        f"Local lane: {model_config.local.provider}/{model_config.local.model}. "
        f"Remote fast lane: {model_config.remote_fast.provider}/{model_config.remote_fast.model}. "
        f"Remote strong lane: {model_config.remote_strong.provider}/{model_config.remote_strong.model}."
    )


def _selected_lane_from_provider(provider: str, model_config: ModelConfig) -> str:
    provider_norm = _normalize_provider(provider, default="disabled")
    if provider_norm == model_config.local.provider:
        return "local"
    if provider_norm == model_config.remote_fast.provider:
        return "remote_fast"
    if provider_norm == model_config.remote_strong.provider:
        return "remote_strong"
    return "disabled"


def _normalize_provider(raw_provider: Any, *, default: str) -> str:
    provider = str(raw_provider or default).strip().lower() or default
    if provider not in ALLOWED_PROVIDERS:
        return default
    return provider


def _get_lane_model(*, env_name: str, default: str, fallback: str | None = None) -> str:
    raw = str(os.getenv(env_name, "")).strip()
    if raw:
        return raw
    if fallback and str(fallback).strip():
        return str(fallback).strip()
    return default


def _provider_module_available(provider: str) -> bool:
    if provider == "disabled":
        return True
    module_name = "openai" if provider == "openai" else "ollama" if provider == "ollama" else None
    if not module_name:
        return False
    return _import_optional_module(module_name) is not None


def _import_optional_module(module_name: str):
    if importlib.util.find_spec(module_name) is None:
        return None
    try:
        return importlib.import_module(module_name)
    except Exception:
        return None


def _truthy_env(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, *, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except Exception:
        return default
