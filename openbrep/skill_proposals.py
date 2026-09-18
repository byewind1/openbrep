"""显式 skill 沉淀候选的持久化存储与路由判定（ST04，纯域模块）。

"把这轮修改沉淀成楼梯 skill / 保存为技能" 走显式提案路径：候选先原子落盘到
当前项目的 ``.openbrep/memory/skill-proposals/<proposal_id>.json``，再由用户
明确审批（draft → approved / rejected）后才进入既有 propose_skill +
verify_skill 双闸。候选不进入 SkillsLoader 搜索路径——未被审批的候选绝不注入。

本模块只做确定性的域逻辑（无 LLM、无 session、无网络）：
- 显式 skill 请求文本判定（含负例："不用保存 skill" / "解释这个 skill"）；
- 候选 JSON 的原子读写与幂等指纹；
- 证据引用（source_refs）解析与 ST02 revision 保护登记/解除。

LLM 提炼与审批编排在 ``openbrep/workbench/skill_proposal_service.py``。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

PROPOSAL_DIR_REL = Path(".openbrep") / "memory" / "skill-proposals"
SCHEMA_VERSION = 1

STATUS_DRAFT = "draft"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUSES = (STATUS_DRAFT, STATUS_APPROVED, STATUS_REJECTED)

VERIFY_UNVERIFIED = "unverified"
VERIFY_VERIFIED = "verified"
VERIFY_FAILED = "failed"

# 候选引用 revision 时的 ST02 统一引用 reason（外部引用，交付对替换时不清理）
PROTECTION_REASON = "pending_candidate"


# ── 项目身份 ─────────────────────────────────────────────


def path_hash(project_root: Any) -> str:
    """脱敏项目身份：解析后绝对路径的 sha256 前 12 位。"""
    resolved = str(Path(project_root).expanduser().resolve())
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:12]


def project_identity(project_root: Any, project_name: str = "") -> dict[str, str]:
    """候选归属的项目身份：路径哈希 + 目录/项目名。

    用路径哈希判定而不是 session epoch/进程内对象——同项目重启后仍能列出并
    继续审批；跨项目（路径哈希不同）禁止套用候选。
    """
    root = Path(project_root)
    return {
        "path_hash": path_hash(root),
        "name": str(project_name or root.name or ""),
    }


# ── 显式请求判定 ─────────────────────────────────────────

# 显式"保存/沉淀成 skill"动作短语（必须出现动作词，避免把"解释这个 skill"误判）
_POSITIVE_PATTERNS = (
    re.compile(r"保存(为|成|作)?\s*技能"),
    re.compile(r"保存(为|成|作)?\s*skill", re.IGNORECASE),
    re.compile(r"存(为|成)\s*(.*?)\s*(技能|skill)", re.IGNORECASE),
    re.compile(r"沉淀(为|成|作)?\s*.*?\s*(技能|skill)", re.IGNORECASE),
    re.compile(r"提炼(为|成)?\s*.*?\s*(技能|skill)", re.IGNORECASE),
    re.compile(r"\bsave\b.{0,24}\b(as|into|to)\b.{0,12}\bskill\b", re.IGNORECASE),
    re.compile(r"\b(distill|persist)\b.{0,24}\b(into|as)\b.{0,12}\bskill\b", re.IGNORECASE),
)

# 否定/取消：命中即整体判否（"不用保存 skill" 不能因为含"保存"就沉淀）
_NEGATIVE_PATTERNS = (
    re.compile(r"(不|别|无需|无须|不需要|不用|取消|放弃|暂不|先不)\s*[^，。,.!?！？]{0,6}(保存|沉淀|提炼|存为)"),
    re.compile(r"\b(don'?t|do not|no need|never|cancel|skip)\b.{0,24}\b(save|distill|persist)\b", re.IGNORECASE),
)


def detect_explicit_skill_request(message: str) -> bool:
    """True = 这条消息要求把当前成果显式沉淀成 skill。

    - 必须出现"保存/沉淀/提炼 … skill/技能"动作短语；
    - 否定短语优先（"不用保存 skill"）；"解释这个 skill"/"修改楼梯"无动作词 → False；
    - 纯函数，不读项目/不调 LLM。
    """
    text = (message or "").strip()
    if not text:
        return False
    for pattern in _NEGATIVE_PATTERNS:
        if pattern.search(text):
            return False
    return any(pattern.search(text) for pattern in _POSITIVE_PATTERNS)


# ── 幂等指纹 ─────────────────────────────────────────────


def _canonical_source_refs(source_refs: Any) -> list[dict[str, Any]]:
    """证据规范化：只保留影响幂等判定的字段，并按 run_id 排序。"""
    refs: list[dict[str, Any]] = []
    for raw in source_refs or []:
        if not isinstance(raw, dict):
            continue
        refs.append({
            "run_id": str(raw.get("run_id") or ""),
            "revision": str(raw.get("revision") or raw.get("after_revision_id") or ""),
            "source_fingerprint": str(raw.get("source_fingerprint") or ""),
            "evidence_complete": bool(raw.get("evidence_complete")),
        })
    refs.sort(key=lambda item: (item["run_id"], item["revision"], item["source_fingerprint"]))
    return refs


def candidate_fingerprint(
    identity: dict[str, str],
    *,
    name: str,
    content: str,
    pattern_type: str = "",
    source_refs: Any = None,
) -> str:
    """请求幂等键：项目身份 + 候选内容 + 规范化证据。"""
    payload = {
        "project": {
            "path_hash": str((identity or {}).get("path_hash") or ""),
            "name": str((identity or {}).get("name") or ""),
        },
        "name": str(name or ""),
        "pattern_type": str(pattern_type or ""),
        "content": str(content or ""),
        "source_refs": _canonical_source_refs(source_refs),
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ── 原子读写 ─────────────────────────────────────────────


def proposal_dir(project_root: Any) -> Path:
    return Path(project_root) / PROPOSAL_DIR_REL


def candidate_path(project_root: Any, proposal_id: str) -> Path:
    return proposal_dir(project_root) / f"{proposal_id}.json"


def _atomic_write_json(target: Path, payload: dict[str, Any]) -> None:
    """同目录 tmp + os.replace 原子写；失败向上抛出（调用方决定可重试语义）。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / f".{target.name}.tmp"
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, target)


