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
STATUS_APPROVING = "approving"   # 审批副作用进行中/上次中断（可重启继续收敛）
STATUS_REJECTING = "rejecting"   # 拒绝副作用进行中/上次中断（可重启继续收敛）
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUSES = (STATUS_DRAFT, STATUS_APPROVING, STATUS_REJECTING, STATUS_APPROVED, STATUS_REJECTED)

VERIFY_UNVERIFIED = "unverified"
VERIFY_VERIFIED = "verified"
VERIFY_FAILED = "failed"
# 技术断言未核验：产物可保留为 proposed，但绝不晋升为已验证知识（K08）
VERIFY_CLAIMS_UNVERIFIED = "claims_unverified"

# 候选引用 revision 时的 ST02 统一引用 reason（外部引用，交付对替换时不清理）
PROTECTION_REASON = "pending_candidate"
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _safe_managed_id(value: Any) -> bool:
    text = str(value or "")
    return bool(text and _SAFE_ID_RE.fullmatch(text))


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
    # 疑问句是询问"怎么做/为什么/要不要"，不是执行命令（"如何保存为技能？"）
    re.compile(
        r"(为什么|为何|怎么|怎样|如何|能否|可否|是否|要不要|需要吗|可以吗)"
        r"[^，。,.!?！？]{0,16}(保存|沉淀|提炼|存为|skill|技能)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(why|how|should|shall|can|could|would|may|do i|does)\b.{0,40}\b(save|distill|persist)\b",
        re.IGNORECASE,
    ),
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
    if not _safe_managed_id(proposal_id):
        raise ValueError("unsafe proposal id")
    root = proposal_dir(project_root).resolve()
    target = (root / f"{proposal_id}.json").resolve()
    if target.parent != root:
        raise ValueError("proposal path escapes proposal store")
    return target


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
    if not _safe_managed_id(proposal_id):
        raise ValueError("candidate requires a safe proposal_id")
    target = candidate_path(project_root, proposal_id)
    _atomic_write_json(target, candidate)
    return target


def load_candidate(project_root: Any, proposal_id: str) -> Optional[dict[str, Any]]:
    """按 proposal_id 读候选；缺失/坏文件返回 None（不抛出）。"""
    proposal_id = str(proposal_id or "").strip()
    if not _safe_managed_id(proposal_id):
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
            proposal_id = str(data.get("proposal_id") or "")
            if not _safe_managed_id(proposal_id) or proposal_id != path.stem:
                logger.warning("skill proposal 跳过不安全或名称不一致的文件 %s", path)
                continue
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


def _safe_changed_file(value: Any) -> bool:
    text = str(value or "")
    path = Path(text)
    if not text or path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        return False
    return (len(path.parts) == 1 and path.suffix.lower() == ".xml") or (
        len(path.parts) >= 2 and path.parts[0] == "scripts"
    )


def _load_quality_record(project_root: Any, run_id: str) -> Optional[dict[str, Any]]:
    if not _safe_managed_id(run_id):
        return None
    root = quality_runs_path(project_root).resolve()
    path = (root / f"{run_id}.json").resolve()
    if path.parent != root:
        return None
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


def revisions_root(project_root: Any) -> Path:
    return Path(project_root) / ".openbrep" / "revisions"


def revision_dir(project_root: Any, revision_id: str) -> Path:
    root = revisions_root(project_root).resolve()
    if not _safe_managed_id(revision_id):
        raise ValueError("unsafe revision id")
    path = (root / str(revision_id)).resolve()
    if path.parent != root:
        raise ValueError("revision path escapes revisions root")
    return path


