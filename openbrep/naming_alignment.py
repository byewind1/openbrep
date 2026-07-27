"""命名规范对齐层（naming alignment）。

按可插拔的命名规范对齐 HSFProject 的参数名：重命名 paramlist 的 name
字段 + 全脚本符号级替换。

定位：通用能力，不是 benchmark 专用层。规范来源可插拔：
  - benchmark 模式：task yaml 的 required_params（当前唯一消费者）
  - 生产模式（未接）：.openbrep/naming_convention.toml
  - Pro 模式（未接）：事务所定制规范包

保留名规则（与维护者确认的细化版）：
  1. A / B / ZZYZX / AC_* 永远不作为重命名的源（ArchiCAD 面板/热点/尺寸编辑依赖）
  2. 非保留名 → 保留名允许：这是在补全对象的 ArchiCAD 标准语义
  3. 期望名对应保留名 → 记 reserved_conflict 跳过（Master 别名方案留待后续）
  4. 目标名已在 paramlist 存在 → 跳过并记录（防冲突）
  5. 规则 2 执行前必须确认目标保留名不存在

符号替换：不用字符串 replace（会误伤注释/字符串/子串），也不用
gdl_ast——它是几何结构解析器（TransformFrame/GeometryCall/ControlBlock），
args 是空白切分的原文 token，不区分代码/注释/字符串区域，做不了符号级
替换。本模块用带区域状态的小型词法扫描器，只替换代码区的完整标识符
（大小写不敏感；GDL 标识符本身大小写不敏感）。

Never raises：任何异常降级为部分完成的 AlignResult。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ── 结果数据结构 ──────────────────────────────────────────


@dataclass
class RenameRecord:
    from_name: str
    to_name: str
    occurrences: int          # 脚本里替换了几处（不含 paramlist 本身）


@dataclass
class ReservedConflict:
    """期望名对应保留名，或保留名被误用于错误角色。"""

    expected_name: str        # 规范要求的名字，如 pipe_od / ZZYZX
    reserved_name: str        # 实际占用的保留名，如 A / B
    role_in_script: str       # 该保留名在脚本里的实际角色证据
    severity: str             # "blocked"（语义相容但受规则阻挡）| "semantic_bug"（角色错配）


@dataclass
class AlignResult:
    renamed: list[RenameRecord] = field(default_factory=list)
    reserved_conflicts: list[ReservedConflict] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    missing_concepts: list[str] = field(default_factory=list)

    @property
    def has_semantic_bug(self) -> bool:
        """B 被当高度用这类角色错配。"""
        return any(c.severity == "semantic_bug" for c in self.reserved_conflicts)


# ── 保留名 ────────────────────────────────────────────────

_RESERVED_EXACT = frozenset({"a", "b", "zzyzx"})


def _is_reserved(name: str) -> bool:
    n = name.strip().lower()
    return n in _RESERVED_EXACT or n.startswith("ac_")


# ── 同义词词典（第一版，从归因分析的实际数据提取）──────────
# 归一方向：缩写 → 全称。"d" 在实测数据中全部是 diameter（leg_d/rail_d/
# post_d/chord_d/hole_d），depth 只用 dep 归一，避免歧义。
_REV_ABBREV = {
    "w": "width",
    "thk": "thickness", "t": "thickness",
    "d": "diameter", "diam": "diameter", "dia": "diameter", "od": "diameter",
    "h": "height",
    "dep": "depth",
    "n": "count", "num": "count", "cnt": "count",
    "len": "length", "l": "length",
    "sp": "spacing", "gap": "spacing",
}


def _canon_token(token: str) -> str:
    """归一单个 token：缩写展开 + 单复数（shelves→shelf, doors→door）。"""
    t = _REV_ABBREV.get(token, token)
    if t.endswith("ves"):
        t = t[:-3] + "f"
    elif len(t) > 2 and t.endswith("s") and not t.endswith("ss"):
        t = t[:-1]
    return t


def _canon_tokens(name: str) -> frozenset[str]:
    """参数名 → 归一 token 多重集。n_shelves 与 shelf_count 归一后相同。"""
    return frozenset(
        _canon_token(tok) for tok in name.strip().lower().split("_") if tok
    )


# ── 符号级替换（词法扫描器，非字符串 replace）──────────────

_IDENT_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
)


def replace_identifier(code: str, from_name: str, to_name: str) -> tuple[str, int]:
    """只替换代码区的完整标识符（大小写不敏感），返回 (新文本, 替换次数)。

    保护区域：
    - 注释：`!` 到行尾
    - 字符串：仅当整体内容等于旧名时替换（VALUES "name" 等按名引用），
      其余字符串原样保留；GDL 字符串内 `""` 为转义双引号
    - 更长标识符的子串（shelf_count_max 不受影响）
    """
    out: list[str] = []
    i, n = 0, len(code)
    count = 0
    in_comment = False
    target = from_name.lower()

    while i < n:
        ch = code[i]
        if in_comment:
            out.append(ch)
            if ch == "\n":
                in_comment = False
            i += 1
            continue
        if ch == "!":
            in_comment = True
            out.append(ch)
            i += 1
            continue
        if ch == '"':
            # 字符串区域：整体内容等于旧名才替换——VALUES "name" / LOCK "name"
            # 等命令按字符串引用参数，这是 GDL 的合法参数引用方式；
            # 含其他文字的字符串（注释性文本）不动。GDL 字符串内 "" 为转义。
            j = i + 1
            content: list[str] = []
            while j < n:
                if code[j] == '"':
                    if j + 1 < n and code[j + 1] == '"':
                        content.append('""')
                        j += 2
                        continue
                    break
                content.append(code[j])
                j += 1
            if j >= n:
                out.append(code[i:])  # 未闭合字符串：原样保留
                i = n
                continue
            inner = "".join(content)
            if inner.lower() == target:
                out.append(f'"{to_name}"')
                count += 1
            else:
                out.append(code[i:j + 1])
            i = j + 1
            continue
        if ch in _IDENT_CHARS:
            j = i + 1
            while j < n and code[j] in _IDENT_CHARS:
                j += 1
            token = code[i:j]
            if token.lower() == target:
                out.append(to_name)
                count += 1
            else:
                out.append(token)
            i = j
            continue
        out.append(ch)
        i += 1

    return "".join(out), count


# ── 脚本角色扫描（ZZYZX/A/B 类保留名的角色证据）────────────

_ROLE_INDEX = {"a": 0, "b": 1, "zzyzx": 2}
_ROLE_ADD = {"a": "ADDX", "b": "ADDY", "zzyzx": "ADDZ"}
_ROLE_LABEL = {"a": "宽度（BLOCK 第 1 维 / ADDX）", "b": "深度（BLOCK 第 2 维 / ADDY）",
               "zzyzx": "高度（BLOCK 第 3 维 / ADDZ）"}


def _bare_ident(text: str) -> Optional[str]:
    text = text.strip().strip("()").strip()
    if text and all(c in _IDENT_CHARS for c in text):
        return text
    return None


def _scan_role_usage(project, role: str) -> tuple[set[str], dict[str, str]]:
    """扫描全部脚本，找出承担指定维度角色的参数。

    返回 (非保留名候选集合, {保留名: 证据描述})。
    证据来源：BLOCK 的前三个位置参数、ADDX/ADDY/ADDZ 的裸标识符参数。
    """
    candidates: set[str] = set()
    reserved_seen: dict[str, str] = {}
    idx = _ROLE_INDEX[role]
    add_cmd = _ROLE_ADD[role]
    param_names = {p.name for p in project.parameters}

    for _stype, code in project.scripts.items():
        for raw in (code or "").splitlines():
            line = raw.strip()
            upper = line.upper()
            if upper.startswith("BLOCK"):
                args = [a.strip() for a in line[5:].split("!", 1)[0].split(",")]
                if len(args) > idx:
                    ident = _bare_ident(args[idx])
                    if ident and ident in param_names:
                        if _is_reserved(ident):
                            reserved_seen[ident] = f"BLOCK 第 {idx + 1} 维参数位"
                        else:
                            candidates.add(ident)
            elif upper.startswith(add_cmd):
                ident = _bare_ident(line[len(add_cmd):].split("!", 1)[0])
                if ident and ident in param_names:
                    if _is_reserved(ident):
                        reserved_seen[ident] = f"{add_cmd} 参数位"
                    else:
                        candidates.add(ident)
    return candidates, reserved_seen


def _is_height_name(name: str) -> bool:
    return "height" in _canon_tokens(name)


# 规则 2 的候选闸门：防止把有语义的名字推断成高度
# （两起实测事故：blade_thickness=片厚、tf=t_flange 被改名为 ZZYZX，
# 反而制造 bbox_mismatch）。只接受：
#   a) 高度语义名（base_height、h_frame）；
#   b) 单字母裸名（C、H）——多字母短码可能是语义缩写（tf），不放行。
_DIM_SEMANTIC_TOKENS = frozenset({
    "width", "thickness", "depth", "diameter", "length", "count", "spacing",
})


def _renameable_to_reserved(source: str) -> bool:
    if _is_height_name(source):
        return True
    if _canon_tokens(source) & _DIM_SEMANTIC_TOKENS:
        return False
    s = source.strip()
    return len(s) == 1 and s.isalpha()


# ── 主入口 ────────────────────────────────────────────────


def align_parameter_names(
    project,
    convention: dict[str, Optional[str]],
    *,
    dry_run: bool = False,
) -> AlignResult:
    """按命名规范对齐 project 的参数名。

    convention: {期望名: 同义词提示 or None}（提示目前保留，第一版未用）。
    dry_run=True 时只计算计划，不修改 project。
    Never raises.
    """
    result = AlignResult()
    try:
        param_names = {p.name for p in project.parameters}
        plan: list[tuple[str, str]] = []  # (from_name, to_name)

        for expected in convention:
            if not expected or expected in param_names:
                continue  # 精确匹配，无需动作
            # 大小写变体：GDL 标识符大小写不敏感，但下游判据按大小写敏感比对
            # （benchmark required_params 即如此），统一归一到规范名的大小写。
            # 保留名变体（如 A/a）不动。
            variant = next(
                (n for n in param_names if n.lower() == expected.lower()), None
            )
            if variant is not None:
                if not _is_reserved(variant):
                    plan.append((variant, expected))
                continue

            if _is_reserved(expected):
                _plan_for_reserved(project, expected, param_names, plan, result)
            else:
                _plan_for_plain(project, expected, param_names, plan, result)

        # 执行
        for from_name, to_name in plan:
            occurrences = 0
            if not dry_run:
                for param in project.parameters:
                    if param.name == from_name:
                        param.name = to_name
                for stype in list(project.scripts.keys()):
                    new_code, n = replace_identifier(project.scripts[stype], from_name, to_name)
                    if n:
                        project.scripts[stype] = new_code
                        occurrences += n
            else:
                for stype in list(project.scripts.keys()):
                    _code, n = replace_identifier(project.scripts[stype], from_name, to_name)
                    occurrences += n
            result.renamed.append(RenameRecord(from_name, to_name, occurrences))

    except Exception as exc:  # Never raises
        logger.warning("align_parameter_names degraded: %s", exc)
    return result


def _plan_for_reserved(project, expected, param_names, plan, result) -> None:
    """期望名是保留名（如 ZZYZX）：只允许 非保留名 → 保留名（规则 2/5）。"""
    role = expected.strip().lower()
    candidates, reserved_seen = _scan_role_usage(project, role)
    viable = {c for c in candidates if _renameable_to_reserved(c)}
    rejected = candidates - viable
    if len(viable) == 1:
        source = next(iter(viable))
        plan.append((source, expected))  # C → ZZYZX：补全 ArchiCAD 标准语义
        return
    if len(viable) > 1:
        result.skipped.append(f"{expected}: 角色候选不唯一 {sorted(viable)}")
        return
    for c in sorted(rejected):
        result.skipped.append(f"{expected}: 角色候选 {c} 语义非高度，未重命名")
    # 无非保留名候选：是不是有保留名被误用到这个角色（C16/C19 的 B 当高度）
    for reserved, evidence in reserved_seen.items():
        if reserved.lower() == role:
            continue
        result.reserved_conflicts.append(ReservedConflict(
            expected_name=expected,
            reserved_name=reserved,
            role_in_script=f"{evidence}（应为 {_ROLE_LABEL[role]}）",
            severity="semantic_bug",  # 保留名角色错配：深度槽位被当高度用
        ))
        return
    result.missing_concepts.append(expected)


def _plan_for_plain(project, expected, param_names, plan, result) -> None:
    """期望名是普通名：同义词归一匹配；唯一候选才自动重命名。"""
    target = _canon_tokens(expected)
    matches = [
        name for name in param_names
        if not _is_reserved(name) and _canon_tokens(name) == target
    ]
    if len(matches) == 1:
        plan.append((matches[0], expected))
        return
    if len(matches) > 1:
        result.skipped.append(f"{expected}: 同义候选不唯一 {sorted(matches)}")
        return
    # 名称匹配失败：只有尺寸类名字才可能被保留名占用
    # （如 pipe_od 实际是 A 在做宽度）；其余按概念缺失记录
    if target & {"width", "diameter", "depth"}:
        _cands, reserved_seen = _scan_role_usage(project, "a")
        for reserved, evidence in reserved_seen.items():
            severity = "semantic_bug" if _is_height_name(expected) else "blocked"
            result.reserved_conflicts.append(ReservedConflict(
                expected_name=expected,
                reserved_name=reserved,
                role_in_script=evidence,
                severity=severity,
            ))
            return
    result.missing_concepts.append(expected)
