"""参数级修改 DSL（V1）：自然语言 → 结构化参数操作 JSON → 确定性应用。

定位（任务 V1）：在"正则微修改"（micro_modify，单参数设值）与"LLM agent loop
全文改写"之间加一层——LLM 只负责把自然语言映射成结构化参数操作 JSON（小
schema），应用操作全是确定性代码。覆盖面从"单参数设值"扩到参数级修改的
主要场景，同时把 LLM 写 GDL 代码的需求降下来。

v1 操作集（全部 paramlist 级，不做 VALUES 列表编辑/脚本级修改）：
- set_value    多参数设值（Boolean/Integer/Length/RealNum/Angle/String 等）
- add_param    新增参数（带默认值）
- del_param    删除参数；脚本里被引用的参数拒绝删除（会留悬挂引用）
- rename_param 重命名参数，脚本引用整词替换（复用 naming_alignment 的
  replace_identifier：标识符词边界 + VALUES "name" 整串替换；保留名
  A/B/ZZYZX/AC_* 不可作 rename 源/目标、不可 del/add，命中即拒绝该 op）

纪律（与 micro_modify 一致：宁可漏判不可误判）：
- 疑问句、非参数级请求 → 直接回落（省一次 LLM 调用）
- 解析失败 / JSON 不合法 / 任一 op 校验不过（参数不存在/类型不符/多义/
  保留名）→ 返回 None 回落 LLM 路径，不硬执行
- 一次 LLM 调用：温度 0、max_tokens 1024、非流式；失败不重试
- 应用守护：变更文件只允许 paramlist.xml + rename 受影响的 scripts/*，
  其他文件出现变更即回滚并返回 applied=False（调用方回落 LLM 路径）

落盘语义与 apply_parameter_value（micro_modify）一致：快照（create_revision）
→ 应用 → save_to_disk；create_revision 由调用方注入。
"""

from __future__ import annotations

import json
import logging
import re
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from openbrep.hsf_project import GDLParameter, HSFProject, VALID_PARAM_TYPES
from openbrep.naming_alignment import _is_reserved, replace_identifier
from openbrep.revisions import get_latest_revision_id, is_hsf_project_dir

logger = logging.getLogger(__name__)

# ── 回落纪律（与 micro_modify 对齐）────────────────────────

_QUESTION_HINTS = ("为什么", "为啥", "怎么看", "吗？", "吗?", "？", "?")

# 明确带"额外非参数工作"的信号（沿用 micro_modify 复合词纪律的窄化版）：
# 出现即直接回落，不浪费 LLM 调用。纯参数连接词（并且/然后/还有/另外/同时/，再）
# 由 DSL 的 LLM 自行解析（多参数 set_value 是 DSL 的核心能力）。
_EXTRA_WORK_HINTS = ("顺便", "再帮")

# 便宜预筛关键词：指令里出现这些才值得调用 LLM 做意图解析
_PARAM_OP_KEYWORDS = (
    "参数", "parameter",
    "改名为", "重命名", "rename",
    "新增参数", "删除参数", "删掉参数", "去掉参数",
    "add parameter", "remove parameter", "delete parameter",
)

_VALID_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


# ── 数据契约 ──────────────────────────────────────────────

@dataclass
class ParamOp:
    """一条参数操作。op 为判别字段，其余字段按 op 使用：

    - set_value:    param / value / old_value
    - add_param:    name / type / value / description
    - del_param:    param / old_value
    - rename_param: from_name / name（to）/ occurrences
    """

    op: str
    param: str = ""
    value: Any = None
    name: str = ""
    from_name: str = ""
    type: str = ""
    description: str = ""
    old_value: str = ""
    occurrences: int = 0


@dataclass
class ParamModifyPlan:
    """一次参数级修改计划：有序 op 列表 + 原始 LLM JSON（metadata/报告用）。"""

    operations: list[ParamOp]
    raw: dict

    def to_dict(self) -> dict:
        return {
            "operations": [op.__dict__ for op in self.operations],
            "raw": self.raw,
        }


