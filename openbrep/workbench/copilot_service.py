"""Workbench service: Archicad GDL Copilot（移植自 ADDON 仓 copilot/server.py）。

把 ADDON 仓 ``copilot/server.py`` 的 FastAPI 后端逻辑移植为工作台 service 模块：

- 工作台路由层是 dict 进 / dict 出，本模块在方法内手写类型校验，
  不引入 FastAPI / pydantic 依赖。
- 剪贴板监听线程改为实例级懒启动（首次任意公开方法调用时），daemon，仅 macOS；
  AppKit 不可用时自动落 pbpaste；两者都不可用则 buffer 恒空。
- LLMAdapter 构建直接复用 ``WorkbenchSession.config``（已加载的 GDLAgentConfig，
  含 custom_providers / provider_keys 等完整配置），与 settings 保存实时同步。
"""

from __future__ import annotations

import platform
import re
import subprocess
import threading
import time
from typing import TYPE_CHECKING, Any

try:
    from AppKit import NSPasteboard, NSStringPboardType
except Exception:  # pragma: no cover - macOS only
    NSPasteboard = None  # type: ignore[assignment]
    NSStringPboardType = None  # type: ignore[assignment]

from openbrep.config import GDLAgentConfig
from openbrep.llm import LLMAdapter

if TYPE_CHECKING:
    from openbrep.workbench_api import WorkbenchSession

# 与 ADDON copilot/server.py 的 FastAPI app version 保持一致
SERVICE_VERSION = "0.2.0"
# Copilot 面板要求的最低 ADDON 版本（addon 侧以该字段做能力门禁）
MIN_ADDON_VERSION = "0.4.0"

SYSTEM_PROMPT = """你是 Archicad GDL 脚本 AI 修复助手。
用户会粘贴编译报错或出问题的代码片段。
任务：
1. 一句话说清楚问题原因
2. 给出可直接粘回 Archicad GDL 编辑器的修复代码
3. 代码用 ```gdl ``` 包裹
不废话，建筑师只要能跑的代码。若信息不足主动追问。"""

ERROR_SUMMARY_SYSTEM_PROMPT = (
    "你是 GDL 错误分析助手，把以下编译错误列表总结成一句简洁的中文描述"
    "（不超过200字），只说错误位置和原因，不给修复建议。"
)
ERROR_CLIPBOARD_PATTERN = re.compile(r"(line|\.gsm|\.gdl|error|warning|错误|警告)", re.IGNORECASE)
FALLBACK_MODELS = ["moonshotai/kimi-k2.5"]


def _is_error_clipboard_text(value: str) -> bool:
    return bool(ERROR_CLIPBOARD_PATTERN.search(value))


def _read_clipboard_text_pbpaste() -> str:
    try:
        return subprocess.check_output(["pbpaste"], text=True, timeout=1).strip()
    except Exception:
        return ""


def _read_clipboard_text_appkit() -> str:
    if NSPasteboard is None:
        return ""

    try:
        pb = NSPasteboard.generalPasteboard()
        text = pb.stringForType_(NSStringPboardType)
        return str(text).strip() if text else ""
    except Exception:
        return ""


def _read_clipboard_snapshot() -> tuple[str, str]:
    """剪贴板快照：优先 AppKit，失败自动落 pbpaste；两者都不可用返回空串。"""
    appkit_text = _read_clipboard_text_appkit()
    shell_text = _read_clipboard_text_pbpaste()

    if appkit_text:
        return appkit_text, "appkit"
    return shell_text, "pbpaste"


def _extract_gdl_code_blocks(text: str) -> list[str]:
    pattern = re.compile(r"```gdl\s*(.*?)\s*```", re.IGNORECASE | re.DOTALL)
    return [m.group(1).strip() for m in pattern.finditer(text)]


def _is_model_route_unavailable(exc: Exception) -> bool:
    message = str(exc).lower()
    signals = (
        "model_not_found",
        "无可用渠道",
        "serviceunavailableerror",
        "service unavailable",
        "no route",
        "route unavailable",
        "deployment_not_found",
        "deployment not found",
        "model not found",
        "no available provider",
    )
    return any(signal in message for signal in signals)


def _is_transient_upstream_error(exc: Exception) -> bool:
    message = str(exc).lower()
    signals = (
        "timeout",
        "timed out",
        "readtimeout",
        "connecttimeout",
        "connection reset",
        "connection refused",
        "temporarily unavailable",
        "bad gateway",
        "gateway error",
        "upstream",
    )
    return any(signal in message for signal in signals)


