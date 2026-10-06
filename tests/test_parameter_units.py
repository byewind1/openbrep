"""U01-A typed type/value/unit 归一化合同测试（openbrep/parameter_units.py）。

验收口径（派单 U01-A）：
- 900mm → 0.9m、0.9m 不变、25m 不变、90° 不缩放；
- 错单位 / NaN / 无穷 / 不合法值拒绝，错误带机器码 + 字段路径；
- unit=None（旧输入）不按数值大小重新解释（900 仍是 900）；
- 纯函数：只换算不落盘。
"""

from __future__ import annotations

import math

from openbrep.parameter_units import (
    NormalizedValue,
    UnitValueError,
    is_unit_error,
    normalize_typed_value,
)


def _ok(type_tag, value, unit=None, field_path="value") -> NormalizedValue:
    result = normalize_typed_value(type_tag, value, unit=unit, field_path=field_path)
    assert isinstance(result, NormalizedValue), f"expected ok, got {result}"
    return result


def _err(type_tag, value, unit=None, field_path="value") -> UnitValueError:
    result = normalize_typed_value(type_tag, value, unit=unit, field_path=field_path)
    assert isinstance(result, UnitValueError), f"expected error, got {result}"
    return result


# ── 验收用例 ─────────────────────────────────────────────────


def test_length_mm_converts_to_meters():
    result = _ok("Length", 900, "mm")
    assert result.number == 0.9
    assert result.canonical == "0.9"
    assert result.scaled is True
    assert result.unit == "mm"


def test_length_meters_with_explicit_unit_unchanged():
    result = _ok("Length", "0.9", "m")
    assert result.number == 0.9
    assert result.canonical == "0.9"
    assert result.scaled is False


def test_length_25m_unchanged():
    result = _ok("Length", "25", "m")
    assert result.number == 25.0
    assert result.canonical == "25"


def test_angle_degrees_never_scaled():
    result = _ok("Angle", "90", "°")
    assert result.number == 90.0
    assert result.canonical == "90"
    assert result.scaled is False
    result = _ok("Angle", "45", "度")
    assert result.number == 45.0
    result = _ok("Angle", 30, "deg")
    assert result.number == 30.0


def test_angle_radians_converts():
    result = _ok("Angle", math.pi, "rad")
    assert result.number == 180.0


def test_no_unit_means_internal_units_no_magnitude_reinterpretation():
    """旧输入（unit=None）不按数值大小重新解释：900 仍是 900（米），不是 0.9。"""
    for value in ("900", 900, "0.9", 25):
        result = _ok("Length", value)
        assert result.number == float(value)
        assert result.unit is None
        assert result.scaled is False


# ── 拒绝路径 ─────────────────────────────────────────────────


def test_nan_and_infinity_rejected():
    for bad in (float("nan"), float("inf"), float("-inf"), "nan", "inf"):
        error = _err("Length", bad)
        assert error.code == "NOT_FINITE"


def test_non_numeric_rejected():
    for bad in ("abc", None, "", "12x"):
        error = _err("Length", bad)
        assert error.code == "INVALID_VALUE"


def test_unknown_unit_rejected_not_guessed():
    error = _err("Length", 1.0, "inch")
    assert error.code == "INVALID_UNIT"
    error = _err("Length", 1.0, "光年")
    assert error.code == "INVALID_UNIT"


def test_unit_type_mismatch_rejected():
    assert _err("Length", 90, "deg").code == "UNIT_MISMATCH"
    assert _err("Angle", 900, "mm").code == "UNIT_MISMATCH"
    assert _err("RealNum", 900, "mm").code == "UNIT_MISMATCH"
    assert _err("Integer", 5, "mm").code == "UNIT_MISMATCH"
    assert _err("String", "oak", "mm").code == "UNIT_MISMATCH"
    assert _err("Boolean", True, "mm").code == "UNIT_MISMATCH"


def test_integer_requires_integral():
    error = _err("Integer", 5.5)
    assert error.code == "INVALID_VALUE"
    assert _ok("Integer", "5").canonical == "5"


def test_error_carries_field_path():
    error = _err("Length", "abc", field_path="parameters.shelf_height.value")
    assert error.field_path == "parameters.shelf_height.value"
    assert error.message


# ── 非数值类型 ────────────────────────────────────────────────


def test_string_passthrough_and_boolean_words():
    assert _ok("String", "oak").canonical == "oak"
    assert _ok("Boolean", True).canonical == "1"
    assert _ok("Boolean", "off").canonical == "0"
    assert _err("Boolean", "也许").code == "INVALID_VALUE"


def test_is_unit_error_dispatch():
    assert is_unit_error(_err("Length", "abc")) is True
    assert is_unit_error(_ok("Length", "1")) is False
