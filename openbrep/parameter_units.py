"""Typed type/value/unit normalization for GDL parameter values（U01-A）。

GDL 内部单位：Length → 米，Angle → 度。本模块是全仓库唯一的单位换算表：
micro_modify、参数 API、mutate_parameters 的显式单位路径都从这里取因子，
不得各自另建同义换算。

约定（派单 U01-A）：
- ``unit=None``（或空串）= 值已是 GDL 内部单位，**不按数值大小重新解释**
  （旧 HSF 读入 / 旧自由文本适配语义不变：0.9 不变、25 不变、900 仍是 900）。
- 显式单位才做换算：``900mm → 0.9``、``90° → 90``（角度永不缩放）。
- 错单位 / NaN / 无穷 / 不合法值一律拒绝，返回带字段路径的
  :class:`UnitValueError`；绝不静默猜测。
- 纯函数：不读不写任何源文件；提交仍走现有服务（mutate_parameters 等）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional

from openbrep.paramlist_builder import _format_value

# ── 唯一单位表 ────────────────────────────────────────────────

LENGTH_UNITS: dict[str, float] = {
    "mm": 0.001, "毫米": 0.001,
    "cm": 0.01, "厘米": 0.01,
    "m": 1.0, "米": 1.0,
}

ANGLE_UNITS: dict[str, float] = {
    "deg": 1.0, "degrees": 1.0, "°": 1.0, "度": 1.0,
    "rad": 180.0 / math.pi, "弧度": 180.0 / math.pi,
}

_BOOLEAN_TRUE = {"1", "true", "yes", "on"}
_BOOLEAN_FALSE = {"0", "false", "no", "off"}

_INTEGER_INDEX_TYPES = {"Integer", "PenColor", "Material", "FillPattern", "LineType"}


@dataclass(frozen=True)
class NormalizedValue:
    """归一化成功：值已是 GDL 内部单位。"""

    canonical: str        # paramlist 值字符串（内部单位，_format_value 规整）
    number: float         # 内部单位数值（Length=米，Angle=度）
    unit: Optional[str]   # 归一后的显式输入单位；None = 未给单位（内部单位透传）
    scaled: bool          # 是否发生了单位换算


@dataclass(frozen=True)
class UnitValueError:
    """归一化拒绝：带机器码、用户可读信息与字段路径。"""

    code: str       # INVALID_VALUE | INVALID_UNIT | UNIT_MISMATCH | NOT_FINITE | INVALID_TYPE
    message: str
    field_path: str


def normalize_typed_value(
    type_tag: str,
    value: Any,
    *,
    unit: Any = None,
    field_path: str = "value",
) -> NormalizedValue | UnitValueError:
    """typed type/value/unit 归一化（纯函数）。

    Args:
        type_tag: GDL 参数类型（Length/Angle/RealNum/Integer/Boolean/String/...）。
        value: 原始值（number / str / bool）。
        unit: 显式输入单位（"mm"/"毫米"/"deg"/"°"…）；None/空串 = 值已是内部单位。
        field_path: 错误信息里的字段路径（如 ``parameters.shelf_height.value``）。

    Returns:
        :class:`NormalizedValue` 或 :class:`UnitValueError`（用 isinstance 分派）。
    """
    unit_token = str(unit or "").strip().lower()
    if type_tag == "String":
        if unit_token:
            return _mismatch(type_tag, unit_token, field_path)
        return NormalizedValue(
            canonical=str(value if value is not None else ""),
            number=0.0, unit=None, scaled=False,
        )
    if type_tag == "Boolean":
        if isinstance(value, bool):
            canonical = "1" if value else "0"
        else:
            low = str(value if value is not None else "").strip().lower()
            if low in _BOOLEAN_TRUE:
                canonical = "1"
            elif low in _BOOLEAN_FALSE:
                canonical = "0"
            else:
                return UnitValueError(
                    "INVALID_VALUE", "Boolean 值必须是 0/1 或 true/false", field_path
                )
        if unit_token:
            return _mismatch(type_tag, unit_token, field_path)
        return NormalizedValue(canonical=canonical, number=float(canonical), unit=None, scaled=False)

    # 其余类型都是数值域
    if isinstance(value, bool):
        number = 1.0 if value else 0.0
    else:
        try:
            number = float(str(value if value is not None else "").strip())
        except (TypeError, ValueError):
            return UnitValueError(
                "INVALID_VALUE", f"{type_tag} 值必须是数字", field_path
            )
    if not math.isfinite(number):
        return UnitValueError(
            "NOT_FINITE", f"{type_tag} 值不能是 NaN 或无穷", field_path
        )

    factor: Optional[float] = None
    if unit_token:
        known: Optional[float] = None
        if type_tag == "Length":
            known = LENGTH_UNITS.get(unit_token)
        elif type_tag == "Angle":
            known = ANGLE_UNITS.get(unit_token)
        if known is None:
            if unit_token in LENGTH_UNITS or unit_token in ANGLE_UNITS:
                # 单位本身合法，但与参数类型不匹配（Angle 配 mm、RealNum 配 mm…）
                return _mismatch(type_tag, unit_token, field_path)
            return UnitValueError(
                "INVALID_UNIT",
                f"未知单位 {unit!r}；Length 支持 mm/毫米/cm/厘米/m/米，"
                "Angle 支持 deg/°/度/rad/弧度",
                field_path,
            )
        factor = known

    scaled = factor is not None and factor != 1.0
    number = number * factor if factor is not None else number
    if not math.isfinite(number):
        return UnitValueError("NOT_FINITE", "换算后数值溢出", field_path)

    if type_tag in _INTEGER_INDEX_TYPES:
        if number != int(number):
            return UnitValueError(
                "INVALID_VALUE", f"{type_tag} 值必须是整数", field_path
            )
        return NormalizedValue(
            canonical=str(int(number)), number=float(int(number)),
            unit=unit_token or None, scaled=scaled,
        )
    if type_tag in ("Length", "Angle", "RealNum"):
        return NormalizedValue(
            canonical=_format_value(type_tag, number), number=number,
            unit=unit_token or None, scaled=scaled,
        )
    return UnitValueError("INVALID_TYPE", f"不支持的参数类型：{type_tag}", field_path)


def _mismatch(type_tag: str, unit_token: str, field_path: str) -> UnitValueError:
    return UnitValueError(
        "UNIT_MISMATCH",
        f"单位 {unit_token!r} 不能用于 {type_tag} 类型参数",
        field_path,
    )


def is_unit_error(result: NormalizedValue | UnitValueError) -> bool:
    """typed 归一化结果的分派助手（True = 被拒绝）。"""
    return isinstance(result, UnitValueError)
