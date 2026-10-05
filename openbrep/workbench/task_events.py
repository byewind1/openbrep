"""统一执行事件 schema（卡04，契约冻结见实施计划 §三 / Obsidian 卡01 契约）。

事件是执行过程的唯一事实源：真实事件驱动，不从自然语言倒推成功；绝不记录
模型内部隐藏推理。事件记录只用于展示与复盘，绝不进入 LLM prompt。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

# ── 契约常量（卡01 冻结）──────────────────────────────────────

TASK_EVENT_SCHEMA_VERSION = 1

# 字段全集：按类型可选（tool_call_id/tool_name/affected_files/duration_ms/
# summary/error_code/run_id 等），schema_version/event_id/seq/timestamp/kind/
# turn_id/session_id/project_epoch 为公共字段。
TASK_EVENT_FIELDS = frozenset({
    "schema_version", "event_id", "seq", "timestamp", "elapsed_ms",
    "session_id", "project_epoch", "turn_id", "run_id", "kind", "stage",
    "state", "message", "tool_call_id", "tool_name", "affected_files",
    "duration_ms", "summary", "error_code",
})

# kind 枚举（≥）：完整生命周期覆盖。
TASK_EVENT_KINDS = frozenset({
    "accepted",           # 任务请求已接受（项目归属在此时固定）
    "preparing",          # prepare/执行准备阶段（语义路由、咨询/计划生成、检查等待）
    "waiting_model",      # 等待模型响应（显式信号；超15s的界面提示由前端按最后事件时间推算）
    "public_commentary",  # 公开模型说明（可转发，与 final 答复分离；不含隐藏推理）
    "tool_started",       # 工具开始（state=running；未返回不显示为失败）
    "tool_finished",      # 工具结束（state=succeeded/failed + 真实结果摘要）
    "verification",       # 验证（编译/静态/语义/效果）
    "source_changed",     # 源码版本变化（指纹/代次守卫）
    "delivery",           # 交付证据（revision/diff 关联）
    "cancelled",          # 终止：取消
    "failed",             # 终止：失败
    "completed",          # 终止：完成（state 区分 delivered/partial/no_change）
})

# 终止类 kind：append_terminal 幂等去重。
TERMINAL_KINDS = frozenset({"cancelled", "failed", "completed"})

# 工具事件 state（start=running；finish=succeeded/failed）
EVENT_STATE_RUNNING = "running"
EVENT_STATE_SUCCEEDED = "succeeded"
EVENT_STATE_FAILED = "failed"
# completed 事件的 state（完整交付 / 部分修改 / 无源码变化）
EVENT_STATE_DELIVERED = "delivered"
EVENT_STATE_PARTIAL = "partial"
EVENT_STATE_NO_CHANGE = "no_change"

# ── 记录限额（卡01 冻结）──────────────────────────────────────

MAX_PUBLIC_TEXT_BYTES = 4 * 1024          # 单条公开文本上限 4KiB
MAX_TURN_RECORD_BYTES = 2 * 1024 * 1024   # 单任务事件记录上限 2MiB


# ── 脱敏（凭据/认证路径/完整prompt/图像base64/工具完整源码不入日志）──

_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{8,}"),
    re.compile(r"(?i)(api[_-]?key|token|password|secret)(\"?\s*[:=]\s*\"?)[^\s\"',}]{4,}"),
    re.compile(r"(?i)authorization\s*[:=]\s*\S+"),
)
_BASE64ISH_RE = re.compile(r"[A-Za-z0-9+/=]{512,}")


def redact_text(text: str) -> str:
    """脱敏公开文本；返回脱敏后的安全文本（永不抛出）。"""
    if not isinstance(text, str) or not text:
        return ""
    out = text
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub("[REDACTED]", out)
    # 长 base64 样 blob（图像/原始凭据体）一律压缩成占位符
    out = _BASE64ISH_RE.sub("[REDACTED-BLOB]", out)
    return out


def clip_public_text(text: str, limit: int = MAX_PUBLIC_TEXT_BYTES) -> str:
    """单条公开文本上限 4KiB（按字节截断，尾部加显式省略标记）。"""
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    clipped = encoded[:limit].decode("utf-8", errors="ignore")
    return clipped + "\n…[truncated]"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")
