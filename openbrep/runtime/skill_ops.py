"""结构化 skill：operations 模板走确定性路径（任务 S3）。

skill frontmatter 增加单行 JSON 的 operations 字段（同 S1 slice 的存法）：

    ---
    status: verified
    pattern_type: shelf_loop
    operations: {"match": {"keywords": ["书架", "bookshelf"], "params": ["shelf_count"]}, "ops": [{"op": "set_value", "param": "shelf_count", "value": "{{number}}"}, {"op": "set_value", "param": "shelf_thk", "value": 0.025}]}
    ---

MODIFY 指令高精度命中"带模板的 active/verified skill"时，优先走确定性路径：
模板占位符填值 → 复用 param_modify 的 _validate_ops 校验 → 产物 ParamModifyPlan
（由 pipeline 走与 V1 DSL 完全相同的应用/守护/编译/语义 advisory）。任何一步
失败一律 None 回落原路径（micro → skill_ops → DSL → LLM）。

纪律（对齐 micro_modify：宁可漏判不可误判）：
- 只做 param 级 ops（set_value/add_param/del_param/rename_param，复用 V1 校验）；
  patch 模板（V2 patch_script）本单不做。
- match 高精度：skill 必须 active/verified + 有合法 operations + 指令命中
  match 声明的触发词/参数名 + 引用的参数在项目里真实存在；任一不满足 → 不候选。
- 多 skill 命中即歧义 → None 回落，不猜测、不降级匹配精度。
- 占位符（{{number}}/{{boolean}}）先确定性抽取（数字/布尔/单位换算，复用
  micro_modify 的解析），抽不出才一次小 LLM 调用只填值；任何一步失败 → None。
- 本模块只读：绝不新增/修改仓库 skills/ 任何文件。
"""

from __future__ import annotations

import json
import logging
import re
from copy import deepcopy
from typing import Any, Callable, Optional

from openbrep.hsf_project import GDLParameter, HSFProject
from openbrep.runtime.param_modify import (
    ParamModifyPlan,
    _extract_json,
    _normalize_value,
    _validate_ops,
)
from openbrep.skills_loader import _INJECTABLE_STATUSES

logger = logging.getLogger(__name__)

# 占位符语法：{{number}} / {{boolean}}（value 字段里出现）
_PLACEHOLDER_RE = re.compile(r"^\s*\{\{(\w+)\}\}\s*$")
_SUPPORTED_PLACEHOLDERS = {"number", "boolean"}

# 允许的 param 级操作（复用 param_modify._validate_ops 的校验；patch 留后续单）
_VALID_OP_SET = {"set_value", "add_param", "del_param", "rename_param"}

# 占位符可直接做确定性/LLM 填值的参数类型（_normalize_value 支持的设值类型）
_FILLABLE_TYPES = {"Boolean", "Integer", "RealNum", "Length", "Angle", "String"}


# ── 模板解析（只读 frontmatter，坏模板视为"无模板"）─────────────────