def _generate_with_fallback(llm: LLMAdapter, messages: list[dict[str, Any]]):
    """主模型路由不可用时依次尝试 FALLBACK_MODELS（语义照搬 addon server.py）。"""
    try:
        return llm.generate(messages)
    except Exception as primary_exc:
        if not _is_model_route_unavailable(primary_exc):
            raise

        for model in FALLBACK_MODELS:
            if model.strip() == llm.config.model.strip():
                continue
            try:
                return llm.generate(messages, model=model)
            except Exception:
                continue

        raise primary_exc


def _build_messages(
    message: str,
    history: list[dict[str, Any]],
    images: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """构造 LLM 消息序列（对应 addon server.py 的 ChatRequest 语义）。"""
    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]

    for item in history:
        role = str(item.get("role") or "user")
        role = role if role in {"user", "assistant"} else "user"
        content = str(item.get("content") or "").strip()
        if content:
            messages.append({"role": role, "content": content})

    valid_images = [img for img in (images or []) if str(img.get("b64") or "").strip()]
    if valid_images:
        user_content: list[dict[str, Any]] = []
        for img in valid_images:
            user_content.append({
                "type": "image_url",
                "image_url": {
                    "url": (
                        f"data:{img.get('mime') or 'image/png'};"
                        f"base64,{str(img.get('b64')).strip()}"
                    ),
                },
            })
        user_content.append({"type": "text", "text": message})
        messages.append({"role": "user", "content": user_content})
    else:
        messages.append({"role": "user", "content": message})

    return messages


