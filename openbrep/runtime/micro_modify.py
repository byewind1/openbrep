"""确定性微修改（P2）：识别"把参数 X 改成值 V"并给出可直接落盘的修改指令。

定位（见 Obsidian《GDL 生成架构精准迭代评估与方案 2026-08-01》）：
高频小修改（改参数默认值）不经过 LLM——零 token、零"顺手改坏"风险。

设计原则：

- 高精度、零召回压力：识别不出就返回 None，请求回落正常 LLM MODIFY 路径，
  行为与今天完全一致。宁可漏判（走 LLM），不可误判（改错参数）。
- 只处理数值（Integer/RealNum/Length）和 Boolean 参数的值设置；
  重命名、加/删参数、多参数同改、String 参数一律回落。
- 单位换算只作用于 Length 类型（GDL 长度单位是米）；其他数值类型带长度
  单位属于语义不明，回落 LLM。
- 纯函数、无副作用：应用（写盘/快照/编译）由 pipeline 负责。
- 落盘语义（快照→改值→save_to_disk）也收在这里（apply_parameter_value），
  pipeline 微修改与 MCP apply_edit set_parameters 共用同一语义。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from openbrep.hsf_project import GDLParameter, HSFProject
from openbrep.revisions import get_latest_revision_id, is_hsf_project_dir

# 设置类动词 + 目标数值（可选长度单位）。只取第一处匹配作为目标值——
# "从 18mm 改为 25mm" 中 18mm 前面没有动词，天然不会被误取。
# 中文语序：把 X 改成 5（值紧跟动词）
_SET_VALUE_RE = re.compile(
    r"(?:改成|改为|设为|设置为|调整为|调成|变为)\s*"
    r"(?:到|为|至)?\s*"
    r"(-?\d+(?:\.\d+)?)\s*(mm|cm|m|毫米|厘米|米)?",
    re.IGNORECASE,
)
# 英文语序：set X to 5（参数夹在动词和值之间）
_SET_EN_RE = re.compile(
    r"(?:set|change|update)\s+\S+\s+(?:to|=)\s*"
    r"(-?\d+(?:\.\d+)?|true|false|yes|no|on|off)\s*(mm|cm|m)?(?![A-Za-z0-9])",
    re.IGNORECASE,
)

_SET_BOOL_RE = re.compile(
    r"(?:改成|改为|设为|设置为|调整为|调成|变为|set)\s*"
    r"(?:到|为|至|to)?\s*"
    r"(开启|打开|启用|关闭|关掉|禁用|开|关|true|false|yes|no|on|off)(?![A-Za-z])",
    re.IGNORECASE,
)

# 开关词义：Boolean 参数可直接说「打开 X」「关闭 X」「enable X」「disable X」
_BOOL_TOGGLE_RE = re.compile(
    r"(?:打开|开启|启用|开|turn\s+on|enable)|"
    r"(?:关闭|关掉|禁用|关|turn\s+off|disable)",
    re.IGNORECASE,
)

# 相对修改：把 X 增加/减少 N（支持长度单位）
_RELATIVE_VALUE_RE = re.compile(
    r"(?:增加|减少|调大|调小|加大|减小|升高|降低|上调|下调|add|subtract|increase|decrease|reduce|raise|lower)\s*"
    r"(-?\d+(?:\.\d+)?)\s*(mm|cm|m|毫米|厘米|米)?",
    re.IGNORECASE,
)
# 英文相对语序：increase X by 50mm / reduce X by 2cm
_RELATIVE_BY_RE = re.compile(
    r"(?:add|subtract|increase|decrease|reduce|raise|lower)\s+\S+\s+by\s*"
    r"(-?\d+(?:\.\d+)?)\s*(mm|cm|m)?(?![A-Za-z0-9])",
    re.IGNORECASE,
)

_BOOL_TRUE = {"开启", "打开", "启用", "开", "true", "yes", "on"}
_BOOL_FALSE = {"关闭", "关掉", "禁用", "关", "false", "no", "off"}

# 明显是复合/多意图请求——保守回落 LLM
_COMPOUND_HINTS = ("然后", "并且", "顺便", "还有", "另外", "同时", "再帮", "，再")
# 疑问句不是修改指令
_QUESTION_HINTS = ("为什么", "为啥", "怎么看", "吗？", "吗?", "？", "?")

_NUMERIC_TYPES = {"Integer", "RealNum", "Length"}

_LENGTH_FACTORS = {
    "mm": 0.001, "毫米": 0.001,
    "cm": 0.01, "厘米": 0.01,
    "m": 1.0, "米": 1.0,
    "": 1.0,
}


@dataclass(frozen=True)
class MicroModify:
    """一次确定性参数值修改。new_value 已按类型规整，可直接赋给 param.value。"""

    param_name: str
    old_value: str
    new_value: str
    matched_via: str  # "name" | "description"


def detect_micro_modify(instruction: str, project: HSFProject) -> Optional[MicroModify]:
    """识别"把参数 X 改成值 V"。识别不出返回 None（调用方回落 LLM 路径）。"""
    text = (instruction or "").strip()
    if not text or not project.parameters:
        return None
    if any(hint in text for hint in _COMPOUND_HINTS):
        return None
    if any(hint in text for hint in _QUESTION_HINTS):
        return None

    resolved = _resolve_param(text, project.parameters)
    if resolved is None:
        return None
    param, matched_via = resolved

    new_value = _extract_value(text, param, param.value)
    if new_value is None:
        return None

    return MicroModify(
        param_name=param.name,
        old_value=param.value,
        new_value=new_value,
        matched_via=matched_via,
    )


def _resolve_param(text: str, params: list[GDLParameter]) -> Optional[tuple[GDLParameter, str]]:
    """在指令文本里定位唯一参数：先按参数名（词边界），再按描述（最长匹配优先）。"""
    name_hits = [
        p for p in params
        if re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(p.name)}(?![A-Za-z0-9_])",
            text,
            re.IGNORECASE,
        )
    ]
    if len(name_hits) == 1:
        return name_hits[0], "name"
    if len(name_hits) > 1:
        return None

    desc_hits = [
        p for p in params
        if p.description and len(p.description.strip()) >= 2 and p.description.strip() in text
    ]
    if not desc_hits:
        return None
    # 描述可能互相包含（"深度" vs "深度方向间距"）：最长匹配唯一才接受
    desc_hits.sort(key=lambda p: -len(p.description.strip()))
    longest = len(desc_hits[0].description.strip())
    if len(desc_hits) > 1 and len(desc_hits[1].description.strip()) == longest:
        return None
    return desc_hits[0], "description"


def _extract_value(text: str, param: GDLParameter, old_value: str) -> Optional[str]:
    """按参数类型提取并规整目标值；语义不明返回 None。"""
    if param.type_tag == "Boolean":
        bool_match = _SET_BOOL_RE.search(text)
        if bool_match:
            word = bool_match.group(1).lower()
            if word in _BOOL_TRUE:
                return "1"
            if word in _BOOL_FALSE:
                return "0"
        # 开关词义：Boolean 参数可直接说「打开 X」「关闭 X」
        toggle_match = _BOOL_TOGGLE_RE.search(text)
        if toggle_match:
            word = toggle_match.group(0).lower()
            if word in _BOOL_TRUE or word in {"打开", "开启", "启用", "开", "enable", "turn on"}:
                return "1"
            if word in _BOOL_FALSE or word in {"关闭", "关掉", "禁用", "关", "disable", "turn off"}:
                return "0"
        # "把开关改成 1" 也允许，落到数值分支

    if param.type_tag not in _NUMERIC_TYPES and param.type_tag != "Boolean":
        return None  # String 等 v1 不处理

    raw: float | None = None
    unit = ""
    is_relative = False
    relative_sign = 1.0

    # 1. 绝对值设置
    match = _SET_VALUE_RE.search(text)
    if match:
        raw = float(match.group(1))
        unit = (match.group(2) or "").lower()
    else:
        en_match = _SET_EN_RE.search(text)
        if en_match:
            token = en_match.group(1).lower()
            if token in _BOOL_TRUE or token in _BOOL_FALSE:
                if param.type_tag != "Boolean":
                    return None
                return "1" if token in _BOOL_TRUE else "0"
            raw = float(token)
            unit = (en_match.group(2) or "").lower()

    # 2. 相对修改
    if raw is None:
        rel_match = _RELATIVE_VALUE_RE.search(text) or _RELATIVE_BY_RE.search(text)
        if rel_match:
            raw = float(rel_match.group(1))
            unit = (rel_match.group(2) or "").lower()
            is_relative = True
            # 中文「减少/调小/降低/下调」与英文 decrease/reduce/lower/subtract 为减
            rel_verb = rel_match.group(0).lower()
            if any(v in rel_verb for v in ("减少", "调小", "降低", "下调", "减小", "subtract", "decrease", "reduce", "lower")):
                relative_sign = -1.0

    if raw is None:
        return None

    if param.type_tag == "Boolean":
        return "1" if raw != 0 else "0"

    # 单位规整：先把输入值换算为米（如果是 Length）
    if param.type_tag == "Length":
        if not unit and raw > 10:
            # 无单位大数值更可能是 mm 意图（"把宽度改成 900"），
            # 静默当米会差 1000 倍——回落 LLM 带上下文判断
            return None
        raw = raw * _LENGTH_FACTORS[unit]

    if is_relative:
        try:
            base = float(old_value)
        except ValueError:
            return None
        raw = base + raw * relative_sign
    elif unit and param.type_tag != "Length":
        return None  # RealNum/Integer 带长度单位，语义不明

    if param.type_tag == "Length":
        return str(raw)

    if param.type_tag == "Integer":
        if raw != int(raw):
            return None  # "层数改成 5.5" 不能静默截断
        return str(int(raw))

    return str(raw)  # RealNum


# ── 参数值落盘（快照→改值→save_to_disk） ──────────────────


def _project_on_disk(project: HSFProject) -> bool:
    root = Path(getattr(project, "root", "") or "")
    try:
        return root.is_dir() and is_hsf_project_dir(root)
    except Exception:
        return False


def apply_parameter_value(
    project: HSFProject,
    param_name: str,
    new_value: str,
    *,
    user_instruction: str = "",
    before_message: str = "auto: before modify",
    trigger: str = "modify",
    intent: str = "MODIFY",
    changed_files: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    create_revision: Callable[..., Any] | None = None,
) -> tuple[Optional[str], list[str]]:
    """快照→改值→落盘：参数值变更的统一落盘语义。

    pipeline 微修改（TaskPipeline._try_micro_modify）与 MCP apply_edit
    set_parameters 共用，行为与微修改原内联流程完全一致：
    - 项目未落盘为 HSF 目录 → 跳过快照，仅改值 + save_to_disk；
    - create_revision 失败 → 记录警告，不阻断改值；
    - 值变更始终落盘。

    create_revision 由调用方注入（pipeline 传自己的模块级 create_revision，
    MCP 传 openbrep.revisions.create_revision），本函数为纯函数、不依赖
    pipeline 状态。返回 (revision_id | None, 警告列表)。
    """
    warnings: list[str] = []
    revision_id: Optional[str] = None
    if create_revision is None:
        from openbrep.revisions import create_revision as _real_create_revision

        create_revision = _real_create_revision

    if not _project_on_disk(project):
        warnings.append("项目尚未保存为 HSF 目录，已跳过自动版本快照")
    else:
        try:
            revision = create_revision(
                project.root,
                message=before_message,
                gsm_name=project.name,
                metadata=metadata,
                trigger=trigger,
                intent=intent,
                user_instruction=user_instruction,
                changed_files=list(changed_files or []),
                parent_revision_id=get_latest_revision_id(project.root),
            )
            revision_id = revision.revision_id
        except Exception as exc:
            warnings.append(f"自动版本快照失败：{exc}")

    param = project.get_parameter(param_name)
    if param is not None:
        param.value = new_value
    project.save_to_disk()
    return revision_id, warnings
