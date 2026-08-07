"""模式级 skill 提案自动提炼（GUI 侧通道，不进 pipeline 默认路径）。

CREATE/MODIFY 成功且验证全过时，从本次生成/修改记录里提炼一个可复用的
模式级 skill 提案：LLM 提炼（一次调用、温度 0、小 max_tokens、非流式）→
GUI 弹"沉淀提案"确认卡 → 用户批准走 propose_skill + verify_skill 双闸晋升，
拒绝则丢弃并记 feedback 事件（skill_proposal_outcome）。

纪律：
- 只在 GUI 侧通道调用（assistant_service / project create 拿到成功 TaskResult
  之后），绝不进 pipeline 默认路径——benchmark 直接调 pipeline，回放不能断。
- 提炼失败静默（返回 None / logger.warning），绝不阻塞已完成的修改交付。
- 触发门禁（全过才提炼）：compile 成功 + 语义无 blocking + 有实际变更；
  intent 限 CREATE / MODIFY。
- 去重：skills 目录已有同名文件（任何状态，propose 不覆盖）或已有
  active/verified 的同 pattern_type skill 时不提议。
- 输出纪律：pattern_type 限于枚举；content 为模式级 Markdown 抽象，
  禁贴实例代码（[FILE: 块）、禁含项目名；严格 JSON {name, pattern_type,
  content, slice?}；解析失败/校验不过 → None 静默。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from openbrep.feedback import append_feedback
from openbrep.skills_loader import SkillsLoader

logger = logging.getLogger(__name__)

# pattern_type 枚举（提案卡片与去重都用它）
PATTERN_TYPES: frozenset[str] = frozenset({
    "shelf_loop",            # 层板/搁架循环（FOR 循环 + 等距分布）
    "transform_stack_pair",  # ADD/DEL 变换栈配对
    "sweep",                 # 拉伸/扫掠（EXTRUDE/SWEEP 组合）
    "panel",                 # 面板/嵌板（带厚度薄片 + 边框）
    "param_group",           # 参数分组/联动（一组参数协同控制几何）
    "mirror_symmetry",       # 镜像对称几何
    "repeating_geometry",    # 重复几何（阵列/循环派生）
})

# 只在这两个 intent 下提炼
HARVESTABLE_INTENTS: frozenset[str] = frozenset({"CREATE", "MODIFY"})

# 提炼 LLM 调用参数
_HARVEST_TEMPERATURE = 0.0
_HARVEST_MAX_TOKENS = 900

# 校验阈值
_CONTENT_MIN_CHARS = 80
_CONTENT_MAX_CHARS = 6000
_SLICE_SCRIPT_TYPES = {"1d", "2d", "3d", "vl", "ui", "master"}
_SLICE_PARAM_TYPES = (
    "Length", "Angle", "RealNum", "Integer", "Boolean",
    "String", "Material", "FillPattern", "LineType", "PenColor",
)

# 提案里必须出现的小节（verify structural 门禁也要触发词小节，直接对齐）
_REQUIRED_SECTION = "When to Use"
_REQUIRED_SECTION_ZH = "适用场景"


# ── 触发门禁 ─────────────────────────────────────────────

def _passes_harvest_gate(result) -> bool:
    """全过才提炼：成功 + intent 限定 + 编译成功 + 语义无 blocking + 有实际变更。"""
    try:
        if (getattr(result, "intent", "") or "") not in HARVESTABLE_INTENTS:
            return False
        if not result.success:
            return False
        if result.project is None:
            return False
        if not result.scripts:
            return False  # 无实际变更
        compile_result = result.compile_result
        if compile_result is None or not compile_result.success:
            return False
        verification = result.verification or {}
        for check in verification.get("checks") or []:
            check_type = check.get("check_type")
            status = check.get("status")
            if check_type in ("semantic", "compile") and status not in (None, "pass"):
                return False
        return True
    except Exception:
        logger.warning("skill_harvest gate check failed", exc_info=True)
        return False


# ── 去重 ─────────────────────────────────────────────────

def _dedup_collision(skills_dir: Any, name: str, pattern_type: str) -> bool:
    """True = 已有同名（任何状态，propose 不覆盖）或同 pattern_type 的
    active/verified skill，不应再提议。"""
    try:
        loader = SkillsLoader(str(skills_dir))
        loader.load()
        for skill_name in loader.skill_names:
            if skill_name == name:
                return True
            meta = loader.skill_meta(skill_name)
            if (
                meta.get("status") in ("active", "verified")
                and meta.get("pattern_type") == pattern_type
            ):
                return True
    except Exception:
        logger.warning("skill_harvest dedup check failed", exc_info=True)
    return False


# ── 校验 ─────────────────────────────────────────────────

def _valid_skill_name(name: Any) -> bool:
    """skill 名合法性（与 mcp_tools._is_valid_skill_name 对齐）。"""
    if not isinstance(name, str) or not name:
        return False
    if name != name.strip() or name in (".", "..") or name.upper() == "README":
        return False
    if name[0] == ".":
        return False
    if any(ord(ch) < 32 for ch in name):
        return False
    if any(ch in name for ch in ('/', "\\", "\x00", "<", ">", ":", '"', "|", "?", "*")):
        return False
    return True


def _extract_json(text: str) -> Optional[Any]:
    """严格 JSON 提取：先整段解析，再退化到第一个平衡的 {…} 对象。"""
    content = (text or "").strip()
    if not content:
        return None
    import re as _re
    stripped = _re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=_re.IGNORECASE).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    start = stripped.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(stripped)):
        ch = stripped[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(stripped[start : i + 1])
                    except json.JSONDecodeError:
                        return None
    return None


def _validate_slice(raw: Any) -> Optional[dict]:
    """slice 轻校验：dict、params/scripts 形态正确；非法返回 None（不提议）。"""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        return None
    if not (raw.get("params") or raw.get("scripts")):
        return None  # 空 slice 视作没有
    params = raw.get("params") or {}
    if not isinstance(params, dict):
        return None
    for pname, spec in params.items():
        if isinstance(spec, dict):
            if "value" not in spec or "type" not in spec:
                return None
            if spec.get("type") not in _SLICE_PARAM_TYPES:
                return None
        elif isinstance(spec, (bool, int, float, str)):
            continue
        else:
            return None
    scripts = raw.get("scripts") or {}
    if not isinstance(scripts, dict):
        return None
    for key, content in scripts.items():
        if str(key).lower() not in _SLICE_SCRIPT_TYPES or not isinstance(content, str):
            return None
    return {"params": params, "scripts": scripts}


def _validate_proposal(raw: Any, project) -> Optional[dict]:
    """校验 LLM 提炼结果：任何一条不过 → None（静默不提议）。"""
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    pattern_type = raw.get("pattern_type")
    content = raw.get("content")
    if not _valid_skill_name(name):
        return None
    if not isinstance(pattern_type, str) or pattern_type not in PATTERN_TYPES:
        return None
    if not isinstance(content, str):
        return None
    content = content.strip()
    if len(content) < _CONTENT_MIN_CHARS or len(content) > _CONTENT_MAX_CHARS:
        return None
    # 禁贴实例代码：出现 [FILE: 块视为贴了实例代码
    if "[FILE:" in content or "[FILE：" in content:
        return None
    # 禁含项目名
    project_name = getattr(project, "name", "") or ""
    if project_name and (project_name in content or project_name.lower() in content.lower()):
        return None
    # 要求触发词小节（与 verify structural 门禁对齐）
    if _REQUIRED_SECTION not in content and _REQUIRED_SECTION_ZH not in content:
        return None
    slice_data = _validate_slice(raw.get("slice"))
    if raw.get("slice") is not None and slice_data is None:
        return None  # 带 slice 但形态非法 → 整单拒绝（严格校验，静默不提议）
    return {
        "name": name,
        "pattern_type": pattern_type,
        "content": content,
        "slice": slice_data,
    }


# ── 提炼 prompt ──────────────────────────────────────────

def _build_harvest_messages(result, instruction: str, project) -> list[dict]:
    """构造一次 LLM 调用的消息：把成功任务摘要 + 输出契约给模型。"""
    changed = list((result.scripts or {}).keys())
    files_hint = "、".join(changed[:8])
    instruction_hint = (instruction or "")[:100]
    enum_hint = " / ".join(sorted(PATTERN_TYPES))
    system = (
        "你是 GDL 模式提炼器。下面是一次【验证全过】的 GDL 对象生成/修改任务记录。"
        "请从中提炼一个可复用的【模式级】skill 提案：抽象出可复用的写法与纪律，"
        "而不是复述本次实例。\n\n"
        "只输出一个 JSON 对象，不要 Markdown 代码块标记，不要任何解释文字：\n"
        '{"name": "<英文蛇形 skill 名>", "pattern_type": "<枚举值>", '
        '"content": "<markdown 模式说明>", "slice": <可选参数/脚本骨架>}\n\n'
        f"pattern_type 只能是以下之一：{enum_hint}\n\n"
        "硬性纪律：\n"
        "- content 必须是模式级抽象：含触发场景、写法要点、注意事项；"
        "必须包含 '## 适用场景 / When to Use' 小节；80-6000 字；\n"
        "- 禁止贴本次实例代码（禁止 [FILE: 块）；禁止出现项目名/专有名词；\n"
        "- slice 可选：{params: {参数名: 值或{value, type}}, "
        'scripts: {"3d": "...", "2d": "..."}}，脚本用占位参数名，不要实例尺寸。'
    )
    user = (
        f"任务意图：{result.intent}\n"
        f"用户指令：{instruction_hint}\n"
        f"变更文件：{files_hint}\n"
        f"项目名：{getattr(project, 'name', '')}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# ── 主入口 ───────────────────────────────────────────────

def maybe_harvest(
    result,
    instruction: str,
    project,
    llm: Any,
    skills_dir: Any,
) -> Optional[dict]:
    """成功任务 → 模式级 skill 提案；不满足门禁/去重/解析失败 → None 静默。

    llm 需有 generate(messages, **kwargs) -> LLMResponse（content 字段）。
    温度 0 / max_tokens 900 / 非流式：从简、失败不重试（省 token）。
    绝不抛出（内部全 try/except），绝不影响主流程。
    """
    try:
        if not _passes_harvest_gate(result):
            return None
    except Exception:
        logger.warning("skill_harvest gate check failed", exc_info=True)
        return None

    try:
        messages = _build_harvest_messages(result, instruction, project)
        resp = llm.generate(
            messages,
            temperature=_HARVEST_TEMPERATURE,
            max_tokens=_HARVEST_MAX_TOKENS,
            stream=False,
        )
    except Exception as exc:
        logger.warning("skill_harvest LLM 提炼失败，静默跳过: %s", exc)
        return None

    raw = _extract_json(getattr(resp, "content", "") or "")
    proposal = _validate_proposal(raw, project)
    if proposal is None:
        return None
    if _dedup_collision(skills_dir, proposal["name"], proposal["pattern_type"]):
        return None
    proposal["evidence"] = {
        "intent": getattr(result, "intent", ""),
        "changed_files": list((result.scripts or {}).keys()),
        "project": getattr(project, "name", ""),
    }
    return proposal


def store_pending_proposal(
    session,
    result,
    instruction: str,
    llm: Any,
    skills_dir: Any,
) -> Optional[dict]:
    """maybe_harvest + 存入 session.pending_skill_proposal（带 project_epoch）。

    返回 proposal（供响应携带 skill_proposal 字段）；无提案返回 None。
    提炼失败/门禁不过静默，绝不阻塞已完成的修改交付。
    """
    proposal = maybe_harvest(result, instruction, result.project, llm, skills_dir)
    if proposal is None:
        return None
    try:
        session.pending_skill_proposal = {
            "proposal": proposal,
            "project_epoch": getattr(session, "project_epoch", None),
            "source_path": getattr(session, "source_path", None),
        }
    except Exception as exc:
        logger.warning("skill_harvest store pending failed: %s", exc)
        return None
    return proposal


# ── 确认 / 拒绝（approve → propose + verify 双闸；reject → 丢弃） ──

def _confirm_skill_proposal_impl(session, body: dict[str, Any], skills_dir: Any) -> dict[str, Any]:
    """审批待确认 skill 提案（POST /api/skill/confirm）。

    approve=True → propose_skill 落盘（status=proposed）→ 立即 verify_skill
    双闸晋升 → 结果进响应；approve=False → 丢弃。两种结局都写
    skill_proposal_outcome 反馈事件（best-effort）。
    无 pending / 跨项目代次失效 → 明确错误码。
    """
    pending = getattr(session, "pending_skill_proposal", None)
    if pending is None:
        return {
            "ok": False,
            "code": "NO_PENDING_SKILL_PROPOSAL",
            "error": "没有待确认的 skill 提案。",
        }
    if pending.get("project_epoch") != getattr(session, "project_epoch", None):
        session.pending_skill_proposal = None
        return {
            "ok": False,
            "code": "NO_PENDING_SKILL_PROPOSAL",
            "error": "skill 提案已失效（项目已切换），请重新生成后再沉淀。",
        }
    proposal = pending.get("proposal") or {}
    source_path = pending.get("source_path") or getattr(session, "source_path", None)
    session.pending_skill_proposal = None
    name = str(proposal.get("name") or "")
    pattern_type = str(proposal.get("pattern_type") or "")

    if body.get("approve") is not True:
        append_feedback(source_path, {
            "kind": "skill_proposal_outcome",
            "summary": f"用户拒绝了 skill 提案：{name}",
            "detail": {"decision": "rejected", "name": name, "pattern_type": pattern_type},
        })
        return {"ok": True, "discarded": True, "message": "已丢弃 skill 提案。"}

    from openbrep.mcp_tools import propose_skill, verify_skill

    propose = propose_skill(
        name=name,
        content=str(proposal.get("content") or ""),
        pattern_type=pattern_type,
        source_project=str(source_path or ""),
        source_trace_id="",
        slice=proposal.get("slice") or None,
        skills_dir=skills_dir,
    )
    detail: dict[str, Any] = {"decision": "approved", "name": name, "pattern_type": pattern_type}
    if not propose.get("ok"):
        detail["propose_error"] = str(propose.get("error"))
        append_feedback(source_path, {
            "kind": "skill_proposal_outcome",
            "summary": f"skill 提案落盘失败：{name}",
            "detail": detail,
        })
        return {
            "ok": False,
            "code": "SKILL_PROPOSE_FAILED",
            "error": str(propose.get("error")),
            "skill": name,
        }

    verify = verify_skill(name=name, skills_dir=skills_dir)
    detail["verified"] = verify.get("passed")
    detail["gate"] = verify.get("gate")
    detail["status"] = verify.get("status")
    append_feedback(source_path, {
        "kind": "skill_proposal_outcome",
        "summary": f"skill 提案已沉淀：{name}（验证{'通过' if verify.get('passed') else '未过'}）",
        "detail": detail,
    })
    return {
        "ok": True,
        "skill": name,
        "verified": verify.get("passed") is True,
        "gate": verify.get("gate"),
        "status": verify.get("status"),
        "path": propose.get("path"),
    }


# ── session 级便捷入口（assistant_service / project create 共用） ──

def resolve_skills_dir() -> str:
    """仓库级 skills 目录（与 pipeline._resolve_skills_dir 一致）。"""
    return str(Path(__file__).resolve().parent.parent.parent / "skills")


def _build_session_llm(session):
    """从 session 配置构建提炼用 LLMAdapter；失败返回 None（静默跳过）。"""
    try:
        import dataclasses

        from openbrep.config import GDLAgentConfig
        from openbrep.llm import LLMAdapter

        config = getattr(session, "config", None)
        if config is None:
            config = GDLAgentConfig()
        llm_cfg = dataclasses.replace(config.llm)
        session_model = getattr(session, "llm_model", None)
        if session_model:
            llm_cfg = dataclasses.replace(llm_cfg, model=session_model)
        if getattr(session, "llm_api_key", ""):
            llm_cfg = dataclasses.replace(llm_cfg, api_key=session.llm_api_key)
        if getattr(session, "llm_api_base", ""):
            llm_cfg = dataclasses.replace(llm_cfg, api_base=session.llm_api_base)
        return LLMAdapter(llm_cfg)
    except Exception:
        logger.warning("skill_harvest llm build failed (skip harvest)", exc_info=True)
        return None


def harvest_for_session(session, result, instruction: str) -> Optional[dict]:
    """session 级入口：门禁/去重/LLM 提炼 → 存 pending_skill_proposal。

    返回提案（供响应携带 skill_proposal 字段）；无提案/失败返回 None。
    提炼失败静默，绝不阻塞已完成的修改交付。
    """
    if not getattr(session, "skill_harvest_enabled", True):
        return None
    if not _passes_harvest_gate(result):
        return None  # 门禁不过不建 LLM（省一次配置解析）
    llm = _build_session_llm(session)
    if llm is None:
        return None
    return store_pending_proposal(
        session, result, instruction, llm, resolve_skills_dir()
    )


def confirm_skill_proposal(session, body: dict[str, Any]) -> dict[str, Any]:
    """session 级入口：审批待确认 skill 提案（skills_dir 自动解析）。"""
    return _confirm_skill_proposal_impl(session, body, resolve_skills_dir())
