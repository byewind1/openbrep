from pathlib import Path

from openbrep.hsf_project import HSFProject
from openbrep.quality.cross_script import (
    analyze_mutation_impact,
    build_cross_script_graph,
    format_graph,
)


def _project(tmp_path: Path, *, malformed: bool = False) -> Path:
    root = tmp_path / "Obj"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    if malformed:
        (root / "paramlist.xml").write_text("<ParamSection>", encoding="utf-8")
    else:
        (root / "paramlist.xml").write_text(
            """<ParamSection><Parameters>
            <Length Name="width"><Value>2</Value></Length>
            <Boolean Name="show"><Value>1</Value></Boolean>
            <String Name="pattern"><Value><![CDATA[\"直棂\"]]></Value></String>
            <Material Name="mat"><Value>1</Value></Material>
            </Parameters></ParamSection>""",
            encoding="utf-8",
        )
    (scripts / "1d.gdl").write_text("derived = width * 2\n", encoding="utf-8")
    (scripts / "vl.gdl").write_text(
        'VALUES "pattern" "直棂", "冰裂"\nLOCK "missing"\n', encoding="utf-8"
    )
    (scripts / "3d.gdl").write_text(
        'IF pattern = "直棂" THEN BLOCK width, 1, 1\n'
        'IF pattern = "错字" THEN BLOCK width, 1, 1\n'
        'IF show THEN BLOCK width, 1, 1\n', encoding="utf-8"
    )
    (scripts / "ui.gdl").write_text("UI_OUT width\n", encoding="utf-8")
    return root


def test_graph_tracks_derivation_enum_and_roles(tmp_path):
    graph = build_cross_script_graph(_project(tmp_path))

    assert graph.status == "measured"
    assert graph.parameters[0]["source"]["file"] == "paramlist.xml"
    assert any(edge["kind"] == "derived" and edge["from"] == "width" for edge in graph.edges)
    assert any(issue["kind"] == "unknown_target" for issue in graph.issues)
    assert any(issue["kind"] == "enum_missing_branch" for issue in graph.issues)
    assert any(issue["kind"] == "enum_unknown_branch" for issue in graph.issues)
    assert graph.eligibility["width"]["role"] in {"geometry_driver", "derived"}
    assert graph.eligibility["show"]["test_values"] == [0, 1]
    assert graph.eligibility["mat"]["role"] == "material"
    assert "cross-script scan" in format_graph(graph)


def test_missing_and_malformed_inputs_are_partial_not_raising(tmp_path):
    root = tmp_path / "missing"
    graph = build_cross_script_graph(root)
    assert graph.status == "partial"
    assert graph.unknown_edges

    graph = build_cross_script_graph(_project(tmp_path, malformed=True))
    assert graph.status == "partial"
    assert any(issue["kind"] == "paramlist_parse_error" for issue in graph.issues)


def test_gosub_body_and_ui_only_are_seen(tmp_path):
    root = _project(tmp_path)
    (root / "paramlist.xml").write_text(
        '<ParamSection><Parameters><Length Name="ui_width"><Value>1</Value></Length>'
        '</Parameters></ParamSection>', encoding="utf-8"
    )
    (root / "scripts" / "ui.gdl").write_text(
        "GOSUB 10\n10:\nui_width = ui_width + 1\nRETURN\n", encoding="utf-8"
    )
    graph = build_cross_script_graph(root)
    assert graph.scripts["ui.gdl"]["read"]
    assert graph.eligibility["ui_width"]["role"] == "ui_only"


def test_quality_uses_conservative_shared_parameter_roles(tmp_path):
    root = tmp_path / "Roles"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    (root / "paramlist.xml").write_text(
        """<ParamSection><Parameters>
        <Length Name="width"><Value>2</Value></Length>
        <Length Name="radius"><Value>1</Value></Length>
        <Length Name="pole"><Value>0.1</Value></Length>
        <Length Name="conditional"><Value>0</Value></Length>
        <Length Name="left"><Value>0</Value></Length>
        <Length Name="right"><Value>0</Value></Length>
        </Parameters></ParamSection>""",
        encoding="utf-8",
    )
    (scripts / "1d.gdl").write_text(
        "radius = width / 2\n"
        "pole = radius * 0.1\n"
        "IF width > 0 THEN conditional = width\n"
        "left = right + 1\n"
        "right = left + 1\n",
        encoding="utf-8",
    )
    (scripts / "3d.gdl").write_text(
        "CYLIND 1, pole\nBLOCK conditional, 1, 1\n",
        encoding="utf-8",
    )

    graph = build_cross_script_graph(root)

    assert graph.eligibility["width"]["role"] == "geometry_driver"
    assert graph.eligibility["radius"]["role"] == "derived"
    assert graph.eligibility["radius"]["depends_on"] == ["width"]
    assert graph.eligibility["pole"]["depends_on"] == ["width"]
    assert graph.eligibility["conditional"]["role"] == "unknown"
    assert graph.eligibility["conditional"]["reason"] == "conditional_assignment"
    assert graph.eligibility["left"]["role"] == "unknown"
    assert graph.eligibility["right"]["role"] == "unknown"


def test_source_edit_invalidates_all_scripts_and_reports_partial_coverage(tmp_path):
    root = _project(tmp_path)
    project = HSFProject.load_from_disk(str(root))

    impact = analyze_mutation_impact(project, {"changed_files": ["scripts/3d.gdl"]})

    assert impact.status == "partial"
    assert "scripts/2d.gdl" in impact.affected_scripts
    assert "scripts/vl.gdl" in impact.affected_scripts
    assert {"compile", "static", "semantic", "preview", "parameter_ui"} <= set(impact.checks)
    assert impact.coverage["complete"] is False


def test_parameter_impact_follows_master_derivations_and_dynamic_edges(tmp_path):
    root = _project(tmp_path)
    (root / "scripts" / "1d.gdl").write_text("derived = width * 2\n", encoding="utf-8")
    (root / "scripts" / "3d.gdl").write_text("BLOCK derived, 1, 1\n", encoding="utf-8")
    (root / "scripts" / "ui.gdl").write_text('CALL "ui_macro"\n', encoding="utf-8")
    project = HSFProject.load_from_disk(str(root))

    impact = analyze_mutation_impact(project, {"changed_parameters": ["WIDTH"]})

    assert "width" in impact.affected_parameters
    assert {"scripts/1d.gdl", "scripts/3d.gdl"} <= set(impact.affected_scripts)
    assert set(impact.affected_scripts) == {
        "scripts/1d.gdl", "scripts/2d.gdl", "scripts/3d.gdl", "scripts/ui.gdl", "scripts/vl.gdl", "scripts/pr.gdl"
    }
    assert impact.coverage["method"] == "conservative_full_script_invalidation"
    assert any("dynamic GDL references" in item for item in impact.unknown_dependencies)
    assert impact.coverage["complete"] is False