@dataclass
class ApplyOutcome:
    """apply_param_modify 的结果。applied=False = 守护回滚，调用方回落 LLM 路径。"""

    applied: bool
    revision_id: Optional[str] = None
    warnings: list[str] = field(default_factory=list)
    changed_files: Optional[list[str]] = None


# ── 系统提示词（DSL 意图解析）──────────────────────────────

_SYSTEM_PROMPT = """你是一个 GDL 对象参数操作解析器。用户给出修改 GDL 对象参数的自然语言指令，你把它解析成结构化参数操作 JSON。你绝不编写或修改 GDL 代码，也不输出 GDL 代码。

# 可用操作（v1，只能输出这四种）
1. set_value：修改已有参数的默认值。
   {"op": "set_value", "param": "<精确参数名>", "value": <数值或布尔>}
2. add_param：新增参数（带默认值）。
   {"op": "add_param", "name": "<新参数名>", "type": "<类型>", "value": <默认值>, "description": "<中文描述，可选>"}
3. del_param：删除参数。
   {"op": "del_param", "param": "<精确参数名>"}
4. rename_param：重命名参数（脚本中的引用会自动整词同步替换）。
   {"op": "rename_param", "from": "<旧名>", "to": "<新名>"}

# 硬性规则
- 参数名必须用下面清单里的【精确名称】；不要用描述文字、不要自编名字，清单里没有的名字会被拒绝。
- Length/Angle 类型参数的值必须以米为单位（GDL 内部单位）：25mm → 0.025，900mm → 0.9，1.2 米 → 1.2。不要写带单位的字符串。
- 布尔值用 true / false（或 1 / 0）。
- Integer 参数的值必须是整数；String 参数的值必须是字符串。
- 保留名 A、B、ZZYZX 及 AC_ 开头：不可删除、不可重命名、不可作为新参数名。
- 模糊的数量描述（如"调高一点""大一些""差不多"）不要猜测具体数值。
- 只有指令能被上述参数操作【完全满足】时才输出操作；只要指令还涉及脚本逻辑、几何形状、材质定义、编译错误修复、VALUES 列表或其他非参数改动，就输出空数组：{"operations": []}
  （例如"加一层板""均匀分布""修复编译错误""定义材质""优化脚本"这类请求，一律空数组。）

# 输出格式
只输出一个 JSON 对象，形如 {"operations": [ ... ]}。不要输出代码块标记，不要任何解释文字。

# 项目参数清单（名称 / 类型 / 描述 / 当前默认值）
{param_list}"""


def build_param_modify_messages(instruction: str, project: HSFProject) -> list[dict]:
    """构造 DSL 意图解析的 LLM 消息（system 给 schema + 参数清单，user 是指令）。

    用 replace 而非 str.format：prompt 里 JSON 示例含大量花括号。
    """
    param_list = "\n".join(
        f"- {p.name} ({p.type_tag}, {p.description or '-'}, 当前值 {p.value})"
        for p in project.parameters
    )
    system = _SYSTEM_PROMPT.replace("{param_list}", param_list)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": instruction},
    ]


# ── 值规整（LLM JSON 值 → paramlist 字符串）────────────────

def _normalize_value(type_tag: str, value: Any) -> Optional[str]:
    """按参数类型把 LLM 给的 JSON 值规整为 paramlist 值字符串；不匹配返回 None。"""
    if type_tag == "Boolean":
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (int, float)):
            if value in (0, 1):
                return "1" if value else "0"
            return None
        if isinstance(value, str):
            v = value.strip().lower()
            if v in {"true", "yes", "on", "1"}:
                return "1"
            if v in {"false", "no", "off", "0"}:
                return "0"
        return None
    if type_tag == "Integer":
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float):
            return str(int(value)) if value == int(value) else None
        if isinstance(value, str):
            m = re.fullmatch(r"\s*(-?\d+)\s*", value)
            return m.group(1) if m else None
        return None
    if type_tag in ("Length", "RealNum", "Angle"):
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return f"{float(value):.6g}"
        if isinstance(value, str):
            m = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*", value)
            return f"{float(m.group(1)):.6g}" if m else None
        return None
    if type_tag == "String":
        return value if isinstance(value, str) else None
    if type_tag in ("PenColor", "Material", "FillPattern", "LineType"):
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float):
            return str(int(value)) if value == int(value) else None
        if isinstance(value, str):
            m = re.fullmatch(r"\s*(-?\d+)\s*", value)
            return m.group(1) if m else None
        return None
    return None  # Title / Separator 等不支持设值