def _validate_ref(project_root: Path, record: Optional[dict[str, Any]], ref: dict[str, Any]) -> dict[str, Any]:
    """严格校验一条 source_ref：项目身份 / delivery state / revision 存在 / 指纹一致。

    返回 ``{"ok": bool, "reasons": [str], "revision_fingerprint": str|None}``。
    只有全部通过才算 evidence_complete；任何一项不过都给出可读原因。
    """
    reasons: list[str] = []
    if not _safe_managed_id(ref.get("run_id")):
        reasons.append("run_id_invalid")
    if not record:
        if "run_id_invalid" not in reasons:
            reasons.append("run_record_missing")
        return {"ok": False, "reasons": reasons, "revision_fingerprint": None}

    source_ref = record.get("project_ref") or {}
    if str(source_ref.get("path_hash") or "") != path_hash(project_root):
        reasons.append("project_identity_mismatch")

    provenance = record.get("provenance") or {}
    delivery = provenance.get("delivery_source")
    if not isinstance(delivery, dict):
        reasons.append("delivery_source_missing")
        delivery = {}
    state = str(delivery.get("state") or "")
    if state != "verified_change":
        reasons.append(f"delivery_state_not_verified_change:{state or 'missing'}")

    after = delivery.get("after_revision_id") or provenance.get("after_revision")
    fingerprint = delivery.get("source_fingerprint") or provenance.get("source_fingerprint")
    if not after:
        reasons.append("after_revision_missing")
    elif not _safe_managed_id(after):
        reasons.append("revision_id_invalid")
    if not fingerprint:
        reasons.append("source_fingerprint_missing")

    revision_fingerprint: Optional[str] = None
    for changed_file in ref.get("changed_files") or []:
        if not _safe_changed_file(changed_file):
            reasons.append(f"changed_file_path_invalid:{changed_file}")

    if after and _safe_managed_id(after):
        rev_path = revision_dir(project_root, str(after))
        if not rev_path.is_dir():
            reasons.append("revision_dir_missing")
        else:
            try:
                from openbrep.source_fingerprint import compute_revision_fingerprint

                revision_fingerprint = compute_revision_fingerprint(rev_path)
            except Exception as exc:  # 指纹算不出 = 证据不可信
                logger.warning("revision %s 指纹计算失败: %s", after, exc)
                reasons.append("revision_fingerprint_unreadable")
            if (
                revision_fingerprint
                and fingerprint
                and str(revision_fingerprint) != str(fingerprint)
            ):
                reasons.append("revision_fingerprint_mismatch")

    return {"ok": not reasons, "reasons": reasons, "revision_fingerprint": revision_fingerprint}


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
            "delivery_state": None,
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
    """解析候选的证据引用并逐条严格校验。

    - 显式 run_ids：逐条读质量档案（缺失也保留 run_id，但 evidence_complete=False）；
    - 未给 run_ids：取最近一次有 after 的交付运行；没有任何质量记录 → 空列表（旧资料
      允许 revision=null，但 evidence_complete=false）。
    - 每条 ref 附 ``validation``：项目身份 / delivery state / revision 目录存在 /
      revision 实际指纹与记录指纹一致；任一不过 → evidence_complete=false。
    """
    project_root = Path(project_root)
    ids = [str(item).strip() for item in (run_ids or []) if str(item).strip()]
    if not ids:
        latest = latest_run_id(project_root)
        if latest is None:
            return []
        ids = [latest]
    refs: list[dict[str, Any]] = []
    for run_id in ids:
        record = _load_quality_record(project_root, run_id)
        ref = _ref_from_record(run_id, record)
        validation = _validate_ref(project_root, record, ref)
        ref["validation"] = validation
        ref["evidence_complete"] = bool(validation["ok"])
        refs.append(ref)
    return refs


def evidence_complete(source_refs: Any) -> bool:
    refs = [ref for ref in (source_refs or []) if isinstance(ref, dict)]
    if not refs:
        return False
    return all(bool(ref.get("evidence_complete")) for ref in refs)


# ── 技术断言未核验（K08） ────────────────────────────────

_MEASUREMENT_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:mm|MM|毫米|cm|CM|厘米)")
_HEURISTIC_WORDS = ("经验", "推荐", "通常", "一般", "建议", "常用", "惯例", "自述", "实测", "实践证明")
_TECH_TOKEN_RE = re.compile(r"\b(?:PRISM_|SPLIT|MUL2|ADDZ|GOSUB|CALL|ROT|BLOCK)\b")


