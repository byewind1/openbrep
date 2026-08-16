"""
Unified LLM interface via litellm.

Supports: GLM-4, Claude, GPT-4, DeepSeek, Ollama local models, and any
provider compatible with the OpenAI API format.
"""

from __future__ import annotations

import logging
import os
import re
import time
import warnings
from dataclasses import dataclass, field
from typing import Optional

from openbrep.codex.errors import error_response
from openbrep.config import PROVIDER_PROFILES, LLMConfig, provider_profile_for_model

logger = logging.getLogger(__name__)
_NATIVE_PROVIDERS = tuple(p.native_prefix for p in PROVIDER_PROFILES if p.native_prefix)

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
class ToolDefinition:
    """工具定义（OpenAI function 格式；litellm 会自动翻译给 anthropic 协议）。"""

    name: str
    description: str = ""
    parameters: dict = field(default_factory=dict)  # JSON Schema

    def to_openai_dict(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters or {"type": "object", "properties": {}},
            },
        }


@dataclass
class ToolCall:
    """模型发起的一次工具调用。arguments 已解析为 dict（解析失败为空 dict）。"""

    id: str
    name: str
    arguments: dict = field(default_factory=dict)
    raw_arguments: str = ""


@dataclass
class LLMResponse:
    content: str
    model: str
    usage: dict = field(default_factory=dict)
    finish_reason: str = ""
    tool_calls: list = field(default_factory=list)  # list[ToolCall]

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


def tool_result_message(tool_call_id: str, content: str, name: str = "") -> dict:
    """构造工具执行结果消息（OpenAI role=tool 格式，litellm 兼容各协议）。"""
    msg = {"role": "tool", "tool_call_id": tool_call_id, "content": str(content)}
    if name:
        msg["name"] = name
    return msg


def assistant_tool_calls_message(response: "LLMResponse") -> dict:
    """把带 tool_calls 的响应还原成 assistant 消息，供 agent loop 回填对话历史。"""
    return {
        "role": "assistant",
        "content": response.content or None,
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": tc.raw_arguments or "{}",
                },
            }
            for tc in response.tool_calls
        ],
    }


# ── 错误分类辅助（断点 1/2 修复）──────────────────────────────

# 认证特征：deepseek 等 provider 的 401 会被 litellm 映射成 BadRequestError，
# 只看异常类型会把"key 无效"误诊为"模型名不对"（2026-07-28 实测踩坑），
# 所以先匹配错误内容，再看异常类型。
_AUTH_PATTERNS = (
    "authentication",
    "api key",
    "api_key",
    "invalid key",
    "unauthorized",
    "401",
    "incorrect api key",
    "no api key",
)


def _looks_like_auth_error(exc_text: str) -> bool:
    text = exc_text.lower()
    return any(p in text for p in _AUTH_PATTERNS)


def _looks_like_rate_limit(exc: BaseException, exc_text: str) -> bool:
    """429 / 速率限制 / 周期配额耗尽（与 402 余额不足是两类）。"""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if "ratelimit" in current.__class__.__name__.lower():
            return True
        current = current.__cause__ or current.__context__
    text = exc_text.lower()
    return "429" in text or "rate limit" in text or "insufficient_quota" in text


def _rate_limit_reset_hint(exc_text: str) -> str:
    match = re.search(r"reset at ([0-9\-: ]+(?:UTC)?)", exc_text, re.IGNORECASE)
    return f"配额将于 {match.group(1)} 重置。" if match else ""


# 各 provider 的 key 控制台，放进"key 无效"的引导文案里（数据来自注册表）
def _console_url_for(provider_name: str | None, model: str | None) -> str:
    text = (provider_name or "").strip().lower()
    if text:
        for profile in PROVIDER_PROFILES:
            if text == profile.name or text in profile.provider_key_names:
                return profile.console_url
    profile = provider_profile_for_model(model or "")
    return profile.console_url if profile else ""


