"""tests/test_library_context.py — 图库上下文 + 按需转换缓存（P2）离线测试。

不依赖真实 Archicad / LP_XMLConverter：外部进程边界
（``_convert_gsm_to_hsf`` / ``_extract_container`` / ``_extract_package``）
全部 monkeypatch 替换为直接写最小 HSF / 假解包的替身。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import openbrep.library_context as lc
from openbrep.hsf_project import HSFProject

GUID_A = "AAAAAAAA-1111-2222-3333-444444444444"
GUID_B = "BBBBBBBB-1111-2222-3333-444444444444"
GUID_INNER = "CCCCCCCC-1111-2222-3333-444444444444"


def _write_fake_gsm(path: Path, payload: bytes = b"FAKE-GSM") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _write_minimal_hsf(out_dir: Path, name: str, guid: str) -> None:
    """在 out_dir 下写一个最小可加载的 HSF（复用 HSFProject 规范写器）。"""
    proj = HSFProject.create_new(name, str(out_dir))
    proj.guid = guid
    proj.called_macros = [("InnerMacro", GUID_INNER)]
    proj.save_to_disk()


def _fake_convert_factory(guid_by_gsm: dict[str, str], counter: dict):
    """_convert_gsm_to_hsf 替身：按 gsm 绝对路径查 GUID，直接写最小 HSF。"""

    def fake(gsm_path: str, out_dir: str, converter_path):
        counter["convert"] = counter.get("convert", 0) + 1
        guid = guid_by_gsm[str(Path(gsm_path).resolve())]
        _write_minimal_hsf(Path(out_dir), Path(gsm_path).stem, guid)
        return True, ""

    return fake


def _build(tmp_path: Path, roots: list[str], counter: dict | None = None):
    return lc.build_macro_resolver(
        roots,
        converter_path=None,
        cache_dir=str(tmp_path / "cache"),
        project_root=None,
        include_autodetect=False,
    )


# ── 目录 root：解析规则 ──────────────────────────────────────────────────


def test_resolve_by_name_directory_root(tmp_path, monkeypatch):
    root = tmp_path / "lib"
    gsm = _write_fake_gsm(root / "ChairMacro.gsm")
    counter: dict = {}
    monkeypatch.setattr(
        lc, "_convert_gsm_to_hsf",
        _fake_convert_factory({str(gsm.resolve()): GUID_A}, counter),
    )

    resolver = _build(tmp_path, [str(root)])
    assert resolver is not None
    result = resolver("ChairMacro", None)

    assert result.status == "resolved"
    assert result.guid == GUID_A
    assert "A" in result.parameters  # 宏默认参数透传（预览器自行归一化）
    assert "3d.gdl" in result.scripts
    assert result.called_macros == {"InnerMacro": GUID_INNER}
    assert str(root) in result.source
    assert result.message == ""


def test_missing_macro(tmp_path, monkeypatch):
    root = tmp_path / "lib"
    root.mkdir()
    monkeypatch.setattr(
        lc, "_convert_gsm_to_hsf", _fake_convert_factory({}, {}),
    )

    resolver = _build(tmp_path, [str(root)])
    result = resolver("NoSuchMacro", None)

    assert result.status == "missing"
    assert "未找到宏 'NoSuchMacro'" in result.message
    assert "1 个图库根" in result.message


def test_case_insensitive_fallback(tmp_path, monkeypatch):
    root = tmp_path / "lib"
    gsm = _write_fake_gsm(root / "ChairMacro.gsm")
    monkeypatch.setattr(
        lc, "_convert_gsm_to_hsf",
        _fake_convert_factory({str(gsm.resolve()): GUID_A}, {}),
    )

    resolver = _build(tmp_path, [str(root)])
    assert resolver("chairmacro", None).status == "resolved"
    # 精确匹配优先
    assert resolver("ChairMacro", None).guid == GUID_A


def test_ambiguous_without_guid_never_picks_first(tmp_path, monkeypatch):
    root1 = tmp_path / "lib1"
    root2 = tmp_path / "lib2"
    gsm1 = _write_fake_gsm(root1 / "ChairMacro.gsm")
    gsm2 = _write_fake_gsm(root2 / "ChairMacro.gsm")
    monkeypatch.setattr(
        lc, "_convert_gsm_to_hsf",
        _fake_convert_factory(
            {str(gsm1.resolve()): GUID_A, str(gsm2.resolve()): GUID_B}, {}
        ),
    )

    resolver = _build(tmp_path, [str(root1), str(root2)])
    result = resolver("ChairMacro", None)

    assert result.status == "ambiguous"
    assert not result.scripts  # 禁止静默选第一个
    assert str(gsm1) in result.message
    assert str(gsm2) in result.message


def test_ambiguous_guid_match_resolves(tmp_path, monkeypatch):
    root1 = tmp_path / "lib1"
    root2 = tmp_path / "lib2"
    gsm1 = _write_fake_gsm(root1 / "ChairMacro.gsm")
    gsm2 = _write_fake_gsm(root2 / "ChairMacro.gsm")
    monkeypatch.setattr(
        lc, "_convert_gsm_to_hsf",
        _fake_convert_factory(
            {str(gsm1.resolve()): GUID_A, str(gsm2.resolve()): GUID_B}, {}
        ),
    )

    resolver = _build(tmp_path, [str(root1), str(root2)])
    result = resolver("ChairMacro", GUID_B)

    assert result.status == "resolved"
    assert result.guid == GUID_B
    assert str(root2) in result.source


def test_ambiguous_guid_matches_none(tmp_path, monkeypatch):
    root1 = tmp_path / "lib1"
    root2 = tmp_path / "lib2"
    gsm1 = _write_fake_gsm(root1 / "ChairMacro.gsm")
    gsm2 = _write_fake_gsm(root2 / "ChairMacro.gsm")
    monkeypatch.setattr(
        lc, "_convert_gsm_to_hsf",
        _fake_convert_factory(
            {str(gsm1.resolve()): GUID_A, str(gsm2.resolve()): GUID_B}, {}
        ),
    )

    resolver = _build(tmp_path, [str(root1), str(root2)])
    result = resolver("ChairMacro", "DDDDDDDD-0000-0000-0000-000000000000")

    assert result.status == "ambiguous"
    assert "无法唯一区分" in result.message


def test_single_candidate_guid_mismatch_still_resolved(tmp_path, monkeypatch):
    root = tmp_path / "lib"
    gsm = _write_fake_gsm(root / "ChairMacro.gsm")
    monkeypatch.setattr(
        lc, "_convert_gsm_to_hsf",
        _fake_convert_factory({str(gsm.resolve()): GUID_A}, {}),
    )

    resolver = _build(tmp_path, [str(root)])
    wrong = "EEEEEEEE-0000-0000-0000-000000000000"
    result = resolver("ChairMacro", wrong)

    assert result.status == "resolved"
    assert result.guid == GUID_A
    assert "GUID 与 calledmacros 记录不符" in result.message
    assert wrong in result.message
    assert GUID_A in result.message


# ── build 行为 ────────────────────────────────────────────────────────────


def test_build_returns_none_without_roots(tmp_path):
    resolver = lc.build_macro_resolver(
        [], None, cache_dir=str(tmp_path / "cache"), include_autodetect=False
    )
    assert resolver is None


def test_build_skips_nonexistent_roots(tmp_path):
    resolver = lc.build_macro_resolver(
        [str(tmp_path / "不存在")],
        None,
        cache_dir=str(tmp_path / "cache"),
        include_autodetect=False,
    )
    assert resolver is None


def test_autodetect_appends_fallback_root(tmp_path, monkeypatch):
    auto_dir = tmp_path / "Archicad Library Packages"
    gsm = _write_fake_gsm(auto_dir / "AutoMacro.gsm")
    monkeypatch.setattr(
        lc, "_autodetect_library_package_dirs", lambda: [str(auto_dir)]
    )
    monkeypatch.setattr(
        lc, "_convert_gsm_to_hsf",
        _fake_convert_factory({str(gsm.resolve()): GUID_A}, {}),
    )

    resolver = lc.build_macro_resolver(
        [], None, cache_dir=str(tmp_path / "cache"), include_autodetect=True
    )
    assert resolver is not None
    assert resolver("AutoMacro", None).status == "resolved"


# ── 转换缓存 ──────────────────────────────────────────────────────────────


def test_cache_second_lookup_skips_conversion(tmp_path, monkeypatch):
    root = tmp_path / "lib"
    gsm = _write_fake_gsm(root / "ChairMacro.gsm")
    counter: dict = {}
    monkeypatch.setattr(
        lc, "_convert_gsm_to_hsf",
        _fake_convert_factory({str(gsm.resolve()): GUID_A}, counter),
    )

    resolver = _build(tmp_path, [str(root)])
    first = resolver("ChairMacro", None)
    second = resolver("ChairMacro", None)

    assert first.status == second.status == "resolved"
    assert counter["convert"] == 1  # 第二次命中缓存，不再转换


def test_cache_shared_across_resolver_instances(tmp_path, monkeypatch):
    root = tmp_path / "lib"
    gsm = _write_fake_gsm(root / "ChairMacro.gsm")
    counter: dict = {}
    monkeypatch.setattr(
        lc, "_convert_gsm_to_hsf",
        _fake_convert_factory({str(gsm.resolve()): GUID_A}, counter),
    )

    assert _build(tmp_path, [str(root)])("ChairMacro", None).status == "resolved"
    # 新 resolver 实例（索引重建）仍命中同一个内容寻址缓存
    assert _build(tmp_path, [str(root)])("ChairMacro", None).status == "resolved"
    assert counter["convert"] == 1


def test_source_mtime_change_invalidates_cache(tmp_path, monkeypatch):
    root = tmp_path / "lib"
    gsm = _write_fake_gsm(root / "ChairMacro.gsm")
    counter: dict = {}
    monkeypatch.setattr(
        lc, "_convert_gsm_to_hsf",
        _fake_convert_factory({str(gsm.resolve()): GUID_A}, counter),
    )

    resolver = _build(tmp_path, [str(root)])
    assert resolver("ChairMacro", None).status == "resolved"
    assert counter["convert"] == 1

    # 源文件 mtime 变化 → 指纹变 → 重新转换
    stat = gsm.stat()
    os.utime(gsm, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    assert resolver("ChairMacro", None).status == "resolved"
    assert counter["convert"] == 2


def test_conversion_error_cached_and_password_hint(tmp_path, monkeypatch):
    root = tmp_path / "lib"
    _write_fake_gsm(root / "Locked.gsm")
    counter: dict = {}

    def failing(gsm_path, out_dir, converter_path):
        counter["convert"] = counter.get("convert", 0) + 1
        return False, "LP_XMLConverter libpart2hsf failed: password protected file"

    monkeypatch.setattr(lc, "_convert_gsm_to_hsf", failing)

    resolver = _build(tmp_path, [str(root)])
    first = resolver("Locked", None)
    assert first.status == "error"
    assert "可能受密码保护" in first.message

    # 失败结果也缓存（内存级）：第二次不再调用转换器
    second = resolver("Locked", None)
    assert second.status == "error"
    assert counter["convert"] == 1


def test_conversion_failure_does_not_poison_disk_cache(tmp_path, monkeypatch):
    """回归：失败结果只内存缓存——新 resolver 实例（模拟下次预览/重启）
    必须重新尝试转换，而不是被磁盘上的失败记录永久跳过（WL-AC图库.lcf
    首次 extractcontainer exit=1 后一直被跳过的实测事故）。"""
    root = tmp_path / "lib"
    _write_fake_gsm(root / "FlakyMacro.gsm")
    counter: dict = {}

    def failing(gsm_path, out_dir, converter_path):
        counter["convert"] = counter.get("convert", 0) + 1
        return False, "transient failure"

    monkeypatch.setattr(lc, "_convert_gsm_to_hsf", failing)
    resolver = _build(tmp_path, [str(root)])
    assert resolver("FlakyMacro", None).status == "error"
    assert counter["convert"] == 1

    # 新实例（同一缓存目录）：失败没落盘 → 重新尝试；这次成功
    monkeypatch.setattr(
        lc,
        "_convert_gsm_to_hsf",
        _fake_convert_factory({str((root / "FlakyMacro.gsm").resolve()): GUID_A}, counter),
    )
    fresh = _build(tmp_path, [str(root)])
    result = fresh("FlakyMacro", None)
    assert result.status == "resolved"
    assert counter["convert"] == 2


def test_converter_unavailable_reports_error(tmp_path):
    root = tmp_path / "lib"
    _write_fake_gsm(root / "ChairMacro.gsm")

    # 不 monkeypatch：真实 _convert_gsm_to_hsf + converter_path=None
    resolver = _build(tmp_path, [str(root)])
    result = resolver("ChairMacro", None)

    assert result.status == "error"
    assert "LP_XMLConverter 不可用" in result.message


# ── 容器 root（LCF / libpack） ────────────────────────────────────────────


def test_lcf_root_extracts_then_indexes(tmp_path, monkeypatch):
    root = tmp_path / "lib"
    gsm = _write_fake_gsm(root / "PackagedMacro.gsm")
    lcf = _write_fake_gsm(tmp_path / "real_library.lcf", b"FAKE-LCF")
    calls: dict = {}

    def fake_extract(lcf_path, out_dir, converter_path):
        calls["extract"] = calls.get("extract", 0) + 1
        # 假解包：把 root 目录的 gsm 复制进解包目标
        import shutil
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        shutil.copy2(gsm, Path(out_dir) / gsm.name)
        return True, ""

    monkeypatch.setattr(lc, "_extract_container", fake_extract)

    # gsm 被假解包复制进缓存目录，转换替身按文件名接受任意路径
    counter: dict = {}

    def fake_convert_any(gsm_path, out_dir, converter_path):
        counter["convert"] = counter.get("convert", 0) + 1
        _write_minimal_hsf(Path(out_dir), Path(gsm_path).stem, GUID_A)
        return True, ""

    monkeypatch.setattr(lc, "_convert_gsm_to_hsf", fake_convert_any)

    resolver = _build(tmp_path, [str(lcf)])
    result = resolver("PackagedMacro", None)

    assert result.status == "resolved"
    assert result.guid == GUID_A
    assert "(LCF)" in result.source
    assert calls["extract"] == 1

    # 新 resolver 实例：容器解包与 gsm 转换全部命中缓存
    resolver2 = _build(tmp_path, [str(lcf)])
    assert resolver2("PackagedMacro", None).status == "resolved"
    assert calls["extract"] == 1
    assert counter["convert"] == 1


def test_lcf_root_without_converter_marks_error(tmp_path):
    lcf = _write_fake_gsm(tmp_path / "real_library.lcf", b"FAKE-LCF")

    # 不 monkeypatch：真实 _extract_container + converter_path=None
    resolver = _build(tmp_path, [str(lcf)])
    result = resolver("AnyMacro", None)

    assert result.status == "missing"
    assert "未找到宏" in result.message
    assert "图库根诊断" in result.message
    assert "LP_XMLConverter 不可用" in result.message


def test_libpack_root_extracts_package_and_inner_lcfs(tmp_path, monkeypatch):
    pack = _write_fake_gsm(tmp_path / "Archicad Library.libpack", b"FAKE-LIBPACK")
    calls: dict = {"package": 0, "container": 0}

    def fake_package(pack_path, out_dir, converter_path):
        calls["package"] += 1
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "inner.lcf").write_bytes(b"FAKE-INNER-LCF")
        return True, ""

    def fake_container(lcf_path, out_dir, converter_path):
        calls["container"] += 1
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "PackMacro.gsm").write_bytes(b"FAKE-GSM")
        return True, ""

    def fake_convert_any(gsm_path, out_dir, converter_path):
        _write_minimal_hsf(Path(out_dir), Path(gsm_path).stem, GUID_B)
        return True, ""

    monkeypatch.setattr(lc, "_extract_package", fake_package)
    monkeypatch.setattr(lc, "_extract_container", fake_container)
    monkeypatch.setattr(lc, "_convert_gsm_to_hsf", fake_convert_any)

    resolver = _build(tmp_path, [str(pack)])
    result = resolver("PackMacro", None)

    assert result.status == "resolved"
    assert result.guid == GUID_B
    assert "(LIBPACK)" in result.source
    assert calls == {"package": 1, "container": 1}

    # 缓存命中：新实例不再解包
    resolver2 = _build(tmp_path, [str(pack)])
    assert resolver2("PackMacro", None).status == "resolved"
    assert calls == {"package": 1, "container": 1}


# ── 项目依赖清单 ──────────────────────────────────────────────────────────


def test_manifest_flush(tmp_path, monkeypatch):
    root = tmp_path / "lib"
    gsm = _write_fake_gsm(root / "ChairMacro.gsm")
    monkeypatch.setattr(
        lc, "_convert_gsm_to_hsf",
        _fake_convert_factory({str(gsm.resolve()): GUID_A}, {}),
    )

    project_root = tmp_path / "project"
    project_root.mkdir()
    resolver = lc.build_macro_resolver(
        [str(root)],
        None,
        cache_dir=str(tmp_path / "cache"),
        project_root=str(project_root),
        include_autodetect=False,
    )
    assert resolver is not None
    resolver("ChairMacro", GUID_A)      # resolved
    resolver("MissingMacro", None)      # missing

    written = resolver.flush_manifest()
    assert written == project_root / ".openbrep" / "dependencies" / "library-parts.json"
    payload = json.loads(written.read_text(encoding="utf-8"))

    assert payload["version"] == 1
    assert payload["updated_at"]
    entries = {e["name"]: e for e in payload["entries"]}
    assert entries["ChairMacro"]["status"] == "resolved"
    assert entries["ChairMacro"]["guid"] == GUID_A
    assert entries["ChairMacro"]["requested_guid"] == GUID_A
    assert entries["ChairMacro"]["source"]
    assert entries["MissingMacro"]["status"] == "missing"
    assert entries["MissingMacro"]["guid"] == ""


def test_manifest_flush_without_project_root(tmp_path, monkeypatch):
    root = tmp_path / "lib"
    gsm = _write_fake_gsm(root / "ChairMacro.gsm")
    monkeypatch.setattr(
        lc, "_convert_gsm_to_hsf",
        _fake_convert_factory({str(gsm.resolve()): GUID_A}, {}),
    )

    resolver = _build(tmp_path, [str(root)])
    resolver("ChairMacro", None)
    assert resolver.flush_manifest() is None
    assert not list((tmp_path / "cache").rglob("library-parts.json"))
