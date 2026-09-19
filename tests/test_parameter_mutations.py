from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

import pytest

from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.parameter_mutations import mutate_parameters
from openbrep.paramlist_builder import parse_paramlist_xml
from openbrep.source_fingerprint import compute_source_fingerprint

FIXTURE = Path(__file__).parent / "fixtures" / "spiral_stair" / "before_top_option"


def _loaded_fixture(tmp_path: Path) -> HSFProject:
    target = tmp_path / "SpiralStair"
    shutil.copytree(FIXTURE, target)
    return HSFProject.load_from_disk(str(target))


def _hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _mutate(project: HSFProject, operations: list[dict], **kwargs):
    return mutate_parameters(
        project,
        expected_source_fingerprint=compute_source_fingerprint(project.root),
        operations=operations,
        **kwargs,
    )


def test_p01_add_boolean_changes_only_paramlist_and_preserves_parameter_semantics(tmp_path):
    project = _loaded_fixture(tmp_path)
    before_hashes = _hashes(project.root)
    before_params = list(project.parameters)

    result = _mutate(
        project,
        [
            {
                "op": "add",
                "name": "show_top_tread",
                "type": "Boolean",
                "value": 1,
                "description": "显示顶部踏步",
            }
        ],
    )

    assert result.to_dict() == {
        "ok": True,
        "changed_parameters": [{"op": "add", "name": "show_top_tread"}],
        "changed_files": ["paramlist.xml"],
        "source_fingerprint": compute_source_fingerprint(project.root),
        "error_code": None,
    }
    assert project.parameters[:-1] == before_params
    assert [p.name for p in project.parameters].count("show_top_tread") == 1
    after_hashes = _hashes(project.root)
    assert {name for name in after_hashes if after_hashes[name] != before_hashes[name]} == {
        "paramlist.xml"
    }


def test_p02_string_with_quotes_and_xml_characters_round_trips(tmp_path):
    project = _loaded_fixture(tmp_path)
    value = '中文 "引号" & <xml> ]]> 尾部'
    description = '标签 & <说明> "原样"'

    result = _mutate(
        project,
        [
            {
                "op": "add",
                "name": "display_label",
                "type": "String",
                "value": value,
                "description": description,
            }
        ],
    )

    assert result.ok
    raw = (project.root / "paramlist.xml").read_text(encoding="utf-8-sig")
    assert "]]]]><![CDATA[>" in raw
    parsed = parse_paramlist_xml(raw)
    added = next(p for p in parsed if p.name == "display_label")
    assert added.value == value
    assert added.description == description
    reloaded = HSFProject.load_from_disk(str(project.root))
    assert reloaded.get_parameter("display_label") == added


@pytest.mark.parametrize(
    ("name", "type_tag", "value", "expected"),
    [
        ("total_angle", "Angle", 270.5, "270.5"),
        ("surface_index", "Material", 12, "12"),
    ],
)
def test_existing_hsf_types_use_strict_type_value_validation(
    tmp_path, name, type_tag, value, expected
):
    project = _loaded_fixture(tmp_path)
    result = _mutate(
        project,
        [{"op": "add", "name": name, "type": type_tag, "value": value}],
    )
    assert result.ok
    assert project.get_parameter(name).value == expected


def test_material_name_is_not_silently_coerced_to_default_index(tmp_path):
    project = _loaded_fixture(tmp_path)
    result = _mutate(
        project,
        [{"op": "add", "name": "surface_index", "type": "Material", "value": "Oak"}],
    )
    assert not result.ok
    assert result.error_code == "INVALID_VALUE"
    assert project.get_parameter("surface_index") is None


@pytest.mark.parametrize(
    ("operation", "error_code"),
    [
        ({"op": "add", "name": "A", "type": "Length", "value": 2}, "DUPLICATE_PARAMETER"),
        ({"op": "add", "name": "bad_type", "type": "Float", "value": 2}, "INVALID_TYPE"),
        ({"op": "delete", "name": "A"}, "PROTECTED_PARAMETER"),
        ({"op": "rename", "name": "diameter", "new_name": "width"}, "UNSUPPORTED_OPERATION"),
        ({"op": "type_change", "name": "diameter", "type": "RealNum"}, "UNSUPPORTED_OPERATION"),
    ],
)
def test_p03_rejections_leave_disk_and_memory_unchanged(tmp_path, operation, error_code):
    project = _loaded_fixture(tmp_path)
    before_bytes = (project.root / "paramlist.xml").read_bytes()
    before_params = list(project.parameters)

    result = _mutate(project, [operation])

    assert not result.ok
    assert result.error_code == error_code
    assert (project.root / "paramlist.xml").read_bytes() == before_bytes
    assert project.parameters == before_params


def test_p04_invalid_second_operation_rolls_back_whole_batch(tmp_path):
    project = _loaded_fixture(tmp_path)
    before_bytes = (project.root / "paramlist.xml").read_bytes()
    before_params = list(project.parameters)

    result = _mutate(
        project,
        [
            {"op": "add", "name": "show_top_tread", "type": "Boolean", "value": 1},
            {"op": "set_value", "name": "missing_parameter", "value": 2},
        ],
    )

    assert not result.ok
    assert result.error_code == "PARAMETER_NOT_FOUND"
    assert (project.root / "paramlist.xml").read_bytes() == before_bytes
    assert project.parameters == before_params


