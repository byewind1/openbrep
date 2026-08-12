from __future__ import annotations

import re
from typing import Any

from openbrep.hsf_project import GDLParameter, HSFProject, VALID_PARAM_TYPES
from openbrep.paramlist_builder import validate_paramlist


GDL_PARAMETER_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
AUTHORABLE_PARAM_TYPES = {"Length", "RealNum", "Integer", "Boolean", "String"}


class WorkbenchProjectParameterService:
    def __init__(self, session: Any) -> None:
        self.session = session

    def _values_for(self, name: str) -> dict[str, Any] | None:
        """当前项目 vl.gdl 中该参数的 VALUES 声明（无项目/无声明 → None）。"""
        if self.session.project is None:
            return None
        from openbrep.hsf_project import ScriptType

        return parse_values_declarations(
            self.session.project.get_script(ScriptType.PARAM)
        ).get(name)

    def apply(self, changes: dict[str, Any]) -> dict[str, Any]:
        if self.session.project is None:
            return {"ok": False, "error": "Create or open a project before applying parameters."}
        changed = apply_parameter_values(self.session.project, changes)
        if changed and self.session.source_path is not None:
            self.session.project.save_to_disk()
        return {"ok": True, "changed": changed, **self.session.snapshot()}

    def add_project_parameter(self, body: dict[str, Any]) -> dict[str, Any]:
        if self.session.project is None:
            return {"ok": False, "error": "Create or open a project before adding parameters."}
        try:
            param = build_parameter_from_authoring_request(self.session.project, body)
            self.session.project.add_parameter(param)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        if self.session.source_path is not None:
            self.session.project.save_to_disk()
        return {
            "ok": True,
            "added": parameter_to_dict(param, values=self._values_for(param.name)),
            **self.session.snapshot(),
        }

    def update_project_parameter(self, body: dict[str, Any]) -> dict[str, Any]:
        if self.session.project is None:
            return {"ok": False, "error": "Create or open a project before updating parameters."}
        name = str(body.get("name") or "").strip()
        param = self.session.project.get_parameter(name)
        if param is None:
            return {"ok": False, "error": f"Parameter '{name}' not found"}

        try:
            new_name = validate_authorable_parameter_name(
                self.session.project,
                str(body.get("new_name") if "new_name" in body else param.name),
                current_name=param.name,
            )
            new_type = validate_authorable_type(
                str(body.get("type_tag") if "type_tag" in body else param.type_tag)
            )
            if param.is_fixed and (new_name != param.name or new_type != param.type_tag):
                return {"ok": False, "error": f"Fixed parameter '{param.name}' cannot be renamed or retagged"}
            if "value" in body:
                param.value = coerce_parameter_value(new_type, body.get("value"))
            if "description" in body:
                param.description = str(body.get("description") or "").strip()
            param.name = new_name
            param.type_tag = new_type
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        if self.session.source_path is not None:
            self.session.project.save_to_disk()
        return {
            "ok": True,
            "updated": parameter_to_dict(param, values=self._values_for(param.name)),
            **self.session.snapshot(),
        }

    def delete_project_parameter(self, body: dict[str, Any]) -> dict[str, Any]:
        if self.session.project is None:
            return {"ok": False, "error": "Create or open a project before deleting parameters."}
        name = str(body.get("name") or "").strip()
        param = self.session.project.get_parameter(name)
        if param is None:
            return {"ok": False, "error": f"Parameter '{name}' not found"}
        if param.is_fixed:
            return {"ok": False, "error": f"Fixed parameter '{name}' cannot be deleted"}
        self.session.project.remove_parameter(name)
        if self.session.source_path is not None:
            self.session.project.save_to_disk()
        return {
            "ok": True,
            "deleted": name,
            **self.session.snapshot(),
        }

    def validate_project_parameters(self) -> dict[str, Any]:
        if self.session.project is None:
            return {"ok": True, "issues": []}
        return {
            "ok": True,
            "issues": validate_paramlist(self.session.project.parameters or []),
        }


