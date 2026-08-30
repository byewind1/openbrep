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
from openbrep.config import (
    API_MODE_RESPONSES,
    PROVIDER_PROFILES,
    LLMConfig,
    provider_profile_for_model,
)

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
    # 自定义 provider 条目的 api_mode（chat_completions 默认 / anthropic_messages /
    # responses / codex_app_server）。官方（非自定义）模型恒为空字符串——
    # 由 litellm 原生前缀决定传输层，从不走 responses 分支。
    api_mode: str = ""


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
    # D6：结构化元数据（如 codex 的 effective model/reasoning_effort），
    # 供 pipeline 写入任务结果元数据；非 codex 路径保持空 dict。
    metadata: dict = field(default_factory=dict)

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
        # D6：最近一次 codex turn 的 effective {model, reasoning_effort}
        # （来自实际 CodexTurnResult；非 codex 路径从不设置）。
        self.last_codex_effective: dict | None = None
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
            if resolved.api_mode and resolved.api_mode != "chat_completions":
                details.append(f"api_mode={resolved.api_mode}")
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

    # ── Codex BYOA（openai-codex）薄分派（D3 + D4）────────────────────────
    # D1：openai-codex 订阅模型绝不落到 litellm / API-key / 环境变量（fail closed）。
    # D3：CHAT/EXPLAIN（codex_intent="CHAT"，由 pipeline 显式传入）走 app-server turn。
    # D4：文本 CREATE（codex_intent="CREATE"）走同一 turn 契约——Codex 只负责生成
    # final text（[FILE:] 协议），OpenBrep 负责解析/落盘/编译/验证/修复/delivery gate。
    # 其余意图（MODIFY/DEBUG/IMAGE/工具调用等）保持 fail closed。

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

    def _codex_turn_generate(self, msg_dicts: list, model: str, **kwargs) -> LLMResponse:
        """CHAT/EXPLAIN、文本 CREATE 与图片 CREATE 走 Codex app-server turn
        （ephemeral thread + 临时只读 cwd + approval never，见 openbrep/codex/turn.py）。

        CHAT/EXPLAIN 的 content 是回复文本；CREATE 的 content 是按 [FILE:]
        协议的完整生成文本（由 pipeline/GDLAgent 负责解析、落盘与验证）。
        D5 图片 CREATE：``images=[{"b64", "mime"}]`` 只含当前请求已授权图片，
        provider.chat 把字节物化进临时 cwd（不透明文件名），app-server 只通过
        localImage 收到这些物化文件，绝不收到用户提供的本地路径。
        错误一律稳定文案（error_response / turn 层稳定文案），绝不透传上游原文。
        """
        from openbrep.codex.errors import STABLE_MESSAGES
        from openbrep.codex.turn import NO_FINAL_MESSAGE_TEXT

        images = kwargs.pop("images", None) or []
        should_cancel = kwargs.pop("codex_should_cancel", None)
        on_event = kwargs.pop("codex_on_event", None)
        timeout = kwargs.pop("timeout", None) or self.config.timeout
        # D6：Fixed 模式 reasoning effort（"" = 不覆盖模型默认）。
        # 只对 codex 模型生效；provider.chat 运行时刻再校验支持性（fail closed）。
        reasoning_effort = str(kwargs.pop("codex_reasoning_effort", None) or "").strip()

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
                images=list(images) or None,
                reasoning_effort=reasoning_effort,
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
        # D6：effective 组合来自实际 turn 结果（与 fake server 实收逐字节一致）。
        # getattr 兜底兼容旧测试替身（无 model/reasoning_effort 属性的结果对象）。
        effective = {
            "model": getattr(result, "model", None) or model,
            "reasoning_effort": str(getattr(result, "reasoning_effort", None) or ""),
        }
        self.last_codex_effective = effective
        return LLMResponse(
            content=result.content,
            model=model,
            usage=dict(result.usage or {}),
            finish_reason="stop",
            metadata={"codex_effective": effective},
        )

    def _responses_generate(
        self,
        msg_dicts: list,
        resolved: _ResolvedModelTarget,
        *,
        tools: list | None = None,
        tool_choice: str = "auto",
        **kwargs,
    ) -> LLMResponse:
        """OpenAI Responses API（/v1/responses）文本与工具调用通道。

        由 generate / generate_with_tools / generate_with_images 在解析到
        api_mode=responses 的自定义 provider 时分派，绝不用于官方裸模型或
        codex_app_server（那些走各自既有路径）。要点：

        - system → instructions；图片块 → input_image；assistant tool_calls →
          function_call items；tool 结果 → function_call_output items。
        - 非流式（stream=False）：OpenBrep 调用方本就自行聚合整段输出，
          Responses 聚合语义与 chat 流式一致，响应体一次成型更稳。
        - gpt-5/codex/o1/o3/o4 推理模型不传 temperature（OpenAI 只接受
          temperature=1 或 reasoning.effort=none 的组合；不传 = 模型默认）。
        - 错误分类复用 _raise_config_error_if_needed（litellm 异常类型一致）。
        """
        model = resolved.litellm_model
        input_items, instructions = _responses_input_items(msg_dicts)
        resp_kwargs: dict = {
            "model": model,
            "input": input_items,
            "max_output_tokens": int(kwargs.pop("max_tokens", None) or self.config.max_tokens),
            "timeout": float(kwargs.pop("timeout", None) or self.config.timeout),
            "stream": False,
        }
        if instructions:
            resp_kwargs["instructions"] = instructions

        model_lower = model.lower()
        if any(token in model_lower for token in ("gpt-5", "codex", "o1", "o3", "o4")):
            # OpenAI 推理模型的 temperature 约束：不传 = 模型默认（与 chat 路径
            # drop_params 的净效果一致），避免 gpt-5 400 / o 系静默忽略
            kwargs.pop("temperature", None)
        else:
            temperature = kwargs.pop("temperature", None)
            if temperature is None:
                temperature = self._effective_temperature(resolved)
            resp_kwargs["temperature"] = float(temperature)

        extra_body = self._effective_extra_body(resolved)
        if extra_body:
            resp_kwargs["extra_body"] = extra_body

        api_key = self.config.resolve_api_key(resolved.configured_model)
        if api_key:
            resp_kwargs["api_key"] = api_key
        is_native = self._is_native_provider_model(model)
        api_base = self.config.resolve_api_base(resolved.configured_model)
        if api_base and (resolved.is_custom_provider_request or not is_native):
            resp_kwargs["api_base"] = api_base

        if tools:
            resp_kwargs["tools"] = tools
            resp_kwargs["tool_choice"] = tool_choice

        # mock_response 测试通道透传（与 completion 路径一致）
        mock_response = kwargs.pop("mock_response", None)
        if mock_response is not None:
            resp_kwargs["mock_response"] = mock_response

        start_time = time.perf_counter()
        try:
            response = self._litellm.responses(**resp_kwargs)
        except Exception as exc:
            elapsed = time.perf_counter() - start_time
            logger.warning(
                "LLM responses call failed model=%s input_items=%d elapsed=%.2fs error=%s",
                model,
                len(input_items),
                elapsed,
                exc.__class__.__name__,
            )
            self._raise_config_error_if_needed(exc, resolved)
            raise
        content, tool_calls, finish_reason, saw_reasoning = _responses_parse(response)
        logger.info(
            "LLM responses call finished model=%s input_items=%d tool_calls=%d elapsed=%.2fs",
            model,
            len(input_items),
            len(tool_calls),
            time.perf_counter() - start_time,
        )
        if not content.strip() and not tool_calls:
            if saw_reasoning:
                raise RuntimeError(
                    "LLM 只输出了思考过程，没有输出内容。\n"
                    "原因：该模型把所有输出配额用于内部 reasoning（Responses API 的"
                    " reasoning item）。\n→ 提高 max_tokens，或在 provider 条目的"
                    " extra_body 里设置 reasoning = { effort = \"low\" } 等更低档位。"
                )
            raise RuntimeError(
                "LLM 返回了空内容 — 可能是内容过滤、模型配置错误，或该模型不适合当前任务。"
            )
        return LLMResponse(
            content=content,
            model=_responses_item_value(response, "model", "") or self.config.model,
            usage=_responses_usage(response),
            finish_reason=finish_reason,
            tool_calls=tool_calls,
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
        # D3 + D4：openai-codex 订阅模型——只有显式 CHAT/EXPLAIN/CREATE 意图走
        # turn（文本生成契约）；其余意图 fail closed（绝不落到 litellm / API-key）。
        if self.config._is_codex_app_server_model(requested_model):
            codex_intent = kwargs.pop("codex_intent", None)
            if codex_intent in ("CHAT", "CREATE"):
                codex_model = requested_model or self.config.model
                return self._codex_turn_generate(msg_dicts, model=codex_model, **kwargs)
            raise RuntimeError(
                "ChatGPT Codex（openai-codex）模型的 MODIFY/DEBUG/IMAGE 生成能力"
                "尚未开放：当前支持登录、模型选择、CHAT/EXPLAIN 与文本 CREATE。"
                "请改用其他已配置的模型。"
            )
        # 非 codex 模型：忽略 codex 专用参数（绝不让它们进入 litellm）
        kwargs.pop("codex_intent", None)
        kwargs.pop("codex_should_cancel", None)
        kwargs.pop("codex_on_event", None)
        kwargs.pop("codex_reasoning_effort", None)
        resolved = self._resolve_model_target(requested_model)
        # Responses API（api_mode=responses）自定义 provider：走 /v1/responses
        if resolved.api_mode == API_MODE_RESPONSES:
            return self._responses_generate(msg_dicts, resolved=resolved, **kwargs)
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
                "ChatGPT Codex（openai-codex）模型的 MODIFY/DEBUG 工具面"
                "尚未开放：当前支持登录、模型选择、CHAT/EXPLAIN 与文本 CREATE。"
                "请改用其他已配置的模型。"
            )
        kwargs.pop("codex_intent", None)
        kwargs.pop("codex_should_cancel", None)
        kwargs.pop("codex_on_event", None)
        kwargs.pop("codex_reasoning_effort", None)
        resolved = self._resolve_model_target(requested_model)
        # Responses API（api_mode=responses）：function_call/function_call_output
        # items 与 tools 一起走 litellm.responses()
        if resolved.api_mode == API_MODE_RESPONSES:
            return self._responses_generate(
                msg_dicts,
                resolved=resolved,
                tools=tool_dicts,
                tool_choice=tool_choice,
                **kwargs,
            )
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
        # D5 + HF2：ChatGPT Codex（openai-codex）订阅模型的图片通道——
        # 只有显式 codex_intent ∈ {CREATE, IMAGE, MODIFY} 才走 app-server turn
        # （CREATE/IMAGE 由 pipeline 注入并经历提取确认门；MODIFY 由 codex 桥
        # 接的简化档 harness 注入，只做提取 hint，原图不进入 MODIFY turn）。
        # localImage 只收当前请求已授权图片（见 provider.chat 物化）。其余
        # （旧单图 image_b64 字段 / MODIFY 生成旧通道 / 无确认门的直接调用）
        # 一律 fail closed，绝不静默回退到 litellm / API-key / 环境变量
        # （BYOA 安全不变量）。
        if self.config._is_codex_app_server_model(requested_model):
            codex_intent = kwargs.pop("codex_intent", None)
            if codex_intent in ("CREATE", "IMAGE", "MODIFY"):
                codex_model = requested_model or self.config.model
                turn_messages = []
                if system_prompt:
                    turn_messages.append({"role": "system", "content": system_prompt})
                turn_messages.append({"role": "user", "content": text_prompt})
                return self._codex_turn_generate(
                    turn_messages,
                    model=codex_model,
                    images=list(images),
                    **kwargs,
                )
            raise RuntimeError(
                "ChatGPT Codex（openai-codex）模型图片通道拒绝该请求：Codex "
                "图片通道必须经显式 codex_intent（CREATE/IMAGE 提取确认流程，"
                "或 codex MODIFY 桥接提取）授权（旧单图 image_b64 通道/无意图"
                "调用不属于此范围）。请通过工作台带图创建后确认读图结果。"
            )
        kwargs.pop("codex_intent", None)
        kwargs.pop("codex_should_cancel", None)
        kwargs.pop("codex_on_event", None)
        kwargs.pop("codex_reasoning_effort", None)
        resolved = self._resolve_model_target(requested_model)

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
        # Responses API（api_mode=responses）：图片块在 _responses_generate 内
        # 转成 input_image item（消息形状与 chat 路径保持逐字节一致）
        if resolved.api_mode == API_MODE_RESPONSES:
            return self._responses_generate(messages, resolved=resolved, **kwargs)
        model = resolved.litellm_model

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
                    # responses 是 OpenAI 线协议：litellm 前缀 openai/ +
                    # 调用方走 litellm.responses()（见 _responses_generate）
                    "responses": "openai",
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
                api_mode=str(
                    custom_match.get("api_mode", "chat_completions") or "chat_completions"
                ),
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