# ── 计划校验（确定性，任一 op 不过 → 整单拒绝）──────────────

def _param_referenced_in_scripts(project: HSFProject, param_name: str) -> bool:
    """参数名是否被任一脚本引用（整词/整串，与 rename 替换语义一致）。"""
    for content in project.scripts.values():
        _new, count = replace_identifier(content, param_name, param_name)
        if count:
            return True
    return False


def _validate_ops(raw_ops: Any, project: HSFProject) -> Optional[list[ParamOp]]:
    """校验并规整 LLM 输出的 operations 列表；任一 op 不过返回 None。

    全部基于【原始状态】校验（确定性）：set_value 只针对已有参数，
    add_param 自带默认值，不依赖 op 之间的顺序副作用。
    """
    if not isinstance(raw_ops, list) or not raw_ops:
        return None
    params = {p.name.lower(): p for p in project.parameters}
    ops: list[ParamOp] = []
    for raw in raw_ops:
        if not isinstance(raw, dict):
            return None
        op_name = raw.get("op")
        if op_name == "set_value":
            pname = raw.get("param")
            if not isinstance(pname, str):
                return None
            param = params.get(pname.strip().lower())
            if param is None:
                return None  # 参数不存在 / 多义（非精确名称）
            new_value = _normalize_value(param.type_tag, raw.get("value"))
            if new_value is None:
                return None  # 类型不符
            ops.append(ParamOp(op="set_value", param=param.name, value=new_value, old_value=param.value))
        elif op_name == "add_param":
            name = raw.get("name")
            typ = raw.get("type")
            if not isinstance(name, str) or not isinstance(typ, str):
                return None
            name = name.strip()
            if not _VALID_NAME_RE.fullmatch(name) or _is_reserved(name):
                return None
            if name.lower() in params:
                return None  # 重名
            if typ not in VALID_PARAM_TYPES or typ in ("Title", "Separator"):
                return None
            value = _normalize_value(typ, raw.get("value"))
            if value is None:
                return None
            desc = raw.get("description")
            if not isinstance(desc, str):
                desc = ""
            ops.append(ParamOp(op="add_param", name=name, type=typ, value=value, description=desc))
        elif op_name == "del_param":
            pname = raw.get("param")
            if not isinstance(pname, str):
                return None
            param = params.get(pname.strip().lower())
            if param is None:
                return None
            if _is_reserved(param.name):
                return None  # 保留名不可删
            if _param_referenced_in_scripts(project, param.name):
                return None  # 脚本引用中，删了留悬挂引用 → 拒绝
            ops.append(ParamOp(op="del_param", param=param.name, old_value=param.value))
        elif op_name == "rename_param":
            frm = raw.get("from")
            to = raw.get("to")
            if not isinstance(frm, str) or not isinstance(to, str):
                return None
            frm = frm.strip()
            to = to.strip()
            param = params.get(frm.lower())
            if param is None:
                return None
            if _is_reserved(param.name):
                return None  # 保留名不可作 rename 源
            if not _VALID_NAME_RE.fullmatch(to) or _is_reserved(to):
                return None
            if to.lower() == param.name.lower():
                return None  # 无意义改名
            if to.lower() in params:
                return None  # 目标重名
            ops.append(ParamOp(op="rename_param", from_name=param.name, name=to))
        else:
            return None  # 未知操作
    return ops


# ── 意图解析入口 ──────────────────────────────────────────

def _mentions_param_level(text: str, project: HSFProject) -> bool:
    """便宜预筛：指令里出现已有参数名/描述或参数操作关键词才值得调 LLM。"""
    for p in project.parameters:
        if re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(p.name)}(?![A-Za-z0-9_])",
            text, re.IGNORECASE,
        ):
            return True
        if p.description and p.description.strip() in text:
            return True
    lower = text.lower()
    return any(kw.lower() in lower for kw in _PARAM_OP_KEYWORDS)


