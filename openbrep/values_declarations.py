"""Pure parsing of static GDL VALUES declarations (no evaluation)."""
import re
from typing import Any

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


