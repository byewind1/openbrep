"""任务事件服务（卡04）：统一入口 turn 的执行事件构建、落盘与查询。

职责边界：
- 项目归属在任务开始（begin_turn）固定：事件记录写到开始时的项目目录，
  中途切换项目不迁移、不写入新项目；project=null 的咨询只保留会话内存，
  不创建项目/输出/任务目录。
- 关键事件先追加落盘再广播（append-before-broadcast）；文本 delta 按合并
  窗口落盘，终止时 flush。
- 事件记录绝不进入 LLM prompt / 质量评分 / benchmark；只服务展示与复盘。
- 查询（read_turn）不拿 session _op_lock，长执行不堵进度查询。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from openbrep.workbench.task_event_store import TaskEventStore
from openbrep.workbench.task_events import (
    EVENT_STATE_DELIVERED,
    EVENT_STATE_FAILED,
    EVENT_STATE_NO_CHANGE,
    EVENT_STATE_PARTIAL,
    EVENT_STATE_RUNNING,
    EVENT_STATE_SUCCEEDED,
    clip_public_text,
)

# assistant_delta 合并落盘窗口（秒 / 字符）
_COMMENTARY_FLUSH_SECONDS = 1.0
_COMMENTARY_FLUSH_CHARS = 256


class WorkbenchTaskEventService:
    """会话级任务事件服务（composition root 持有一个实例，保持薄）。"""

    def __init__(self, session: Any) -> None:
        self.session = session
        self._stores: dict[str, TaskEventStore] = {}
        # turn_id -> 固定的项目归属（None = 仅内存）
        self._turn_project_root: dict[str, str | None] = {}
        # turn_id -> {"text": str, "at": float}（public_commentary 合并缓冲）
        self._commentary: dict[str, dict[str, Any]] = {}

    # ── 存储 ─────────────────────────────────────────────────

    def _store_for(self, project_root: str | None) -> TaskEventStore | None:
        if not project_root:
            return None
        if project_root not in self._stores:
            self._stores[project_root] = TaskEventStore(project_root)
        return self._stores[project_root]

    def _persist(self, turn_id: str, event: dict[str, Any]) -> None:
        root = self._turn_project_root.get(turn_id)
        store = self._store_for(root)
        if store is None:
            return  # project=null：只保留会话内存，不落盘
        kind = event.get("kind")
        try:
            if kind in {"cancelled", "failed", "completed"}:
                store.append_terminal(turn_id, event)
            else:
                store.append(turn_id, event)
        except Exception:  # noqa: BLE001 —— 记录失败不回滚源码修改、不阻塞执行
            pass

    # ── 生命周期 ─────────────────────────────────────────────

    def begin_turn(
        self,
        turn_id: str,
        *,
        project_epoch: int,
        message: str = "",
        requested_mode: str = "",
    ) -> None:
        """任务开始：固定项目归属并落 accepted 事件。"""
        project = self.session.project
        root = str(project.root) if project is not None and getattr(project, "root", None) else None
        self._turn_project_root[turn_id] = root
        self._commentary.pop(turn_id, None)
        self._persist(turn_id, {
            "kind": "accepted",
            "session_id": self.session.session_id,
            "project_epoch": project_epoch,
            "message": clip_public_text(message or ""),
            "stage": requested_mode or None,
        })

    def drop_turn(self, turn_id: str) -> None:
        """清理会话内存缓冲（落盘记录保留，供复盘）。"""
        self._turn_project_root.pop(turn_id, None)
        self._commentary.pop(turn_id, None)

    def record_stage(
        self,
        turn_id: str,
        *,
        kind: str = "preparing",
        stage: str | None = None,
        message: str | None = None,
        **extra: Any,
    ) -> None:
        event = {
            "kind": kind,
            "session_id": self.session.session_id,
            "project_epoch": self._turn_epoch(turn_id),
            "stage": stage,
            "message": clip_public_text(message) if message else None,
        }
        event.update({k: v for k, v in extra.items() if v is not None})
        self._persist(turn_id, event)

    def _turn_epoch(self, turn_id: str) -> int:
        return int(getattr(self.session, "project_epoch", 0) or 0)

    # ── pipeline 原始事件适配（普通 loop 与 Codex 桥共用）──────

    def build_pipeline_event(self, turn_id: str, kind: str, data: dict[str, Any]) -> dict[str, Any] | None:
        """把 pipeline/agent/桥接的原始事件映射为任务事件（返回 None = 不记录）。"""
        epoch = self._turn_epoch(turn_id)
        base = {
            "session_id": self.session.session_id,
            "project_epoch": epoch,
        }
        if kind == "tool_started":
            return {**base, "kind": "tool_started", "state": EVENT_STATE_RUNNING,
                    "tool_name": str(data.get("tool") or data.get("name") or "tool"),
                    "tool_call_id": data.get("call_id") or data.get("tool_call_id"),
                    "stage": data.get("stage") or "think"}
        if kind == "tool_call":
            return {**base, "kind": "tool_finished",
                    "state": EVENT_STATE_SUCCEEDED if data.get("ok") is True else EVENT_STATE_FAILED,
                    "tool_name": str(data.get("tool") or data.get("name") or data.get("display_name") or "tool"),
                    "summary": clip_public_text(str(data.get("summary") or "")) or None}
        if kind == "compile_result":
            return {**base, "kind": "verification", "stage": "compile",
                    "state": EVENT_STATE_SUCCEEDED if data.get("success") is True else EVENT_STATE_FAILED,
                    "message": clip_public_text(str(data.get("message") or data.get("error") or "")) or None}
        if kind == "status":
            return {**base, "kind": "preparing", "stage": data.get("stage"),
                    "message": clip_public_text(str(data.get("message") or "")) or None}
        if kind == "plan":
            return {**base, "kind": "preparing", "stage": "plan",
                    "message": clip_public_text(str(data.get("intent_summary") or "")) or None}
        if kind == "assistant_delta":
            self._buffer_commentary(turn_id, data)
            return None
        return None

    def record_pipeline_event(self, turn_id: str, kind: str, data: dict[str, Any]) -> None:
        """适配 + 落盘（广播由调用方在之后进行：先落盘再广播）。"""
        event = self.build_pipeline_event(turn_id, kind, data if isinstance(data, dict) else {})
        if event is not None:
            self._persist(turn_id, event)

    def _buffer_commentary(self, turn_id: str, data: dict[str, Any]) -> None:
        text = data.get("content") if isinstance(data, dict) else None
        if not isinstance(text, str) or not text:
            return
        buffer = self._commentary.setdefault(turn_id, {"text": "", "at": time.monotonic()})
        buffer["text"] += text
        if buffer["text"].encode("utf-8").__len__() >= _COMMENTARY_FLUSH_CHARS or \
                time.monotonic() - buffer["at"] >= _COMMENTARY_FLUSH_SECONDS:
            self.flush_commentary(turn_id)

    def flush_commentary(self, turn_id: str) -> None:
        """把合并缓冲的公开说明落盘（终止前调用）。"""
        buffer = self._commentary.get(turn_id)
        if not buffer or not buffer.get("text"):
            self._commentary.pop(turn_id, None)
            return
        text = buffer.pop("text")
        self._persist(turn_id, {
            "kind": "public_commentary",
            "session_id": self.session.session_id,
            "project_epoch": self._turn_epoch(turn_id),
            "message": clip_public_text(text),
        })

    # ── 终止 ─────────────────────────────────────────────────

    def finish_turn(
        self,
        turn_id: str,
        *,
        kind: str,
        state: str | None = None,
        message: str | None = None,
        run_id: str | None = None,
        error_code: str | None = None,
        changed_files: list[str] | None = None,
    ) -> None:
        """终止事件（幂等）：先 flush 公开说明缓冲，再落终止记录。"""
        self.flush_commentary(turn_id)
        self.record_stage(
            turn_id,
            kind=kind,
            stage=None,
            state=state,
            message=message,
            run_id=run_id,
            error_code=error_code,
            affected_files=changed_files,
        )
        if kind in {"cancelled", "failed", "completed"}:
            self._commentary.pop(turn_id, None)

    def finish_turn_from_response(self, turn_id: str, response: dict[str, Any]) -> None:
        """按执行响应组装终止事件：完成状态区分完整交付/部分修改/无变化。"""
        assistant = response.get("assistant") if isinstance(response.get("assistant"), dict) else {}
        delivery = assistant.get("delivery") if isinstance(assistant.get("delivery"), dict) else {}
        presentation = delivery.get("presentation") if isinstance(delivery.get("presentation"), dict) else delivery
        run_id = assistant.get("run_id") or presentation.get("run_id")
        changed_files = list(assistant.get("changed_files") or presentation.get("changed_files") or [])
        if response.get("cancelled"):
            self.finish_turn(turn_id, kind="cancelled", state="cancelled",
                             message="任务已取消；已发生的部分修改保留。", run_id=run_id,
                             changed_files=changed_files or None)
            return
        if not response.get("ok"):
            self.finish_turn(turn_id, kind="failed", state=EVENT_STATE_FAILED,
                             message=clip_public_text(str(response.get("error") or "任务失败。")),
                             run_id=run_id, error_code=response.get("code"),
                             changed_files=changed_files or None)
            return
        if response.get("awaiting_extraction_confirmation"):
            self.record_stage(turn_id, kind="preparing", stage="extraction_gate",
                              message="读图结果待确认。")
            return
        if response.get("result_kind") == "awaiting_confirmation" or response.get("awaiting_confirmation"):
            self.record_stage(turn_id, kind="preparing", stage="plan_gate",
                              message="修改计划待确认。")
            return
        delivery_status = presentation.get("status")
        if delivery_status == "no_change":
            state = EVENT_STATE_NO_CHANGE
        elif delivery_status == "partial_change" or delivery_status == "incomplete":
            state = EVENT_STATE_PARTIAL
        else:
            state = EVENT_STATE_DELIVERED
        headline = presentation.get("headline") or assistant.get("reply") or "任务完成。"
        # 交付证据事件（revision/diff 关联）与终止事件分开落
        self.record_stage(
            turn_id, kind="delivery", stage=None,
            state=state, message=clip_public_text(str(headline)),
            run_id=run_id, affected_files=changed_files or None,
        )
        self.finish_turn(turn_id, kind="completed", state=state,
                         message=clip_public_text(str(headline)), run_id=run_id,
                         changed_files=changed_files or None)

    # ── 查询（只读；不拿 session _op_lock）────────────────────

    def read_turn(self, turn_id: str) -> dict[str, Any]:
        root = self._turn_project_root.get(turn_id)
        if root is None:
            store = None
            # 本进程未知 turn：尝试按当前项目读取（重开项目后的复盘场景）
            project = self.session.project
            if project is not None and getattr(project, "root", None):
                store = self._store_for(str(project.root))
        else:
            store = self._store_for(root)
        events = store.read_turn(turn_id) if store is not None else []
        return {
            "ok": True,
            "turn_id": turn_id,
            "session_id": self.session.session_id,
            "events": events,
        }

    def project_root_for(self, turn_id: str) -> str | None:
        return self._turn_project_root.get(turn_id)


def tasks_dir_for(project_root: str | Path) -> Path:
    return TaskEventStore(project_root).tasks_dir
