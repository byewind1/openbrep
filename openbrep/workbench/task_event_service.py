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
import uuid
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
    utc_now_iso,
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
        # RF03：seq 单一权威在服务层（store 尊重已提供的 seq）；run_id 建立后绑定
        self._seq: dict[str, int] = {}
        self._run_ids: dict[str, str] = {}
        # RF03：每 turn 有界会话内存记录（含无项目/持久化失败场景）+ 失败原因
        self._memory_turns: dict[str, list] = {}
        self._persist_errors: dict[str, str] = {}
        self._turn_started_at: dict[str, float] = {}
        self._MEMORY_TURN_LIMIT = 500

    # ── 存储 ─────────────────────────────────────────────────

    def _store_for(self, project_root: str | None) -> TaskEventStore | None:
        if not project_root:
            return None
        if project_root not in self._stores:
            self._stores[project_root] = TaskEventStore(project_root)
        return self._stores[project_root]

    def _record_memory(self, turn_id: str, canonical: dict[str, Any]) -> None:
        """RF03：每个事件（无论是否落盘）都进入有界会话内存记录。"""
        memory = self._memory_turns.setdefault(turn_id, [])
        memory.append(canonical)
        if len(memory) > self._MEMORY_TURN_LIMIT:
            del memory[: len(memory) - self._MEMORY_TURN_LIMIT]

    def _persist(self, turn_id: str, event: dict[str, Any]) -> dict[str, Any] | None:
        """落盘：失败不回滚源码修改、不阻塞执行，但必须可见（degraded 状态）。

        返回落盘的规范事件；None = 未落盘（project=null / 终态后迟到事件被拒 /
        存储故障）。U02-A：终态后迟到的非终态事件不进内存缓冲、不落盘、不广播——
        "最后一条 = 终态" 的消费口径由存储与广播共同保证。
        """
        root = self._turn_project_root.get(turn_id)
        store = self._store_for(root)
        kind = str(event.get("kind") or "")
        if (
            store is not None
            and kind not in {"cancelled", "failed", "completed"}
            and store.has_terminal(turn_id)
        ):
            return None
        self._record_memory(turn_id, event)
        if store is None:
            return None  # project=null：只保留会话内存，不创建任何目录
        try:
            if kind in {"cancelled", "failed", "completed"}:
                return store.append_terminal(turn_id, event)
            return store.append(turn_id, event)
        except Exception as exc:  # noqa: BLE001 —— 失败进入 recording 状态，绝不再写存储
            # RF03：degraded 为 turn 内粘滞——丢失的事件无法补写，
            # 后续恢复落盘也不能把该 turn 报成完好 persisted。
            self._persist_errors.setdefault(turn_id, str(exc) or exc.__class__.__name__)
            return None

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
        self._run_ids.pop(turn_id, None)
        store = self._store_for(root)
        try:
            self._seq[turn_id] = len(store.read_turn(turn_id)) if store is not None else 0
        except Exception:  # noqa: BLE001
            self._seq[turn_id] = 0
        self._memory_turns.setdefault(turn_id, [])
        self._turn_started_at[turn_id] = time.monotonic()
        self._persist(turn_id, self._canonicalize(turn_id, {
            "kind": "accepted",
            "session_id": self.session.session_id,
            "project_epoch": project_epoch,
            "message": clip_public_text(message or ""),
            "stage": requested_mode or None,
        }))

    def drop_turn(self, turn_id: str) -> None:
        """清理会话内存缓冲（落盘记录保留，供复盘）。"""
        self._turn_project_root.pop(turn_id, None)
        self._commentary.pop(turn_id, None)
        self._seq.pop(turn_id, None)
        self._run_ids.pop(turn_id, None)
        self._memory_turns.pop(turn_id, None)
        self._persist_errors.pop(turn_id, None)
        self._turn_started_at.pop(turn_id, None)

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
        self._persist(turn_id, self._canonicalize(turn_id, event))

    def _turn_epoch(self, turn_id: str) -> int:
        return int(getattr(self.session, "project_epoch", 0) or 0)

    # ── pipeline 原始事件适配（普通 loop 与 Codex 桥共用）──────

    def build_pipeline_event(self, turn_id: str, kind: str, data: dict[str, Any]) -> dict[str, Any] | None:
        """把 pipeline/agent/桥接的原始事件映射为任务事件（返回 None = 不记录）。"""
        epoch = self._turn_epoch(turn_id)
        run_id = data.get("run_id") or self._run_ids.get(turn_id)
        if run_id:
            self._run_ids[turn_id] = str(run_id)
        base = {
            "session_id": self.session.session_id,
            "project_epoch": epoch,
            "run_id": run_id,
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
                    "summary": clip_public_text(str(data.get("summary") or "")) or None,
                    "tool_call_id": data.get("tool_call_id") or data.get("call_id"),
                    "duration_ms": data.get("duration_ms")}
        if kind == "compile_result":
            return {**base, "kind": "verification", "stage": "compile",
                    "state": EVENT_STATE_SUCCEEDED if data.get("success") is True else EVENT_STATE_FAILED,
                    "message": clip_public_text(str(data.get("message") or data.get("error") or "")) or None}
        if kind in ("status", "preparing"):
            return {**base, "kind": "preparing", "stage": data.get("stage"),
                    "message": clip_public_text(str(data.get("message") or "")) or None}
        if kind == "plan":
            return {**base, "kind": "preparing", "stage": "plan",
                    "message": clip_public_text(str(data.get("intent_summary") or "")) or None}
        if kind == "assistant_delta":
            self._buffer_commentary(turn_id, data)
            return None
        return None

    def handle_pipeline_event(self, turn_id: str, kind: str, data: dict[str, Any]) -> list[dict[str, Any]]:
        """适配原始事件 → 规范任务事件：落盘并返回需广播的同一事件（先记录后广播）。

        RF02/RF03：落盘与广播共用同一 event_id/seq/timestamp（canonical 事件）；
        public_commentary 按窗口合并，段落收束（后续非 commentary 事件到达）时
        flush 出规范事件；assistant_delta 是 final 文本流，不误记为公开思考。
        """
        data = data if isinstance(data, dict) else {}
        out: list[dict[str, Any]] = []
        if kind not in ("assistant_delta", "public_commentary"):
            flushed = self.flush_commentary(turn_id)
            if flushed:
                out.append(flushed)
        if kind == "assistant_delta":
            return out  # final 文本流由最终答复承载，不逐 token 记录
        if kind == "public_commentary":
            self._buffer_commentary(turn_id, data)
            return out
        event = self.build_pipeline_event(turn_id, kind, data)
        if event is None:
            return out
        canonical = self._canonicalize(turn_id, event)
        stored = self._persist(turn_id, canonical)
        if stored is None:
            # U02-A：终态后迟到事件被拒——不落盘、不进内存、不广播
            return out
        out.append(stored)
        return out

    def record_pipeline_event(self, turn_id: str, kind: str, data: dict[str, Any]) -> None:
        """兼容入口：适配 + 落盘（不广播）。"""
        self.handle_pipeline_event(turn_id, kind, data)

    def _canonicalize(self, turn_id: str, event: dict[str, Any]) -> dict[str, Any]:
        """补全公共身份字段（event_id/seq/timestamp；seq 单调由服务层保证）。"""
        self._seq[turn_id] = self._seq.get(turn_id, 0) + 1
        started_at = self._turn_started_at.get(turn_id)
        canonical = {
            "schema_version": 1,
            "event_id": uuid.uuid4().hex,
            "seq": self._seq[turn_id],
            "timestamp": utc_now_iso(),
            "elapsed_ms": round((time.monotonic() - started_at) * 1000, 1) if started_at else None,
            "turn_id": turn_id,
        }
        canonical.update({k: v for k, v in event.items() if v is not None})
        return canonical

    def _persist_canonical(self, turn_id: str, canonical: dict[str, Any]) -> dict[str, Any] | None:
        return self._persist(turn_id, canonical)

    def _buffer_commentary(self, turn_id: str, data: dict[str, Any]) -> None:
        text = data.get("content") if isinstance(data, dict) else None
        if not isinstance(text, str) or not text:
            return
        buffer = self._commentary.setdefault(turn_id, {"text": "", "at": time.monotonic()})
        buffer["text"] += text
        if buffer["text"].encode("utf-8").__len__() >= _COMMENTARY_FLUSH_CHARS or \
                time.monotonic() - buffer["at"] >= _COMMENTARY_FLUSH_SECONDS:
            self.flush_commentary(turn_id)

    def flush_commentary(self, turn_id: str) -> dict[str, Any] | None:
        """把合并缓冲的公开说明落盘，返回规范事件（供同体广播；无缓冲返回 None）。"""
        buffer = self._commentary.get(turn_id)
        if not buffer or not buffer.get("text"):
            self._commentary.pop(turn_id, None)
            return None
        text = buffer.pop("text")
        canonical = self._canonicalize(turn_id, {
            "kind": "public_commentary",
            "session_id": self.session.session_id,
            "project_epoch": self._turn_epoch(turn_id),
            "message": clip_public_text(text),
        })
        stored = self._persist(turn_id, canonical)
        return stored if stored is not None else None

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
        """只读查询：返回 recording 状态（persisted/in_memory/degraded）与事件。

        degraded = 本进程内有落盘失败：返回内存记录（包含全部已发生事件）；
        in_memory = 无项目或未落盘；persisted = 存储为权威。查询不拿 _op_lock。
        """
        root = self._turn_project_root.get(turn_id)
        if root is None:
            store = None
            # 本进程未知 turn：尝试按当前项目读取（重开项目后的复盘场景）
            project = self.session.project
            if project is not None and getattr(project, "root", None):
                store = self._store_for(str(project.root))
        else:
            store = self._store_for(root)
        error = self._persist_errors.get(turn_id)
        memory = self._memory_turns.get(turn_id) or []
        if error:
            status = "degraded"
            events = memory if memory else (store.read_turn(turn_id) if store is not None else [])
        elif store is None:
            status = "in_memory"
            events = memory
        else:
            status = "persisted"
            events = store.read_turn(turn_id)
        return {
            "ok": True,
            "turn_id": turn_id,
            "session_id": self.session.session_id,
            "recording": {"status": status, "error": error},
            "events": events,
        }

    def list_turns(self, limit: int = 50) -> list[dict[str, Any]]:
        """RF04：任务索引——进程退出后仍可发现已开始的任务（不依赖前端 save）。

        扫描项目任务目录（最近 mtime 排序，截断到 limit）+ 本进程内存 turn；
        每项给出 turn_id/started_at/last_kind/last_state/terminal/message 摘要。
        terminal=False 表示已开始但未结束——读取端展示为未完成，不伪造答复。
        """
        entries: dict[str, dict[str, Any]] = {}

        def absorb(turn_id: str, events: list[dict[str, Any]]) -> None:
            if not events:
                return
            first, last = events[0], events[-1]
            terminal = last.get("kind") in {"completed", "failed", "cancelled"}
            entries[turn_id] = {
                "turn_id": turn_id,
                "started_at": first.get("timestamp"),
                "last_kind": last.get("kind"),
                "last_state": last.get("state"),
                "terminal": terminal,
                "message": (first.get("message") or "")[:120] or None,
                "run_id": last.get("run_id") or first.get("run_id"),
            }

        project = self.session.project
        store = None
        if project is not None and getattr(project, "root", None):
            store = self._store_for(str(project.root))
        if store is not None and store.tasks_dir.exists():
            paths = sorted(store.tasks_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
            for path in paths[:limit]:
                absorb(path.stem, store.read_turn(path.stem))
        for turn_id, memory in self._memory_turns.items():
            if turn_id not in entries and memory:
                absorb(turn_id, memory)
        return sorted(entries.values(), key=lambda e: str(e.get("started_at") or ""), reverse=True)

    def recording_status(self, turn_id: str) -> dict[str, Any]:
        """RF03：当前 turn 的记录状态（进入执行响应 events_recording）。"""
        error = self._persist_errors.get(turn_id)
        if error:
            return {"status": "degraded", "error": error}
        if self._turn_project_root.get(turn_id) is None or self._store_for(
            self._turn_project_root.get(turn_id)
        ) is None:
            return {"status": "in_memory", "error": None}
        return {"status": "persisted", "error": None}

    def project_root_for(self, turn_id: str) -> str | None:
        return self._turn_project_root.get(turn_id)


def tasks_dir_for(project_root: str | Path) -> Path:
    return TaskEventStore(project_root).tasks_dir
