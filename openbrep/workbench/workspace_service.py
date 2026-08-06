"""工作区层（Workspace Service）：四区目录约定 + 索引/搜索/导入。

一个工作区目录 = 一个客户/一条业务线。四区布局（设计定稿，勿自行发挥）::

    workspace/
      materials/    资料区：PDF/图片/文档（只读参考，不解析）
      sources/      素材区：原始 .gsm/.glb/.txt/.gdl（不可变原始件）
      hsf/          HSF 代码库：每对象一个子目录（现有 HSF 项目模型不变）
      artifacts/    工作区级成品视图（可选；项目级 artifacts/ 已存在，不重复存储）
      .openbrep/    工作区级元数据：workspace.toml（索引与配置）

风格约定：全部函数 dict in/out、统一错误形态、不抛异常（与 mcp_tools 同风格）。
错误形态: {"ok": False, "error": {"code": str, "message": str, "details": dict}}。
"""

from __future__ import annotations

import shutil
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openbrep.hsf_project import HSFProject
from openbrep.project_context import load_project_origin
from openbrep.revisions import get_latest_revision_id, is_hsf_project_dir, list_archived_artifacts

WORKSPACE_SCHEMA_VERSION = 1
WORKSPACE_ZONES = ("materials", "sources", "hsf", "artifacts")
WORKSPACE_META_DIR = ".openbrep"
WORKSPACE_TOML = "workspace.toml"

ZONE_DESCRIPTIONS = {
    "materials": "资料区：PDF/图片/文档（只读参考，不解析）",
    "sources": "素材区：原始 .gsm/.glb/.txt/.gdl（不可变原始件）",
    "hsf": "HSF 代码库：每对象一个子目录",
    "artifacts": "工作区级成品视图（可选；项目级 artifacts/ 已存在，不重复存储）",
}

IMPORT_KINDS = ("gdl", "gsm", "blender_py")
KIND_BY_SUFFIX = {
    ".gdl": "gdl",
    ".gsm": "gsm",
    ".glb": "glb",
    ".txt": "txt",
    ".py": "blender_py",
}


def _error(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message, "details": details or {}}}


def _workspace_toml_path(root: Path) -> Path:
    return root / WORKSPACE_META_DIR / WORKSPACE_TOML


def _is_workspace(root: Path) -> bool:
    return _workspace_toml_path(root).is_file()


def _unexpected_entries(root: Path) -> list[str]:
    """顶层既不属于四区、也不属于 .openbrep 的条目（冲突项）。"""
    allowed = set(WORKSPACE_ZONES) | {WORKSPACE_META_DIR}
    try:
        entries = sorted(e.name for e in root.iterdir() if e.name not in allowed)
    except OSError:
        return []
    return entries


def _write_workspace_toml(root: Path) -> Path:
    toml_path = _workspace_toml_path(root)
    toml_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# OpenBrep 工作区元数据（四区目录约定，见 WORKSPACE_ZONES）",
        "[workspace]",
        f"schema = {WORKSPACE_SCHEMA_VERSION}",
        'created_at = "%s"' % datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "",
        "[zones]",
    ]
    for zone in WORKSPACE_ZONES:
        lines.append(f'{zone} = "{ZONE_DESCRIPTIONS[zone]}"')
    toml_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return toml_path


# ── 1. init_workspace ──────────────────────────────────────


def init_workspace(path: str) -> dict[str, Any]:
    """创建四区 + .openbrep/workspace.toml；已存在则校验结构幂等返回。

    - 已是工作区（workspace.toml 存在）：校验四区，缺区/冲突项只报告不报错；
    - 首次初始化：路径含非工作区内容时不炸，报告冲突项并继续创建四区。
    """
    try:
        root = Path(path).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return _error("workspace_error", f"无法创建工作区目录: {exc}", {"path": str(path)})

    conflicts = _unexpected_entries(root)

    if _is_workspace(root):
        missing_zones = [z for z in WORKSPACE_ZONES if not (root / z).is_dir()]
        return {
            "ok": True,
            "initialized": True,
            "idempotent": True,
            "zones": list(WORKSPACE_ZONES),
            "missing_zones": missing_zones,
            "conflicts": conflicts,
            "workspace_toml": str(_workspace_toml_path(root)),
        }

    try:
        for zone in WORKSPACE_ZONES:
            (root / zone).mkdir(exist_ok=True)
        toml_path = _write_workspace_toml(root)
    except Exception as exc:
        return _error("workspace_error", f"初始化失败: {exc}", {"path": str(path)})

    return {
        "ok": True,
        "initialized": True,
        "idempotent": False,
        "zones": list(WORKSPACE_ZONES),
        "missing_zones": [],
        "conflicts": conflicts,
        "workspace_toml": str(toml_path),
    }