def new_proposal_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"sp_{stamp}_{uuid.uuid4().hex[:6]}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_candidate(project_root: Any, candidate: dict[str, Any]) -> Path:
    """原子写候选；返回写盘路径。异常向上抛（审批路径据此保留可重试语义）。"""
    proposal_id = str(candidate.get("proposal_id") or "").strip()
    if not proposal_id:
        raise ValueError("candidate requires proposal_id")
    target = candidate_path(project_root, proposal_id)
    _atomic_write_json(target, candidate)
    return target


def load_candidate(project_root: Any, proposal_id: str) -> Optional[dict[str, Any]]:
    """按 proposal_id 读候选；缺失/坏文件返回 None（不抛出）。"""
    proposal_id = str(proposal_id or "").strip()
    if not proposal_id or "/" in proposal_id or "\\" in proposal_id or proposal_id.startswith("."):
        return None
    path = candidate_path(project_root, proposal_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:  # 坏 JSON：当作不存在，由调用方报明确错误
        logger.warning("skill proposal %s 读取失败: %s", proposal_id, exc)
        return None
    return data if isinstance(data, dict) else None


def list_candidates(project_root: Any) -> list[dict[str, Any]]:
    """列出当前项目全部候选（按 created_at 升序；坏文件跳过）。"""
    directory = proposal_dir(project_root)
    if not directory.is_dir():
        return []
    candidates: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("skill proposal 跳过不可解析文件 %s: %s", path, exc)
            continue
        if isinstance(data, dict):
            candidates.append(data)
    candidates.sort(key=lambda item: str(item.get("created_at") or ""))
    return candidates


def find_live_by_fingerprint(project_root: Any, fingerprint: str) -> Optional[dict[str, Any]]:
    """按幂等指纹找未被拒绝的候选（含已审批）；用于去重。"""
    fp = str(fingerprint or "")
    if not fp:
        return None
    for candidate in list_candidates(project_root):
        if str(candidate.get("fingerprint") or "") != fp:
            continue
        if str(candidate.get("status") or STATUS_DRAFT) == STATUS_REJECTED:
            continue
        return candidate
    return None


# ── 证据引用（ST02 保护） ────────────────────────────────


def quality_runs_path(project_root: Any) -> Path:
    return Path(project_root) / ".openbrep" / "quality" / "runs"


def _load_quality_record(project_root: Any, run_id: str) -> Optional[dict[str, Any]]:
    path = quality_runs_path(project_root) / f"{run_id}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def latest_run_id(project_root: Any, *, require_delivery: bool = True) -> Optional[str]:
    """最近一次运行 id：默认优先"有显式 after 的成功交付"，否则退最近一条记录。"""
    runs_dir = quality_runs_path(project_root)
    if not runs_dir.is_dir():
        return None
    records: list[tuple[float, str, dict[str, Any]]] = []
    for path in runs_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict) or not data.get("run_id"):
            continue
        records.append((path.stat().st_mtime, str(data["run_id"]), data))
    records.sort(key=lambda item: (item[0], item[1]))
    if require_delivery:
        for _, run_id, data in reversed(records):
            provenance = data.get("provenance") or {}
            delivery = provenance.get("delivery_source") or {}
            after = delivery.get("after_revision_id") or provenance.get("after_revision")
            if after:
                return run_id
    return records[-1][1] if records else None


