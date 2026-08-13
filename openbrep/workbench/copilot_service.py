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

import json
import logging
import platform
import re
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

try:
    from AppKit import NSPasteboard, NSStringPboardType
except Exception:  # pragma: no cover - macOS only
    NSPasteboard = None  # type: ignore[assignment]
    NSStringPboardType = None  # type: ignore[assignment]

from openbrep.config import GDLAgentConfig
from openbrep.learning import (
    ERROR_LESSONS_FILE,
    ErrorLearningStore,
    ErrorLesson,
    _lesson_from_dict,
    _merge_lesson,
    classify_error,
    error_fingerprint,
    guidance_for_category,
    summarize_error,
)
from openbrep.llm import LLMAdapter

if TYPE_CHECKING:
    from openbrep.workbench_api import WorkbenchSession

logger = logging.getLogger(__name__)

# 与 ADDON copilot/server.py 的 FastAPI app version 保持一致
SERVICE_VERSION = "0.2.0"
# Copilot 面板要求的最低 ADDON 版本（addon 侧以该字段做能力门禁）
MIN_ADDON_VERSION = "0.4.0"

# 全局错题本（E1：错误自动沉淀的去重落盘目标；与工作区级
# <work_dir>/.openbrep/memory/learnings/error_lessons.jsonl 相互独立）
GLOBAL_ERROR_LESSONS_PATH = Path.home() / ".openbrep" / ERROR_LESSONS_FILE

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


# E4：沉淀侧结构化匹配的结构信号（与 ERROR_CLIPBOARD_PATTERN 的宽关键词互补）。
# at line N 是 line N 的子集，这里按任务卡列出的形状逐一保留，便于阅读。
_STRUCTURED_ERROR_SIGNALS = (
    re.compile(r"\bline\s*\d+", re.IGNORECASE),
    re.compile(r"\bat\s+line\s*\d+", re.IGNORECASE),
    re.compile(r"第\s*\d+\s*行"),
    re.compile(r"\.gdl\b", re.IGNORECASE),
    re.compile(r"\.gsm\b", re.IGNORECASE),
)


def _is_structured_error_text(value: str) -> bool:
    """沉淀侧更严格的结构化匹配（E4）：错误关键词 + 结构信号，且非文档形状。

    - 错误关键词沿用 ERROR_CLIPBOARD_PATTERN 语义（error/warning/错误/警告等）；
    - 结构信号至少一条：``line N`` / ``at line N`` / ``第N行`` / ``.gdl`` 或 ``.gsm``；
    - 文档形状直接拒：任一行以 ``#`` 开头，或含 ``` 代码围栏。

    与 ``_is_error_clipboard_text``（buffer 用，单词命中即真）互不干扰：
    本判定只服务于自动沉淀路径，不满足 → 不沉淀（与"非错误文本不沉淀"同一语义）。
    """
    text = str(value or "")
    if not ERROR_CLIPBOARD_PATTERN.search(text):
        return False
    if not any(signal.search(text) for signal in _STRUCTURED_ERROR_SIGNALS):
        return False
    if any(line.lstrip().startswith("#") for line in text.splitlines()):
        return False
    if "```" in text:
        return False
    return True


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


def _global_error_lessons_store() -> ErrorLearningStore:
    """构建指向全局错题本 (~/.openbrep/error_lessons.jsonl) 的 store。

    仅重定向 ``root`` / ``error_lessons_path`` 到全局位置；序列化与写盘
    完全复用父类 ``_write_lessons``（内部 ``_lesson_to_dict``），不新造序列化。
    """
    store = ErrorLearningStore(Path.home())
    store.root = GLOBAL_ERROR_LESSONS_PATH.parent
    store.error_lessons_path = GLOBAL_ERROR_LESSONS_PATH
    return store