def detect_unverified_claims(content: str, *, limit: int = 8) -> list[dict[str, str]]:
    """从候选正文里识别"未核验的技术断言"（K08）。

    - ``measurement_experience``：出现了 mm/cm 经验值且同行带经验/推荐/通常等措辞
      （如"25/50mm 经验"）；
    - ``asserted_technique``：出现 GDL 技术词且同行是自述式断言（经验/自述/实测等）。

    纯规则、可解释；命中项只用于"标注未核验、不得晋升"，不判内容对错。
    """
    claims: list[dict[str, str]] = []
    for raw_line in (content or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        has_heuristic = any(word in line for word in _HEURISTIC_WORDS)
        if not has_heuristic:
            continue
        if _MEASUREMENT_RE.search(line):
            claims.append({"kind": "measurement_experience", "snippet": line[:160]})
        elif _TECH_TOKEN_RE.search(line):
            claims.append({"kind": "asserted_technique", "snippet": line[:160]})
        if len(claims) >= limit:
            break
    return claims


def project_selection(project_root: Any, project_name: str) -> dict[str, Any]:
    """项目选择标注：经验/技术断言来自哪个项目、未跨项目核验。"""
    return {
        "project": str(project_name or ""),
        "path_hash": path_hash(project_root),
        "note": "经验值/技术断言来自该项目用例，未跨项目核验",
    }


# ── 候选 → artifact 所有权 ───────────────────────────────


def is_valid_skill_name(name: Any) -> bool:
    """Skill 文件名统一校验：拒绝路径、隐藏/保留名和控制字符。"""
    if not isinstance(name, str) or not name or name != name.strip():
        return False
    if name in (".", "..") or name.upper() == "README" or name[0] == ".":
        return False
    if any(ord(ch) < 32 for ch in name):
        return False
    return not any(ch in name for ch in ('/', "\\", "\x00", "<", ">", ":", '"', "|", "?", "*"))


def content_digest(content: str) -> str:
    return "sha256:" + hashlib.sha256(str(content or "").encode("utf-8")).hexdigest()


def read_skill_artifact(skills_dir: Any, name: str) -> Optional[dict[str, Any]]:
    """读取 skills_dir/<name>.md 的正文/frontmatter（不存在返回 None）。"""
    from openbrep.skills_loader import _split_frontmatter

    if not is_valid_skill_name(name):
        return None
    root = Path(skills_dir).expanduser().resolve()
    path = (root / f"{name}.md").resolve()
    if path.parent != root:
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None
    body, meta, has_fm = _split_frontmatter(text)
    return {
        "path": str(path),
        "text": text,
        "body": body,
        "meta": meta or {},
        "has_frontmatter": bool(has_fm),
    }


def artifact_ownership(
    skills_dir: Any, name: str, proposal_id: str, content: str
) -> dict[str, Any]:
    """判定磁盘上的同名 skill 是否由本 proposal 写出的（所有权 + 内容摘要）。

    - 文件不存在 → owned=False / artifact_missing；
    - frontmatter ``source_trace_id`` != proposal_id → foreign_artifact；
    - 正文与候选 content 不一致 → content_mismatch；
    只有两者一致才允许续跑 verify / 允许拒绝回收。
    """
    if not is_valid_skill_name(name):
        return {"owned": False, "reason": "invalid_skill_name", "path": None}
    root = Path(skills_dir).expanduser().resolve()
    expected_path = root / f"{name}.md"
    resolved_path = expected_path.resolve()
    if resolved_path.parent != root:
        return {
            "owned": False,
            "reason": "artifact_path_escape",
            "path": str(expected_path),
        }
    artifact = read_skill_artifact(root, name)
    if artifact is None:
        return {"owned": False, "reason": "artifact_missing", "path": str(expected_path)}
    meta = artifact.get("meta") or {}
    trace_id = str(meta.get("source_trace_id") or "")
    if trace_id != str(proposal_id):
        return {"owned": False, "reason": "foreign_artifact", "path": artifact["path"], "artifact_trace_id": trace_id}
    if str(artifact.get("body") or "").strip() != str(content or "").strip():
        return {
            "owned": False,
            "trace_owned": True,
            "reason": "content_mismatch",
            "path": artifact["path"],
        }
    return {"owned": True, "reason": "", "path": artifact["path"]}


def register_candidate_protection(project_root: Any, candidate: dict[str, Any]) -> dict[str, Any]:
    """把候选引用的 after revision 登记进 ST02 保护集合（幂等，可观察）。"""
    from openbrep.revisions import register_revision_protection

    registered: list[str] = []
    errors: list[dict[str, str]] = []
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
            errors.append({"revision": revision, "error": str(exc)})
    return {"registered": registered, "errors": errors}


def reconcile_candidate_protections(project_root: Any) -> dict[str, Any]:
    """重启/列候选时对账，并把结果持久化回候选。"""
    reconciled: list[str] = []
    errors: list[dict[str, str]] = []
    for candidate in list_candidates(project_root):
        if str(candidate.get("status") or "") == STATUS_REJECTED:
            release = release_candidate_protection(project_root, candidate)
            candidate["protection"] = {
                "registered": [],
                "errors": release["errors"],
                "released": not release["errors"],
                "checked_at": utc_now(),
            }
            save_candidate(project_root, candidate)
            continue
        result = register_candidate_protection(project_root, candidate)
        candidate["protection"] = {**result, "checked_at": utc_now()}
        save_candidate(project_root, candidate)
        reconciled.extend(result.get("registered") or [])
        errors.extend(result.get("errors") or [])
    return {"reconciled": sorted(set(reconciled)), "errors": errors}


def release_candidate_protection(project_root: Any, candidate: dict[str, Any]) -> dict[str, Any]:
    """解除候选登记的保护引用（拒绝/删除候选时调用）。"""
    from openbrep.revisions import unregister_revision_protection

    removed = 0
    errors: list[dict[str, str]] = []
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
            errors.append({"revision": revision, "error": str(exc)})
    return {"removed": removed, "errors": errors}
