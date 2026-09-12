from __future__ import annotations

from typing import Any

from openbrep.gdl_previewer import preview_2d_script, preview_3d_script
from openbrep.hsf_project import HSFProject, ScriptType
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
    result = preview_3d_script(
        script_for(project, ScriptType.SCRIPT_3D, scripts),
        parameters=parameter_values(project, overrides),
        setup_script=script_for(project, ScriptType.MASTER, scripts),
        unknown_command_policy="warn",
        quality=normalize_quality(quality),
        macro_resolver=macro_resolver,
        macro_guid_map=project.called_macro_guid_map(),
    )
    payload = preview_3d_to_three_payload(result)
    payload["warnings"] = result.warnings
    payload["verification"] = preview_verification(scripts)
    return payload


def empty_preview_payload() -> dict[str, Any]:
    return {
        "meshes": [],
        "wires": [],
        "warnings": [],
        "verification": preview_verification({}),
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
    }


def empty_preview_2d_payload() -> dict[str, Any]:
    return {
        "lines": [],
        "polygons": [],
        "circles": [],
        "arcs": [],
        "warnings": [],
        "verification": preview_verification({}),
    }


def script_for(project: HSFProject, script_type: ScriptType, script_overrides: dict[str, str]) -> str:
    return script_overrides.get(script_type.value, project.get_script(script_type))


def preview_verification(script_overrides: dict[str, str]) -> dict[str, Any]:
    names = sorted(script_overrides)
    return {
        "source": "editor_buffer" if names else "saved",
        "script_overrides": names,
    }
