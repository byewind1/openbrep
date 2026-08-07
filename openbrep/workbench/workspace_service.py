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
TRASH_DIR = "trash"  # 回收站：.openbrep/trash/（删除 = 移动，可恢复）

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


_WORKSPACE_AGENTS_MD = """# 工作区说明（由 OpenBrep 管理，可编辑）

> 本工作区由 OpenBrep 管理。此文件是给 AI 助手/协作者的工作区自描述骨架，
> 已存在的内容不会被覆盖；可自由编辑补充。

## 业务背景
<!-- 记录：这个工作区服务哪个客户/业务线？典型构件是什么？ -->

## 构件清单
<!-- 记录：hsf/ 下有哪些构件、各自用途。也可用 workspace scan 自动索引。 -->

## 命名约定
<!-- 记录：参数命名（长度前缀/单位）、文件命名、版本规范。 -->

## 禁忌
<!-- 记录：不允许做的事（如不改动 sources/ 原件、不删除某些文件）。 -->
"""

_PROJECT_AGENTS_MD = """# {project_name} 构件说明（由 OpenBrep 管理，可编辑）

> 本文件由 OpenBrep 在导入时生成，已存在的内容不会被覆盖；可自由编辑补充。

## 构件规格
<!-- 记录：用途、几何组成、适用场景。 -->

## 参数语义
<!-- 记录：每个参数的含义/单位/取值范围（A/B/ZZYZX 为 ArchiCAD 保留尺寸参数）。 -->

## 当前状态
<!-- 记录：开发进度、已知问题、待办。 -->
"""


def _ensure_workspace_agents_md(root: Path) -> Path:
    """生成 workspace/AGENTS.md 骨架；已存在则跳过（不覆盖用户内容）。"""
    target = root / "AGENTS.md"
    if not target.exists():
        target.write_text(_WORKSPACE_AGENTS_MD.lstrip("\n"), encoding="utf-8")
    return target


def _ensure_project_agents_md(project_dir: Path) -> Path:
    """生成 hsf/<项目>/AGENTS.md 骨架；已存在则跳过（不覆盖用户内容）。"""
    target = Path(project_dir) / "AGENTS.md"
    if not target.exists():
        target.write_text(
            _PROJECT_AGENTS_MD.format(project_name=Path(project_dir).name).lstrip("\n"),
            encoding="utf-8",
        )
    return target


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
        try:
            _ensure_workspace_agents_md(root)
            (root / WORKSPACE_META_DIR / "plans").mkdir(exist_ok=True)
        except Exception:
            pass  # 自描述/plans 生成失败不阻断幂等返回
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
        _ensure_workspace_agents_md(root)
        (root / WORKSPACE_META_DIR / "plans").mkdir(exist_ok=True)
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

    # staged 清理：import_source 的 _stage_source 会把源文件复制进 hsf/ 根，
    # 导入完成后该副本不再需要（项目已在 hsf/<名>/ 下）。只删 hsf/ 下的
    # staged 文件，sources/ 归档件永不删；清理失败不阻断导入结果。
    try:
        staged = root / "hsf" / f"{archived_copy.stem}{archived_copy.suffix}"
        if staged.is_file() and staged.resolve() != archived_copy.resolve():
            staged.unlink()
    except Exception:
        pass

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

    # 项目级 AGENTS.md 自描述骨架（已存在则跳过，不覆盖用户内容）
    try:
        if project_path:
            _ensure_project_agents_md(project_path)
    except Exception:
        pass

    payload: dict[str, Any] = {
        "ok": True,
        "project_path": project_path,
        "source_path": str(archived_copy),
        "warnings": list(result.get("warnings") or []),
    }
    # gsm 分支透传 decompile/normalization（GUI 工作区导入结果需要）
    for key in ("decompile", "normalization"):
        if result.get(key) is not None:
            payload[key] = result[key]
    return payload


# ── 5. build_handoff ───────────────────────────────────────