# ── OpenAI Responses API（/v1/responses）消息转换与输出解析 ────────────────
# Responses API 的 input 是"item 数组"而非 chat messages：message / function_call
# / function_call_output / reasoning 等带 type 的条目。system 消息收敛到
# instructions 参数（OpenAI 官方 chat→responses 迁移语义）；chat 风格的
# image_url 内容块转成 input_image 条目。解析侧把 output 数组里的 message 文本
# 与 function_call 还原成与 chat 路径一致的 LLMResponse 形状，上层零改动。


def _responses_text_of_content(content) -> str:
    """从 chat 消息 content（str 或 list）提取纯文本，供 system 消息/说明用。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    texts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") in (None, "text"):
            text = str(block.get("text") or "")
            if text:
                texts.append(text)
    return "\n".join(texts)


def _responses_content_parts(content) -> list:
    """把 chat 内容（str 或 OpenAI content 数组）转成 Responses API 内容块。

    text → {"type": "input_text", ...}；image_url → {"type": "input_image", ...}
    （data URI 原样透传）。空内容返回 []（调用方决定是否生成 message item）。
    """
    if content is None:
        return []
    if isinstance(content, str):
        text = content.strip()
        if not text:
            return []
        return [{"type": "input_text", "text": content}]
    parts: list = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type in (None, "text"):
            text = str(block.get("text") or "")
            if text:
                parts.append({"type": "input_text", "text": text})
        elif block_type == "image_url":
            image_url = block.get("image_url") or {}
            url = image_url.get("url") if isinstance(image_url, dict) else str(image_url)
            if url:
                part: dict = {"type": "input_image", "image_url": url}
                if isinstance(image_url, dict) and image_url.get("detail"):
                    part["detail"] = str(image_url["detail"])
                parts.append(part)
    return parts


def _responses_input_items(messages: list) -> tuple[list, str]:
    """把聊天消息列表转成 Responses API input items + instructions。

    - system 消息 → 全部收敛进 instructions（空前缀/中缀都行，Responses 不要求
      role 顺序，但 instructions 是官方迁移语义；多段用空行连接）。
    - user 消息 → message item（文本 + 图片块）。
    - assistant 消息 → 文本 message item + function_call items（按 tool_calls）。
    - tool 消息 → function_call_output items。
    """
    items: list = []
    instructions: list[str] = []
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "").strip()
        content = msg.get("content")
        if role == "system":
            text = _responses_text_of_content(content).strip()
            if text:
                instructions.append(text)
            continue
        if role == "user":
            parts = _responses_content_parts(content)
            if parts:
                items.append({"type": "message", "role": "user", "content": parts})
            continue
        if role == "assistant":
            text = content if isinstance(content, str) else _responses_text_of_content(content)
            tool_calls = msg.get("tool_calls") or []
            if text and text.strip():
                items.append({
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text}],
                })
            for tc in tool_calls:
                if isinstance(tc, dict):
                    function = tc.get("function") or {}
                else:
                    function = getattr(tc, "function", None) or {}
                fn = function if isinstance(function, dict) else {}
                name = str(fn.get("name") or "")
                raw_args = str(fn.get("arguments") or "") or "{}"
                call_id = str(tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", "") or "")
                items.append({
                    "type": "function_call",
                    "call_id": call_id,
                    "name": name,
                    "arguments": raw_args,
                })
            continue
        if role == "tool":
            items.append({
                "type": "function_call_output",
                "call_id": str(msg.get("tool_call_id") or ""),
                "output": str(content or ""),
            })
            continue
        # 其他 role（如 codex 私有 role）不参与 responses 传输
    return items, "\n\n".join(instructions)


def _responses_item_value(item, name: str, default=""):
    """从 responses output item（对象或 dict）取字段。"""
    if isinstance(item, dict):
        value = item.get(name)
        return value if value is not None else default
    return getattr(item, name, default) or default


def _responses_parse(response) -> tuple[str, list, str, bool]:
    """解析 litellm.responses() 返回：content / tool_calls / finish_reason / saw_reasoning。

    finish_reason：status=completed → "stop"；其余原样（用户可据 status 判断）。
    tool_calls 复用 ToolCall 形状（arguments 容错 JSON，raw_arguments 保留原文）。
    """
    import json as _json

    content_parts: list[str] = []
    calls: list[ToolCall] = []
    saw_reasoning = False
    status = str(_responses_item_value(response, "status", ""))
    finish_reason = "stop" if status == "completed" else status
    output = _responses_item_value(response, "output", None) or []
    if isinstance(output, dict):
        output = []
    for item in output or []:
        item_type = str(_responses_item_value(item, "type", ""))
        if item_type == "message":
            for part in _responses_item_value(item, "content", None) or []:
                part_type = str(_responses_item_value(part, "type", ""))
                if part_type == "output_text":
                    text = str(_responses_item_value(part, "text", ""))
                    if text:
                        content_parts.append(text)
                elif part_type == "refusal":
                    text = str(_responses_item_value(part, "refusal", ""))
                    if text:
                        content_parts.append(text)
        elif item_type == "function_call":
            raw_args = str(_responses_item_value(item, "arguments", "") or "") or "{}"
            name = str(_responses_item_value(item, "name", ""))
            call_id = str(_responses_item_value(item, "call_id", "")) or str(
                _responses_item_value(item, "id", "")
            )
            try:
                arguments = _json.loads(raw_args)
                if not isinstance(arguments, dict):
                    arguments = {}
            except Exception:
                logger.warning(
                    "Responses function_call args not valid JSON for %s: %.120s", name, raw_args
                )
                arguments = {}
            calls.append(ToolCall(
                id=call_id,
                name=name,
                arguments=arguments,
                raw_arguments=raw_args,
            ))
        elif item_type == "reasoning":
            saw_reasoning = True
    return "".join(content_parts), calls, finish_reason, saw_reasoning


def _responses_usage(response) -> dict:
    """usage 转 dict：pydantic 模型用 model_dump，避免 dict() 触发序列化告警。"""
    usage = _responses_item_value(response, "usage", None)
    if not usage:
        return {}
    if isinstance(usage, dict):
        return dict(usage)
    try:
        dumped = usage.model_dump(exclude_none=True)
        return dict(dumped) if isinstance(dumped, dict) else {}
    except Exception:
        return {}


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
