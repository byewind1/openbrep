from pathlib import Path

from openbrep.quality.cross_script import build_cross_script_graph, format_graph


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
