"""显式 skill 沉淀提案服务（ST04）。

职责（全部走同一 service，assistant 文本路由与 REST 路由不复制逻辑）：

- ``propose``  : POST /api/skill/proposals —— 当前项目 + instruction（可选
  source_run_ids）→ 严格校验证据 → LLM 提炼（优先读 after revision）→ 持久候选
  （draft），登记 ST02 证据保护；
- ``list_proposals`` : GET /api/skill/proposals —— 列当前项目候选并做保护对账；
- ``confirm``  : POST /api/skill/confirm —— 有 proposal_id 走 store（校验项目身份；
  draft/approving → approved/rejected），无 proposal_id 保留原自动 harvest 行为。

审批状态机（返工后）：
``draft`` → 先持久化 ``approving`` 意图 → 外部副作用（propose_skill / verify_skill）
→ 最终状态写盘。最终写盘失败返回 retryable error，磁盘停在 ``approving``；同一
proposal_id 重试时会核对 artifact 所有权（frontmatter source_trace_id + 正文摘要）
后收敛，不重复创建、不丢用户决策。

纪律：
- 候选先落盘再审批；未审批候选绝不进 SkillsLoader（只在 .openbrep 下）；
- 落盘失败/写入被拒必须显式报错并保留可重试候选，不静默丢提案；
- 审批通过只代表用户决策，验证态单独记录；技术断言未核验不晋升（K08）；
- 拒绝只回收已证明属于本 proposal 的产物（外部同名文件不动）。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from openbrep.feedback import append_feedback
from openbrep.runtime import skill_harvest
from openbrep.skill_proposals import (
    SCHEMA_VERSION,
    STATUS_APPROVED,
    STATUS_APPROVING,
    STATUS_DRAFT,
    STATUS_REJECTED,
    VERIFY_CLAIMS_UNVERIFIED,
    VERIFY_FAILED,
    VERIFY_UNVERIFIED,
    VERIFY_VERIFIED,
    artifact_ownership,
    candidate_fingerprint,
    content_digest,
    detect_unverified_claims,
    evidence_complete,
    find_live_by_fingerprint,
    list_candidates,
    load_candidate,
    new_proposal_id,
    project_identity,
    project_selection,
    reconcile_candidate_protections,
    register_candidate_protection,
    release_candidate_protection,
    resolve_source_refs,
    save_candidate,
    utc_now,
)
from openbrep.skills_loader import rewrite_skill_frontmatter

logger = logging.getLogger(__name__)


def _error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "code": code, "error": message, **extra}


def _mcp_error_message(result: dict[str, Any]) -> str:
    """把 mcp_tools 的错误形态压成可读文本（error 可能是 dict）。"""
    error = result.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or error)
    return str(error or "")


def _mcp_error_code(result: dict[str, Any]) -> str:
    error = result.get("error")
    if isinstance(error, dict):
        return str(error.get("code") or "")
    return ""


def _skills_dir_writable(skills_dir: Path) -> tuple[bool, str]:
    """判定 skills_dir 是否可写；不可写时返回明确原因（不偷偷改写安装目录）。"""
    try:
        if skills_dir.exists():
            if not skills_dir.is_dir():
                return False, f"skills 路径不是目录：{skills_dir}"
            if not os.access(skills_dir, os.W_OK):
                return False, f"skills 目录不可写：{skills_dir}"
            return True, ""
        parent = skills_dir.parent
        if not parent.is_dir():
            return False, f"skills 目录的父目录不存在：{parent}"
        if not os.access(parent, os.W_OK):
            return False, f"skills 目录不可创建（父目录不可写）：{parent}"
        return True, ""
    except Exception as exc:  # 判定失败按不可写处理（fail closed）
        return False, f"skills 目录可写性判定失败：{exc}"


class SkillProposalService:
    """当前 session 的显式 skill 候选服务。"""

    def __init__(self, session: Any) -> None:
        self.session = session

    # ── REST 路由（workbench_api 只装配；request_gate 锁在 route 层）──

    def route(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """分发 /api/skill/proposals 与 /api/skill/confirm（新路由默认 locked）。"""
        payload = body or {}
        if path == "/api/skill/proposals":
            if method == "GET":
                return self.list_proposals()
            if method == "POST":
                return self.propose(payload)
        if path == "/api/skill/confirm" and method == "POST":
            return self.confirm(payload)
        return {"ok": False, "error": f"Unknown route: {method} {path}"}

    # ── 公共入口 ─────────────────────────────────────────

    def propose(self, body: dict[str, Any]) -> dict[str, Any]:
        """显式沉淀请求 → 持久 draft 候选（幂等）。"""
        root = self._project_root()
        if root is None:
            return _error("NO_PROJECT", "请先创建或打开一个 GDL 项目，再沉淀 skill。")
        project = getattr(self.session, "project", None)
        instruction = str(body.get("instruction") or body.get("message") or "").strip()
        if not instruction:
            return _error("SKILL_PROPOSAL_EMPTY_INSTRUCTION", "缺少沉淀指令（instruction）。")

        raw_ids = body.get("source_run_ids") or []
        if isinstance(raw_ids, str):
            raw_ids = [raw_ids]
        if not isinstance(raw_ids, list):
            return _error("SKILL_PROPOSAL_BAD_SOURCE_RUN_IDS", "source_run_ids 必须是字符串列表。")

        source_refs = resolve_source_refs(root, [str(item) for item in raw_ids])
        llm = skill_harvest._build_session_llm(self.session)
        if llm is None:
            return _error(
                "SKILL_PROPOSAL_LLM_UNAVAILABLE",
                "无法构造提炼用的 LLM 适配器，候选未生成（可重试）。",
                retryable=True,
            )

        outcome = skill_harvest.distill_explicit_skill(
            project,
            instruction,
            source_refs,
            llm,
            skill_harvest.resolve_skills_dir(),
            project_root=root,
        )
        if not outcome.get("ok"):
            return _error(
                str(outcome.get("code") or "SKILL_PROPOSAL_FAILED"),
                str(outcome.get("error") or "skill 提炼失败。"),
                retryable=True,
                source_refs=source_refs,
            )

        proposal = outcome["proposal"]
        identity = self._identity()
        fingerprint = candidate_fingerprint(
            identity,
            name=proposal["name"],
            content=proposal["content"],
            pattern_type=proposal["pattern_type"],
            source_refs=source_refs,
        )
        existing = find_live_by_fingerprint(root, fingerprint)
        if existing is not None:
            return {"ok": True, **self._candidate_payload(existing, reused=True)}

        now = utc_now()
        candidate = {
            "schema_version": SCHEMA_VERSION,
            "proposal_id": new_proposal_id(),
            "project": identity,
            "project_epoch": getattr(self.session, "project_epoch", None),
            "instruction": instruction,
            "name": proposal["name"],
            "pattern_type": proposal["pattern_type"],
            "content": proposal["content"],
            "slice": proposal.get("slice"),
            "fingerprint": fingerprint,
            "source_refs": source_refs,
            "evidence_complete": evidence_complete(source_refs),
            "claims": {
                "unverified": detect_unverified_claims(proposal["content"]),
                "project_selection": project_selection(root, identity.get("name") or ""),
            },
            "artifact": None,
            "protection": None,
            "status": STATUS_DRAFT,
            "verification": {"state": VERIFY_UNVERIFIED},
            "created_at": now,
            "updated_at": now,
            "error": None,
            "store_error": None,
            "approved_path": None,
        }
        try:
            save_candidate(root, candidate)
        except Exception as exc:
            logger.warning("skill proposal store write failed: %s", exc)
            return _error(
                "SKILL_PROPOSAL_STORE_FAILED",
                f"候选落盘失败，未生成持久候选（可重试）：{exc}",
                retryable=True,
                source_refs=source_refs,
            )
        candidate["protection"] = {
            **register_candidate_protection(root, candidate),
            "checked_at": utc_now(),
        }
        self._try_save(root, candidate)
        return {"ok": True, **self._candidate_payload(candidate)}

    def list_proposals(self) -> dict[str, Any]:
        root = self._project_root()
        if root is None:
            return {"ok": True, "proposals": [], "total": 0}
        # 保护对账（重启后收敛）：只读候选 + 幂等重登记，失败不影响列表
        try:
            reconcile_candidate_protections(root)
        except Exception as exc:  # best-effort
            logger.debug("candidate protection reconcile skipped: %s", exc)
        candidates = list_candidates(root)
        return {
            "ok": True,
            "proposals": [self._candidate_payload(item) for item in candidates],
            "total": len(candidates),
        }

    def confirm(self, body: dict[str, Any]) -> dict[str, Any]:
        """审批：有 proposal_id 走 store；否则保留原自动 harvest 行为。"""
        proposal_id = str(body.get("proposal_id") or "").strip()
        if not proposal_id:
            return skill_harvest.confirm_skill_proposal(self.session, body)
        return self._confirm_stored(proposal_id, body)

    # ── 内部 ─────────────────────────────────────────────

    def _project_root(self) -> Path | None:
        raw = getattr(self.session, "source_path", None)
        if raw is None:
            return None
        try:
            root = Path(raw)
        except Exception:
            return None
        return root if root.is_dir() else None

    def _identity(self) -> dict[str, str]:
        root = self._project_root()
        project = getattr(self.session, "project", None)
        name = getattr(project, "name", "") or (root.name if root else "")
        return project_identity(root, name)

    def _confirm_stored(self, proposal_id: str, body: dict[str, Any]) -> dict[str, Any]:
        root = self._project_root()
        if root is None:
            return _error("NO_PROJECT", "请先打开项目再审批 skill 候选。")
        candidate = load_candidate(root, proposal_id)
        if candidate is None:
            return _error(
                "SKILL_PROPOSAL_NOT_FOUND",
                f"找不到候选 {proposal_id}（可能已被删除）。",
            )

        identity = self._identity()
        stored_identity = candidate.get("project") or {}
        if str(stored_identity.get("path_hash") or "") != str(identity.get("path_hash") or ""):
            return _error(
                "SKILL_PROPOSAL_PROJECT_MISMATCH",
                "候选属于另一个项目，禁止跨项目套用。",
                proposal_id=proposal_id,
            )

        status = str(candidate.get("status") or STATUS_DRAFT)
        if status == STATUS_APPROVED and body.get("approve") is not False:
            # 幂等：已批准且再次请求批准 → 返回既有结果，不重复 propose/verify
            return self._decided_payload(candidate)
        if status == STATUS_REJECTED:
            return _error(
                "SKILL_PROPOSAL_ALREADY_REJECTED",
                "该候选已被拒绝，不再重复审批；如需重新沉淀请重新发起请求。",
                proposal_id=proposal_id,
            )

        if body.get("approve") is not True:
            # draft / approving / approved 都可拒绝：approved 的拒绝是"撤销"，
            # 只回收所有权属于本候选的产物（已 verified 也拉回 deprecated）。
            return self._reject(root, candidate)
        return self._approve(root, candidate)

    def _set_draft_error(self, root: Path, candidate: dict[str, Any], message: str) -> None:
        """失败回退到 draft 并尽力落盘（磁盘可能是 approving；重试仍可收敛）。"""
        candidate["status"] = STATUS_DRAFT
        candidate["error"] = message
        candidate["updated_at"] = utc_now()
        self._try_save(root, candidate)

    def _reject(self, root: Path, candidate: dict[str, Any]) -> dict[str, Any]:
        # 先回收已证明属于本 proposal 的产物（已 verified 也拉回 deprecated，避免
        # "候选被拒但 skill 仍激活"的分叉）；外部同名文件不动。
        reclaim = self._reclaim_artifact(candidate)
        candidate["status"] = STATUS_REJECTED
        candidate["updated_at"] = utc_now()
        candidate["verification"] = {
            "state": VERIFY_UNVERIFIED,
            "decision": "rejected",
            "reclaimed": reclaim,
        }
        candidate["error"] = None
        try:
            save_candidate(root, candidate)
        except Exception as exc:
            logger.warning("skill proposal reject save failed: %s", exc)
            return _error(
                "SKILL_PROPOSAL_STATE_SAVE_FAILED",
                f"拒绝状态落盘失败，候选仍可重试（产物已按所有权回收）：{exc}",
                retryable=True,
                proposal_id=str(candidate.get("proposal_id") or ""),
                reclaimed=reclaim,
            )
        released = release_candidate_protection(root, candidate)
        self._feedback(candidate, "rejected", f"用户拒绝了 skill 候选：{candidate.get('name')}")
        return {
            "ok": True,
            "discarded": True,
            "proposal_id": str(candidate.get("proposal_id") or ""),
            "status": STATUS_REJECTED,
            "released_protections": released,
            "reclaimed": reclaim,
            "message": "已丢弃 skill 候选。",
        }

    def _reclaim_artifact(self, candidate: dict[str, Any]) -> dict[str, Any]:
        """只回收所有权属于本 proposal 的产物：status → deprecated（不删除文件）。"""
        skills_dir = Path(skill_harvest.resolve_skills_dir())
        name = str(candidate.get("name") or "")
        proposal_id = str(candidate.get("proposal_id") or "")
        ownership = artifact_ownership(skills_dir, name, proposal_id, str(candidate.get("content") or ""))
        if not ownership.get("owned"):
            return {"reclaimed": False, "reason": ownership.get("reason"), "path": ownership.get("path")}
        try:
            ok = rewrite_skill_frontmatter(Path(ownership["path"]), updates={"status": "deprecated"})
        except Exception as exc:
            logger.warning("artifact reclaim failed: %s", exc)
            return {"reclaimed": False, "reason": f"rewrite_failed:{exc}", "path": ownership.get("path")}
        return {"reclaimed": bool(ok), "reason": "" if ok else "no_frontmatter", "path": ownership.get("path")}

    def _approve(self, root: Path, candidate: dict[str, Any]) -> dict[str, Any]:
        from openbrep.mcp_tools import propose_skill, verify_skill

        proposal_id = str(candidate.get("proposal_id") or "")
        name = str(candidate.get("name") or "")
        content = str(candidate.get("content") or "")
        skills_dir = Path(skill_harvest.resolve_skills_dir())

        # 1) 外部副作用之前先持久化审批意图（draft/approving → approving）。
        if str(candidate.get("status") or STATUS_DRAFT) != STATUS_APPROVING:
            candidate = {
                **candidate,
                "status": STATUS_APPROVING,
                "attempt": {"started_at": utc_now(), "kind": "approve"},
                "updated_at": utc_now(),
                "error": None,
            }
            try:
                save_candidate(root, candidate)
            except Exception as exc:
                return _error(
                    "SKILL_PROPOSAL_STATE_SAVE_FAILED",
                    f"审批意图写盘失败，未执行任何 skill 落盘（可重试）：{exc}",
                    retryable=True,
                    proposal_id=proposal_id,
                )

        # 2) skills_dir 可写性（不可写不改安装目录，候选回退 draft）
        writable, reason = _skills_dir_writable(skills_dir)
        if not writable:
            self._set_draft_error(root, candidate, reason)
            return _error(
                "SKILL_PROPOSAL_SKILLS_DIR_UNWRITABLE",
                f"skills 目录不可写，未落盘任何 skill：{reason}",
                proposal_id=proposal_id,
                retryable=True,
            )

        # 3) propose_skill（幂等：skill_exists 时核对所有权与内容摘要）
        proposal = propose_skill(
            name=name,
            content=content,
            pattern_type=str(candidate.get("pattern_type") or ""),
            source_project=str(root),
            source_trace_id=proposal_id,
            slice=candidate.get("slice") or None,
            skills_dir=str(skills_dir),
        )
        if not proposal.get("ok"):
            message = _mcp_error_message(proposal)
            code = _mcp_error_code(proposal)
            if code != "skill_exists":
                self._set_draft_error(root, candidate, message)
                self._feedback(candidate, "propose_failed", f"skill 候选落盘失败：{name}")
                return _error(
                    "SKILL_PROPOSE_FAILED",
                    message,
                    proposal_id=proposal_id,
                    retryable=True,
                )
            ownership = artifact_ownership(skills_dir, name, proposal_id, content)
            if not ownership.get("owned"):
                detail = (
                    f"同名 skill 已存在且不属于本候选（{ownership.get('reason')}），"
                    "拒绝覆盖/续跑；请改名或先处理该文件。"
                )
                self._set_draft_error(root, candidate, detail)
                self._feedback(candidate, "propose_conflict", f"skill 名称冲突：{name}")
                return _error(
                    "SKILL_PROPOSAL_NAME_CONFLICT",
                    detail,
                    proposal_id=proposal_id,
                    retryable=False,
                    artifact=ownership,
                )
            logger.info("skill %s owned by proposal %s; resume verify", name, proposal_id)

        artifact_path = proposal.get("path") or str(skills_dir / f"{name}.md")
        candidate = {
            **candidate,
            "artifact": {
                "name": name,
                "path": str(artifact_path),
                "content_digest": content_digest(content),
                "proposal_id": proposal_id,
                "written_at": (candidate.get("artifact") or {}).get("written_at") or utc_now(),
            },
            "approved_path": str(artifact_path),
            "updated_at": utc_now(),
        }

        # 4) K08：存在未核验技术断言时，纯 structural 不得晋升
        unverified_claims = (candidate.get("claims") or {}).get("unverified") or []
        if unverified_claims and not candidate.get("slice"):
            verification = {
                "state": VERIFY_CLAIMS_UNVERIFIED,
                "passed": False,
                "gate": "structural_capped",
                "status": "proposed",
                "error": "存在未核验技术断言，未执行晋升验证；产物保留为未激活。",
                "unverified_claims": unverified_claims,
                "claims": candidate.get("claims"),
            }
            candidate = {
                **candidate,
                "status": STATUS_APPROVED,
                "verification": verification,
                "error": None,
                "updated_at": utc_now(),
            }
            try:
                save_candidate(root, candidate)
            except Exception as exc:
                return _error(
                    "SKILL_PROPOSAL_STATE_SAVE_FAILED",
                    f"候选最终状态写盘失败（skill 已作为未激活 proposed 保留，重试可收敛）：{exc}",
                    retryable=True,
                    proposal_id=proposal_id,
                    skill=name,
                    verified=False,
                )
            self._feedback(candidate, "approved_unverified_claims", f"skill 候选未晋升（含未核验断言）：{name}")
            return {
                "ok": True,
                "proposal_id": proposal_id,
                "skill": name,
                "verified": False,
                "gate": "structural_capped",
                "status": "proposed",
                "path": candidate.get("approved_path"),
                "verification": verification,
                "claims": candidate.get("claims"),
                "message": (
                    f"skill「{name}」已落盘为未激活产物：含未核验技术断言，未晋升为已验证知识。"
                ),
            }

        # 5) verify（双闸晋升）
        verify = verify_skill(name=name, skills_dir=str(skills_dir))
        passed = verify.get("passed") is True and verify.get("ok") is True
        verification = {
            "state": VERIFY_VERIFIED if passed else VERIFY_FAILED,
            "passed": passed,
            "gate": verify.get("gate"),
            "status": verify.get("status"),
            "error": None if passed else _mcp_error_message(verify),
            "unverified_claims": unverified_claims,
        }
        candidate = {
            **candidate,
            "status": STATUS_APPROVED,
            "verification": verification,
            "error": None if passed else _mcp_error_message(verify),
            "updated_at": utc_now(),
        }
        try:
            save_candidate(root, candidate)
        except Exception as exc:
            # 副作用已发生（skill 可能已 verified），但候选磁盘停在 approving。
            # 返回 retryable；重试用同一 proposal_id 会核对所有权后续跑并收敛。
            logger.warning("skill proposal final state save failed: %s", exc)
            return _error(
                "SKILL_PROPOSAL_STATE_SAVE_FAILED",
                "skill 已落盘/验证，但候选最终状态写盘失败；用同一 proposal_id 重试会自动收敛"
                "（不会重复创建）。",
                retryable=True,
                proposal_id=proposal_id,
                skill=name,
                verified=passed,
                path=candidate.get("approved_path"),
                verification=verification,
            )
        self._feedback(
            candidate,
            "approved",
            f"skill 候选已沉淀：{name}（验证{'通过' if passed else '未过'}）",
        )
        return {
            "ok": True,
            "proposal_id": proposal_id,
            "skill": name,
            "verified": passed,
            "gate": verify.get("gate"),
            "status": verify.get("status"),
            "path": candidate.get("approved_path"),
            "verification": verification,
            "claims": candidate.get("claims"),
            "message": (
                f"skill「{name}」已沉淀并通过验证。"
                if passed
                else f"skill「{name}」已落盘为未激活产物（验证未过），暂不可用。"
            ),
        }

    def _decided_payload(self, candidate: dict[str, Any]) -> dict[str, Any]:
        verification = candidate.get("verification") or {}
        passed = verification.get("state") == VERIFY_VERIFIED
        return {
            "ok": True,
            "proposal_id": str(candidate.get("proposal_id") or ""),
            "skill": candidate.get("name"),
            "verified": passed,
            "gate": verification.get("gate"),
            "status": verification.get("status"),
            "path": candidate.get("approved_path"),
            "verification": verification,
            "claims": candidate.get("claims"),
            "already_decided": True,
        }

    def _try_save(self, root: Path, candidate: dict[str, Any]) -> bool:
        try:
            save_candidate(root, candidate)
            return True
        except Exception as exc:
            logger.warning("skill proposal update save failed: %s", exc)
            return False

    def _feedback(self, candidate: dict[str, Any], decision: str, summary: str) -> None:
        try:
            append_feedback(getattr(self.session, "source_path", None), {
                "kind": "skill_proposal_outcome",
                "summary": summary,
                "detail": {
                    "decision": decision,
                    "proposal_id": candidate.get("proposal_id"),
                    "name": candidate.get("name"),
                    "pattern_type": candidate.get("pattern_type"),
                    "source_refs": candidate.get("source_refs"),
                    "evidence_complete": candidate.get("evidence_complete"),
                    "verification": candidate.get("verification"),
                    "claims": candidate.get("claims"),
                },
            })
        except Exception as exc:  # best-effort
            logger.debug("skill proposal feedback skipped: %s", exc)

    @staticmethod
    def _candidate_payload(candidate: dict[str, Any], *, reused: bool = False) -> dict[str, Any]:
        """候选 → assistant/API 载荷（前端审批卡直接消费）。"""
        source_refs = candidate.get("source_refs") or []
        changed_files: list[str] = []
        run_ids: list[str] = []
        revisions: list[str] = []
        fingerprints: list[str] = []
        validation_reasons: list[str] = []
        for ref in source_refs:
            if not isinstance(ref, dict):
                continue
            if ref.get("run_id"):
                run_ids.append(str(ref["run_id"]))
            if ref.get("revision"):
                revisions.append(str(ref["revision"]))
            if ref.get("source_fingerprint"):
                fingerprints.append(str(ref["source_fingerprint"]))
            for item in ref.get("changed_files") or []:
                changed_files.append(str(item))
            for reason in (ref.get("validation") or {}).get("reasons") or []:
                validation_reasons.append(str(reason))
        verification = candidate.get("verification") or {"state": VERIFY_UNVERIFIED}
        claims = candidate.get("claims") or {}
        return {
            "proposal_id": candidate.get("proposal_id"),
            "name": candidate.get("name"),
            "pattern_type": candidate.get("pattern_type"),
            "content": candidate.get("content"),
            "slice": candidate.get("slice"),
            "status": candidate.get("status") or STATUS_DRAFT,
            "verification": verification,
            "claims": claims,
            "artifact": candidate.get("artifact"),
            "protection": candidate.get("protection"),
            "error": candidate.get("error"),
            "created_at": candidate.get("created_at"),
            "updated_at": candidate.get("updated_at"),
            "reused": reused,
            "evidence": {
                "source": "explicit",
                "intent": next(
                    (ref.get("intent") for ref in source_refs if isinstance(ref, dict) and ref.get("intent")),
                    None,
                ),
                "changed_files": list(dict.fromkeys(changed_files)),
                "project": (candidate.get("project") or {}).get("name"),
                "project_path_hash": (candidate.get("project") or {}).get("path_hash"),
                "source_run_ids": list(dict.fromkeys(run_ids)),
                "revisions": list(dict.fromkeys(revisions)),
                "source_fingerprints": list(dict.fromkeys(fingerprints)),
                "evidence_complete": bool(candidate.get("evidence_complete")),
                "validation_reasons": list(dict.fromkeys(validation_reasons)),
                "project_selection": claims.get("project_selection"),
            },
        }