# ── P11: vl.gdl VALUES 枚举解析 ────────────────────────────────
# 轻量行解析：只关心 `VALUES "name" ...` 声明，其余行（注释/IF/LOCK 等）
# 一律跳过。解析失败只影响本参数（不进入结果 → payload 字段为 None），
# 绝不抛出异常，不影响既有参数链路。

VALUES_DECL_RE = re.compile(r'^\s*VALUES\s+"([^"]+)"\s*(.*)$', re.IGNORECASE)
VALUES_RANGE_RE = re.compile(r'^RANGE\s*\[(.*)\]$', re.IGNORECASE | re.DOTALL)
_VALUES_INT_RE = re.compile(r'^[+-]?\d+$')
_VALUES_NUM_RE = re.compile(r'^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$')


def _strip_gdl_comment(text: str) -> str:
    """去掉行尾 GDL 注释（`!` 起，引号内的 `!` 不算注释起点）。"""
    in_quote = False
    for i, ch in enumerate(text):
        if ch == '"':
            in_quote = not in_quote
        elif ch == "!" and not in_quote:
            return text[:i]
    return text


def _split_values_tokens(text: str) -> list[str]:
    """按逗号切分 VALUES 列表；引号内的逗号是字符串内容，不算分隔符。"""
    tokens: list[str] = []
    current: list[str] = []
    in_quote = False
    for ch in text:
        if ch == '"':
            in_quote = not in_quote
            current.append(ch)
        elif ch == "," and not in_quote:
            tokens.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        tokens.append("".join(current))
    return tokens


def _parse_values_token(token: str) -> str | int | float:
    """解析单个 VALUES 条目：引号字符串 / 整数 / 浮点数 / 原样字符串。"""
    token = token.strip()
    if len(token) >= 2 and token.startswith('"') and token.endswith('"'):
        return token[1:-1]
    if _VALUES_INT_RE.match(token):
        return int(token)
    if _VALUES_NUM_RE.match(token):
        return float(token)
    return token


def parse_values_declarations(vl_content: str) -> dict[str, dict[str, Any]]:
    """解析 vl.gdl 的 VALUES 声明。

    返回 ``{参数名: {"options": list | None, "range": list | None}}``：
    - ``VALUES "name" v1, v2, ...`` → ``options``（保持脚本里的顺序与原始类型）
    - ``VALUES "name" RANGE [a, b]`` → ``range``（数字列表原样透传）
    - 同一参数多条声明：后声明覆盖先声明
    - 无 vl.gdl / 无 VALUES / 解析失败：该参数不出现（上层字段为 None）
    """
    result: dict[str, dict[str, Any]] = {}
    if not vl_content:
        return result
    for raw_line in vl_content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("!"):
            continue
        match = VALUES_DECL_RE.match(line)
        if not match:
            continue
        name, rest = match.group(1), _strip_gdl_comment(match.group(2)).strip()
        entry: dict[str, Any] = {"options": None, "range": None}
        range_match = VALUES_RANGE_RE.match(rest)
        if range_match:
            numbers: list[str | int | float] = []
            for part in range_match.group(1).split(","):
                token = _parse_values_token(part)
                if isinstance(token, str):
                    numbers = []
                    break
                numbers.append(token)
            if numbers:
                entry["range"] = numbers
        elif rest.upper().startswith("RANGE"):
            # 以 RANGE 开头但括号/数字不合法 → 声明残缺，跳过（解析失败兜底）
            continue
        else:
            tokens = [token for token in _split_values_tokens(rest) if token.strip()]
            if tokens:
                entry["options"] = [_parse_values_token(token) for token in tokens]
        if entry["options"] is not None or entry["range"] is not None:
            result[name] = entry
    return result