class WorkbenchCopilotService:
    """Archicad GDL Copilot 工作台服务（原 addon copilot/server.py 的后端逻辑）。

    构造接收 ``WorkbenchSession``（同其他 workbench service 的范式）。
    """

    def __init__(self, session: "WorkbenchSession") -> None:
        self.session = session
        self._clipboard_lock = threading.Lock()
        self._clipboard_buffer: list[str] = []
        self._clipboard_last_signature = ""
        self._clipboard_thread: threading.Thread | None = None

    # ── 公开 API（dict 进 / dict 出）──────────────────────────────

    def status(self) -> dict[str, Any]:
        self._ensure_clipboard_watch()
        return {"ok": True, "version": SERVICE_VERSION, "min_addon_version": MIN_ADDON_VERSION}

    def route(self, method: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """Dispatch the service-owned ``/api/copilot/*`` route family."""
        if method == "GET" and path == "/api/copilot/status":
            return self.status()
        if method == "POST" and path == "/api/copilot/chat":
            return self.chat(body)
        if method == "GET" and path == "/api/copilot/clipboard-buffer":
            return self.clipboard_buffer()
        if method == "POST" and path == "/api/copilot/clipboard-buffer/clear":
            return self.clipboard_buffer_clear(body)
        if method == "POST" and path == "/api/copilot/summarize-errors":
            return self.summarize_errors()
        return {"ok": False, "error": f"Unknown route: {method} {path}"}

    def chat(self, body: dict[str, Any]) -> dict[str, Any]:
        """请求 ``{message, history[], images[]?}``，响应 ``{ok, reply, code_blocks[]}``。

        LLM 错误映射为 ``{ok: False, error, status}``，保留 addon 原文的
        400（配置/鉴权）/ 503（模型路由不可用或上游瞬时故障）/ 500（其他）语义。
        """
        self._ensure_clipboard_watch()
        body = body or {}

        message = str(body.get("message") or "").strip()
        if not message:
            return {"ok": False, "error": "message is required.", "status": 400}

        history = body.get("history", [])
        if not isinstance(history, list):
            return {"ok": False, "error": "history must be a list.", "status": 400}
        if any(not isinstance(item, dict) for item in history):
            return {"ok": False, "error": "history items must be objects.", "status": 400}
        images = body.get("images")
        if images is not None and not isinstance(images, list):
            return {"ok": False, "error": "images must be a list.", "status": 400}
        if images is not None and any(not isinstance(item, dict) for item in images):
            return {"ok": False, "error": "image items must be objects.", "status": 400}

        llm = self._create_llm_adapter()
        messages = _build_messages(message, history, images)
        try:
            resp = _generate_with_fallback(llm, messages)
        except RuntimeError as exc:
            err_text = str(exc)
            err_lower = err_text.lower()
            if (
                err_text.startswith("LLM 配置错误：")
                or "authentication" in err_lower
                or "unauthorized" in err_lower
            ):
                return {"ok": False, "error": err_text, "status": 400}
            return {"ok": False, "error": err_text or "LLM 暂时不可用", "status": 503}
        except Exception as exc:
            err_text = str(exc)
            if _is_model_route_unavailable(exc) or _is_transient_upstream_error(exc):
                return {
                    "ok": False,
                    "error": "当前模型暂不可用，已尝试备用模型仍失败，请稍后重试。",
                    "status": 503,
                }
            return {"ok": False, "error": err_text or "后端内部错误", "status": 500}

        reply = (resp.content or "").strip()
        code_blocks = _extract_gdl_code_blocks(reply)
        return {"ok": True, "reply": reply, "code_blocks": code_blocks}

    def clipboard_buffer(self) -> dict[str, Any]:
        self._ensure_clipboard_watch()
        with self._clipboard_lock:
            return {"ok": True, "items": list(self._clipboard_buffer)}

    def clipboard_buffer_clear(self, body: dict[str, Any]) -> dict[str, Any]:
        """清空 buffer 并用 body.items（可选）回填；非错误类文本被过滤。"""
        self._ensure_clipboard_watch()
        body = body or {}

        raw_items = body.get("items")
        if raw_items is None:
            items: list[str] = []
        elif isinstance(raw_items, list):
            items = [str(item) for item in raw_items]
        else:
            return {"ok": False, "error": "items must be a list.", "status": 400}

        normalized = [
            item.strip() for item in items if item.strip() and _is_error_clipboard_text(item)
        ]
        with self._clipboard_lock:
            self._clipboard_buffer.clear()
            self._clipboard_buffer.extend(normalized)
            return {"ok": True, "items": list(self._clipboard_buffer)}

    def summarize_errors(self) -> dict[str, Any]:
        """合并剪贴板错误列表为一句中文摘要（addon 语义：消费后清空 buffer）。"""
        self._ensure_clipboard_watch()
        with self._clipboard_lock:
            errors = list(self._clipboard_buffer)
            self._clipboard_buffer.clear()

        if not errors:
            return {"ok": True, "summary": ""}

        llm = self._create_llm_adapter()
        merged = "\n\n".join(f"[{idx + 1}] {item}" for idx, item in enumerate(errors))
        messages = [
            {"role": "system", "content": ERROR_SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": merged},
        ]

        try:
            resp = llm.generate(messages)
            summary = (resp.content or "").strip()
        except Exception:
            summary = ""

        if not summary:
            summary = "；".join(errors[:2])
            if len(summary) > 100:
                summary = summary[:100]

        if len(summary) > 100:
            summary = summary[:100]

        return {"ok": True, "summary": summary}

    # ── 内部实现 ─────────────────────────────────────────────────

    def _create_llm_adapter(self) -> LLMAdapter:
        """构建 LLMAdapter。

        与 blender_import_service 的 ``LLMConfig(model=..., api_key=...,
        api_base=...)`` 范式不同：直接复用 ``session.config`` ——
        WorkbenchSession.__init__ 已通过 load_workbench_config(config_path) 把
        config.toml 加载成 GDLAgentConfig，且 update_llm_settings 等设置变更会
        原地修改并持久化它（见 workbench_api.py / settings_service.py），因此
        ``session.config.llm`` 是含 custom_providers / provider_keys / timeout
        等完整配置的最新 LLMConfig，无需再 GDLAgentConfig.load 一次。
        """
        cfg = getattr(self.session, "config", None)
        if cfg is None or not hasattr(cfg, "llm"):
            cfg = GDLAgentConfig.load(self.session.config_path)
        return LLMAdapter(cfg.llm)

    def _ensure_clipboard_watch(self) -> None:
        """懒启动剪贴板监听线程：仅 macOS、daemon、每个 service 实例一个。"""
        if platform.system() != "Darwin":
            return
        with self._clipboard_lock:
            if self._clipboard_thread is not None and self._clipboard_thread.is_alive():
                return
            self._clipboard_thread = threading.Thread(
                target=self._clipboard_watch_loop, daemon=True
            )
            self._clipboard_thread.start()

    def _clipboard_watch_iteration(self) -> None:
        """单次监听 tick：读剪贴板 → 按签名去重 → 错误类文本入 buffer。"""
        value, source = _read_clipboard_snapshot()
        if not value:
            return
        signature = f"{source}:{value}"
        if signature == self._clipboard_last_signature:
            return
        self._clipboard_last_signature = signature
        if _is_error_clipboard_text(value):
            with self._clipboard_lock:
                if value not in self._clipboard_buffer:
                    self._clipboard_buffer.append(value)

    def _clipboard_watch_loop(self) -> None:
        while True:
            self._clipboard_watch_iteration()
            time.sleep(0.8)
