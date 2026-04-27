"""
Unified LLM interface via litellm.

Supports: GLM-4, Claude, GPT-4, DeepSeek, Ollama local models, and any
provider compatible with the OpenAI API format.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
import logging
import time
from typing import Optional
import warnings

from openbrep.config import LLMConfig


logger = logging.getLogger(__name__)
_NATIVE_PROVIDERS = ("zai/", "deepseek/", "anthropic/", "claude/", "gemini/", "ollama/", "openai/")

@dataclass
class _ResolvedModelTarget:
    configured_model: str
    litellm_model: str
    is_custom_provider_request: bool
    provider_name: str = ""
    protocol: str = ""
    target_model: str = ""


@dataclass
class Message:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass
class LLMResponse:
    content: str
    model: str
    usage: dict = field(default_factory=dict)
    finish_reason: str = ""


class LLMAdapter:
    """
    Unified LLM interface.

    Uses litellm under the hood for cross-provider compatibility.
    Falls back to a mock mode when litellm is not available (for testing).
    """

    def __init__(self, config: LLMConfig):
        self.config = config
        self._litellm = None
        # Re-register warning filter here so it survives pytest's filter reset
        warnings.filterwarnings(
            "ignore",
            category=UserWarning,
            message=r"(?s).*Pydantic serializer warnings:.*ResponseAPIUsage.*field_name='usage'.*",
        )
        self._setup()

    def _is_custom_provider_model(self, model: str | None = None) -> bool:
        return self.config._is_custom_provider_model(model)

    def _get_custom_provider_config(self, model: str | None = None) -> Optional[dict]:
        custom_match = self.config._find_custom_provider_match(model)
        return custom_match.get("provider") if custom_match else None

    def _get_custom_provider_match(self, model: str | None = None) -> Optional[dict]:
        return self.config._find_custom_provider_match(model)

    def _is_native_provider_model(self, model: str) -> bool:
        return any(model.startswith(prefix) for prefix in _NATIVE_PROVIDERS)

    def _build_config_error_message(self, exc: Exception, resolved: _ResolvedModelTarget) -> str:
        configured_model = resolved.configured_model or self.config.model or resolved.litellm_model
        resolved_api_key = self.config.resolve_api_key(configured_model)
        resolved_api_base = self.config.resolve_api_base(configured_model)
        provider_name = resolved.provider_name
        exc_text = str(exc).strip() or exc.__class__.__name__

        litellm_exceptions = getattr(self._litellm, "exceptions", None)
        bad_request = getattr(litellm_exceptions, "BadRequestError", None) if litellm_exceptions else None
        auth_error = getattr(litellm_exceptions, "AuthenticationError", None) if litellm_exceptions else None
        exc_text_lower = exc_text.lower()

        if "insufficient balance" in exc_text_lower or "code\":\"402" in exc_text_lower or "code='402'" in exc_text_lower:
            summary = (
                f"LLM 账户余额或额度不足：模型 `{configured_model}` 所属 provider "
                f"`{provider_name or configured_model}` 返回余额不足，请充值或切换到有额度的模型/provider。"
            )
        elif auth_error and isinstance(exc, auth_error):
            if not resolved_api_key:
                summary = (
                    f"LLM 配置错误：当前模型 `{configured_model}` 未找到可用 API Key。"
                    "请检查 config.toml 中 [llm.provider_keys] 或对应 [[llm.custom_providers]] 的 api_key。"
                )
            else:
                summary = (
                    f"LLM 认证失败：模型 `{configured_model}` 的 API Key 可能无效、已过期，"
                    "或与当前 provider 不匹配。"
                )
        elif bad_request and isinstance(exc, bad_request):
            if resolved.is_custom_provider_request and not resolved_api_base:
                summary = (
                    f"LLM 配置错误：自定义 provider `{provider_name or configured_model}` 缺少 base_url，"
                    "无法请求当前模型。请检查 [[llm.custom_providers]] 配置。"
                )
            elif resolved.is_custom_provider_request:
                summary = (
                    f"LLM 请求被拒绝：模型 `{configured_model}` 所属自定义 provider `{provider_name or configured_model}` "
                    "可能不兼容当前协议、base_url 或模型名配置。"
                )
            else:
                summary = (
                    f"LLM 配置错误：模型 `{configured_model}` 可能未被当前官方 provider 支持，"
                    "或 model 名称填写不正确。"
                )
        else:
            summary = f"LLM 调用失败：模型 `{configured_model}` 的配置或请求参数可能有误。"

        details = [summary, f"底层错误：{exc_text}"]
        if resolved.is_custom_provider_request:
            details.insert(1, f"provider={provider_name or '(未命名自定义 provider)' }")
            if resolved.protocol:
                details.append(f"protocol={resolved.protocol}")
            if resolved.target_model:
                details.append(f"target_model={resolved.target_model}")
        if resolved_api_base:
            details.append(f"api_base={resolved_api_base}")
        details.append(f"resolved_model={resolved.litellm_model}")
        return " ".join(details)

    def _raise_config_error_if_needed(self, exc: Exception, resolved: _ResolvedModelTarget) -> None:
        litellm_exceptions = getattr(self._litellm, "exceptions", None)
        bad_request = getattr(litellm_exceptions, "BadRequestError", None) if litellm_exceptions else None
        auth_error = getattr(litellm_exceptions, "AuthenticationError", None) if litellm_exceptions else None
        if (bad_request and isinstance(exc, bad_request)) or (auth_error and isinstance(exc, auth_error)):
            raise RuntimeError(self._build_config_error_message(exc, resolved)) from exc

    def _setup(self):
        """Initialize litellm with config."""
        try:
            import litellm

            self._litellm = litellm

            # Set API key env vars by provider so litellm can find them
            api_key = self.config.resolve_api_key()
            if api_key:
                model_lower = self.config.model.lower()
                if "glm" in model_lower:
                    os.environ["ZAI_API_KEY"] = api_key
                elif "deepseek" in model_lower:
                    os.environ["DEEPSEEK_API_KEY"] = api_key
                elif "claude" in model_lower:
                    os.environ["ANTHROPIC_API_KEY"] = api_key
                elif "gemini" in model_lower:
                    os.environ["GEMINI_API_KEY"] = api_key
                else:
                    os.environ.setdefault("OPENAI_API_KEY", api_key)

            # Set custom base URL if provided
            if self.config.api_base:
                self._litellm.api_base = self.config.api_base

            # Suppress litellm's noisy logging
            litellm.suppress_debug_info = True

        except ImportError:
            self._litellm = None

    def generate(self, messages: list, **kwargs) -> LLMResponse:
        """
        Send messages to the LLM and return the response.

        Args:
            messages: List of Message objects or dicts with 'role' and 'content'.
            **kwargs: Additional parameters passed to litellm.completion().

        Returns:
            LLMResponse with the generated content.
        """
        # Accept both Message objects and plain dicts
        msg_dicts = []
        for m in messages:
            if isinstance(m, dict):
                msg_dicts.append(m)
            else:
                msg_dicts.append({"role": m.role, "content": m.content})

        if self._litellm is None:
            raise RuntimeError(
                "litellm is not installed. Install it with: pip install litellm"
            )

        requested_model = kwargs.pop("model", None)
        resolved = self._resolve_model_target(requested_model)
        model = resolved.litellm_model

        # Build completion kwargs
        completion_kwargs = {
            "model": model,
            "messages": msg_dicts,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "timeout": self.config.timeout,
            "stream": True,
        }

        model_lower = model.lower()
        if "gpt-5" in model_lower or "codex" in model_lower:
            completion_kwargs["drop_params"] = True

        # Pass API key and base URL
        api_key = self.config.resolve_api_key(resolved.configured_model)
        if api_key:
            completion_kwargs["api_key"] = api_key
        # Skip api_base for native LiteLLM providers (zai/, deepseek/, etc.)
        # — they handle endpoints internally. Only pass for openai-compatible custom endpoints.
        is_native = self._is_native_provider_model(model)
        api_base = self.config.resolve_api_base(resolved.configured_model)
        if api_base and (resolved.is_custom_provider_request or not is_native):
            completion_kwargs["api_base"] = api_base

        completion_kwargs.update(kwargs)

        start_time = time.perf_counter()
        try:
            response = self._litellm.completion(**completion_kwargs)
        except Exception as exc:
            elapsed = time.perf_counter() - start_time
            logger.warning(
                "LLM text call failed model=%s prompt_messages=%d elapsed=%.2fs error=%s",
                model,
                len(msg_dicts),
                elapsed,
                exc.__class__.__name__,
            )
            self._raise_config_error_if_needed(exc, resolved)
            raise
        if completion_kwargs.get("stream"):
            chunks = []
            for chunk in response:
                chunks.append(chunk)
            response = self._litellm.stream_chunk_builder(chunks)
        if not response.choices:
            raise RuntimeError("LLM returned empty choices list — possible rate limit or content filter")
        logger.info(
            "LLM text call finished model=%s prompt_messages=%d elapsed=%.2fs",
            model,
            len(msg_dicts),
            time.perf_counter() - start_time,
        )
        choice = response.choices[0]
        return LLMResponse(
            content=choice.message.content or "",
            model=response.model or self.config.model,
            usage=dict(response.usage) if response.usage else {},
            finish_reason=choice.finish_reason or "",
        )

    def generate_with_image(
        self,
        text_prompt: str,
        image_b64: str,
        image_mime: str = "image/jpeg",
        system_prompt: str | None = None,
        **kwargs,
    ) -> LLMResponse:
        """
        Call LLM with a base64-encoded image + text prompt (vision mode).

        Uses litellm's OpenAI-compatible image_url format, which litellm
        automatically translates to each provider's native format
        (Anthropic image blocks, Gemini inline_data, etc.).

        Args:
            text_prompt: User text accompanying the image.
            image_b64:   Base64-encoded image bytes (no data-URI prefix).
            image_mime:  MIME type, e.g. "image/jpeg", "image/png".
            system_prompt: Optional system message prepended to the call.
        """
        if self._litellm is None:
            raise RuntimeError(
                "litellm is not installed. Install it with: pip install litellm"
            )

        requested_model = kwargs.pop("model", None)
        resolved = self._resolve_model_target(requested_model)
        model = resolved.litellm_model

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        messages.append({
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{image_mime};base64,{image_b64}"},
                },
                {"type": "text", "text": text_prompt},
            ],
        })

        completion_kwargs = {
            "model": model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "timeout": self.config.timeout,
            "stream": True,
        }

        model_lower = model.lower()
        if "gpt-5" in model_lower or "codex" in model_lower:
            completion_kwargs["drop_params"] = True

        api_key = self.config.resolve_api_key(resolved.configured_model)
        if api_key:
            completion_kwargs["api_key"] = api_key

        is_native = self._is_native_provider_model(model)
        api_base = self.config.resolve_api_base(resolved.configured_model)
        if api_base and (resolved.is_custom_provider_request or not is_native):
            completion_kwargs["api_base"] = api_base

        completion_kwargs.update(kwargs)

        start_time = time.perf_counter()
        logger.info(
            "LLM vision call started model=%s image_mime=%s image_b64_len=%d prompt_len=%d",
            model,
            image_mime,
            len(image_b64),
            len(text_prompt or ""),
        )
        try:
            response = self._litellm.completion(**completion_kwargs)
        except Exception as exc:
            elapsed = time.perf_counter() - start_time
            logger.warning(
                "LLM vision call failed model=%s image_mime=%s image_b64_len=%d prompt_len=%d elapsed=%.2fs error=%s",
                model,
                image_mime,
                len(image_b64),
                len(text_prompt or ""),
                elapsed,
                exc.__class__.__name__,
            )
            self._raise_config_error_if_needed(exc, resolved)
            raise
        if completion_kwargs.get("stream"):
            chunks = []
            for chunk in response:
                chunks.append(chunk)
            response = self._litellm.stream_chunk_builder(chunks)
        if not response.choices:
            raise RuntimeError("LLM returned empty choices list — possible rate limit or content filter")
        logger.info(
            "LLM vision call finished model=%s image_mime=%s image_b64_len=%d prompt_len=%d elapsed=%.2fs",
            model,
            image_mime,
            len(image_b64),
            len(text_prompt or ""),
            time.perf_counter() - start_time,
        )
        choice = response.choices[0]
        return LLMResponse(
            content=choice.message.content or "",
            model=response.model or self.config.model,
            usage=dict(response.usage) if response.usage else {},
            finish_reason=choice.finish_reason or "",
        )

    def _resolve_model_target(self, model: str | None = None) -> _ResolvedModelTarget:
        configured_model = str(model or self.config.model or "").strip()

        custom_match = self._get_custom_provider_match(configured_model)
        if custom_match:
            provider_name = str(custom_match.get("provider_name", "") or "").strip()
            protocol = str(custom_match.get("protocol", "openai") or "openai").strip().lower()
            target_model = str(custom_match.get("model", configured_model) or configured_model).strip()
            if provider_name:
                provider_prefix = f"{provider_name.strip()}-"
                remainder = ""
                if (
                    target_model.lower() == configured_model.lower()
                    and configured_model.lower().startswith(provider_prefix.lower())
                ):
                    remainder = configured_model[len(provider_prefix):].strip()
                remainder_lower = remainder.lower()
                if remainder and (
                    remainder_lower.startswith("gpt-")
                    or remainder_lower.startswith("o1")
                    or remainder_lower.startswith("o3")
                    or remainder_lower.startswith("o4")
                ):
                    target_model = remainder

            if "/" in target_model and not target_model.startswith("http"):
                litellm_model = target_model
            else:
                protocol_prefix = {
                    "openai": "openai",
                    "anthropic": "anthropic",
                    "claude": "claude",
                    "gemini": "gemini",
                    "zai": "zai",
                    "deepseek": "deepseek",
                    "ollama": "ollama",
                }.get(protocol, "openai")
                litellm_model = f"{protocol_prefix}/{target_model}"

            return _ResolvedModelTarget(
                configured_model=configured_model,
                litellm_model=litellm_model,
                is_custom_provider_request=True,
                provider_name=provider_name,
                protocol=protocol,
                target_model=target_model,
            )

        if "/" in configured_model and not configured_model.startswith("http"):
            return _ResolvedModelTarget(
                configured_model=configured_model,
                litellm_model=configured_model,
                is_custom_provider_request=False,
            )

        model_lower = configured_model.lower()
        if "glm" in model_lower:
            litellm_model = f"zai/{configured_model}"
        elif "claude" in model_lower:
            litellm_model = f"claude/{configured_model}" if "claude/" not in configured_model else configured_model
        elif "deepseek" in model_lower:
            litellm_model = f"deepseek/{configured_model}"
        elif "gpt" in model_lower or "o1" in model_lower or "o3" in model_lower:
            litellm_model = f"openai/{configured_model}"
        elif "gemini" in model_lower:
            litellm_model = f"gemini/{configured_model}" if "gemini/" not in configured_model else configured_model
        elif "ollama" in model_lower:
            litellm_model = configured_model
        else:
            litellm_model = configured_model

        return _ResolvedModelTarget(
            configured_model=configured_model,
            litellm_model=litellm_model,
            is_custom_provider_request=False,
        )

    def _resolve_model_string(self) -> str:
        """
        Resolve the model string for litellm.

        litellm uses provider prefixes like 'ollama/', 'anthropic/', etc.
        If the user already provided a prefixed model, use it as-is.
        Otherwise, try to infer the provider from the model name.
        """
        return self._resolve_model_target().litellm_model



class MockLLM:
    """
    Mock LLM for testing without API access.

    Accepts a list of responses that will be returned in order.
    """

    def __init__(self, responses: Optional[list[str]] = None):
        self.responses = responses or ["<!-- Mock LLM response -->"]
        self.call_count = 0
        self.call_history: list[list[Message]] = []

    def generate(self, messages: list[Message], **kwargs) -> LLMResponse:
        self.call_history.append(messages)
        idx = min(self.call_count, len(self.responses) - 1)
        content = self.responses[idx]
        self.call_count += 1
        return LLMResponse(
            content=content,
            model="mock-model",
            usage={"prompt_tokens": 100, "completion_tokens": 200},
            finish_reason="stop",
        )