def _classify_network_error(exc: BaseException) -> str | None:
    """沿异常链识别网络类错误：connection | timeout | bad_gateway | ssl | None。"""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        name = current.__class__.__name__.lower()
        text = str(current).lower()
        if "ssl" in name or "certificate" in text:
            return "ssl"
        if "timeout" in name or "timed out" in text:
            return "timeout"
        if (
            "badgateway" in name
            or "bad gateway" in text
            or "502" in text
            or "503" in text
            or "serviceunavailable" in name
        ):
            return "bad_gateway"
        if "connection" in name or "connect" in name:
            return "connection"
        current = current.__cause__ or current.__context__
    return None


class LLMAdapter:
    """
    Unified LLM interface.

    Uses litellm under the hood for cross-provider compatibility.
    Falls back to a mock mode when litellm is not available (for testing).
    """
    def __init__(self, config: LLMConfig):
        self.config = config
        self._litellm = None
        # D3：Codex CHAT/EXPLAIN 的 provider 注入点（实例级 > 进程共享默认）。
        # 测试/管线可显式注入 fake provider；None = 走默认注册表。
        self.codex_provider = None
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
        elif _looks_like_rate_limit(exc, exc_text_lower):
            summary = (
                f"LLM 速率/周期配额已用尽：模型 `{configured_model}`"
                f"（provider `{provider_name or configured_model}`）的调用额度已达上限。"
                f"{_rate_limit_reset_hint(exc_text)}"
                "→ 等配额重置，或切换到有额度的模型/provider。"
            )
        elif (auth_error and isinstance(exc, auth_error)) or _looks_like_auth_error(exc_text):
            # 判定顺序关键：先看内容里的认证特征再看异常类型——
            # deepseek 等 provider 的 401 会被 litellm 映射成 BadRequestError，
            # 只看类型会把"key 无效"误诊成"模型名不对"（实测踩过）
            console_url = _console_url_for(provider_name, configured_model)
            if not resolved_api_key:
                summary = (
                    f"LLM 配置错误：当前模型 `{configured_model}` 未找到可用 API Key。"
                    "请检查 config.toml 中 [llm.provider_keys] 或对应 [[llm.custom_providers]] 的 api_key。"
                )
            else:
                summary = (
                    f"LLM API Key 无效或被拒绝：当前模型 `{configured_model}`"
                    f"（provider `{provider_name or configured_model}`）的认证未通过。\n"
                    "→ 检查 config.toml 中该 provider 的 key 是否完整、是否已过期"
                    + (f"\n→ {provider_name or configured_model} 控制台：{console_url}" if console_url else "")
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
        if _looks_like_rate_limit(exc, str(exc).lower()):
            # RateLimitError 不属于 BadRequest/Auth，需显式进入分类文案
            raise RuntimeError(self._build_config_error_message(exc, resolved)) from exc
        network_kind = _classify_network_error(exc)
        if network_kind is not None:
            raise RuntimeError(self._build_network_error_message(exc, resolved, network_kind)) from exc

    def _raise_if_reasoning_only(self, choice) -> None:
        """检测模型把所有输出配额用于内部 reasoning、未生成可见内容的情况。

        部分 provider 上的推理模型（如 deepseek-v4-flash）在流式/非流式
        返回中都会把全部 completion_tokens 填入 reasoning_content，导致 message.content
        为空。此时需要给调用方清晰引导，而不是让它拿到空字符串后静默生成空文件。
        """
        reasoning = getattr(choice.message, "reasoning_content", None) or ""
        if reasoning.strip():
            reasoning_tokens = 0
            usage = getattr(choice, "usage", None)
            if usage:
                details = getattr(usage, "completion_tokens_details", None)
                if details:
                    reasoning_tokens = getattr(details, "reasoning_tokens", 0) or 0
            raise RuntimeError(
                f"LLM 只输出了思考过程，没有输出内容。\n"
                f"原因：thinking 模式消耗了全部 output token（本次 reasoning 用了 {reasoning_tokens} token）\n"
                f"→ 在 config.toml 设置 extra_body = {{ thinking = {{ type = \"disabled\" }} }}\n"
                f"→ 或把 max_tokens 提高到 16384 以上"
            )
        raise RuntimeError(
            "LLM 返回了空内容 — 可能是内容过滤、模型配置错误，或该模型不适合当前任务。"
        )

    def _build_network_error_message(self, exc: Exception, resolved: _ResolvedModelTarget, kind: str) -> str:
        configured_model = resolved.configured_model or self.config.model or resolved.litellm_model
        api_base = self.config.resolve_api_base(configured_model) or "(provider 默认端点)"
        provider_name = resolved.provider_name or configured_model
        exc_text = str(exc).strip() or exc.__class__.__name__

        if kind == "connection":
            summary = (
                f"无法连接到 {api_base}：\n"
                "→ 检查网络连接\n"
                "→ 如果使用代理，检查代理设置\n"
                "→ 检查 api_base 拼写是否正确"
            )
        elif kind == "timeout":
            summary = (
                f"连接 {api_base} 超时（{self.config.timeout} 秒）：\n"
                "→ 网络可能不稳定，稍后重试\n"
                "→ 或在 config.toml 增大 timeout 值"
            )
        elif kind == "bad_gateway":
            summary = (
                f"provider `{provider_name}` 服务端错误：\n"
                "→ 这通常是服务商临时故障，稍后重试\n"
                "→ 如果持续出现，检查 api_base 是否指向正确的端点"
            )
        else:  # ssl
            summary = (
                "SSL 证书验证失败：\n"
                "→ 如果在公司网络，可能需要配置代理证书\n"
                "→ 检查 api_base 的协议是否正确（http/https）"
            )
        return f"{summary}\n底层错误：{exc_text}"

    def _setup(self):
        """Initialize litellm with config."""
        try:
            import litellm

            self._litellm = litellm

            # Set API key env vars by provider so litellm can find them
            # （走 PROVIDER_PROFILES 注册表，不再硬编码关键字链；
            # 语义与旧版一致：已知 provider 覆盖写，未知走 OPENAI setdefault）
            api_key = self.config.resolve_api_key()
            if api_key:
                profile = provider_profile_for_model(self.config.model)
                if profile and profile.env_vars:
                    for env_name in profile.env_vars:
                        os.environ[env_name] = api_key
                else:
                    os.environ.setdefault("OPENAI_API_KEY", api_key)

            # Set custom base URL if provided
            if self.config.api_base:
                self._litellm.api_base = self.config.api_base

            # Suppress litellm's noisy logging
            litellm.suppress_debug_info = True

        except ImportError:
            self._litellm = None

    # ── Codex BYOA（openai-codex）薄分派（D3）─────────────────────────────
    # D1：openai-codex 订阅模型绝不落到 litellm / API-key / 环境变量（fail closed）。
    # D3：只有 CHAT/EXPLAIN（codex_intent="CHAT"，由 pipeline 显式传入）走
    # app-server turn；其余意图（CREATE/MODIFY/DEBUG/IMAGE/工具调用等）保持
    # fail closed，绝不把生成类请求当作闲聊发给订阅模型。

    def _codex_provider(self):
        """返回可用的 CodexProvider：实例注入 > 进程共享默认。

        没有可用 provider 时返回 None（调用方 fail closed，绝不自动回退）。
        """
        provider = getattr(self, "codex_provider", None)
        if provider is not None:
            return provider
        try:
            from openbrep.codex.provider import get_default_codex_provider

            return get_default_codex_provider()
        except Exception as exc:  # noqa: BLE001
            logger.warning("codex provider 读取失败（%s）", exc.__class__.__name__)
            return None

    def _codex_chat_generate(self, msg_dicts: list, model: str, **kwargs) -> LLMResponse:
        """CHAT/EXPLAIN 走 Codex app-server turn（ephemeral thread + 临时只读
        cwd + approval never，见 openbrep/codex/turn.py）。

        错误一律稳定文案（error_response / turn 层稳定文案），绝不透传上游原文。
        """
        from openbrep.codex.errors import STABLE_MESSAGES
        from openbrep.codex.turn import NO_FINAL_MESSAGE_TEXT

        should_cancel = kwargs.pop("codex_should_cancel", None)
        on_event = kwargs.pop("codex_on_event", None)
        timeout = kwargs.pop("timeout", None) or self.config.timeout

        provider = self._codex_provider()
        if provider is None:
            raise RuntimeError(
                "ChatGPT Codex（openai-codex）模型不可用：未检测到可用的 Codex "
                "连接。请先在 AI 设置中完成 ChatGPT 登录后重试。"
            )
        try:
            result = provider.chat(
                msg_dicts,
                model=model,
                timeout=float(timeout),
                should_cancel=should_cancel,
                on_event=on_event,
            )
        except Exception as exc:  # noqa: BLE001 —— 映射稳定文案
            from openbrep.codex.provider import CodexNotSignedInError

            if isinstance(exc, CodexNotSignedInError):
                raise RuntimeError(
                    STABLE_MESSAGES.get("not_signed_in", "尚未连接 ChatGPT，请先完成登录。")
                ) from exc
            stable = error_response(exc)
            raise RuntimeError(stable["error"]) from exc
        if result.finish_reason != "stop":
            raise RuntimeError(result.error or NO_FINAL_MESSAGE_TEXT)
        return LLMResponse(
            content=result.content,
            model=model,
            usage=dict(result.usage or {}),
            finish_reason="stop",
        )

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
        # D3：openai-codex 订阅模型——只有显式 CHAT/EXPLAIN 意图走 turn；
        # 其余意图 fail closed（绝不落到 litellm / API-key / 环境变量）。
        if self.config._is_codex_app_server_model(requested_model):
            codex_intent = kwargs.pop("codex_intent", None)
            if codex_intent == "CHAT":
                codex_model = requested_model or self.config.model
                return self._codex_chat_generate(msg_dicts, model=codex_model, **kwargs)
            raise RuntimeError(
                "ChatGPT Codex（openai-codex）模型的 CREATE/MODIFY/DEBUG 生成能力"
                "尚未开放：当前版本支持登录、模型选择与 CHAT/EXPLAIN。"
                "请改用其他已配置的模型。"
            )
        # 非 codex 模型：忽略 codex 专用参数（绝不让它们进入 litellm）
        kwargs.pop("codex_intent", None)
        kwargs.pop("codex_should_cancel", None)
        kwargs.pop("codex_on_event", None)
        resolved = self._resolve_model_target(requested_model)
        model = resolved.litellm_model

        # Build completion kwargs
        completion_kwargs = {
            "model": model,
            "messages": msg_dicts,
            "temperature": self._effective_temperature(resolved),
            "max_tokens": self.config.max_tokens,
            "timeout": self.config.timeout,
            "stream": True,
        }

        model_lower = model.lower()
        if "gpt-5" in model_lower or "codex" in model_lower:
            completion_kwargs["drop_params"] = True

        # 透传用户配置的 extra_body（如 DeepSeek 的 thinking={"type": "disabled"}）。
        # provider 条目级 extra_body 覆盖顶层；不传时行为不变。
        extra_body = self._effective_extra_body(resolved)
        if extra_body:
            completion_kwargs["extra_body"] = extra_body

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
        content = choice.message.content or ""
        if not content.strip():
            self._raise_if_reasoning_only(choice)
        return LLMResponse(
            content=content,
            model=response.model or self.config.model,
            usage=dict(response.usage) if response.usage else {},
            finish_reason=choice.finish_reason or "",
        )

    def generate_with_tools(
        self,
        messages: list,
        tools: list,
        tool_choice: str = "auto",
        **kwargs,
    ) -> LLMResponse:
        """带工具调用的 LLM 请求（Phase 3 agent loop 前置能力）。

        tools 用 OpenAI function 格式（ToolDefinition 或等价 dict）；
        litellm 会自动翻译到 anthropic 等协议，custom_providers 走
        与 generate() 相同的模型解析，无需额外配置。

        对话历史里的 dict 消息（含 role="tool" 结果与 assistant tool_calls
        回填消息）原样透传。返回的 LLMResponse.tool_calls 非空时，
        调用方执行工具后用 tool_result_message() 回填并再次调用。
        """
        if self._litellm is None:
            raise RuntimeError(
                "litellm is not installed. Install it with: pip install litellm"
            )

        msg_dicts = []
        for m in messages:
            if isinstance(m, dict):
                msg_dicts.append(m)
            else:
                msg_dicts.append({"role": m.role, "content": m.content})

        tool_dicts = []
        for t in tools:
            if isinstance(t, ToolDefinition):
                tool_dicts.append(t.to_openai_dict())
            elif isinstance(t, dict):
                tool_dicts.append(t)

        requested_model = kwargs.pop("model", None)
        # D1 不变量：openai-codex 订阅模型绝不落到 litellm / API-key（fail closed）。
        # MODIFY/DEBUG 工具面在 D10/D11 门禁开放前一律拒绝，绝不把 agent loop
        # 的生成类请求当作闲聊发给订阅模型。
        if self.config._is_codex_app_server_model(requested_model):
            raise RuntimeError(
                "ChatGPT Codex（openai-codex）模型的 CREATE/MODIFY/DEBUG 生成能力"
                "尚未开放：当前版本支持登录、模型选择与 CHAT/EXPLAIN。"
                "请改用其他已配置的模型。"
            )
        kwargs.pop("codex_intent", None)
        kwargs.pop("codex_should_cancel", None)
        kwargs.pop("codex_on_event", None)
        resolved = self._resolve_model_target(requested_model)
        model = resolved.litellm_model

        # 工具调用不走流式：tool_calls 分片重组容易丢参数，一次性拿完整响应
        completion_kwargs = {
            "model": model,
            "messages": msg_dicts,
            "temperature": self._effective_temperature(resolved),
            "max_tokens": self.config.max_tokens,
            "timeout": self.config.timeout,
            "stream": False,
            "tools": tool_dicts,
            "tool_choice": tool_choice,
        }

        model_lower = model.lower()
        if "gpt-5" in model_lower or "codex" in model_lower:
            completion_kwargs["drop_params"] = True

        extra_body = self._effective_extra_body(resolved)
        if extra_body:
            completion_kwargs["extra_body"] = extra_body

        api_key = self.config.resolve_api_key(resolved.configured_model)
        if api_key:
            completion_kwargs["api_key"] = api_key
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
                "LLM tools call failed model=%s tools=%d elapsed=%.2fs error=%s",
                model,
                len(tool_dicts),
                elapsed,
                exc.__class__.__name__,
            )
            self._raise_config_error_if_needed(exc, resolved)
            raise
        if not response.choices:
            raise RuntimeError("LLM returned empty choices list — possible rate limit or content filter")
        choice = response.choices[0]
        tool_calls = _parse_tool_calls(getattr(choice.message, "tool_calls", None))
        logger.info(
            "LLM tools call finished model=%s tools=%d tool_calls=%d elapsed=%.2fs",
            model,
            len(tool_dicts),
            len(tool_calls),
            time.perf_counter() - start_time,
        )
        return LLMResponse(
            content=choice.message.content or "",
            model=response.model or self.config.model,
            usage=dict(response.usage) if response.usage else {},
            finish_reason=choice.finish_reason or "",
            tool_calls=tool_calls,
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
        # 薄封装：单图调用方零改动；实际实现为多图通道的单个元素调用。
        return self.generate_with_images(
            text_prompt=text_prompt,
            images=[{"b64": image_b64, "mime": image_mime}],
            system_prompt=system_prompt,
            **kwargs,
        )

    def generate_with_images(
        self,
        text_prompt: str,
        images: list,
        system_prompt: str | None = None,
        **kwargs,
    ) -> LLMResponse:
        """
        Call LLM with an ordered list of base64-encoded images + text prompt.

        content 数组按序拼多个 image_url + text（litellm 原生支持，同一
        OpenAI-compatible 格式；providers 的翻译逻辑与 generate_with_image
        完全一致）。单图调用（images 长度为 1）产出的消息与旧
        generate_with_image 逐字节一致。

        Args:
            text_prompt: User text accompanying the images.
            images:      Ordered list of {"b64": str, "mime": str} dicts.
            system_prompt: Optional system message prepended to the call.
        """
        if self._litellm is None:
            raise RuntimeError(
                "litellm is not installed. Install it with: pip install litellm"
            )

        requested_model = kwargs.pop("model", None)
        # ChatGPT Codex（openai-codex）订阅模型：D1 只交付登录与模型选择，
        # 生成能力尚未开放。必须 fail closed——绝不静默回退到 litellm /
        # API-key / 环境变量（BYOA 安全不变量）。
        if self.config._is_codex_app_server_model(requested_model):
            raise RuntimeError(
                "ChatGPT Codex（openai-codex）模型的 CREATE/IMAGE 生成能力"
                "尚未开放：当前版本支持登录、模型选择与 CHAT/EXPLAIN。"
                "请改用其他已配置的模型。"
            )
        kwargs.pop("codex_intent", None)
        kwargs.pop("codex_should_cancel", None)
        kwargs.pop("codex_on_event", None)
        resolved = self._resolve_model_target(requested_model)
        model = resolved.litellm_model

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        content: list = []
        for img in images:
            image_b64 = str(img.get("b64") or "")
            image_mime = str(img.get("mime") or "image/jpeg").strip().lower()
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{image_mime};base64,{image_b64}"},
            })
        content.append({"type": "text", "text": text_prompt})
        messages.append({"role": "user", "content": content})

        completion_kwargs = {
            "model": model,
            "messages": messages,
            "temperature": self._effective_temperature(resolved),
            "max_tokens": self.config.max_tokens,
            "timeout": self.config.timeout,
            "stream": True,
        }

        model_lower = model.lower()
        if "gpt-5" in model_lower or "codex" in model_lower:
            completion_kwargs["drop_params"] = True

        extra_body = self._effective_extra_body(resolved)
        if extra_body:
            completion_kwargs["extra_body"] = extra_body

        api_key = self.config.resolve_api_key(resolved.configured_model)
        if api_key:
            completion_kwargs["api_key"] = api_key

        is_native = self._is_native_provider_model(model)
        api_base = self.config.resolve_api_base(resolved.configured_model)
        if api_base and (resolved.is_custom_provider_request or not is_native):
            completion_kwargs["api_base"] = api_base

        completion_kwargs.update(kwargs)

        start_time = time.perf_counter()
        total_b64_len = sum(len(str(img.get("b64") or "")) for img in images)
        logger.info(
            "LLM vision call started model=%s image_count=%d image_b64_len=%d prompt_len=%d",
            model,
            len(images),
            total_b64_len,
            len(text_prompt or ""),
        )
        try:
            response = self._litellm.completion(**completion_kwargs)
        except Exception as exc:
            elapsed = time.perf_counter() - start_time
            logger.warning(
                "LLM vision call failed model=%s image_count=%d image_b64_len=%d prompt_len=%d elapsed=%.2fs error=%s",
                model,
                len(images),
                total_b64_len,
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
            "LLM vision call finished model=%s image_count=%d image_b64_len=%d prompt_len=%d elapsed=%.2fs",
            model,
            len(images),
            total_b64_len,
            len(text_prompt or ""),
            time.perf_counter() - start_time,
        )
        choice = response.choices[0]
        content_text = choice.message.content or ""
        if not content_text.strip():
            self._raise_if_reasoning_only(choice)
        return LLMResponse(
            content=content_text,
            model=response.model or self.config.model,
            usage=dict(response.usage) if response.usage else {},
            finish_reason=choice.finish_reason or "",
        )

    def _effective_extra_body(self, resolved: _ResolvedModelTarget) -> dict:
        """本次调用生效的 extra_body：provider 条目级覆盖顶层（config.resolve_extra_body）。"""
        try:
            return self.config.resolve_extra_body(resolved.configured_model)
        except Exception:
            return dict(self.config.extra_body or {})

    def _effective_temperature(self, resolved: _ResolvedModelTarget) -> float:
        """本次调用生效的 temperature：provider 条目级覆盖顶层（config.resolve_temperature）。"""
        try:
            return self.config.resolve_temperature(resolved.configured_model)
        except Exception:
            return float(self.config.temperature)

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
                    "chat_completions": "openai",
                    "anthropic": "anthropic",
                    "anthropic_messages": "anthropic",
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

        # 官方模型 → litellm 原生前缀（走 PROVIDER_PROFILES 注册表）
        profile = provider_profile_for_model(configured_model)
        if profile and profile.native_prefix:
            litellm_model = (
                configured_model
                if configured_model.startswith(profile.native_prefix)
                else f"{profile.native_prefix}{configured_model}"
            )
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