def _extract_json(text: str) -> Optional[Any]:
    """从 LLM 输出里提取 JSON：先整段解析，再退化为截取第一个平衡对象。"""
    content = (text or "").strip()
    if not content:
        return None
    stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.IGNORECASE).strip()
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


def _notify_fallback(on_fallback, reason: str) -> None:
    """把回落 reason 透出给调用方（question/compound/non_param/…）。

    best-effort：回调自身抛异常也不影响回落语义（调用方只用于采集）。
    """
    if on_fallback is None:
        return
    try:
        on_fallback(reason)
    except Exception:
        logger.warning("param_modify fallback callback failed for %r", reason, exc_info=True)


def parse_param_modify(
    instruction: str,
    project: HSFProject,
    llm: Any,
    *,
    on_fallback=None,
) -> Optional[ParamModifyPlan]:
    """一次 LLM 调用把自然语言解析成 ParamModifyPlan；任何失败返回 None 回落。

    llm 需有 generate(messages, **kwargs) -> LLMResponse（content 字段）。
    温度 0 / max_tokens 1024 / 非流式：从简、失败不重试（省 token）。

    on_fallback：可选回调，每次回落返回 None 前以 reason 字符串调用一次
    （question / compound / non_param / bad_json / validation / llm_error）；
    返回 None 的回落语义不变，调用方可用它采集 dsl_fallback 信号。
    """
    text = (instruction or "").strip()
    if not text or not project.parameters:
        _notify_fallback(on_fallback, "non_param")
        return None
    if any(hint in text for hint in _QUESTION_HINTS):
        _notify_fallback(on_fallback, "question")
        return None  # 疑问句不是修改指令
    if any(hint in text for hint in _EXTRA_WORK_HINTS):
        _notify_fallback(on_fallback, "compound")
        return None  # "顺便/再帮"带额外非参数工作，直接回落
    if not _mentions_param_level(text, project):
        _notify_fallback(on_fallback, "non_param")
        return None  # 非参数级请求，不浪费一次 LLM 调用

    try:
        messages = build_param_modify_messages(text, project)
        resp = llm.generate(messages, temperature=0.0, max_tokens=1024, stream=False)
    except Exception as exc:
        logger.warning("param_modify 意图解析 LLM 调用失败，回落: %s", exc)
        _notify_fallback(on_fallback, "llm_error")
        return None

    data = _extract_json(getattr(resp, "content", "") or "")
    if not isinstance(data, dict):
        _notify_fallback(on_fallback, "bad_json")
        return None  # JSON 不合法
    ops = _validate_ops(data.get("operations"), project)
    if ops is None:
        _notify_fallback(on_fallback, "validation")
        return None  # 空操作或任一 op 校验不过
    return ParamModifyPlan(operations=ops, raw=data)


# ── 确定性应用（快照→应用→save_to_disk + 守护）──────────────

def _project_on_disk(project: HSFProject) -> bool:
    root = Path(getattr(project, "root", "") or "")
    try:
        return root.is_dir() and is_hsf_project_dir(root)
    except Exception:
        return False


def _snapshot_disk(root: Path) -> dict[str, bytes]:
    """磁盘全量快照（相对路径 → bytes），排除 .openbrep 版本数据。"""
    snap: dict[str, bytes] = {}
    for path in root.rglob("*"):
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            if rel.startswith(".openbrep/"):
                continue
            snap[rel] = path.read_bytes()
    return snap


def _disk_changes(root: Path, snapshot: dict[str, bytes]) -> list[str]:
    current = _snapshot_disk(root)
    return sorted(
        rel for rel in set(snapshot) | set(current)
        if snapshot.get(rel) != current.get(rel)
    )


