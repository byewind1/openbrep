from __future__ import annotations

from typing import Any

from openbrep.gdl_previewer import preview_2d_script, preview_3d_script
from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.materials import load_materials
from openbrep.workbench.project_parameter_service import parameter_values
from openbrep.workbench.three_preview import preview_3d_to_three_payload


class WorkbenchPreviewService:
    def __init__(self, session: Any) -> None:
        self.session = session

    def _macro_resolver(self) -> Any:
        """按当前配置构建 CALL 宏解析器（GSM-CALL 研究 2026-09-12 P2）。

        图库根来自 config.toml [library] roots（不存在自动写入，纯手改配置）；
        转换器用会话的 LP_XMLConverter 路径。无可用图库根时返回 None——
        预览器据此发 MACRO_NO_RESOLVER（"未配置图库上下文"）诊断。
        """
        from openbrep.library_context import build_macro_resolver

        config = getattr(self.session, "config", None)
        library = getattr(config, "library", None)
        roots = list(getattr(library, "roots", None) or [])
        cache_dir = str(getattr(library, "cache_dir", "") or "") or None
        converter_path = str(getattr(self.session, "converter_path", "") or "") or None
        project_root = None
        project = getattr(self.session, "project", None)
        if project is not None and getattr(project, "root", None):
            project_root = str(project.root)
        return build_macro_resolver(
            roots,
            converter_path,
            cache_dir=cache_dir,
            project_root=project_root,
        )

    def preview(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.session.project is None:
            return {"ok": True, "preview": empty_preview_payload()}
        parameters, scripts, quality = split_preview_request(request)
        resolver = self._macro_resolver()
        payload = preview_payload(
            self.session.project, parameters, scripts, quality=quality,
            macro_resolver=resolver,
        )
        _flush_macro_manifest(resolver)
        return {"ok": True, "preview": payload}

    def preview_2d(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.session.project is None:
            return {"ok": True, "preview": empty_preview_2d_payload()}
        parameters, scripts, quality = split_preview_request(request)
        resolver = self._macro_resolver()
        payload = preview_2d_payload(
            self.session.project, parameters, scripts, quality=quality,
            macro_resolver=resolver,
        )
        _flush_macro_manifest(resolver)
        return {"ok": True, "preview": payload}


def _flush_macro_manifest(resolver: Any) -> None:
    """预览后 best-effort 落盘项目宏依赖清单（失败不影响预览）。"""
    flush = getattr(resolver, "flush_manifest", None)
    if callable(flush):
        try:
            flush()
        except Exception:  # noqa: BLE001 — 清单落盘绝不影响预览
            pass


def normalize_quality(quality: Any) -> str:
    """预览质量档白名单：fast/accurate，非法值回退 fast（P1b）。"""
    return quality if isinstance(quality, str) and quality in {"fast", "accurate"} else "fast"


def split_preview_request(
    request: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, str], str]:
    if not request:
        return {}, {}, "fast"
    if "parameters" in request or "scripts" in request or "quality" in request:
        parameters = request.get("parameters") if isinstance(request.get("parameters"), dict) else {}
        scripts = request.get("scripts") if isinstance(request.get("scripts"), dict) else {}
        return parameters, normalize_script_overrides(scripts), normalize_quality(request.get("quality"))
    return request, {}, "fast"


def normalize_script_overrides(scripts: dict[Any, Any]) -> dict[str, str]:
    valid_names = {script_type.value for script_type in ScriptType}
    return {
        str(name): str(content)
        for name, content in scripts.items()
        if str(name) in valid_names and isinstance(content, str)
    }


def preview_payload(
    project: HSFProject,
    overrides: dict[str, Any] | None = None,
    script_overrides: dict[str, str] | None = None,
    quality: str = "fast",
    macro_resolver: Any = None,
) -> dict[str, Any]:
    scripts = script_overrides or {}
    script_3d = script_for(project, ScriptType.SCRIPT_3D, scripts)
    result = preview_3d_script(
        script_3d,
        parameters=parameter_values(project, overrides),
        setup_script=script_for(project, ScriptType.MASTER, scripts),
        unknown_command_policy="warn",
        quality=normalize_quality(quality),
        macro_resolver=macro_resolver,
        macro_guid_map=project.called_macro_guid_map(),
    )
    payload = preview_3d_to_three_payload(result)
    document, material_warnings = load_materials(project.root)
    payload["materials"] = {**document["slots"], **result.materials}
    payload["warnings"] = [*result.warnings, *material_warnings]
    known = {key.casefold() for key in payload["materials"]}
    unresolved = sum(not mesh.material_id or mesh.material_id.casefold() not in known for mesh in result.meshes)
    payload["material_check"] = {
        "status": "unresolved" if unresolved else ("resolved" if result.meshes else "empty"),
        "unresolved_meshes": unresolved,
        "total_meshes": len(result.meshes),
        "visual_verified": False,
    }
    from openbrep.runtime.visual_self_check import check_preview_visual
    payload["visual_check"] = check_preview_visual(payload)
    if unresolved and payload["materials"]:
        payload["warnings"].append(f"{unresolved} 个网格未解析材质，材质效果尚未验证")
    if not result.meshes and not result.wires and not _has_executable_statement(script_3d):
        # P14：空 3D 脚本 / "! Hidden Script."（加密保护构件）原本静默空白，
        # 显式告知原因
        payload["warnings"] = [
            *result.warnings,
            "3D 脚本为空或为隐藏（受保护）脚本，无可预览几何",
        ]
    payload["verification"] = preview_verification(scripts)
    # 自描述质量档：前端据此发现"显示中的预览"与所选质量档不一致并自动重取
    payload["quality"] = normalize_quality(quality)
    return payload


def _has_executable_statement(script: str) -> bool:
    """脚本是否含有可执行语句（非空行、非 ! 注释行）。"""
    for raw in (script or "").splitlines():
        line = raw.strip()
        if line and not line.startswith("!"):
            return True
    return False


def empty_preview_payload() -> dict[str, Any]:
    return {
        "meshes": [],
        "wires": [],
        "warnings": [],
        "verification": preview_verification({}),
        "quality": "fast",
    }


def authoritative_preview_payload(
    project: HSFProject,
    overrides: dict[str, Any] | None,
    tapir_adapter: Any,
) -> dict[str, Any]:
    """P15：Archicad 权威预览——让后台 Archicad 用真实引擎求值当前物件。

    图库物件按项目名（HSF 目录名）在当前活动图库中查找；参数取
    paramlist 当前值 + 调用方覆盖。返回与本地预览相同的 three.js payload
    形状，外加 source="archicad" 与参数应用明细。add-on 返回的 vertices/
    faces 是扁平数组，这里恢复成三元组。
    """
    if tapir_adapter is None:
        return {"ok": False, "error": "Archicad 连接不可用"}
    parameters = parameter_values(project, overrides)
    result = tapir_adapter.evaluate_library_part(
        lib_part_name=project.name,
        parameters=parameters,
        want=["mesh3d", "prims2d"],
    )
    if not result.get("ok"):
        return {"ok": False, "error": str(result.get("error") or "权威求值失败")}

    meshes: list[dict[str, Any]] = []
    for raw_mesh in result.get("meshes") or []:
        if not isinstance(raw_mesh, dict):
            continue
        flat_v = raw_mesh.get("vertices") or []
        flat_f = raw_mesh.get("faces") or []
        vertices = [list(flat_v[i:i + 3]) for i in range(0, len(flat_v) - 2, 3)]
        vertex_count = len(vertices)
        raw_faces = [list(flat_f[i:i + 3]) for i in range(0, len(flat_f) - 2, 3)]
        faces = [
            face for face in raw_faces
            if all(isinstance(index, int) and 0 <= index < vertex_count for index in face)
        ]
        item: dict[str, Any] = {
            "name": str(raw_mesh.get("name") or "body"),
            "vertices": vertices,
            "faces": faces,
        }
        color = raw_mesh.get("color")
        if isinstance(color, dict):
            item["color"] = color
        meshes.append(item)

    warnings: list[str] = []
    invalid_face_count = int(result.get("invalidFaceCount") or 0)
    if invalid_face_count:
        warnings.append(f"Archicad 返回 {invalid_face_count} 个退化子多边形，已忽略其非法三角面")

    preview2d = _authoritative_preview_2d(result.get("preview2d"), warnings)
    payload: dict[str, Any] = {
        "meshes": meshes,
        "wires": [],
        "warnings": warnings,
        "source": "archicad",
        "bounds": result.get("bounds"),
        "appliedParameters": result.get("appliedParameters") or [],
        "skippedParameters": result.get("skippedParameters") or [],
        "preview2d": preview2d,
    }
    return {"ok": True, "preview": payload}


def _authoritative_preview_2d(raw: Any, warnings: list[str]) -> dict[str, Any]:
    """Normalize the add-on's compact 2D primitive payload for Preview2DViewport."""
    if not isinstance(raw, dict):
        return empty_preview_2d_payload()
    polygons: list[list[list[float]]] = []
    polygon_fills: list[bool] = []
    for item in raw.get("polygons") or []:
        if not isinstance(item, dict):
            continue
        flat = item.get("points") or []
        polygons.append([list(flat[i:i + 2]) for i in range(0, len(flat) - 1, 2)])
        polygon_fills.append(bool(item.get("filled")))
    arcs = []
    circles = []
    for item in raw.get("arcs") or []:
        if not isinstance(item, dict):
            continue
        normalized = {key: float(item.get(key) or 0.0) for key in ("cx", "cy", "r", "a0", "a1")}
        if item.get("whole"):
            circles.append({key: normalized[key] for key in ("cx", "cy", "r")})
        else:
            arcs.append(normalized)
    unsupported = int(raw.get("unsupportedCount") or 0)
    approximated = int(raw.get("approximatedCurveCount") or 0)
    if unsupported:
        warnings.append(f"Archicad 2D 中有 {unsupported} 个当前无法表示的 primitive")
    if approximated:
        warnings.append(f"Archicad 2D 中有 {approximated} 条多段线/多边形曲线以弦线近似")
    return {
        "lines": list(raw.get("lines") or []),
        "polygons": polygons,
        "polygon_fills": polygon_fills,
        "polygon_contours": [True] * len(polygons),
        "circles": circles,
        "arcs": arcs,
        "texts": list(raw.get("texts") or []),
        "warnings": list(warnings),
    }


def preview_2d_payload(
    project: HSFProject,
    overrides: dict[str, Any] | None = None,
    script_overrides: dict[str, str] | None = None,
    quality: str = "fast",
    macro_resolver: Any = None,
) -> dict[str, Any]:
    scripts = script_overrides or {}
    result = preview_2d_script(
        script_for(project, ScriptType.SCRIPT_2D, scripts),
        parameters=parameter_values(project, overrides),
        setup_script=script_for(project, ScriptType.MASTER, scripts),
        unknown_command_policy="warn",
        quality=normalize_quality(quality),
        # P3a：PROJECT2 顶视图投影需要 3D 脚本执行结果（同一组 scripts 覆盖）
        script_3d=script_for(project, ScriptType.SCRIPT_3D, scripts),
        macro_resolver=macro_resolver,
        macro_guid_map=project.called_macro_guid_map(),
    )
    return {
        "lines": [{"from": list(p1), "to": list(p2)} for p1, p2 in result.lines],
        "polygons": [[list(point) for point in polygon] for polygon in result.polygons],
        # P4：与 polygons 严格等长对齐的填充/轮廓标志 + 2D 文本项
        "polygon_fills": list(result.polygon_fills),
        "polygon_contours": list(result.polygon_contours),
        "texts": [
            {"x": t.x, "y": t.y, "text": t.text, "size": t.size}
            for t in result.texts
        ],
        "circles": [
            {"cx": cx, "cy": cy, "r": r}
            for cx, cy, r in result.circles
        ],
        "arcs": [
            {"cx": cx, "cy": cy, "r": r, "a0": a0, "a1": a1}
            for cx, cy, r, a0, a1 in result.arcs
        ],
        "warnings": result.warnings,
        "verification": preview_verification(scripts),
        "quality": normalize_quality(quality),
    }


def empty_preview_2d_payload() -> dict[str, Any]:
    return {
        "lines": [],
        "polygons": [],
        "polygon_fills": [],
        "polygon_contours": [],
        "texts": [],
        "circles": [],
        "arcs": [],
        "warnings": [],
        "verification": preview_verification({}),
        "quality": "fast",
    }


def script_for(project: HSFProject, script_type: ScriptType, script_overrides: dict[str, str]) -> str:
    return script_overrides.get(script_type.value, project.get_script(script_type))


def preview_verification(script_overrides: dict[str, str]) -> dict[str, Any]:
    names = sorted(script_overrides)
    return {
        "source": "editor_buffer" if names else "saved",
        "script_overrides": names,
    }