# ── 2. scan_workspace ──────────────────────────────────────


def _project_scan_entry(root: Path, project_dir: Path) -> dict[str, Any] | None:
    """单个 HSF 项目的索引条目；加载失败返回 None（调用方记入 warnings）。"""
    try:
        project = HSFProject.load_from_disk(str(project_dir))
    except Exception:
        return None
    scripts_present = [st.name for st, content in project.scripts.items() if content.strip()]
    try:
        artifacts = list_archived_artifacts(project_dir)
    except Exception:
        artifacts = []
    return {
        "name": project.name,
        "path": str(project_dir),
        "parameter_count": len(project.parameters),
        "scripts_present": scripts_present,
        "latest_revision_id": get_latest_revision_id(project_dir),
        "origin": load_project_origin(project_dir),
        "artifact_count": len(artifacts),
    }


def scan_workspace(path: str) -> dict[str, Any]:
    """工作区索引：projects / sources / materials / zones 完整性。"""
    try:
        root = Path(path).expanduser().resolve()
    except Exception as exc:
        return _error("workspace_error", f"无效路径: {exc}", {"path": str(path)})
    if not root.is_dir() or not _is_workspace(root):
        return _error(
            "not_a_workspace",
            f"不是已初始化的工作区（缺 {WORKSPACE_META_DIR}/{WORKSPACE_TOML}）: {path}",
            {"path": str(path)},
        )

    # zones 完整性
    zones = {
        zone: {"exists": (root / zone).is_dir()}
        for zone in WORKSPACE_ZONES
    }
    missing_zones = [zone for zone, info in zones.items() if not info["exists"]]

    # projects：hsf/ 下每个合法 HSF 项目目录
    projects: list[dict[str, Any]] = []
    project_warnings: list[str] = []
    hsf_dir = root / "hsf"
    if hsf_dir.is_dir():
        for entry in sorted(hsf_dir.iterdir()):
            if not entry.is_dir() or not is_hsf_project_dir(entry):
                continue
            item = _project_scan_entry(root, entry)
            if item is None:
                project_warnings.append(f"hsf/{entry.name}：HSF 项目加载失败，已跳过")
            else:
                projects.append(item)
    projects.sort(key=lambda p: p["name"].lower())

    # sources：文件清单 + kind 推断
    sources: list[dict[str, Any]] = []
    sources_dir = root / "sources"
    if sources_dir.is_dir():
        for entry in sorted(sources_dir.iterdir()):
            if not entry.is_file():
                continue
            sources.append({
                "name": entry.name,
                "path": str(entry),
                "size_bytes": entry.stat().st_size,
                "kind": KIND_BY_SUFFIX.get(entry.suffix.lower(), "other"),
            })

    # materials：计数（递归）
    materials_count = 0
    materials_dir = root / "materials"
    if materials_dir.is_dir():
        materials_count = sum(1 for _ in materials_dir.rglob("*") if _.is_file())

    return {
        "ok": True,
        "path": str(root),
        "zones": zones,
        "missing_zones": missing_zones,
        "projects": projects,
        "project_count": len(projects),
        "sources": sources,
        "source_count": len(sources),
        "materials_count": materials_count,
        "warnings": project_warnings,
    }


# ── 3. search_workspace ────────────────────────────────────


def search_workspace(path: str, query: str) -> dict[str, Any]:
    """跨项目搜索：项目名/参数名/脚本内容，大小写不敏感子串。

    命中条目：{project, location, line, snippet}。纯遍历，不做索引。
    """
    query_text = (query or "").strip()
    if not query_text:
        return _error("invalid_query", "query 不能为空", {"query": query})
    try:
        root = Path(path).expanduser().resolve()
    except Exception as exc:
        return _error("workspace_error", f"无效路径: {exc}", {"path": str(path)})
    if not root.is_dir() or not _is_workspace(root):
        return _error(
            "not_a_workspace",
            f"不是已初始化的工作区（缺 {WORKSPACE_META_DIR}/{WORKSPACE_TOML}）: {path}",
            {"path": str(path)},
        )

    needle = query_text.lower()
    hits: list[dict[str, Any]] = []
    hsf_dir = root / "hsf"

    def add(project_name: str, location: str, line: int | None, snippet: str) -> None:
        hits.append({
            "project": project_name,
            "location": location,
            "line": line,
            "snippet": snippet,
        })

    if hsf_dir.is_dir():
        for entry in sorted(hsf_dir.iterdir()):
            if not entry.is_dir() or not is_hsf_project_dir(entry):
                continue
            project_name = entry.name
            # 项目名命中
            if needle in project_name.lower():
                add(project_name, "name", None, entry.name)
            # 参数名命中
            try:
                project = HSFProject.load_from_disk(str(entry))
            except Exception:
                continue
            for param in project.parameters:
                if needle in param.name.lower():
                    add(project_name, "paramlist.xml", None, param.name)
            # 脚本内容命中（带行号）
            for st, content in project.scripts.items():
                if not content.strip():
                    continue
                for line_no, line in enumerate(content.splitlines(), start=1):
                    if needle in line.lower():
                        add(project_name, f"scripts/{st.value}", line_no, line.strip()[:200])

    return {
        "ok": True,
        "path": str(root),
        "query": query_text,
        "hits": hits,
        "hit_count": len(hits),
    }