def _restore_disk(root: Path, snapshot: dict[str, bytes]) -> None:
    for rel, data in snapshot.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def _plan_changed_files_hint(plan: ParamModifyPlan, project: HSFProject) -> list[str]:
    """revision 元数据用的变更文件提示：paramlist.xml + rename 实际触及的脚本。"""
    files = ["paramlist.xml"]
    for op in plan.operations:
        if op.op == "rename_param":
            for st, content in project.scripts.items():
                _new, count = replace_identifier(content, op.from_name, op.name)
                if count:
                    files.append(f"scripts/{st.value}")
    return sorted(set(files))


def _apply_op(project: HSFProject, op: ParamOp) -> None:
    if op.op == "set_value":
        param = project.get_parameter(op.param)
        if param is not None:
            param.value = op.value
    elif op.op == "add_param":
        project.add_parameter(
            GDLParameter(name=op.name, type_tag=op.type, description=op.description, value=op.value)
        )
    elif op.op == "del_param":
        project.remove_parameter(op.param)
    elif op.op == "rename_param":
        for p in project.parameters:
            if p.name == op.from_name:
                p.name = op.name
                break
        total = 0
        for st, content in project.scripts.items():
            new_content, count = replace_identifier(content, op.from_name, op.name)
            if count:
                project.scripts[st] = new_content
                total += count
        op.occurrences = total


def apply_param_modify(
    project: HSFProject,
    plan: ParamModifyPlan,
    *,
    user_instruction: str = "",
    before_message: str = "auto: before modify",
    trigger: str = "modify",
    intent: str = "MODIFY",
    metadata: dict[str, Any] | None = None,
    create_revision: Callable[..., Any] | None = None,
) -> ApplyOutcome:
    """快照→应用→落盘（与 apply_parameter_value 相同的落盘语义）+ 变更守护。

    守护：应用后磁盘变更只允许 paramlist.xml + scripts/*；其他文件出现变更
    即回滚磁盘与内存并返回 applied=False（调用方回落 LLM 路径）。
    create_revision 由调用方注入（pipeline 传模块级 create_revision）。
    """
    warnings: list[str] = []
    if create_revision is None:
        from openbrep.revisions import create_revision as _real_create_revision

        create_revision = _real_create_revision

    on_disk = _project_on_disk(project)
    revision_id: Optional[str] = None
    if not on_disk:
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
                changed_files=_plan_changed_files_hint(plan, project),
                parent_revision_id=get_latest_revision_id(project.root),
            )
            revision_id = revision.revision_id
        except Exception as exc:
            warnings.append(f"自动版本快照失败：{exc}")

    # 守护快照：create_revision 之后、应用之前（排除 .openbrep）
    snapshot = _snapshot_disk(project.root) if on_disk else None
    params_before = deepcopy(project.parameters)
    scripts_before = deepcopy(project.scripts)

    for op in plan.operations:
        _apply_op(project, op)
    project.save_to_disk()

    if snapshot is None:
        return ApplyOutcome(applied=True, revision_id=revision_id, warnings=warnings, changed_files=[])

    changed = _disk_changes(project.root, snapshot)
    out_of_scope = [
        rel for rel in changed
        if rel != "paramlist.xml" and not rel.startswith("scripts/")
    ]
    if out_of_scope:
        _restore_disk(project.root, snapshot)
        project.parameters = params_before
        project.scripts = scripts_before
        warnings.append("守护回滚：检测到计划外文件变更 " + ", ".join(sorted(out_of_scope)))
        return ApplyOutcome(applied=False, revision_id=revision_id, warnings=warnings)

    return ApplyOutcome(applied=True, revision_id=revision_id, warnings=warnings, changed_files=changed)


# ── 报告辅助 ──────────────────────────────────────────────

def format_op_summary(op: ParamOp) -> str:
    """单条 op 的人类可读摘要（返回文案用）。"""
    if op.op == "set_value":
        return f"set_value：{op.param}：{op.old_value} → {op.value}"
    if op.op == "add_param":
        return f"add_param：新增 {op.name}（{op.type}，默认 {op.value}）"
    if op.op == "del_param":
        return f"del_param：删除 {op.param}（原值 {op.old_value}）"
    if op.op == "rename_param":
        return f"rename_param：{op.from_name} → {op.name}（脚本引用同步 {op.occurrences} 处）"
    return op.op