def _read_error_lessons(path: Path) -> list[ErrorLesson]:
    """读取 jsonl 错题本（逐行 dict → ErrorLesson，schema 同 learning.py）。

    文件不存在返回空列表；解析/读取异常向上抛出，由调用方（自动沉淀 /
    ingest_error）统一按旁路失败处理——不静默吞错，避免下次写盘时覆盖损坏数据。
    """
    lessons: list[ErrorLesson] = []
    if not path.exists():
        return lessons
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        lessons.append(_lesson_from_dict(json.loads(line)))
    return lessons


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
        # E1：全局错题本写入锁（service 自有，与 request_gate 无关；
        # 沉淀是旁路，锁只保护错题本文件读写，不触碰 session/project 状态）
        self._error_lessons_lock = threading.Lock()

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
        if method == "POST" and path == "/api/copilot/ingest-error":
            return self.ingest_error(body)
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

    def ingest_error(self, body: dict[str, Any]) -> dict[str, Any]:
        """手动沉淀入口（E1）。

        请求 ``{error_text, code_context?}``：``error_text`` 为错误文本，
        ``code_context`` 为关联代码/上下文（存 ``example`` 字段，蒸馏时
        错误↔代码配对用）。归一化去重并入全局错题本
        ``~/.openbrep/error_lessons.jsonl``，**无需项目打开**（不触碰
        session/project，文件写入由 service 自有锁保护）。沉淀是旁路：
        落盘失败返回 500 错误，不影响 chat / 剪贴板 buffer 主路径。
        """
        body = body or {}

        error_text = str(body.get("error_text") or "").strip()
        if not error_text:
            return {"ok": False, "error": "error_text is required.", "status": 400}
        code_context = str(body.get("code_context") or "").strip()

        try:
            return self._persist_error_lesson(
                error_text,
                source="copilot_manual",
                code_context=code_context,
            )
        except Exception as exc:
            logger.exception("copilot: ingest_error 落盘失败（沉淀是旁路，不影响主功能）")
            return {"ok": False, "error": f"错题本写入失败: {exc}", "status": 500}

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
        """单次监听 tick：读剪贴板 → 按签名去重 → 新错误文本入 buffer + 自动沉淀。"""
        value, source = _read_clipboard_snapshot()
        if not value:
            return
        signature = f"{source}:{value}"
        if signature == self._clipboard_last_signature:
            return
        self._clipboard_last_signature = signature
        if _is_error_clipboard_text(value):
            with self._clipboard_lock:
                is_new_entry = value not in self._clipboard_buffer
                if is_new_entry:
                    self._clipboard_buffer.append(value)
            # E1：新捕获的错误条目自动沉淀进全局错题本（旁路；失败只记日志，
            # 绝不影响 buffer 主路径——buffer 追加已完成，且内部已 try/except）
            if is_new_entry:
                self._auto_ingest_clipboard_error(value)

    def _auto_ingest_clipboard_error(self, value: str) -> None:
        """剪贴板新错误 → 自动沉淀（E1）。沉淀是旁路：任何异常只记日志。

        E4：入口加结构化匹配门——不满足 ``_is_structured_error_text`` 直接返回
        （不沉淀、不报错，与"非错误文本不沉淀"同一语义）。该门只影响自动沉淀，
        不影响 buffer chips（第一层 ``_is_error_clipboard_text`` 行为不变）。
        """
        if not _is_structured_error_text(value):
            return
        try:
            self._persist_error_lesson(value, source="copilot_clipboard")
        except Exception:
            logger.exception(
                "copilot: 剪贴板错误自动沉淀失败（沉淀是旁路，不影响对话主功能）"
            )

    def _persist_error_lesson(
        self,
        error_text: str,
        *,
        source: str,
        code_context: str = "",
    ) -> dict[str, Any]:
        """归一化去重并入全局错题本（E1 核心）。

        复用 ``learning.py`` 的 ``classify_error`` / ``error_fingerprint`` /
        ``summarize_error`` / ``guidance_for_category`` 产出字段，merge 用
        ``_merge_lesson``、写盘用 ``ErrorLearningStore._write_lessons``（内部
        复用 ``_lesson_to_dict``），**不新造序列化**。fingerprint 已存在则
        count+1 并更新 last_seen；文件写入由 service 自有 ``_error_lessons_lock``
        保护，与 request_gate 无关。
        """
        raw = str(error_text or "").strip()
        now = datetime.now().isoformat(timespec="seconds")
        category = classify_error(raw)
        fingerprint = error_fingerprint(raw, category)
        lesson = ErrorLesson(
            fingerprint=fingerprint,
            category=category,
            summary=summarize_error(raw, category),
            guidance=guidance_for_category(category),
            example=str(code_context or "").strip()[:500],
            count=1,
            first_seen=now,
            last_seen=now,
            source=source,
            raw_excerpt=raw[:500],
        )

        with self._error_lessons_lock:
            store = _global_error_lessons_store()
            lessons = _read_error_lessons(store.error_lessons_path)
            existing = next(
                (item for item in lessons if item.fingerprint == fingerprint),
                None,
            )
            _merge_lesson(lessons, lesson)
            store._write_lessons(lessons)
            merged = existing if existing is not None else lesson

        return {
            "ok": True,
            "fingerprint": merged.fingerprint,
            "category": merged.category,
            "count": merged.count,
            "first_seen": merged.first_seen,
            "last_seen": merged.last_seen,
            "created": existing is None,
        }

    def _clipboard_watch_loop(self) -> None:
        while True:
            self._clipboard_watch_iteration()
            time.sleep(0.8)
