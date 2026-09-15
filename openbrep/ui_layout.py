"""L0b：把 ui.gdl 解析成 Archicad 风格参数面板控件树。

只做声明式布局解析，不执行 GDL 语义。IF 分支按当前参数值裁剪；
复杂多行 UI_INFIELD（图片画廊等）降级为 unsupported，由前端回落到
ParameterRail 全参数表。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

# Archicad UI 命令 → 控件类型
_COMMAND_KIND = {
    "UI_INFIELD": "infield",
    "UI_OUTFIELD": "outfield",
    "UI_GROUPBOX": "groupbox",
    "UI_SEPARATOR": "separator",
    "UI_STYLE": "style",
    "UI_PAGE": "page",
    "UI_DIALOG": "dialog",
    "UI_BUTTON": "button",
    "UI_INFIELD{2}": "infield",
    "UI_OUTFIELD{2}": "outfield",
}

_NAME_TOKEN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)$")
# 注意：UI_INFIELD{2} 的 `}` 后不是词边界，不能在命令后写 \b
_UI_CMD = re.compile(
    r"^(UI_INFIELD(?:\{[0-9]\})?|UI_OUTFIELD(?:\{[0-9]\})?|UI_GROUPBOX|"
    r"UI_SEPARATOR|UI_STYLE|UI_PAGE|UI_DIALOG|UI_BUTTON)(?:\s+|$)(.*)$",
    re.IGNORECASE,
)


@dataclass
class UIControl:
    type: str
    param: str | None = None
    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0
    text: str | None = None
    options: list[Any] | None = None
    line: int = 0
    raw: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class UILayoutResult:
    ok: bool = True
    title: str | None = None
    pages: list[int] = field(default_factory=list)
    active_page: int | None = None
    width: float = 480.0
    height: float = 360.0
    controls: list[UIControl] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "title": self.title,
            "pages": list(self.pages),
            "active_page": self.active_page,
            "width": self.width,
            "height": self.height,
            "controls": [c.to_dict() for c in self.controls],
            "unsupported": list(self.unsupported),
            "warnings": list(self.warnings),
            "has_infield": any(c.type == "infield" for c in self.controls),
        }


def parse_ui_layout(
    script: str,
    parameters: dict[str, Any] | None = None,
    values_declarations: dict[str, Any] | None = None,
) -> UILayoutResult:
    """解析 ui.gdl → 控件树。parameters 为当前参数名→值（大小写不敏感查找）。"""
    params = _normalize_params(parameters or {})
    values = values_declarations or {}
    result = UILayoutResult()
    vars_map: dict[str, float | str] = {}
    if_depth = 0
    # 每层 IF：当前分支是否已命中（用于 ELSE）
    branch_taken: list[bool] = []
    active = True
    page_stack: list[int] = []
    max_x = 0.0
    max_y = 0.0

    lines = _logical_lines(script)
    for line_no, logical in lines:
        line = _strip_comment(logical).strip()
        if not line:
            continue

        upper = line.upper()
        if upper.startswith("IF ") and " THEN" in upper:
            cond_text = _extract_if_condition(line)
            if cond_text is None:
                branch_taken.append(False)
                if_depth += 1
                continue
            cond = _eval_condition(cond_text, params, vars_map)
            if cond is None:
                # 条件无法离线求值：保守跳过该分支
                branch_taken.append(False)
                active = False
                if_depth += 1
                continue
            branch_taken.append(bool(cond))
            active = all(branch_taken)
            if_depth += 1
            continue
        if upper.startswith("ELSEIF ") or upper.startswith("ELSE IF "):
            if not branch_taken:
                continue
            cond_text = _extract_if_condition(line)
            if branch_taken[-1] or cond_text is None:
                active = False
                continue
            cond = _eval_condition(cond_text, params, vars_map)
            if cond:
                branch_taken[-1] = True
                active = all(branch_taken)
            else:
                active = False
            continue
        if upper == "ELSE" or upper.startswith("ELSE "):
            if not branch_taken:
                continue
            if branch_taken[-1]:
                active = False
            else:
                branch_taken[-1] = True
                active = all(branch_taken)
            continue
        if upper == "ENDIF":
            if branch_taken:
                branch_taken.pop()
            if_depth = max(0, if_depth - 1)
            active = all(branch_taken) if branch_taken else True
            continue

        if not active:
            continue

        assign = _ASSIGN.match(line)
        if assign and not line.upper().startswith("UI_"):
            name, expr = assign.group(1), assign.group(2).strip()
            val = _eval_value(expr, params, vars_map)
            if val is not None:
                vars_map[name.lower()] = val
                vars_map[name.upper()] = val
            continue

        m = _UI_CMD.match(line)
        if not m:
            continue
        cmd = m.group(1).upper().replace("{2}", "")
        # UI_INFIELD{2} 与 UI_INFIELD 同族；上面已剥掉后缀
        rest = m.group(2) or ""
        # 复杂多行（画廊/图片）：rest 含引号文件名且后续还有数字网格
        if _looks_complex_infield(rest):
            result.unsupported.append(f"line {line_no}: {cmd} 复杂多行控件（图片画廊等）暂不渲染")
            continue

        try:
            args = _split_args(rest)
        except ValueError as exc:
            result.warnings.append(f"line {line_no}: 参数解析失败 ({exc})")
            continue
        if not args:
            continue

        kind = _COMMAND_KIND.get(cmd)
        if kind is None:
            continue

        if kind == "dialog":
            if args:
                text = _as_text(args[0], vars_map)
                if text:
                    result.title = text
            if len(args) >= 3:
                result.width = _as_number(args[1], vars_map) or result.width
                result.height = _as_number(args[2], vars_map) or result.height
            continue
        if kind == "page":
            page = int(_as_number(args[0], vars_map) or 1)
            if page not in result.pages:
                result.pages.append(page)
            if result.active_page is None:
                result.active_page = page
            page_stack.append(page)
            continue
        if kind == "style":
            continue

        if kind == "separator":
            # UI_SEPARATOR x1,y1,x2,y2 或 UI_SEPARATOR y
            nums = [_as_number(a, vars_map) for a in args[:4]]
            if len(nums) >= 4 and None not in nums[:4]:
                x1, y1, x2, y2 = nums[:4]
                ctl = UIControl(
                    type="separator",
                    x=min(x1, x2), y=min(y1, y2),
                    w=abs(x2 - x1) or 1.0, h=abs(y2 - y1) or 1.0,
                    line=line_no, raw=line,
                )
            else:
                y = nums[0] or 0.0
                ctl = UIControl(type="separator", x=0, y=y, w=result.width, h=1, line=line_no, raw=line)
            result.controls.append(ctl)
            max_x = max(max_x, ctl.x + ctl.w)
            max_y = max(max_y, ctl.y + ctl.h)
            continue

        if kind == "groupbox":
            text = _as_text(args[0], vars_map) if args else None
            x, y, w, h = _rect(args[1:5], vars_map)
            ctl = UIControl(type="groupbox", text=text, x=x, y=y, w=w, h=h, line=line_no, raw=line)
            result.controls.append(ctl)
            max_x = max(max_x, x + w)
            max_y = max(max_y, y + h)
            continue

        if kind == "outfield":
            text = _as_text(args[0], vars_map) if args else None
            x, y, w, h = _rect(args[1:5], vars_map)
            ctl = UIControl(type="outfield", text=text, x=x, y=y, w=w, h=h, line=line_no, raw=line)
            result.controls.append(ctl)
            max_x = max(max_x, x + w)
            max_y = max(max_y, y + h)
            continue

        if kind == "infield":
            raw_param = args[0] if args else ""
            param = _as_param_name(raw_param)
            if not param:
                result.unsupported.append(f"line {line_no}: UI_INFIELD 缺少参数名")
                continue
            x, y, w, h = _rect(args[1:5], vars_map)
            options = None
            decl = values.get(param) or values.get(param.upper()) or values.get(param.lower())
            if isinstance(decl, dict) and decl.get("options"):
                options = list(decl["options"])
            # 行内 VALUES 形态：…, 1, `类型1`, 2, `类型2`
            inline = _inline_options(args[5:])
            if inline:
                options = inline
            ctl = UIControl(
                type="infield", param=param, x=x, y=y, w=w or 120, h=h or 22,
                options=options, line=line_no, raw=line,
            )
            result.controls.append(ctl)
            max_x = max(max_x, ctl.x + ctl.w)
            max_y = max(max_y, ctl.y + ctl.h)
            continue

        if kind == "button":
            text = _as_text(args[0], vars_map) if args else None
            x, y, w, h = _rect(args[1:5], vars_map)
            result.controls.append(
                UIControl(type="button", text=text, x=x, y=y, w=w, h=h, line=line_no, raw=line)
            )
            continue

    if max_x > result.width:
        result.width = max_x + 16
    if max_y > result.height:
        result.height = max_y + 24
    return result


def _normalize_params(parameters: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in parameters.items():
        out[str(key)] = value
        out[str(key).upper()] = value
        out[str(key).lower()] = value
    return out


def _logical_lines(script: str) -> list[tuple[int, str]]:
    """合并以逗号结尾的续行，返回 (起始行号, 文本)。"""
    text = (script or "").lstrip("﻿")
    raw_lines = text.splitlines()
    out: list[tuple[int, str]] = []
    buf = ""
    start = 1
    for i, raw in enumerate(raw_lines, 1):
        stripped = raw.strip()
        if not buf:
            start = i
            buf = stripped
        else:
            buf = f"{buf} {stripped}"
        if buf.endswith(","):
            continue
        out.append((start, buf))
        buf = ""
    if buf:
        out.append((start, buf))
    return out


def _strip_comment(line: str) -> str:
    # GDL 注释：行内 ! 之后（尊重引号）
    in_q = False
    qchar = ""
    for i, ch in enumerate(line):
        if ch in ('"', "'", "`"):
            if not in_q:
                in_q, qchar = True, ch
            elif ch == qchar:
                in_q = False
        elif ch == "!" and not in_q:
            return line[:i]
    return line


def _split_args(text: str) -> list[str]:
    args: list[str] = []
    buf: list[str] = []
    in_q = False
    qchar = ""
    for ch in text:
        if ch in ('"', "'", "`"):
            if not in_q:
                in_q, qchar = True, ch
                buf.append(ch)
            elif ch == qchar:
                in_q = False
                buf.append(ch)
            else:
                buf.append(ch)
            continue
        if ch == "," and not in_q:
            args.append("".join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        args.append(tail)
    return args


def _as_number(token: str, vars_map: dict[str, Any]) -> float | None:
    token = (token or "").strip()
    if not token:
        return None
    try:
        return float(token)
    except ValueError:
        pass
    key = token.lower()
    if key in vars_map:
        try:
            return float(vars_map[key])
        except (TypeError, ValueError):
            return None
    # 简单加减：pos_x+10
    m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*([+\-])\s*([0-9.]+)$", token)
    if m:
        base = _as_number(m.group(1), vars_map)
        if base is None:
            return None
        return base + float(m.group(3)) if m.group(2) == "+" else base - float(m.group(3))
    return None


def _as_text(token: str, vars_map: dict[str, Any]) -> str | None:
    token = (token or "").strip()
    if not token:
        return None
    if token[0] in ('"', "'", "`") and token[-1] == token[0] and len(token) >= 2:
        return token[1:-1]
    if token in vars_map:
        return str(vars_map[token])
    key = token.lower()
    if key in vars_map:
        return str(vars_map[key])
    if _NAME_TOKEN.match(token):
        return token
    return token


def _as_param_name(token: str) -> str | None:
    token = (token or "").strip()
    if not token:
        return None
    if token[0] in ('"', "'", "`") and token[-1] == token[0] and len(token) >= 2:
        token = token[1:-1]
    token = token.strip()
    if _NAME_TOKEN.match(token):
        return token
    return None


def _rect(tokens: list[str], vars_map: dict[str, Any]) -> tuple[float, float, float, float]:
    nums = [_as_number(t, vars_map) for t in tokens[:4]]
    while len(nums) < 4:
        nums.append(None)
    x = nums[0] or 0.0
    y = nums[1] or 0.0
    w = nums[2] or 0.0
    h = nums[3] or 0.0
    return x, y, w, h


def _inline_options(tokens: list[str]) -> list[Any] | None:
    """解析 UI_INFIELD 行内 VALUES：value, label, value, label…"""
    if len(tokens) < 2:
        return None
    options: list[Any] = []
    i = 0
    while i + 1 < len(tokens):
        val_tok = tokens[i].strip()
        label_tok = tokens[i + 1].strip()
        # 遇到图片文件名（含引号且非纯数字/关键字）则停止
        if val_tok.startswith('"') and "." in val_tok:
            break
        try:
            value: Any = int(val_tok) if val_tok.isdigit() else float(val_tok)
        except ValueError:
            value = _as_text(val_tok, {})
        label = _as_text(label_tok, {}) or str(value)
        options.append({"value": value, "label": label})
        i += 2
    return options or None


def _looks_complex_infield(rest: str) -> bool:
    """图片画廊等多行 infield：几何参数后跟图片资源名（ASCII 标识符）。"""
    if re.search(r"['\"][^'\"]+\.(?:png|bmp|jpg|jpeg|gif)['\"]", rest, re.IGNORECASE):
        return True
    try:
        args = _split_args(rest)
    except ValueError:
        return False
    if len(args) < 7:
        return False
    for token in args[5:]:
        token = token.strip()
        if len(token) >= 2 and token[0] in ('"', "'", "`") and token[-1] == token[0]:
            inner = token[1:-1]
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", inner):
                return True
    return False


def _extract_if_condition(line: str) -> str | None:
    m = re.match(r"^IF\s+(.+?)\s+THEN\b", line, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return None


def _eval_condition(
    condition: str,
    params: dict[str, Any],
    vars_map: dict[str, Any],
) -> bool | None:
    """求值简单 IF 条件：比较与 AND/OR/NOT 的一层组合。"""
    text = condition.strip()
    if not text:
        return None
    # 拆 AND / OR（不处理括号嵌套）
    for op in (" AND ", " OR "):
        # 仅顶层拆分（粗略：忽略括号内）
        parts = _split_top_level(text, op.strip())
        if len(parts) > 1:
            results = [_eval_condition(p, params, vars_map) for p in parts]
            if any(r is None for r in results):
                return None
            if op.strip().upper() == "AND":
                return all(bool(r) for r in results)
            return any(bool(r) for r in results)
    if text.upper().startswith("NOT "):
        inner = _eval_condition(text[4:].strip(), params, vars_map)
        return None if inner is None else (not inner)

    for op in ("<>", "#", ">=", "<=", "=", ">", "<"):
        parts = _split_compare(text, op)
        if parts is None:
            continue
        left, right = parts
        lv = _eval_value(left, params, vars_map)
        rv = _eval_value(right, params, vars_map)
        if lv is None or rv is None:
            return None
        try:
            if op in ("=", "=="):
                return lv == rv
            if op in ("<>", "#"):
                return lv != rv
            lf, rf = float(lv), float(rv)  # type: ignore[arg-type]
            if op == ">":
                return lf > rf
            if op == "<":
                return lf < rf
            if op == ">=":
                return lf >= rf
            if op == "<=":
                return lf <= rf
        except (TypeError, ValueError):
            return None
    # 裸布尔/数值
    val = _eval_value(text, params, vars_map)
    if val is None:
        return None
    return bool(val)


def _split_top_level(text: str, op: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    upper = text.upper()
    op_u = op.upper()
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if depth == 0 and upper.startswith(op_u, i) and (i == 0 or not text[i - 1].isalnum()):
            j = i + len(op_u)
            if j >= len(text) or not text[j].isalnum():
                parts.append("".join(buf).strip())
                buf = []
                i = j
                continue
        buf.append(ch)
        i += 1
    parts.append("".join(buf).strip())
    return [p for p in parts if p]


def _split_compare(text: str, op: str) -> tuple[str, str] | None:
    depth = 0
    in_q = False
    qchar = ""
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in ('"', "'", "`"):
            if not in_q:
                in_q, qchar = True, ch
            elif ch == qchar:
                in_q = False
        elif not in_q:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif depth == 0 and text.startswith(op, i):
                left = text[:i].strip()
                right = text[i + len(op):].strip()
                if left and right:
                    return left, right
                return None
        i += 1
    return None


def _eval_value(
    expr: str,
    params: dict[str, Any],
    vars_map: dict[str, Any],
) -> float | str | None:
    expr = (expr or "").strip()
    if not expr:
        return None
    if expr[0] in ('"', "'", "`") and expr[-1] == expr[0] and len(expr) >= 2:
        return expr[1:-1]
    try:
        return float(expr)
    except ValueError:
        pass
    key = expr.lower()
    if key in vars_map:
        return vars_map[key]
    # 参数查找（大小写不敏感）
    for name in (expr, expr.upper(), expr.lower()):
        if name in params:
            return params[name]
    if _NAME_TOKEN.match(expr):
        # 未知变量：视为 0/空，条件无法区分时返回 None 更安全
        return None
    return None
