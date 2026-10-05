"""任务事件存储（卡04）：每 turn 独立 JSONL 的追加式记录。

契约（卡01 冻结，实施计划 §四）：
- 路径 ``<project>/.openbrep/memory/chats/tasks/<turn_id>.jsonl``；
- seq 按 turn 单调（从 1），重启后从既有文件恢复计数；
- 终止事件（completed/failed/cancelled）幂等去重；
- 读取容忍最后半行（进程崩溃场景）；
- 单 turn 记录 2MiB 上限，溢出写一次显式 truncated 事件；终止摘要仍保留；
- 目录按需创建：project=null 的咨询不建目录、不落盘；
- 线程安全：store 内部锁保证读写一致性（查询不拿 session _op_lock）。
"""

from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Any

from openbrep.workbench.task_events import (
    MAX_TURN_RECORD_BYTES,
    TASK_EVENT_SCHEMA_VERSION,
    TERMINAL_KINDS,
    redact_text,
    utc_now_iso,
)

# turn_id 仅用于文件名：限制字符集防路径穿越
_TURN_ID_SAFE_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
)


class TaskEventStore:
    """一个项目的任务事件存储根（``<root>/.openbrep/memory/chats/tasks``）。"""

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root)
        self._lock = threading.Lock()
        # 内存 seq 计数与截断标记（重启后从文件恢复）
        self._seq: dict[str, int] = {}
        self._truncated: set[str] = set()

    @property
    def tasks_dir(self) -> Path:
        return self.project_root / ".openbrep" / "memory" / "chats" / "tasks"

    def turn_path(self, turn_id: str) -> Path:
        return self.tasks_dir / f"{self._safe_turn_id(turn_id)}.jsonl"

    @staticmethod
    def _safe_turn_id(turn_id: str) -> str:
        cleaned = "".join(c for c in str(turn_id) if c in _TURN_ID_SAFE_CHARS)
        return cleaned or "unknown"

    # ── 写入 ─────────────────────────────────────────────────

    def append(self, turn_id: str, event: dict[str, Any]) -> dict[str, Any]:
        """补全公共字段并追加一条事件（关键事件先落盘再广播的落盘点）。"""
        stored = self._fill(turn_id, event)
        with self._lock:
            self._ensure_seq_loaded(turn_id)
            path = self.turn_path(turn_id)
            if not self._mark_truncated_if_needed(turn_id, path):
                return stored
            self._assign_seq(turn_id, stored)
            line = json.dumps(self._sanitize(stored), ensure_ascii=False, sort_keys=True)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return stored

    def append_terminal(self, turn_id: str, event: dict[str, Any]) -> dict[str, Any] | None:
        """终止事件（completed/failed/cancelled）：幂等——同 turn 同 kind 只落一条。

        终止摘要不受 2MiB 截断限制（终止摘要另保留）。重复终止返回 None。
        """
        kind = str(event.get("kind") or "")
        if kind not in TERMINAL_KINDS:
            return self.append(turn_id, event)
        with self._lock:
            existing = [e.get("kind") for e in self._read_unlocked(turn_id)]
            if kind in existing:
                return None
        stored = self._fill(turn_id, event)
        with self._lock:
            self._ensure_seq_loaded(turn_id)
            self._assign_seq(turn_id, stored)
            path = self.turn_path(turn_id)
            line = json.dumps(self._sanitize(stored), ensure_ascii=False, sort_keys=True)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return stored

    def _assign_seq(self, turn_id: str, stored: dict[str, Any]) -> None:
        """RF03：seq 单一权威——调用方（服务层）已分配的 seq 原样保留；
        未提供时按 turn 计数分配。保证落盘与广播同一 seq。"""
        provided = stored.get("seq")
        if isinstance(provided, int) and provided > 0:
            self._seq[turn_id] = max(self._seq.get(turn_id, 0), provided)
            stored["seq"] = provided
            return
        self._seq[turn_id] += 1
        stored["seq"] = self._seq[turn_id]

    # ── 读取 ─────────────────────────────────────────────────

    def read_turn(self, turn_id: str) -> list[dict[str, Any]]:
        """读取一个 turn 的全部事件；容忍最后半行（崩溃/截断场景）。"""
        with self._lock:
            return self._read_unlocked(turn_id)

    def read_turns(self, turn_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        return {turn_id: self.read_turn(turn_id) for turn_id in turn_ids}

    # ── 内部 ─────────────────────────────────────────────────

    def _fill(self, turn_id: str, event: dict[str, Any]) -> dict[str, Any]:
        stored = {
            "schema_version": TASK_EVENT_SCHEMA_VERSION,
            "event_id": uuid.uuid4().hex,
            "seq": 0,  # append 时按 turn 单调赋值
            "timestamp": utc_now_iso(),
            "turn_id": turn_id,
        }
        stored.update({k: v for k, v in event.items() if v is not None})
        return stored

    @staticmethod
    def _sanitize(event: dict[str, Any]) -> dict[str, Any]:
        """脱敏公开文本字段（message/summary）；脱敏失败不写原始数据。"""
        cleaned = dict(event)
        for key in ("message", "summary"):
            value = cleaned.get(key)
            if isinstance(value, str) and value:
                try:
                    cleaned[key] = redact_text(value)
                except Exception:
                    cleaned[key] = "[REDACT-FAILED]"
        return cleaned

    def _ensure_seq_loaded(self, turn_id: str) -> None:
        if turn_id in self._seq:
            return
        events = self._read_unlocked(turn_id)
        self._seq[turn_id] = len(events)

    def _mark_truncated_if_needed(self, turn_id: str, path: Path) -> bool:
        """超限时写一次 truncated 事件；此后普通事件丢弃，终止事件仍可写。"""
        if turn_id in self._truncated:
            return False
        if not path.exists() or path.stat().st_size < MAX_TURN_RECORD_BYTES:
            return True
        self._truncated.add(turn_id)
        marker = {
            "schema_version": TASK_EVENT_SCHEMA_VERSION,
            "event_id": uuid.uuid4().hex,
            "seq": self._seq[turn_id] + 1,
            "timestamp": utc_now_iso(),
            "turn_id": turn_id,
            "kind": "truncated",
            "message": "任务事件记录超出 2MiB 上限，后续过程事件不再落盘（终止摘要保留）。",
        }
        line = json.dumps(marker, ensure_ascii=False, sort_keys=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        self._seq[turn_id] += 1
        return False

    def _read_unlocked(self, turn_id: str) -> list[dict[str, Any]]:
        path = self.turn_path(turn_id)
        if not path.exists():
            return []
        events: list[dict[str, Any]] = []
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return events
        for line in lines:
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except ValueError:
                continue  # 最后半行/损坏行：容忍并跳过
        return events