def build_handoff(workspace_path: str, limit: int = 10) -> dict[str, Any]:
    """聚合各项目最近 revision 的 manifest，生成 .openbrep/handoff.md（全量重写）。

    - 每个 hsf/ 项目取其最新一条 revision（created_at/trigger/intent/
      user_instruction/trace_id；trace_id 为 manifest 保留位，当前 schema 无此
      字段时为 None）；
    - 按 created_at 倒序取前 limit 条；handoff.md 全量重写并标注生成时间；
    - 只读聚合，不触碰项目内容；失败条目跳过。

    纪律：自描述/交接文件生成只发生在用户工作区目录内；本函数没有任何
    benchmark/workdir 等仓库内临时目录的调用方，将来也不得添加这类调用。
    """
    try:
        root = Path(workspace_path).expanduser().resolve()
    except Exception as exc:
        return _error("workspace_error", f"无效路径: {exc}", {"path": str(workspace_path)})
    if not root.is_dir() or not _is_workspace(root):
        return _error(
            "not_a_workspace",
            f"不是已初始化的工作区（缺 {WORKSPACE_META_DIR}/{WORKSPACE_TOML}）: {workspace_path}",
            {"path": str(workspace_path)},
        )

    from openbrep.revisions import list_revisions

    entries: list[dict[str, Any]] = []
    hsf_dir = root / "hsf"
    if hsf_dir.is_dir():
        for entry in sorted(hsf_dir.iterdir()):
            if not entry.is_dir() or not is_hsf_project_dir(entry):
                continue
            try:
                revisions = list_revisions(entry)
                if not revisions:
                    continue
                latest = revisions[-1]
                entries.append({
                    "project": entry.name,
                    "revision_id": latest.revision_id,
                    "created_at": latest.created_at,
                    "trigger": latest.trigger,
                    "intent": latest.intent,
                    "user_instruction": latest.user_instruction,
                    "trace_id": None,
                })
            except Exception:
                continue

    entries.sort(key=lambda e: e.get("created_at") or "", reverse=True)
    entries = entries[:limit]

    lines = [
        "# OpenBrep 工作区交接（handoff）",
        "",
        f"> 生成时间：{datetime.now(timezone.utc).isoformat(timespec='seconds')}（UTC）",
        "> 由 workspace build_handoff 全量重写；后续可在此追加交接说明。",
        "",
    ]
    if not entries:
        lines.append("暂无带 revision 的项目。")
    else:
        lines.append("## 各项目最近版本")
        lines.append("")
        lines.append("| 项目 | revision | 触发 | 意图 | 时间 | 指令 |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for e in entries:
            instruction = str(e["user_instruction"] or "").replace("|", "\|")[:60]
            lines.append(
                f"| {e['project']} | {e['revision_id']} | {e['trigger']} | "
                f"{e['intent']} | {e['created_at']} | {instruction} |"
            )

    try:
        handoff_path = root / WORKSPACE_META_DIR / "handoff.md"
        handoff_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception as exc:
        return _error("workspace_error", f"写入 handoff.md 失败: {exc}", {"path": str(root)})

    return {
        "ok": True,
        "path": str(handoff_path),
        "entries": entries,
    }


# ── 6. 会话附着判定（纯函数，供 WorkbenchSession 使用） ─────


def workspace_root_for_project(project_dir: str | Path) -> Path | None:
    """隐式附着判定：项目父目录的父目录若是工作区（含 .openbrep/workspace.toml），
    返回工作区根；否则返回 None。只做判定，不改任何状态。"""
    try:
        project_dir = Path(project_dir).expanduser().resolve()
        grandparent = project_dir.parent.parent
        if _is_workspace(grandparent):
            return grandparent
    except Exception:
        pass
    return None


def resolve_workspace(path: str | Path) -> Path | None:
    """路径是已初始化工作区则返回其 resolve 后的根，否则 None（静默降级用）。"""
    try:
        root = Path(path).expanduser().resolve()
    except Exception:
        return None
    return root if _is_workspace(root) else None


# ── 7. trash_project（删除 = 移入回收站，P3-f） ─────────────


def trash_project(workspace_path: str | Path, project_path: str | Path) -> dict[str, Any]:
    """把工作区 hsf/ 下的项目目录移入 .openbrep/trash/（可恢复，不是 rm -rf）。

    删除语义 = 移动：hsf/<项目>/ → <workspace>/.openbrep/trash/<YYYYMMDD-HHMMSS>-<项目名>/。
    项目内 revisions/artifacts/AGENTS.md 全部随目录走；恢复 = 用户手动把目录挪回
    hsf/（v1 不做 GUI 恢复）；彻底删除 = 用户自己清空 trash（v1 不做清空）。

    安全闸（纯路径部分，会话相关校验在 workbench_api 层）：
      - no_workspace: workspace 未初始化；
      - outside_workspace: 目标不在 <workspace>/hsf/ 之内（resolve 后 prefix 判定，
        防 ../ 注入；hsf/ 本身也不算合法目标）；
      - not_found: 目录不存在。
    trash 目录不存在则自动创建；同名冲突由时间戳前缀天然避免。
    """
    try:
        root = Path(workspace_path).expanduser().resolve()
    except Exception as exc:
        return _error("no_workspace", f"无效工作区路径: {exc}", {"workspace_path": str(workspace_path)})
    if not _is_workspace(root):
        return _error(
            "no_workspace",
            f"不是已初始化的工作区（缺 {WORKSPACE_META_DIR}/{WORKSPACE_TOML}）: {workspace_path}",
            {"workspace_path": str(workspace_path)},
        )

    try:
        target = Path(project_path).expanduser().resolve()
    except Exception as exc:
        return _error("not_found", f"无效项目路径: {exc}", {"project_path": str(project_path)})
    hsf_dir = root / "hsf"
    if target == hsf_dir:
        return _error(
            "outside_workspace",
            "目标不是 hsf/ 内的项目目录",
            {"project_path": str(project_path)},
        )
    try:
        target.relative_to(hsf_dir)
    except ValueError:
        return _error(
            "outside_workspace",
            f"项目不在工作区 hsf/ 内: {project_path}",
            {"project_path": str(project_path)},
        )
    if not target.is_dir():
        return _error("not_found", f"项目目录不存在: {project_path}", {"project_path": str(project_path)})

    trash_dir = root / WORKSPACE_META_DIR / TRASH_DIR
    try:
        trash_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        trashed = trash_dir / f"{stamp}-{target.name}"
        shutil.move(str(target), str(trashed))
    except Exception as exc:
        return _error(
            "workspace_error",
            f"移入回收站失败: {exc}",
            {"project_path": str(project_path)},
        )

    return {"ok": True, "trashed_to": str(trashed)}