def parse_operations(raw: Any) -> Optional[dict]:
    """解析 operations 字段：单行 JSON 字符串（或已解析 dict）→ 规范形态。

    返回 {"match": {...}, "ops": [...]}；坏 JSON / 形态非法 → None
    （调用方按"该 skill 无模板"处理）。
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return None
    else:
        data = raw
    if not isinstance(data, dict):
        return None
    match = _parse_match(data.get("match"))
    ops = data.get("ops")
    if not isinstance(ops, list) or not ops:
        return None
    if not _validate_ops_shape(ops):
        return None
    if match is None:
        return None
    return {"match": match, "ops": ops}


def _parse_match(match: Any) -> Optional[dict]:
    """match 段：{"keywords": [必填非空], "params": [可选]}；形态非法 → None。"""
    if not isinstance(match, dict):
        return None
    keywords = match.get("keywords")
    if not isinstance(keywords, list) or not keywords:
        return None
    if not all(isinstance(k, str) and k.strip() for k in keywords):
        return None
    params = match.get("params")
    if params is not None:
        if not isinstance(params, list) or not all(isinstance(p, str) and p.strip() for p in params):
            return None
        params = [p.strip() for p in params]
    else:
        params = []
    return {"keywords": [k.strip() for k in keywords], "params": params}


def _validate_ops_shape(ops: list) -> bool:
    """ops 基本形态：每个元素是 dict 且 op 在允许集合内。"""
    for op in ops:
        if not isinstance(op, dict):
            return False
        if op.get("op") not in _VALID_OP_SET:
            return False
    return True


# ── match 高精度匹配（宁缺毋滥）────────────────────────────

def _match_instruction(text: str, match: dict) -> bool:
    """指令命中：至少一个关键词出现；match.params（若声明）全部以词边界出现。"""
    keywords = match.get("keywords") or []
    params = match.get("params") or []
    low = text.lower()
    if not any(k.lower() in low for k in keywords):
        return False
    for p in params:
        if not re.search(rf"(?<![A-Za-z0-9_]){re.escape(p)}(?![A-Za-z0-9_])", text, re.IGNORECASE):
            return False
    return True


def _ops_params_exist(ops: list, project: HSFProject) -> bool:
    """ops 引用的参数在项目里真实存在（add_param 不引用既有参数，跳过）。"""
    params = {p.name.lower(): p for p in project.parameters}
    for op in ops:
        opname = op.get("op")
        if opname == "set_value":
            p = op.get("param")
            if not isinstance(p, str) or p.strip().lower() not in params:
                return False
        elif opname == "del_param":
            p = op.get("param")
            if not isinstance(p, str) or p.strip().lower() not in params:
                return False
        elif opname == "rename_param":
            f = op.get("from")
            if not isinstance(f, str) or f.strip().lower() not in params:
                return False
        elif opname == "add_param":
            continue
        else:
            return False
    return True


def _match_candidates(instruction: str, project: HSFProject, loader) -> list[tuple[str, dict]]:
    """所有命中条件的 (skill_name, template)。loader 为 None → 无候选。"""
    if loader is None:
        return []
    text = (instruction or "").strip()
    candidates: list[tuple[str, dict]] = []
    for name in loader.skill_names:
        meta = loader.skill_meta(name)
        if meta.get("status") not in _INJECTABLE_STATUSES:
            continue
        template = parse_operations(meta.get("operations"))
        if template is None:
            continue
        if not _match_instruction(text, template["match"]):
            continue
        if not _ops_params_exist(template["ops"], project):
            continue
        candidates.append((name, template))
    return candidates


# ── 占位符填值（先确定性，抽不出才一次小 LLM 调用）────────────────

def _param_for_op(op: dict, project: HSFProject) -> Optional[GDLParameter]:
    """op 引用的目标参数：set/del/rename 取项目参数；add_param 合成（用声明类型）。"""
    params = {p.name.lower(): p for p in project.parameters}
    opname = op.get("op")
    if opname in ("set_value", "del_param"):
        pname = op.get("param")
        if not isinstance(pname, str):
            return None
        return params.get(pname.strip().lower())
    if opname == "rename_param":
        frm = op.get("from")
        if not isinstance(frm, str):
            return None
        return params.get(frm.strip().lower())
    if opname == "add_param":
        name = op.get("name")
        typ = op.get("type")
        if not isinstance(name, str) or not isinstance(typ, str):
            return None
        if typ not in _FILLABLE_TYPES:
            return None
        return GDLParameter(name=name.strip(), type_tag=typ, description="", value="")
    return None


def _extract_value_deterministic(instruction: str, param: GDLParameter) -> Optional[str]:
    """复用 micro_modify 的高精度值抽取（数字/布尔/单位换算，同一纪律）。"""
    from openbrep.runtime.micro_modify import _extract_value

    try:
        return _extract_value(instruction, param, param.value or "")
    except Exception:
        return None


def _llm_fill_values(
    instruction: str, project: HSFProject, missing: list[tuple[int, str, str, str]], llm
) -> Optional[dict]:
    """一次小 LLM 调用只填占位符值：返回 {param_name: value} 或 None。"""
    lines = "\n".join(
        f"- {pname}（{ptype}{('，' + desc) if desc else ''}）"
        for _i, pname, ptype, desc in missing
    )
    system = (
        "你是 GDL 参数值抽取器。下面是一次 GDL 参数修改指令和需要抽取值的参数清单。"
        "请从指令中抽取每个参数的目标值，只输出一个 JSON 对象：键为参数名，值为数值或布尔。\n"
        "硬性规则：\n"
        "- Length/Angle 参数的值以米为单位（900mm → 0.9，25mm → 0.025）；\n"
        "- Integer 只输出整数；Boolean 用 true/false；\n"
        "- 从指令里抽不出明确值的参数不要输出该键（不要猜）。\n"
        "只输出 JSON，不要 Markdown 代码块，不要任何解释文字。"
    )
    user = f"指令：{instruction}\n\n参数清单：\n{lines}"
    try:
        resp = llm.generate(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.0, max_tokens=300, stream=False,
        )
    except Exception as exc:
        logger.warning("skill_ops 占位符 LLM 填值失败，回落: %s", exc)
        return None
    data = _extract_json(getattr(resp, "content", "") or "")
    if not isinstance(data, dict):
        return None
    return data


def _fill_ops(
    instruction: str,
    project: HSFProject,
    ops: list,
    make_llm: Callable[[], Any] | None,
) -> Optional[list[dict]]:
    """填充占位符：先确定性（数字/布尔/单位），抽不出才一次 LLM 调用只填值。

    返回填好值的 raw ops（具体值已规整为 paramlist 字符串）；任何一步失败 → None。
    """
    filled = deepcopy(ops)
    missing: list[tuple[int, str, str, str]] = []
    for i, op in enumerate(filled):
        value = op.get("value")
        if not isinstance(value, str):
            continue  # 具体值（数字/布尔/非占位符字符串）原样保留，交给 _validate_ops 规整
        m = _PLACEHOLDER_RE.match(value)
        if not m:
            continue
        ph = m.group(1)
        if ph not in _SUPPORTED_PLACEHOLDERS:
            return None  # 未知占位符 → 模板不可填 → 回落
        param = _param_for_op(op, project)
        if param is None:
            return None
        val = _extract_value_deterministic(instruction, param)
        if val is not None:
            op["value"] = val
        else:
            missing.append((i, param.name, param.type_tag, param.description or ""))

    if not missing:
        return filled

    llm = make_llm() if callable(make_llm) else None
    if llm is None:
        return None
    filled_values = _llm_fill_values(instruction, project, missing, llm)
    if filled_values is None:
        return None
    values_lower = {str(k).lower(): v for k, v in filled_values.items()}
    for i, pname, ptype, _desc in missing:
        raw_value = values_lower.get(pname.lower())
        if raw_value is None:
            return None
        norm = _normalize_value(ptype, raw_value)
        if norm is None:
            return None
        filled[i]["value"] = norm
    return filled


# ── 主入口 ────────────────────────────────────────────────

def try_skill_ops(
    instruction: str,
    project: HSFProject,
    loader,
    make_llm: Callable[[], Any] | None = None,
) -> Optional[tuple[ParamModifyPlan, str]]:
    """扫描带模板 skill：恰好一个高精度命中 → 填值 → 校验 → (plan, skill_name)。

    0 命中 / 多命中歧义 / 填值失败 / 校验不过 → None（回落原路径）。
    make_llm 仅在被占位符触发时调用（pipeline 传惰性构造，不命中不建 LLM）。
    绝不抛出：任何异常静默回落。
    """
    try:
        candidates = _match_candidates(instruction, project, loader)
        if len(candidates) != 1:
            return None  # 0 或 >1 都是歧义，回落
        name, template = candidates[0]
        filled = _fill_ops(instruction, project, template["ops"], make_llm)
        if filled is None:
            return None
        ops = _validate_ops(filled, project)
        if ops is None:
            return None
        plan = ParamModifyPlan(operations=ops, raw={"source": "skill_ops", "skill": name})
        return plan, name
    except Exception as exc:
        logger.warning("skill_ops try failed (fallback to default path): %s", exc)
        return None