def _parse_tool_calls(raw_tool_calls) -> list[ToolCall]:
    """把 litellm 响应里的 tool_calls 解析为 ToolCall 列表（容错 JSON）。"""
    import json as _json

    calls: list[ToolCall] = []
    for tc in raw_tool_calls or []:
        function = getattr(tc, "function", None) or {}
        name = getattr(function, "name", None) or (function.get("name") if isinstance(function, dict) else "")
        raw_args = getattr(function, "arguments", None) or (
            function.get("arguments") if isinstance(function, dict) else ""
        ) or ""
        try:
            arguments = _json.loads(raw_args) if raw_args else {}
            if not isinstance(arguments, dict):
                arguments = {}
        except Exception:
            logger.warning("Tool call arguments not valid JSON for %s: %.120s", name, raw_args)
            arguments = {}
        calls.append(ToolCall(
            id=str(getattr(tc, "id", "") or ""),
            name=str(name or ""),
            arguments=arguments,
            raw_arguments=str(raw_args),
        ))
    return calls


class MockLLM:
    """
    Mock LLM for testing without API access.

    Accepts a list of responses that will be returned in order.
    响应条目可以是 str（纯文本），也可以是 dict：
    {"content": "...", "tool_calls": [{"name": "...", "arguments": {...}}]}
    —— 用于测试 generate_with_tools 的 agent loop。
    """

    def __init__(self, responses: Optional[list] = None):
        self.responses = responses or ["<!-- Mock LLM response -->"]
        self.call_count = 0
        self.call_history: list[list[Message]] = []

    def _next_response(self, messages: list) -> LLMResponse:
        import json as _json

        self.call_history.append(messages)
        idx = min(self.call_count, len(self.responses) - 1)
        scripted = self.responses[idx]
        self.call_count += 1
        if isinstance(scripted, dict):
            tool_calls = [
                ToolCall(
                    id=tc.get("id", f"mock_call_{i}"),
                    name=tc.get("name", ""),
                    arguments=tc.get("arguments", {}) or {},
                    raw_arguments=_json.dumps(tc.get("arguments", {}) or {}, ensure_ascii=False),
                )
                for i, tc in enumerate(scripted.get("tool_calls", []))
            ]
            return LLMResponse(
                content=scripted.get("content", ""),
                model="mock-model",
                usage={"prompt_tokens": 100, "completion_tokens": 200},
                finish_reason="tool_calls" if tool_calls else "stop",
                tool_calls=tool_calls,
            )
        return LLMResponse(
            content=scripted,
            model="mock-model",
            usage={"prompt_tokens": 100, "completion_tokens": 200},
            finish_reason="stop",
        )

    def generate(self, messages: list[Message], **kwargs) -> LLMResponse:
        return self._next_response(messages)

    def generate_with_tools(self, messages: list, tools: list, **kwargs) -> LLMResponse:
        return self._next_response(messages)

    def generate_with_image(
        self,
        text_prompt: str,
        image_b64: str,
        image_mime: str = "image/jpeg",
        system_prompt: str | None = None,
        **kwargs,
    ) -> LLMResponse:
        """Mock vision 调用：同 generate 语义（按序返回脚本化响应）。"""
        return self.generate_with_images(
            text_prompt=text_prompt,
            images=[{"b64": image_b64, "mime": image_mime}],
            system_prompt=system_prompt,
            **kwargs,
        )

    def generate_with_images(
        self,
        text_prompt: str,
        images: list,
        system_prompt: str | None = None,
        **kwargs,
    ) -> LLMResponse:
        """Mock 多图 vision 调用：content 数组与 llm.py 同构，响应按序脚本化。"""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        content = []
        for img in images:
            image_b64 = str(img.get("b64") or "")
            image_mime = str(img.get("mime") or "image/jpeg").strip().lower()
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{image_mime};base64,{image_b64}"},
            })
        content.append({"type": "text", "text": text_prompt})
        messages.append({"role": "user", "content": content})
        return self._next_response(messages)
