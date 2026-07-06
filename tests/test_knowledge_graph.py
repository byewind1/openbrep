"""最小合同测试：GDLGraphManager 公开接口。"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from openbrep.graph_schema import APIFunction, BIMConcept
from openbrep.knowledge_graph import GDLGraphManager, _extract_undefined_names


# ── 测试夹具 ──────────────────────────────────────────────


def _make_minimal_graph_json(tmp_path: Path) -> Path:
    """写一个最小测试图谱 JSON。"""
    data = {
        "version": "1.0",
        "api_functions": [
            {
                "name": "BLOCK",
                "signature": "a, b, c",
                "return_type": "void",
                "description": "长方体",
                "param_count_min": 3,
                "param_count_max": 3,
                "category": "geometry",
            },
            {
                "name": "ADD",
                "signature": "x, y, z",
                "return_type": "void",
                "description": "平移入栈",
                "param_count_min": 3,
                "param_count_max": 3,
                "category": "transform",
            },
            {
                "name": "MATERIAL",
                "signature": "mat_name_or_index",
                "return_type": "void",
                "description": "设置材质",
                "param_count_min": 1,
                "param_count_max": 1,
                "category": "attribute",
            },
        ],
        "bim_concepts": [
            {
                "name": "Box",
                "aliases": ["cube", "box", "长方体"],
                "description": "矩形体块",
                "required_apis": ["BLOCK"],
                "init_pattern": "BLOCK A, B, ZZYZX",
            },
            {
                "name": "Material",
                "aliases": ["material", "材质"],
                "description": "材质概念",
                "required_apis": ["MATERIAL"],
                "init_pattern": "MATERIAL \"mat_name\"",
            },
        ],
        "known_error_patterns": [
            {
                "pattern": "mat_",
                "concept": "Material",
                "diagnosis": "材质变量未初始化。",
                "fix_hint": "在使用前添加 DEFINE MATERIAL。",
            }
        ],
    }
    graph_file = tmp_path / "gdl_graph.json"
    graph_file.write_text(json.dumps(data), encoding="utf-8")
    return graph_file


@pytest.fixture
def manager(tmp_path: Path) -> GDLGraphManager:
    path = _make_minimal_graph_json(tmp_path)
    m = GDLGraphManager(graph_path=path)
    m.load()
    return m


# ── query_api ─────────────────────────────────────────────


def test_query_api_hit(manager: GDLGraphManager) -> None:
    result = manager.query_api("BLOCK")
    assert result is not None
    assert result.name == "BLOCK"
    assert "a, b, c" in result.signature


def test_query_api_case_insensitive(manager: GDLGraphManager) -> None:
    assert manager.query_api("block") is not None
    assert manager.query_api("Block") is not None


def test_query_api_miss(manager: GDLGraphManager) -> None:
    assert manager.query_api("NONEXISTENT_CMD_XYZ") is None


# ── suggest_apis ──────────────────────────────────────────


def test_suggest_apis_by_alias(manager: GDLGraphManager) -> None:
    apis = manager.suggest_apis("做一个长方体")
    names = [a.name for a in apis]
    assert "BLOCK" in names


def test_suggest_apis_no_match(manager: GDLGraphManager) -> None:
    apis = manager.suggest_apis("完全不相关的内容 xyz123")
    assert apis == []


# ── diagnose_error ────────────────────────────────────────


def test_diagnose_error_undefined_var(manager: GDLGraphManager) -> None:
    err = "(0) : error: Undefined variable 'mat_01'"
    result = manager.diagnose_error(err)
    assert result != ""
    assert "mat_" in result or "Material" in result


def test_diagnose_error_empty(manager: GDLGraphManager) -> None:
    assert manager.diagnose_error("") == ""


def test_diagnose_error_unknown(manager: GDLGraphManager) -> None:
    result = manager.diagnose_error("some completely unrelated error xyz")
    # 不应抛出异常，返回字符串即可
    assert isinstance(result, str)


# ── build_constraint_prompt ───────────────────────────────


def test_build_constraint_prompt_matches(manager: GDLGraphManager) -> None:
    prompt = manager.build_constraint_prompt("生成一个 cube")
    assert prompt != ""
    assert "BLOCK" in prompt


def test_build_constraint_prompt_no_match(manager: GDLGraphManager) -> None:
    prompt = manager.build_constraint_prompt("随机无关意图 xyz123")
    assert prompt == ""


# ── _extract_undefined_names ──────────────────────────────


def test_extract_undefined_names_lp_style() -> None:
    err = "(0) : error: Undefined variable 'mat_floor'"
    names = _extract_undefined_names(err)
    assert "mat_floor" in names


def test_extract_undefined_names_multiple() -> None:
    err = "Undefined variable 'foo' and undeclared identifier 'bar'"
    names = _extract_undefined_names(err)
    assert "foo" in names
    assert "bar" in names


def test_extract_undefined_names_empty() -> None:
    assert _extract_undefined_names("") == []


# ── 图谱加载容错 ──────────────────────────────────────────


def test_graph_missing_file_does_not_raise(tmp_path: Path) -> None:
    m = GDLGraphManager(graph_path=tmp_path / "nonexistent.json")
    m.load()
    # 加载失败后图是空的，但接口仍可用
    assert m.query_api("BLOCK") is None
    assert m.suggest_apis("cube") == []
    assert m.diagnose_error("error") == ""


# ── error_category schema 校验 ────────────────────────────


def _graph_json_with_error_category(tmp_path: Path, error_category) -> Path:
    data = {
        "version": "1.0",
        "api_functions": [],
        "bim_concepts": [],
        "known_error_patterns": [
            {
                "pattern": "mat_",
                "concept": "Material",
                "diagnosis": "材质变量未初始化。",
                "fix_hint": "在使用前添加 DEFINE MATERIAL。",
                "error_category": error_category,
            }
        ],
    }
    graph_file = tmp_path / "gdl_graph.json"
    graph_file.write_text(json.dumps(data), encoding="utf-8")
    return graph_file


def test_valid_error_category_does_not_log_schema_error(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    from openbrep.error_classifier import ErrorCategory

    path = _graph_json_with_error_category(tmp_path, ErrorCategory.UNDEFINED_VAR.value)
    with caplog.at_level("ERROR", logger="openbrep.knowledge_graph"):
        GDLGraphManager(graph_path=path).load()

    assert "GDL graph schema error" not in caplog.text


def test_unknown_error_category_logs_schema_error_but_does_not_raise(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    path = _graph_json_with_error_category(tmp_path, "totally_made_up_category")

    with caplog.at_level("ERROR", logger="openbrep.knowledge_graph"):
        m = GDLGraphManager(graph_path=path)
        m.load()  # must not raise despite the bad field

    assert "GDL graph schema error" in caplog.text
    assert "totally_made_up_category" in caplog.text
    # graph still usable — schema mismatch degrades gracefully, doesn't disable the whole graph
    assert m.diagnose_error("error") == ""