# ── 4. import_to_workspace ─────────────────────────────────


def _copy_to_sources(root: Path, source_path: Path) -> Path:
    """把源文件拷入 sources/（不可变原件保留）；同名已存在且字节相同则复用，
    否则追加短数字后缀，绝不覆盖已有原件。"""
    sources_dir = root / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    target = sources_dir / source_path.name
    if target.exists():
        if target.read_bytes() == source_path.read_bytes():
            return target
        stem, suffix = source_path.stem, source_path.suffix
        counter = 1
        while (sources_dir / f"{stem}-{counter}{suffix}").exists():
            counter += 1
        target = sources_dir / f"{stem}-{counter}{suffix}"
    shutil.copy2(source_path, target)
    return target


def import_to_workspace(path: str, source_path: str, kind: str) -> dict[str, Any]:
    """把外部素材导入工作区：原件先进 sources/，再从 sources/ 副本走现有
    import_source 逻辑把项目建到 hsf/ 下；origin 的 imported_from 指向
    sources/ 内的归档副本（任务 L 的 [origin] 机制复用）。
    """
    if kind not in IMPORT_KINDS:
        return _error(
            "invalid_mode",
            f"kind 必须是 gdl/gsm/blender_py，收到: {kind!r}",
            {"kind": kind},
        )
    try:
        root = Path(path).expanduser().resolve()
    except Exception as exc:
        return _error("workspace_error", f"无效路径: {exc}", {"path": str(path)})
    if not root.is_dir() or not _is_workspace(root):
        return _error(
            "not_a_workspace",
            f"不是已初始化的工作区（缺 {WORKSPACE_META_DIR}/{WORKSPACE_TOML}）: {path}",
            {"path": str(path)},
        )

    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        return _error("source_not_found", f"源文件不存在: {source_path}", {"path": source_path})
    expected_suffix = {"gdl": ".gdl", "gsm": ".gsm", "blender_py": ".py"}[kind]
    if source.suffix.lower() != expected_suffix:
        return _error(
            "invalid_mode",
            f"kind={kind} 期望 {expected_suffix} 文件，收到: {source.suffix or '(无后缀)'}",
            {"path": str(source), "kind": kind},
        )

    try:
        archived_copy = _copy_to_sources(root, source)
    except Exception as exc:
        return _error("workspace_error", f"复制进 sources/ 失败: {exc}", {"path": str(source)})

    # 走现有 import_source 逻辑（懒加载，避免 service → mcp_tools 顶层循环依赖）
    try:
        from openbrep.mcp_tools import import_source

        result = import_source(str(archived_copy), kind, str(root / "hsf"))
    except Exception as exc:
        return _error("mcp_internal_error", f"导入失败: {exc}", {"path": str(source)})
    if not result.get("ok"):
        error = result.get("error") or {}
        return _error(
            str(error.get("code") or "import_failed"),
            str(error.get("message") or "导入失败"),
            {"path": str(source), "kind": kind},
        )

    # origin 指向 sources/ 内的归档副本（覆盖 import_source 写入的 staged 路径）
    project_path = result.get("project_path")
    try:
        from openbrep.workbench.project_session_service import write_project_origin

        if project_path:
            write_project_origin(
                project_path,
                imported_from=str(archived_copy),
                imported_kind=kind,
            )
    except Exception:
        pass  # origin 写入失败不阻断导入结果

    return {
        "ok": True,
        "project_path": project_path,
        "source_path": str(archived_copy),
        "warnings": list(result.get("warnings") or []),
    }
