"""wiki→图谱派生管道（Phase 2a）合同测试。

覆盖：签名解析、生成页/手写概念页/archetype 派生、
GDLGraphManager 两层加载（derived + 手工 override）、意图命中验收。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openbrep.graph_derivation import (
    _extract_signature,
    _parse_signature_chunks,
    derive_graph,
)
from openbrep.knowledge_graph import GDLGraphManager, _alias_in_text


# ── 签名解析 ──────────────────────────────────────────────


def test_parse_signature_multi_param() -> None:
    parsed = _parse_signature_chunks("h, r1, r2, alpha1, alpha2")
    assert parsed is not None
    assert parsed.params == ["h", "r1", "r2", "alpha1", "alpha2"]
    assert not parsed.variadic


def test_parse_signature_inline_prose() -> None:
    # SPHERE 形态：签名与描述同行，句首大写
    parsed = _parse_signature_chunks("r A sphere with its center at the origin.")
    assert parsed is not None
    assert parsed.params == ["r"]
    assert "sphere" in parsed.description


def test_parse_signature_rejects_global_variable_prose() -> None:
    # 全局变量页形态："BEAM_VOLUME volume of the beam" — 小写短语接续，必须拒绝
    assert _parse_signature_chunks("volume of the beam") is None
    assert _parse_signature_chunks("line type of the beam axes") is None


def test_parse_signature_variadic() -> None:
    parsed = _parse_signature_chunks("n, m, mask, u1, w1, s1, ... un, wn, sn")
    assert parsed is not None
    assert parsed.variadic
    assert parsed.params[:3] == ["n", "m", "mask"]
    assert parsed.signature.endswith("...")


def test_extract_signature_from_body() -> None:
    body = "###### CONE\n\nCONE h, r1, r2, alpha1, alpha2\n\nFrustum of a cone where alpha1 is the angle.\n"
    parsed = _extract_signature(body, "CONE")
    assert parsed is not None
    assert parsed.signature == "h, r1, r2, alpha1, alpha2"
    assert "Frustum" in parsed.description


def test_extract_signature_no_prefix_collision() -> None:
    # PRISM 不应匹配到 PRISM_ 的行
    body = "PRISM_ n, h, x1, y1, s1\n"
    assert _extract_signature(body, "PRISM") is None


# ── 派生：fixture wiki 目录 ───────────────────────────────


@pytest.fixture
def fixture_dirs(tmp_path: Path) -> tuple[Path, Path]:
    wiki = tmp_path / "wiki"
    arch = tmp_path / "archetypes"
    wiki.mkdir()
    arch.mkdir()

    (wiki / "cone.md").write_text(
        '---\nid: wiki.generated.cone\ntype: wiki\ncategory: 3d\ncommands: ["CONE"]\n'
        "status: draft\n---\n\n###### CONE\n\nCONE h, r1, r2, alpha1, alpha2\n\n"
        "Frustum of a cone with endcircle radii r1 and r2.\n",
        encoding="utf-8",
    )
    (wiki / "beam_volume.md").write_text(
        '---\nid: wiki.generated.beam_volume\ntype: wiki\ncategory: 3d\ncommands: ["BEAM_VOLUME"]\n'
        "status: draft\n---\n\n###### BEAM_VOLUME volume of the beam\n\n|Beam Segment|\n|---|\n",
        encoding="utf-8",
    )
    (wiki / "MYCMD.md").write_text(
        "---\ntype: concept\nstatus: stable\ntags: [3d]\naliases: [MYCMD, mycommand, 自定义命令]\n---\n\n"
        "# MYCMD\n\nMYCMD builds a custom shape for testing purposes.\n\n"
        "```gdl\nMYCMD w, d\nCONE h, r1, r2, 90, 90\n```\n",
        encoding="utf-8",
    )
    (arch / "bookshelf.md").write_text(
        "---\nid: archetype.bookshelf\ntitle: 参数化书架\ntype: archetype\n"
        "object_types: [bookshelf, bookcase, 书架, 书柜]\ncommands: [BLOCK, ADDZ, DEL]\n---\n\n# 书架\n",
        encoding="utf-8",
    )
    return wiki, arch


def test_derive_generated_page_yields_api_and_concept(fixture_dirs) -> None:
    wiki, arch = fixture_dirs
    graph = derive_graph(wiki_dir=wiki, archetypes_dir=arch)
    apis = {a["name"]: a for a in graph["api_functions"]}
    concepts = {c["name"]: c for c in graph["bim_concepts"]}

    assert apis["CONE"]["signature"] == "h, r1, r2, alpha1, alpha2"
    assert apis["CONE"]["param_count_min"] == 5
    assert "Cone" in concepts
    assert "cone" in concepts["Cone"]["aliases"]


def test_derive_global_variable_page_is_api_only(fixture_dirs) -> None:
    wiki, arch = fixture_dirs
    graph = derive_graph(wiki_dir=wiki, archetypes_dir=arch)
    apis = {a["name"]: a for a in graph["api_functions"]}
    concept_names = {c["name"] for c in graph["bim_concepts"]}

    # 全局变量页进 API 索引（存在性查询），但不升级为概念、不误抽签名
    assert "BEAM_VOLUME" in apis
    assert apis["BEAM_VOLUME"]["signature"] == ""
    assert not any("Beam" in n for n in concept_names)


def test_derive_concept_page(fixture_dirs) -> None:
    wiki, arch = fixture_dirs
    graph = derive_graph(wiki_dir=wiki, archetypes_dir=arch)
    apis = {a["name"]: a for a in graph["api_functions"]}
    concepts = {c["name"]: c for c in graph["bim_concepts"]}

    concept = concepts["MYCMD"]
    # 页名命令置顶，其余按代码块出现顺序
    assert concept["required_apis"][0] == "MYCMD"
    assert "CONE" in concept["required_apis"]
    assert "自定义命令" in concept["aliases"]
    assert concept["init_pattern"].startswith("MYCMD w, d")
    # 生成页未覆盖 MYCMD → 从手写页补 API 条目
    assert apis["MYCMD"]["signature"] == "w, d"


def test_derive_archetype(fixture_dirs) -> None:
    wiki, arch = fixture_dirs
    graph = derive_graph(wiki_dir=wiki, archetypes_dir=arch)
    concepts = {c["name"]: c for c in graph["bim_concepts"]}

    bookshelf = concepts["Bookshelf"]
    assert "书架" in bookshelf["aliases"]
    assert "书柜" in bookshelf["aliases"]
    assert bookshelf["required_apis"] == ["BLOCK", "ADDZ", "DEL"]


# ── GDLGraphManager 两层加载 ──────────────────────────────


def test_manager_merges_derived_and_manual_override(tmp_path: Path) -> None:
    derived = {
        "version": "1.0",
        "api_functions": [
            {"name": "CONE", "signature": "h, r1, r2, alpha1, alpha2", "param_count_min": 5, "param_count_max": 5},
            {"name": "SPHERE", "signature": "r", "param_count_min": 1, "param_count_max": 1},
        ],
        "bim_concepts": [
            {"name": "Cone", "aliases": ["cone"], "required_apis": ["CONE"], "description": "derived cone"},
        ],
        "known_error_patterns": [],
    }
    manual = {
        "version": "1.1",
        "api_functions": [
            {"name": "CONE", "signature": "h, r1, r2, a1, a2", "description": "手工修正", "param_count_min": 5, "param_count_max": 5},
        ],
        "bim_concepts": [
            {"name": "Cone", "aliases": ["圆锥"], "required_apis": ["CONE"], "description": "手工版"},
        ],
        "known_error_patterns": [],
    }
    (tmp_path / "gdl_graph_derived.json").write_text(json.dumps(derived), encoding="utf-8")
    manual_path = tmp_path / "gdl_graph.json"
    manual_path.write_text(json.dumps(manual), encoding="utf-8")

    m = GDLGraphManager(graph_path=manual_path)
    m.load()

    # 手工层同名覆盖派生层
    cone = m.query_api("CONE")
    assert cone is not None and cone.description == "手工修正"
    # 派生层独有条目仍可查
    assert m.query_api("SPHERE") is not None
    # 别名并集：派生层 "cone" 与手工层 "圆锥" 都命中同一概念（手工版）
    assert m._find_concept("draw a cone here").description == "手工版"
    assert m._find_concept("画一个圆锥").description == "手工版"


def test_manager_derived_only_no_manual(tmp_path: Path) -> None:
    derived = {
        "version": "1.0",
        "api_functions": [{"name": "SPHERE", "signature": "r"}],
        "bim_concepts": [],
        "known_error_patterns": [],
    }
    (tmp_path / "gdl_graph_derived.json").write_text(json.dumps(derived), encoding="utf-8")
    m = GDLGraphManager(graph_path=tmp_path / "gdl_graph.json")  # 手工文件不存在
    m.load()
    assert m.query_api("SPHERE") is not None


# ── 别名匹配规则 ──────────────────────────────────────────


def test_alias_word_boundary_ascii() -> None:
    assert _alias_in_text("cone", "draw a cone")
    assert not _alias_in_text("arc", "please search the file")  # arc ⊄ search
    assert not _alias_in_text("cone", "silicone mold")


def test_alias_substring_chinese() -> None:
    assert _alias_in_text("书架", "做一个宽600的书架")


def test_find_concept_prefers_longest_alias(tmp_path: Path) -> None:
    manual = {
        "version": "1.0",
        "api_functions": [],
        "bim_concepts": [
            {"name": "Generic", "aliases": ["柜"], "required_apis": []},
            {"name": "ShoeCabinet", "aliases": ["鞋柜"], "required_apis": []},
        ],
        "known_error_patterns": [],
    }
    path = tmp_path / "gdl_graph.json"
    path.write_text(json.dumps(manual), encoding="utf-8")
    m = GDLGraphManager(graph_path=path)
    m.load()
    assert m._find_concept("做一个鞋柜").name == "ShoeCabinet"


# ── 真实仓库数据验收（Phase 2a 目标）─────────────────────


def test_real_repo_derivation_meets_targets() -> None:
    graph = derive_graph()
    assert len(graph["bim_concepts"]) >= 50, "Phase 2a 验收：派生概念数应 ≥50"
    assert len(graph["api_functions"]) >= 400
    with_sig = sum(1 for a in graph["api_functions"] if a["signature"])
    assert with_sig >= 150


def test_real_repo_high_frequency_intents_hit() -> None:
    """验收：书架类高频意图 100% 命中概念注入。"""
    m = GDLGraphManager()
    m.load()
    intents = [
        "做一个宽600mm深400mm的书架",
        "生成书柜",
        "做一张餐桌",
        "做个鞋柜",
        "生成一个圆锥体",
        "来一个球体",
        "做一个窗户",
        "生成异形板",
    ]
    for intent in intents:
        prompt = m.build_constraint_prompt(intent)
        assert prompt != "", f"意图未命中概念注入: {intent}"


# ── 错误模式派生（GDL_common_errors.md → known_error_patterns）──


def test_derive_error_patterns_from_common_errors_doc(tmp_path: Path) -> None:
    from openbrep.graph_derivation import _derive_error_patterns

    doc = tmp_path / "GDL_common_errors.md"
    doc.write_text(
        "# GDL 常见错误\n\n"
        "## 1. 多行 IF 缺少 ENDIF\n\n"
        "**现象**：Archicad 报错 `ENDIF expected` 或 `Syntax error near IF block`。\n\n"
        "**原因**：多行 IF 代码块没有闭合。\n\n"
        "**修复**：每个多行 IF 都补齐 ENDIF。\n\n"
        "## 2. 几何位置错乱\n\n"
        "**现象**：几何位置错乱、对象漂移。\n\n"
        "**原因**：变换层数不一致。\n\n"
        "**修复**：严格配平。\n",
        encoding="utf-8",
    )
    patterns = _derive_error_patterns(doc)
    # 第 1 节两条报错短语；第 2 节无可匹配短语被跳过
    assert len(patterns) == 2
    assert patterns[0]["pattern"] == "endif expected"
    assert "没有闭合" in patterns[0]["diagnosis"]
    assert patterns[0]["fix_hint"]


def test_real_repo_error_patterns_derived() -> None:
    from openbrep.graph_derivation import _derive_error_patterns

    patterns = _derive_error_patterns()
    assert len(patterns) >= 10
    # 派生 + 手工层合计应超过历史 9 条
    m = GDLGraphManager()
    m.load()
    assert len(m.error_patterns) >= 20


def test_derived_error_pattern_reachable_via_diagnose() -> None:
    m = GDLGraphManager()
    m.load()
    result = m.diagnose_error("3D script failed: ENDWHILE expected near line 4")
    assert "ENDWHILE" in result or "endwhile" in result


# ── 词法回退匹配 + 未命中落 trace ─────────────────────────


def test_fuzzy_fallback_matches_token_overlap(tmp_path: Path) -> None:
    manual = {
        "version": "1.0",
        "api_functions": [],
        "bim_concepts": [
            {
                "name": "Bookshelf",
                "aliases": ["parametric bookshelf", "书架"],
                "description": "参数化书架",
                "required_apis": [],
            },
        ],
        "known_error_patterns": [],
    }
    path = tmp_path / "gdl_graph.json"
    path.write_text(json.dumps(manual), encoding="utf-8")
    m = GDLGraphManager(graph_path=path)
    m.load()
    # "parametric bookshelf" 整体不在文本里，但两个词都各自出现 → 词法回退命中
    c = m._find_concept("i want a bookshelf that is parametric")
    assert c is not None and c.name == "Bookshelf"
    # 不相关文本不应误命中
    assert m._find_concept("完全无关的东西 qwerty") is None


def test_constraint_prompt_miss_logged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manual = {"version": "1.0", "api_functions": [], "bim_concepts": [], "known_error_patterns": []}
    path = tmp_path / "gdl_graph.json"
    path.write_text(json.dumps(manual), encoding="utf-8")
    m = GDLGraphManager(graph_path=path)
    m.load()

    monkeypatch.chdir(tmp_path)
    result = m.build_constraint_prompt("完全无关的意图 qwerty", log_miss=True)
    assert result == ""
    miss_log = tmp_path / "traces" / "graph_misses.jsonl"
    assert miss_log.exists()
    record = json.loads(miss_log.read_text(encoding="utf-8").splitlines()[0])
    assert "qwerty" in record["intent"]
    assert record["timestamp"]


def test_constraint_prompt_miss_not_logged_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manual = {"version": "1.0", "api_functions": [], "bim_concepts": [], "known_error_patterns": []}
    path = tmp_path / "gdl_graph.json"
    path.write_text(json.dumps(manual), encoding="utf-8")
    m = GDLGraphManager(graph_path=path)
    m.load()

    monkeypatch.chdir(tmp_path)
    m.build_constraint_prompt("完全无关的意图 qwerty")
    assert not (tmp_path / "traces" / "graph_misses.jsonl").exists()