def _ref_from_record(run_id: str, record: Optional[dict[str, Any]]) -> dict[str, Any]:
    """把质量档案映射成候选 source_ref；缺证据时 revision/fingerprint 为空。"""
    if not record:
        return {
            "run_id": run_id,
            "revision": None,
            "after_revision_id": None,
            "source_fingerprint": None,
            "intent": None,
            "changed_files": [],
            "evidence_complete": False,
        }
    provenance = record.get("provenance") or {}
    delivery = provenance.get("delivery_source") or {}
    after = delivery.get("after_revision_id") or provenance.get("after_revision") or None
    fingerprint = delivery.get("source_fingerprint") or provenance.get("source_fingerprint") or None
    changed = delivery.get("changed_files") or []
    if not isinstance(changed, list):
        changed = []
    return {
        "run_id": run_id,
        "revision": after,
        "after_revision_id": after,
        "source_fingerprint": fingerprint,
        "intent": record.get("intent"),
        "changed_files": [str(item) for item in changed],
        "delivery_state": delivery.get("state"),
        "evidence_complete": bool(after and fingerprint),
    }


def resolve_source_refs(
    project_root: Any, run_ids: Optional[list[str]] = None
) -> list[dict[str, Any]]:
    """解析候选的证据引用。

    - 显式 run_ids：逐条读质量档案（缺失也保留 run_id，但 evidence_complete=False）；
    - 未给 run_ids：取最近一次有 after 的交付运行；没有任何质量记录 → 空列表（旧资料
      允许 revision=null，但 evidence_complete=false）。
    """
    project_root = Path(project_root)
    ids = [str(item).strip() for item in (run_ids or []) if str(item).strip()]
    if not ids:
        latest = latest_run_id(project_root)
        if latest is None:
            return []
        ids = [latest]
    return [_ref_from_record(run_id, _load_quality_record(project_root, run_id)) for run_id in ids]


def evidence_complete(source_refs: Any) -> bool:
    refs = [ref for ref in (source_refs or []) if isinstance(ref, dict)]
    if not refs:
        return False
    return all(bool(ref.get("evidence_complete")) for ref in refs)


def register_candidate_protection(project_root: Any, candidate: dict[str, Any]) -> list[str]:
    """把候选引用的 after revision 登记进 ST02 保护集合（幂等）。"""
    from openbrep.revisions import register_revision_protection

    registered: list[str] = []
    proposal_id = str(candidate.get("proposal_id") or "")
    for ref in candidate.get("source_refs") or []:
        if not isinstance(ref, dict):
            continue
        revision = str(ref.get("revision") or "").strip()
        if not revision:
            continue
        try:
            register_revision_protection(
                project_root,
                revision,
                reason=PROTECTION_REASON,
                run_id=proposal_id,
                ref={"kind": PROTECTION_REASON, "proposal_id": proposal_id},
            )
            registered.append(revision)
        except Exception as exc:  # 保护登记失败不阻断候选落盘，但如实记录
            logger.warning("候选 %s 保护登记失败（%s）: %s", proposal_id, revision, exc)
    return registered


def release_candidate_protection(project_root: Any, candidate: dict[str, Any]) -> int:
    """解除候选登记的保护引用（拒绝/删除候选时调用）。"""
    from openbrep.revisions import unregister_revision_protection

    removed = 0
    proposal_id = str(candidate.get("proposal_id") or "")
    for ref in candidate.get("source_refs") or []:
        if not isinstance(ref, dict):
            continue
        revision = str(ref.get("revision") or "").strip()
        if not revision:
            continue
        try:
            removed += unregister_revision_protection(
                project_root, revision, reason=PROTECTION_REASON, run_id=proposal_id
            )
        except Exception as exc:
            logger.warning("候选 %s 保护解除失败（%s）: %s", proposal_id, revision, exc)
    return removed