def test_p05_delete_requires_script_references_to_be_removed_first(tmp_path):
    project = _loaded_fixture(tmp_path)

    blocked = _mutate(project, [{"op": "delete", "name": "mat_landing"}])
    assert not blocked.ok
    assert blocked.error_code == "PARAMETER_IN_USE"
    assert "scripts/3d.gdl" in (blocked.error or "")

    project.scripts[ScriptType.SCRIPT_3D] = project.get_script(ScriptType.SCRIPT_3D).replace(
        "mat_landing", "mat_tread"
    )
    (project.root / "scripts" / "3d.gdl").write_text(
        project.get_script(ScriptType.SCRIPT_3D), encoding="utf-8-sig"
    )
    deleted = _mutate(project, [{"op": "delete", "name": "mat_landing"}])
    assert deleted.ok
    assert project.get_parameter("mat_landing") is None


def test_p06_stale_fingerprint_and_replace_failure_are_non_destructive(tmp_path):
    project = _loaded_fixture(tmp_path)
    stale = compute_source_fingerprint(project.root)
    script_path = project.root / "scripts" / "2d.gdl"
    script_path.write_bytes(script_path.read_bytes() + b"\n! external change\n")
    before_bytes = (project.root / "paramlist.xml").read_bytes()
    before_params = list(project.parameters)

    conflict = mutate_parameters(
        project,
        expected_source_fingerprint=stale,
        operations=[{"op": "add", "name": "show_top_tread", "type": "Boolean", "value": 1}],
    )
    assert conflict.error_code == "SOURCE_CHANGED"
    assert (project.root / "paramlist.xml").read_bytes() == before_bytes
    assert project.parameters == before_params

    def fail_replace(_source: str, _target: str) -> None:
        raise OSError("injected replace failure")

    failed = _mutate(
        project,
        [{"op": "add", "name": "show_top_tread", "type": "Boolean", "value": 1}],
        replace_fn=fail_replace,
    )
    assert failed.error_code == "SAVE_FAILED"
    assert (project.root / "paramlist.xml").read_bytes() == before_bytes
    assert project.parameters == before_params
    assert not list(project.root.glob(".paramlist.xml.*.tmp"))
    assert HSFProject.load_from_disk(str(project.root)).parameters == before_params


def test_p08_auxiliary_hsf_files_and_untouched_nodes_survive(tmp_path):
    project = _loaded_fixture(tmp_path)
    before = _hashes(project.root)
    original_paramlist = (project.root / "paramlist.xml").read_text(encoding="utf-8-sig")
    a_start = original_paramlist.index('<Length Name="A">')
    a_end = original_paramlist.index("</Length>") + len("</Length>")
    untouched_a = original_paramlist[a_start:a_end]

    result = _mutate(
        project,
        [
            {
                "op": "set_description",
                "name": "diameter",
                "description": "楼梯直径",
            }
        ],
    )

    assert result.ok
    after = _hashes(project.root)
    for relative in ("calledmacros.xml", "ancestry.xml", "libpartdocs.xml", "libpartdata.xml"):
        assert after[relative] == before[relative]
    updated = (project.root / "paramlist.xml").read_text(encoding="utf-8-sig")
    assert untouched_a in updated


def test_before_commit_runs_once_only_for_real_successful_write(tmp_path):
    project = _loaded_fixture(tmp_path)
    calls: list[str] = []
    result = _mutate(
        project,
        [{"op": "add", "name": "show_top_tread", "type": "Boolean", "value": 1}],
        before_commit=lambda: calls.append("before"),
    )
    assert result.ok
    assert calls == ["before"]

    failed = _mutate(
        project,
        [{"op": "add", "name": "show_top_tread", "type": "Boolean", "value": 1}],
        before_commit=lambda: calls.append("unexpected"),
    )
    assert not failed.ok
    assert calls == ["before"]


def test_lossless_mapping_rejects_unknown_parameter_node(tmp_path):
    project = _loaded_fixture(tmp_path)
    path = project.root / "paramlist.xml"
    raw = path.read_text(encoding="utf-8-sig").replace(
        "\t</Parameters>",
        '\t\t<CustomParameter Name="vendor"><Value>1</Value></CustomParameter>\n\t</Parameters>',
    )
    path.write_text(raw, encoding="utf-8-sig")
    project = HSFProject.load_from_disk(str(project.root))
    before = path.read_bytes()

    result = _mutate(project, [{"op": "set_value", "name": "diameter", "value": 2}])

    assert result.error_code == "LOSSLESS_UNSUPPORTED"
    assert path.read_bytes() == before


def test_atomic_write_uses_same_directory_temp_file(tmp_path):
    project = _loaded_fixture(tmp_path)
    seen: list[tuple[str, str]] = []

    def recording_replace(source: str, target: str) -> None:
        seen.append((source, target))
        os.replace(source, target)

    result = _mutate(
        project,
        [{"op": "set_value", "name": "diameter", "value": 1.8}],
        replace_fn=recording_replace,
    )
    assert result.ok
    assert len(seen) == 1
    assert Path(seen[0][0]).parent == project.root
    assert Path(seen[0][1]) == project.root / "paramlist.xml"