def parameter_to_dict(param: GDLParameter, values: dict[str, Any] | None = None) -> dict[str, Any]:
    options = values.get("options") if values else None
    range_values = values.get("range") if values else None
    return {
        "name": param.name,
        "type": param.type_tag,
        "type_tag": param.type_tag,
        "description": param.description,
        "value": param.value,
        "is_fixed": bool(param.is_fixed),
        # P11：vl.gdl VALUES 枚举（options）与 RANGE 约束（range）透传；
        # 无声明/解析失败时为 None，不改变既有 payload 形状。
        "options": options,
        "range": range_values,
    }


def parameter_values(project: HSFProject, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for param in project.parameters:
        numeric = to_preview_number(param.value)
        if numeric is not None:
            values[param.name.upper()] = numeric
        elif isinstance(param.value, str) and param.value.strip():
            # P9：非数值字符串参数（如 String 型 pattern_type）原样保留，
            # 供预览器字符串比较（IF pattern_type = "直棂"）使用。
            values[param.name.upper()] = param.value
    for key, value in (overrides or {}).items():
        numeric = to_preview_number(value)
        if numeric is not None:
            values[str(key).upper()] = numeric
        elif isinstance(value, str) and value.strip():
            values[str(key).upper()] = value
    return values


def apply_parameter_values(project: HSFProject, changes: dict[str, Any]) -> dict[str, Any]:
    changed: dict[str, Any] = {}
    for name, value in changes.items():
        param = project.get_parameter(name)
        if param is None:
            continue
        param.value = coerce_parameter_value(param.type_tag, value)
        changed[name] = value
    return changed


def build_parameter_from_authoring_request(project: HSFProject, body: dict[str, Any]) -> GDLParameter:
    name = str(body.get("name") or "").strip()
    if not name:
        raise ValueError("Parameter name is required.")
    if not GDL_PARAMETER_NAME_RE.match(name):
        raise ValueError("Invalid parameter name.")
    if project.get_parameter(name) is not None:
        raise ValueError(f"Parameter '{name}' already exists")

    type_tag = str(body.get("type_tag") or "").strip()
    if not type_tag:
        raise ValueError("Parameter type is required.")
    if type_tag not in AUTHORABLE_PARAM_TYPES or type_tag not in VALID_PARAM_TYPES:
        raise ValueError(f"Unsupported parameter type: {type_tag}")

    value = coerce_parameter_value(type_tag, body.get("value"))
    description = str(body.get("description") or "").strip()
    return GDLParameter(name=name, type_tag=type_tag, description=description, value=value)


def validate_authorable_parameter_name(project: HSFProject, name: str, *, current_name: str = "") -> str:
    cleaned = str(name or "").strip()
    if not cleaned:
        raise ValueError("Parameter name is required.")
    if not GDL_PARAMETER_NAME_RE.match(cleaned):
        raise ValueError("Invalid parameter name.")
    if cleaned != current_name and project.get_parameter(cleaned) is not None:
        raise ValueError(f"Parameter '{cleaned}' already exists")
    return cleaned


def validate_authorable_type(type_tag: str) -> str:
    cleaned = str(type_tag or "").strip()
    if not cleaned:
        raise ValueError("Parameter type is required.")
    if cleaned not in AUTHORABLE_PARAM_TYPES or cleaned not in VALID_PARAM_TYPES:
        raise ValueError(f"Unsupported parameter type: {cleaned}")
    return cleaned


def coerce_parameter_value(type_tag: str, value: Any) -> str:
    if type_tag == "Boolean":
        if isinstance(value, bool):
            return "1" if value else "0"
        return "1" if str(value).strip().lower() in {"1", "true", "yes", "on"} else "0"
    if type_tag == "Integer":
        return str(int(float(value or 0)))
    if type_tag in {"Length", "RealNum"}:
        return str(float(value or 0))
    return str(value or "")


def to_preview_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    if text.lower() in {"true", "yes", "on"}:
        return 1.0
    if text.lower() in {"false", "no", "off"}:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return None
