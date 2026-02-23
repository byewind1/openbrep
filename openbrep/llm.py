"""
Unified LLM interface via litellm.

Supports: GLM-4, Claude, GPT-4, DeepSeek, Ollama local models, and any
provider compatible with the OpenAI API format.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from openbrep.config import LLMConfig


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
        self._setup()

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

        # Build model string for litellm
        model = self._resolve_model_string()

        # Build completion kwargs
        completion_kwargs = {
            "model": model,
            "messages": msg_dicts,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }

        # Pass API key and base URL
        api_key = self.config.resolve_api_key()
        if api_key:
            completion_kwargs["api_key"] = api_key
        # Skip api_base for native LiteLLM providers (zai/, deepseek/, etc.)
        # — they handle endpoints internally. Only pass for openai-compatible custom endpoints.
        native_providers = ("zai/", "deepseek/", "anthropic/", "claude/", "gemini/", "ollama/")
        is_native = any(model.startswith(p) for p in native_providers)
        if self.config.api_base and not is_native:
            completion_kwargs["api_base"] = self.config.api_base

        completion_kwargs.update(kwargs)

        response = self._litellm.completion(**completion_kwargs)

        if not response.choices:
            raise RuntimeError("LLM returned empty choices list — possible rate limit or content filter")
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

        model = self._resolve_model_string()

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
        }

        api_key = self.config.resolve_api_key()
        if api_key:
            completion_kwargs["api_key"] = api_key

        native_providers = ("zai/", "deepseek/", "anthropic/", "claude/", "gemini/", "ollama/")
        is_native = any(model.startswith(p) for p in native_providers)
        if self.config.api_base and not is_native:
            completion_kwargs["api_base"] = self.config.api_base

        completion_kwargs.update(kwargs)

        response = self._litellm.completion(**completion_kwargs)
        if not response.choices:
            raise RuntimeError("LLM returned empty choices list — possible rate limit or content filter")
        choice = response.choices[0]
        return LLMResponse(
            content=choice.message.content or "",
            model=response.model or self.config.model,
            usage=dict(response.usage) if response.usage else {},
            finish_reason=choice.finish_reason or "",
        )

    def _resolve_model_string(self) -> str:
        """
        Resolve the model string for litellm.

        litellm uses provider prefixes like 'ollama/', 'anthropic/', etc.
        If the user already provided a prefixed model, use it as-is.
        Otherwise, try to infer the provider from the model name.
        """
        model = self.config.model

        # Already has a provider prefix
        if "/" in model and not model.startswith("http"):
            return model

        # Infer provider from model name
        model_lower = model.lower()
        if "glm" in model_lower:
            # 智谱 GLM models: LiteLLM provider prefix is 'zai/' (Z.AI)
            return f"zai/{model}"
        elif "claude" in model_lower:
            return f"claude/{model}" if "claude/" not in model else model
        elif "deepseek" in model_lower:
            return f"deepseek/{model}"
        elif "gpt" in model_lower or "o1" in model_lower or "o3" in model_lower:
            return f"openai/{model}"
        elif "gemini" in model_lower:
            return f"gemini/{model}" if "gemini/" not in model else model
        elif "ollama" in model_lower:
            return model  # Already has ollama/ prefix or will be handled

        return model


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
