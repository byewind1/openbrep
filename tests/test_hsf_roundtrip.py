"""HSF load→save 完整性（P0）：calledmacros / ancestry / libpartdocs /
Identification 标志在导入规范化后不丢失。

背景：Archicad 通过 calledmacros.xml 的 名称—GUID 表绑定 CALL 宏；
此前 HSFProject 加载不读、保存重建为空表，导入规范化会把 63 条宏映射
清空却仍报告 lossless=True（见 OpenBrep-GSM-CALL 研究 2026-09-12 §4）。
"""

from __future__ import annotations

from pathlib import Path

from openbrep.hsf_project import HSFProject, normalize_project_after_import


CALLEDMACROS_XML = '''<?xml version="1.0" encoding="UTF-8"?>
<CalledMacros>
\t<Macro>
\t\t<MName><![CDATA["煤气报警器"]]></MName>
\t\t<MainGUID>03DA4AE3-17FB-4440-8BBB-AF6273503226</MainGUID>
\t</Macro>
\t<Macro>
\t\t<MName><![CDATA["煤气报警器立面"]]></MName>
\t\t<MainGUID>6848A682-0E9A-4F4B-B991-34E2CD8EC15C</MainGUID>
\t</Macro>
</CalledMacros>
'''

LIBPARTDOCS_XML = '''<?xml version="1.0" encoding="UTF-8"?>
<libpartdocs>
\t<Copyright>
\t\t<Author>Some Vendor</Author>
\t\t<License>
\t\t\t<Type>CC0</Type>
\t\t\t<Version>1.0</Version>
\t\t</License>
\t</Copyright>
</libpartdocs>
'''

LIBPARTDATA_XML = '''<?xml version="1.0" encoding="UTF-8"?>
<LibpartData Owner="1196638531" Signature="1196644685" Version="46">
\t<Identification>
\t\t<MainGUID>C17731C0-49F9-41CD-8E92-596661842A64</MainGUID>
\t\t<IsPlaceable>false</IsPlaceable>
\t\t<IsArchivable>true</IsArchivable>
\t\t<MigrationValue>Normal</MigrationValue>
\t\t<IsTemplate>false</IsTemplate>
\t</Identification>
</LibpartData>
'''

ANCESTRY_XML = '''<?xml version="1.0" encoding="UTF-8"?>
<Ancestry>
\t<MainGUID>AAAAAAA1-0000-0000-0000-000000000001</MainGUID>
\t<MainGUID>AAAAAAA2-0000-0000-0000-000000000002</MainGUID>
\t<MainGUID>AAAAAAA3-0000-0000-0000-000000000003</MainGUID>
</Ancestry>
'''

PARAMLIST_XML = '''<?xml version="1.0" encoding="UTF-8"?>
<ParamSection>
\t<Parameters SectVersion="27" SectionFlags="0" SubIdent="0">
\t\t<Length Name="A">
\t\t\t<Description><![CDATA["Width"]]></Description>
\t\t\t<Fix/>
\t\t\t<Value>1.5</Value>
\t\t</Length>
\t</Parameters>
</ParamSection>
'''


def _write_macro_hsf(root: Path) -> None:
    """构造一个模拟 LP_XMLConverter libpart2hsf 输出的 HSF 目录。"""
    (root / "scripts").mkdir(parents=True)
    (root / "libpartdata.xml").write_text(LIBPARTDATA_XML, encoding="utf-8")
    (root / "paramlist.xml").write_text(PARAMLIST_XML, encoding="utf-8")
    (root / "ancestry.xml").write_text(ANCESTRY_XML, encoding="utf-8")
    (root / "calledmacros.xml").write_text(CALLEDMACROS_XML, encoding="utf-8")
    (root / "libpartdocs.xml").write_text(LIBPARTDOCS_XML, encoding="utf-8")
    (root / "scripts" / "3d.gdl").write_text(
        "CALL '煤气报警器立面' PARAMETERS _A=_A\n", encoding="utf-8"
    )


def test_calledmacros_survive_load_save(tmp_path):
    root = tmp_path / "Panel"
    _write_macro_hsf(root)

    project = HSFProject.load_from_disk(str(root))
    assert project.called_macros == [
        ("煤气报警器", "03DA4AE3-17FB-4440-8BBB-AF6273503226"),
        ("煤气报警器立面", "6848A682-0E9A-4F4B-B991-34E2CD8EC15C"),
    ]
    assert project.called_macro_guid_map()["煤气报警器立面"] == (
        "6848A682-0E9A-4F4B-B991-34E2CD8EC15C"
    )

    project.save_to_disk()
    reloaded = HSFProject.load_from_disk(str(root))
    assert reloaded.called_macros == project.called_macros


def test_ancestry_full_guid_chain_survives(tmp_path):
    root = tmp_path / "Panel"
    _write_macro_hsf(root)

    project = HSFProject.load_from_disk(str(root))
    assert project.subtype_guid == "AAAAAAA1-0000-0000-0000-000000000001"
    assert project.subtype_guids == [
        "AAAAAAA1-0000-0000-0000-000000000001",
        "AAAAAAA2-0000-0000-0000-000000000002",
        "AAAAAAA3-0000-0000-0000-000000000003",
    ]

    project.save_to_disk()
    reloaded = HSFProject.load_from_disk(str(root))
    assert reloaded.subtype_guids == project.subtype_guids


def test_identification_flags_survive(tmp_path):
    root = tmp_path / "Panel"
    _write_macro_hsf(root)

    project = HSFProject.load_from_disk(str(root))
    assert project.is_placeable is False
    assert project.is_archivable is True

    project.save_to_disk()
    reloaded = HSFProject.load_from_disk(str(root))
    assert reloaded.is_placeable is False
    assert reloaded.is_archivable is True


def test_libpartdocs_raw_passthrough(tmp_path):
    root = tmp_path / "Panel"
    _write_macro_hsf(root)

    project = HSFProject.load_from_disk(str(root))
    project.save_to_disk()
    text = (root / "libpartdocs.xml").read_text(encoding="utf-8-sig")
    assert "CC0" in text
    assert "Some Vendor" in text
    assert "CC BY" not in text  # 不得被默认模板覆盖


def test_normalize_after_import_lossless_with_calledmacros(tmp_path):
    root = tmp_path / "Panel"
    _write_macro_hsf(root)

    result = normalize_project_after_import(root)
    assert result["ok"] is True
    assert result["lossless"] is True

    reloaded = HSFProject.load_from_disk(str(root))
    assert len(reloaded.called_macros) == 2
    assert reloaded.subtype_guids == [
        "AAAAAAA1-0000-0000-0000-000000000001",
        "AAAAAAA2-0000-0000-0000-000000000002",
        "AAAAAAA3-0000-0000-0000-000000000003",
    ]


def test_create_new_writes_empty_calledmacros(tmp_path):
    """新建对象（无宏）行为不变：空 CalledMacros + 默认两 GUID ancestry。"""
    project = HSFProject.create_new("NewObj", work_dir=str(tmp_path))
    project.save_to_disk()

    text = (tmp_path / "NewObj" / "calledmacros.xml").read_text(encoding="utf-8-sig")
    assert "<CalledMacros>" in text
    assert "<Macro>" not in text

    reloaded = HSFProject.load_from_disk(str(tmp_path / "NewObj"))
    assert reloaded.called_macros == []
    assert reloaded.is_placeable is True
