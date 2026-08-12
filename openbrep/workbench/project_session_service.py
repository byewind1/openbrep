from __future__ import annotations

import base64
import binascii
import datetime as _dt
import logging
import os
import re
import shutil
import tempfile
import tomllib
from pathlib import Path
from typing import Any, Callable

from openbrep.gdl_parser import gdl_source_has_sections, parse_gdl_source_with_warnings
from openbrep.hsf_project import GDLParameter, HSFProject, ScriptType, normalize_project_after_import
from openbrep.naming import (
    DEFAULT_PROJECT_NAME,
    project_name_from_prompt,
    safe_project_name,
    unique_project_name,
)
from openbrep.runtime.pipeline import TaskRequest
from openbrep.workbench.preview_service import preview_payload
from openbrep.workbench.project_parameter_service import parameter_to_dict
from openbrep.workbench.project_script_service import SCRIPT_NAME_TO_TYPE
from openbrep.workbench.settings_service import save_workbench_config
from openbrep.workbench.view_models import classify_vision_error


MAX_WORKBENCH_IMAGE_BYTES = 5 * 1024 * 1024
MAX_WORKBENCH_IMAGES = 4
SUPPORTED_WORKBENCH_IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp"}
_DEMO_PROJECT: HSFProject | None = None
UNTITLED_PROJECT_NAME = "Untitled GDL Object"

logger = logging.getLogger(__name__)


_ORIGIN_HEADER_RE = re.compile(r"^\[origin\][ \t]*(?:#.*)?\r?$", re.MULTILINE)
_SECTION_HEADER_RE = re.compile(r"^\[[^\]]+\][ \t]*(?:#.*)?\r?$", re.MULTILINE)
_ORIGIN_KEYS = ("imported_from", "imported_kind", "imported_at")


def _toml_basic_string(value: str) -> str:
    """TOML 基本字符串转义（路径可能含反斜杠/引号/控制符）。"""
    return '"' + (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    ) + '"'


def _render_origin_section(origin: dict[str, str]) -> str:
    lines = ["[origin]"]
    for key in _ORIGIN_KEYS:
        lines.append(f"{key} = {_toml_basic_string(origin[key])}")
    return "\n".join(lines) + "\n"


def _replace_origin_section(text: str, new_section: str) -> str:
    """节级最小更新：只替换 [origin] 一节，其余字节逐字保留。

    - 已有 [origin]：从该表头到下一个 [表头]（或文件尾）的整段被替换；
      [origin.sub] 等子表视为下一个表头，保持不动。
    - 没有 [origin]：在文件末尾追加新节（保证前有换行）。
    """
    header = _ORIGIN_HEADER_RE.search(text)
    if header is None:
        if text and not text.endswith("\n"):
            text += "\n"
        return text + new_section
    start = header.start()
    next_header = _SECTION_HEADER_RE.search(text, header.end())
    if next_header is None:
        return text[:start] + new_section
    end = next_header.start()
    # [origin] 最后一行自身的行尾由 new_section 自带；其后的空白行属于
    # 节间分隔，不属于任何节，原样保留（0 个就 0 个，2 个就 2 个）。
    trailing_newlines = 0
    probe = end
    while probe > start and text[probe - 1] == "\n":
        probe -= 1
        trailing_newlines += 1
    blank_lines = max(trailing_newlines - 1, 0)
    return text[:start] + new_section + "\n" * blank_lines + text[end:]


def write_project_origin(
    project_root: str | Path,
    *,
    imported_from: str,
    imported_kind: str,
) -> Path | None:
    """把导入溯源持久化到 <project_root>/.openbrep/project.toml 的 [origin] 节。

    - 三个键：imported_from（源文件绝对路径）、imported_kind（gdl/gsm/blender_py）、
      imported_at（ISO 时间）；重复导入同名项目 = 覆盖这三个键，不追加。
    - 已存在 [origin] 节则节级更新，其他节与其他字节一个不动（见
      _replace_origin_section）；文件不存在则创建（含 .openbrep 目录）。
    - 文件坏 TOML / 写失败：跳过写入并记 warning，绝不抛异常。
    """
    root = Path(project_root).expanduser().resolve()
    toml_path = root / ".openbrep" / "project.toml"
    origin = {
        "imported_from": str(imported_from),
        "imported_kind": str(imported_kind),
        "imported_at": _dt.datetime.now().isoformat(timespec="seconds"),
    }
    new_section = _render_origin_section(origin)
    try:
        if toml_path.exists():
            text = toml_path.read_text(encoding="utf-8")
            try:
                tomllib.loads(text)
            except Exception as exc:
                logger.warning("skip [origin] write: %s is not valid TOML: %s", toml_path, exc)
                return None
            updated = _replace_origin_section(text, new_section)
        else:
            updated = new_section
        toml_path.parent.mkdir(parents=True, exist_ok=True)
        toml_path.write_text(updated, encoding="utf-8")
        return toml_path
    except Exception as exc:
        logger.warning("failed to persist [origin] to %s: %s", toml_path, exc)
        return None


class WorkbenchProjectSessionService:
    def __init__(
        self,
        session: Any,
        *,
        real_compiler_factory: Callable[[str | None], Any],
    ) -> None:
        self.session = session
        self.real_compiler_factory = real_compiler_factory

    def load_hsf_directory(self, path: str) -> dict[str, Any]:
        hsf_path = Path(path).expanduser().resolve()
        if not hsf_path.is_dir():
            return {"ok": False, "error": f"HSF directory not found: {path}"}

        try:
            project = HSFProject.load_from_disk(str(hsf_path))
        except Exception as exc:
            return {"ok": False, "error": f"Failed to load HSF project: {exc}"}

        self.session.project = project
        self.session.source = "hsf"
        self.session.source_path = hsf_path
        self.remember_project_path(hsf_path)
        return {"ok": True, **self.session.snapshot()}

    # ── 工作区附着导入（任务 U：GUI 导入收敛到工作区）─────────────────

    def _workspace_import_root(self) -> Path | None:
        """返回当前附着的有效工作区根；未附着或 workspace.toml 缺失 → None。

        GUI 导入的分支开关：附着有效工作区时，GSM/GDL 导入收敛到工作区
        （原件进 sources/ → 项目进 hsf/ → 会话加载新项目）；否则走原独立
        路径，行为逐字节不变。判定与 workspace_open 同口径（检查
        .openbrep/workspace.toml 存在）。
        """
        workspace = getattr(self.session, "workspace_path", None)
        if not workspace:
            return None
        root = Path(workspace).expanduser().resolve()
        if not (root / ".openbrep" / "workspace.toml").is_file():
            return None
        return root

    def _import_via_workspace(
        self, workspace_root: Path, source_file: Path, kind: str
    ) -> dict[str, Any]:
        """附着工作区时的导入路径：sources/ 归档 → hsf/ 项目 → 会话加载新项目。

        复用 workspace_service.import_to_workspace（sources 归档 + import_source
        + origin 指向归档副本）。失败返回与独立模式同形的错误字典；成功返回
        ok/imported_from/archived_source/project_path/warnings + session snapshot，
        gsm 分支额外透传 decompile 与 normalization。
        """
        from openbrep.workbench.workspace_service import import_to_workspace

        result = import_to_workspace(str(workspace_root), str(source_file), kind)
        if not result.get("ok"):
            error = result.get("error") or {}
            return {
                "ok": False,
                "code": str(error.get("code") or "import_failed"),
                "error": str(error.get("message") or "导入失败"),
            }

        project_path = result.get("project_path")
        loaded = self.load_hsf_directory(project_path)
        if not loaded.get("ok"):
            return {
                "ok": False,
                "error": f"导入成功但加载项目失败: {loaded.get('error')}",
                "project_path": str(project_path) if project_path else None,
            }

        out: dict[str, Any] = {
            "ok": True,
            "imported_from": str(source_file),
            "archived_source": str(result.get("source_path") or ""),
            "project_path": str(project_path) if project_path else None,
            "warnings": list(result.get("warnings") or []),
            **self.session.snapshot(),
        }
        if kind == "gsm":
            decompile = result.get("decompile")
            if decompile is not None:
                out["decompile"] = decompile
            normalization = result.get("normalization")
            if normalization is not None:
                out["normalization"] = normalization
                if not normalization.get("lossless"):
                    warning_text = str(
                        normalization.get("warning")
                        or "GSM 导入规范化失败，已保留原始文件（未规范化）"
                    )
                    out["warnings"] = list(out["warnings"]) + [warning_text]
        return out

    def import_gdl_file(self, body: dict[str, Any]) -> dict[str, Any]:
        raw_path = str(body.get("path") or "").strip()
        if not raw_path:
            try:
                raw_path = self.session._choose_file_for_purpose("gdl")
            except Exception as exc:
                return {"ok": False, "error": f"File chooser failed: {exc}"}
        if not raw_path:
            return {"ok": False, "cancelled": True, "error": "GDL file selection cancelled."}

        source_file = Path(raw_path).expanduser().resolve()
        if not source_file.is_file():
            return {"ok": False, "error": f"GDL file not found: {raw_path}"}
        if source_file.suffix.lower() != ".gdl":
            return {"ok": False, "error": f"Unsupported file type: {source_file.suffix or '(none)'}"}

        # 工作区附着：导入收敛到工作区（sources/ 归档 + hsf/ 项目 + 自动加载）。
        # 未附着/工作区无效时走下方原独立路径，行为逐字节不变。
        workspace_root = self._workspace_import_root()
        if workspace_root is not None:
            return self._import_via_workspace(workspace_root, source_file, "gdl")

        script_name = str(body.get("script_name") or ScriptType.SCRIPT_3D.value)
        script_type = SCRIPT_NAME_TO_TYPE.get(script_name)
        if script_type is None:
            return {"ok": False, "error": f"Unsupported target script: {script_name}"}

        content = source_file.read_text(encoding="utf-8-sig")
        project_name = unique_project_name(safe_project_name(source_file.stem), source_file.parent)

        # 结构化解析：参数注释块 → GDLParameter；分节横幅 → 对应脚本；任何丢失
        # 或无法识别的内容都会进 warnings（零静默）。
        project, warnings = parse_gdl_source_with_warnings(content, project_name)

        explicit_script = str(body.get("script_name") or "").strip()
        if gdl_source_has_sections(content):
            if explicit_script:
                warnings.append(
                    f"文件含可识别脚本分节，已按分节拆分到对应脚本，忽略目标脚本参数: {script_name}"
                )
        else:
            # 未识别出分节：保持"全文进目标脚本"的导入语义（默认 3D），不猜分节；
            # 已解析出的参数与 warnings 仍然保留。
            if script_type != ScriptType.SCRIPT_3D:
                project.scripts = {script_type: content}
            elif not project.scripts:
                project.scripts = {ScriptType.SCRIPT_3D: content}

        project.name = project_name
        project.work_dir = source_file.parent
        project.root = source_file.parent / project_name
        project.description = f"Imported from {source_file.name}"
        hsf_dir = project.save_to_disk()

        self.session.project = project
        self.session.source = "hsf"
        self.session.source_path = hsf_dir
        self.remember_project_path(hsf_dir)
        write_project_origin(hsf_dir, imported_from=str(source_file), imported_kind="gdl")
        result = self.session.snapshot()
        result["ok"] = True
        result["imported_from"] = str(source_file)
        result["warnings"] = warnings
        return result

    def import_gsm_file(self, body: dict[str, Any]) -> dict[str, Any]:
        raw_path = str(body.get("path") or "").strip()
        if not raw_path:
            try:
                raw_path = self.session._choose_file_for_purpose("gsm")
            except Exception as exc:
                return {"ok": False, "error": f"File chooser failed: {exc}"}
        if not raw_path:
            return {"ok": False, "cancelled": True, "error": "GSM file selection cancelled."}

        source_file = Path(raw_path).expanduser().resolve()
        if not source_file.is_file():
            return {"ok": False, "error": f"GSM file not found: {raw_path}"}
        if source_file.suffix.lower() != ".gsm":
            return {"ok": False, "error": f"Unsupported file type: {source_file.suffix or '(none)'}"}

        # 工作区附着：导入收敛到工作区；compiler 由 import_source 内部解析
        # （GSM 分支的 LP_XMLConverter 可用性检查在 import_source._import_gsm 内）。
        workspace_root = self._workspace_import_root()
        if workspace_root is not None:
            return self._import_via_workspace(workspace_root, source_file, "gsm")

        if self.session.compiler_mode != "lp":
            return {
                "ok": False,
                "error": "GSM import requires LP_XMLConverter mode. Open settings and select Real compiler first.",
            }

        compiler = self.real_compiler_factory(self.session.converter_path)
        if not compiler.is_available:
            return {
                "ok": False,
                "error": f"LP_XMLConverter not found: {compiler.converter_path or '(not configured)'}",
            }

        tmp_dir = Path(tempfile.mkdtemp(prefix="openbrep-gsm-import-"))
        try:
            hsf_out = tmp_dir / "hsf_out"
            result = compiler.libpart2hsf(str(source_file), str(hsf_out))
            if not result.success:
                diag = result.stderr or result.stdout or "(no converter output)"
                return {
                    "ok": False,
                    "error": f"GSM decompile failed (exit={result.exit_code}): {diag[:800]}",
                }

            hsf_root = find_hsf_root(hsf_out)
            if hsf_root is None:
                contents = sorted(path.name for path in hsf_out.iterdir()) if hsf_out.exists() else []
                return {"ok": False, "error": f"Could not locate HSF root in converter output: {contents}"}

            target_name = unique_project_name(safe_project_name(source_file.stem), source_file.parent)
            target_dir = source_file.parent / target_name
            shutil.copytree(hsf_root, target_dir)
            project = HSFProject.load_from_disk(str(target_dir))
            # GSM 导入落盘后立即做一次规范化重写（load→save 无损守卫 + 回滚）：
            # LP_XMLConverter 原始输出与 OpenBrep 规范写器不一致，第一次保存会
            # 产生一次性大 diff；把这次重写变成导入时的显式事件（P3-e）。
            # 有损/异常时 helper 内部已回滚原始文件并返回 warning，绝不会把导入搞失败。
            normalization = normalize_project_after_import(target_dir)
        except Exception as exc:
            return {"ok": False, "error": f"Failed to import GSM file: {exc}"}
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        self.session.project = project
        self.session.source = "hsf"
        self.session.source_path = target_dir
        self.remember_project_path(target_dir)
        write_project_origin(target_dir, imported_from=str(source_file), imported_kind="gsm")
        result = {
            "ok": True,
            "imported_from": str(source_file),
            "decompile": {
                "mode": "lp",
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
            "normalization": normalization,
            **self.session.snapshot(),
        }
        if not normalization.get("lossless"):
            warning_text = str(
                normalization.get("warning")
                or "GSM 导入规范化失败，已保留原始文件（未规范化）"
            )
            result["warnings"] = list(result.get("warnings") or []) + [warning_text]
        return result

    def create_project_from_prompt(self, body: dict[str, Any]) -> dict[str, Any]:
        prompt = str(body.get("prompt") or body.get("message") or "").strip()
        if not prompt:
            return {"ok": False, "error": "Create prompt is empty."}
        image_payload = validate_image_payload(body)
        if not image_payload["ok"]:
            return {"ok": False, "error": image_payload["error"]}

        # AI 新建项目落点：请求显式指定 > 设置里的 output_dir > ./output 兜底，
        # 保证用户在设置面板看到的目录就是项目实际生成的位置。
        output_root = (
            Path(str(body.get("output_dir") or self.session.output_dir or "./output")).expanduser().resolve()
        )
        output_root.mkdir(parents=True, exist_ok=True)
        requested_name = str(body.get("project_name") or "").strip()
        project_name = unique_project_name(
            safe_project_name(requested_name or project_name_from_prompt(prompt)),
            output_root,
        )
        target_dir = output_root / project_name
        events: list[dict[str, Any]] = []

        def on_event(event_type, data):
            events.append({"type": event_type, "data": data})

        pipeline = self.session.pipeline_class(trace_dir="./traces")
        if hasattr(pipeline, "config"):
            pipeline.config.llm.model = self.session.llm_model
            if self.session.llm_api_key:
                pipeline.config.llm.api_key = self.session.llm_api_key
            if self.session.llm_api_base:
                pipeline.config.llm.api_base = self.session.llm_api_base
            pipeline.config.llm.assistant_settings = self.session.assistant_settings
            pipeline.config.agent.max_iterations = self.session.max_retries

        result = pipeline.execute(
            TaskRequest(
                user_input=prompt,
                intent="IMAGE" if (image_payload["image_b64"] or image_payload["images"]) else "CREATE",
                work_dir=str(output_root),
                output_dir=str(output_root),
                gsm_name=project_name,
                image_b64=image_payload["image_b64"],
                image_mime=image_payload["image_mime"],
                images=_image_refs_from_payload(image_payload["images"]),
                assistant_settings=str(body.get("assistant_settings") or self.session.assistant_settings),
                history=list(body.get("history") or []),
                on_event=on_event,
            )
        )
        # 验证未过（success=False）但有 project 产出时照常交付并挂载，
        # verification 报告会如实显示 FAIL；只有无产出才算硬失败
        if result.project is None:
            error = result.error or "Create failed."
            if image_payload["image_b64"] or image_payload["images"]:
                error = classify_vision_error(Exception(error))
            return {"ok": False, "error": error, "events": events}

        result.project.name = project_name
        result.project.work_dir = target_dir.parent
        result.project.root = target_dir
        hsf_dir = result.project.save_to_disk()

        # ── P7b：生成成功后按 object_type（规划阶段 LLM 产出，一级命名来源）rename 目录 ──
        # 规则提取名只是临时名；object_type 可用（非空、sanitize 后非兜底名、与临时名不同）
        # 时执行 rename；失败一律保留临时名照常交付，绝不阻断主流程。
        rename_warnings: list[str] = []
        object_type = str((result.object_plan or {}).get("object_type") or "").strip()
        # 显式 project_name（API 调用方指定）优先于 object_type，不 rename
        if object_type and not requested_name:
            candidate_name = safe_project_name(object_type)
            if candidate_name != DEFAULT_PROJECT_NAME and candidate_name != project_name:
                new_name = unique_project_name(candidate_name, output_root)
                if new_name != project_name:
                    new_dir = output_root / new_name
                    try:
                        os.rename(target_dir, new_dir)
                    except OSError as exc:
                        rename_warnings.append(
                            f"目录改名失败（{project_name} → {new_name}），已保留临时名：{exc}"
                        )
                    else:
                        result.project.name = new_name
                        result.project.root = new_dir
                        result.project.work_dir = new_dir.parent
                        hsf_dir = new_dir
                        # 最近项目列表里的旧临时路径移除（rename 后旧路径已失效）
                        self._drop_recent_project_path(target_dir)
                        # 编译产物 <临时名>.gsm 随目录一起改名（best-effort，失败仅记 warning）
                        old_gsm = output_root / f"{project_name}.gsm"
                        new_gsm = output_root / f"{new_name}.gsm"
                        if old_gsm.exists():
                            try:
                                os.rename(old_gsm, new_gsm)
                            except OSError as exc:
                                rename_warnings.append(
                                    f"目录已改名为 {new_name}，但编译产物 {old_gsm.name} 未能同步改名：{exc}"
                                )
                        project_name = new_name

        self.session.project = result.project
        self.session.source = "hsf"
        self.session.source_path = hsf_dir
        self.remember_project_path(hsf_dir)
        # ── P5d-1：vision 提取工件落盘（设计 D7 内容哈希寻址）──────────────
        # 数据源：pipeline 透出的 TaskResult.metadata["vision_extractions"]。
        # 位置必须在 save_to_disk + P7b 目录 rename 之后（root 已是最终路径）；
        # 落盘失败只记 warning 不阻断交付（设计 D8 零静默的存储侧兜底）。
        vision_warnings: list[str] = []
        extraction_entries = (result.metadata or {}).get("vision_extractions") or []
        if extraction_entries:
            from openbrep.vision.extraction_store import save_extraction
            from openbrep.vision.modeling_plan import ModelingPlan

            for entry in extraction_entries:
                if entry.get("skipped"):
                    continue
                try:
                    plan = ModelingPlan(
                        schema_name=str(entry.get("schema_name") or ""),
                        fields=entry.get("fields") or {},
                        confidence=entry.get("confidence") or {},
                        corrections=entry.get("corrections") or [],
                        source_images=[str(entry.get("sha256") or "")],
                        raw_description=str(entry.get("raw_description") or ""),
                        degraded=bool(entry.get("degraded")),
                        critic_degraded=bool(entry.get("critic_degraded")),
                    )
                    save_extraction(result.project.root, plan, model=self.session.llm_model)
                except Exception as exc:
                    vision_warnings.append(f"vision 提取工件落盘失败（{entry.get('token') or '?'}）：{exc}")
        # skill 效果回写（GUI 侧通道，best-effort）：失败任务按注入 skill 计 fail_count
        try:
            from openbrep.runtime.skill_harvest import record_skill_outcome

            record_skill_outcome(self.session, result)
        except Exception:
            logger.warning("skill outcome record skipped after create (best-effort)", exc_info=True)
        # 模式级 skill 提案（GUI 侧通道，best-effort；提炼失败不影响交付）
        proposal = None
        try:
            if getattr(self.session, "skill_harvest_enabled", True):
                from openbrep.runtime.skill_harvest import harvest_for_session

                proposal = harvest_for_session(self.session, result, prompt)
        except Exception:
            logger.warning("skill harvest skipped after create (best-effort)", exc_info=True)
        response: dict[str, Any] = {
            "ok": True,
            "assistant": {
                "kind": "create",
                "reply": result.plain_text,
                "changed_files": list((result.scripts or {}).keys()),
                "intent": result.intent,
                "verification": result.verification,
            },
            "events": events,
            **self.session.snapshot(),
        }
        if rename_warnings:
            response["warnings"] = list(response.get("warnings") or []) + rename_warnings
        if vision_warnings:
            response["warnings"] = list(response.get("warnings") or []) + vision_warnings
        if proposal:
            response["skill_proposal"] = proposal
        return response

    def new_project(self) -> dict[str, Any]:
        project = HSFProject.create_new(UNTITLED_PROJECT_NAME)
        self.session.project = project
        self.session.source = "untitled"
        self.session.source_path = None
        return {"ok": True, **self.session.snapshot()}

    def close_project(self) -> dict[str, Any]:
        self.session.project = None
        self.session.source = "empty"
        self.session.source_path = None
        return {"ok": True, **self.session.snapshot()}

    def save_project(self, body: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.session.project is None:
            return {"ok": False, "error": "No project to save."}
        if self.session.source_path is None:
            return {
                "ok": False,
                "needs_save_as": True,
                "error": "Project has no HSF path. Use Save As HSF.",
            }
        self.session.project.save_to_disk()
        return {"ok": True, "saved_to": str(self.session.source_path), **self.session.snapshot()}

    def _save_as_auto_dir(self) -> Path:
        """P7c 自动落点：只给 name 不给 parent_dir 时，落点自动取
        工作区 hsf/ ＞ 设置 output_dir ＞ ./output（与 create/导入收敛同口径）。

        工作区附着判定与 workspace_open/_workspace_import_root 一致
        （检查 .openbrep/workspace.toml 存在）。
        """
        workspace = getattr(self.session, "workspace_path", None)
        if workspace:
            root = Path(workspace).expanduser().resolve()
            if (root / ".openbrep" / "workspace.toml").is_file():
                return root / "hsf"
        configured = str(getattr(self.session, "output_dir", "") or "").strip()
        if configured:
            return Path(configured).expanduser().resolve()
        return Path("./output").expanduser().resolve()

    def export_hsf_project(self, body: dict[str, Any]) -> dict[str, Any]:
        if self.session.project is None:
            return {"ok": False, "error": "No project to export."}
        requested_name = str(body.get("name") or "").strip()
        raw_parent = str(body.get("parent_dir") or "").strip()
        # P7c：只给 name 不给 parent_dir → 自动落点（工作区 hsf/ ＞ output_dir ＞
        # ./output），不再弹目录选择器；name 过 safe_project_name + unique_project_name。
        # 两者都空才走旧目录选择器路径（显式 Save As 无名字 / 设置面板导出）。
        auto_dir = bool(requested_name) and not raw_parent
        if auto_dir:
            raw_parent = str(self._save_as_auto_dir())
        if not raw_parent:
            try:
                raw_parent = self.session.directory_chooser()
            except Exception as exc:
                return {"ok": False, "error": f"Directory chooser failed: {exc}"}
        if not raw_parent:
            return {"ok": False, "cancelled": True, "error": "HSF export directory selection cancelled."}

        parent = Path(raw_parent).expanduser().resolve()
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            return {"ok": False, "error": f"Failed to create export directory: {exc}"}
        if not parent.is_dir():
            return {"ok": False, "error": f"Export target is not a directory: {parent}"}

        base_name = safe_project_name(requested_name or self.session.project.name or "OpenBrep_Project")
        # P7c 自动落点唯一化（_vN 后缀，与 create/导入同一命名管线）；显式
        # parent_dir 路径保持原有"非空已存在目录直接拒绝"语义，不做唯一化。
        project_name = unique_project_name(base_name, parent) if auto_dir else base_name
        target = (parent / project_name).resolve()
        previous_source = self.session.source_path.expanduser().resolve() if self.session.source_path else None
        current_root = self.session.project.root.expanduser().resolve() if self.session.project.root else None
        allowed_existing_roots = {root for root in (previous_source, current_root) if root is not None}
        if target.exists() and target not in allowed_existing_roots and any(target.iterdir()):
            return {"ok": False, "error": f"Target HSF directory already exists and is not empty: {target}"}

        self.session.project.name = project_name
        self.session.project.work_dir = parent
        self.session.project.root = target
        try:
            saved_root = self.session.project.save_to_disk().expanduser().resolve()
            if previous_source is not None and previous_source.exists() and previous_source != saved_root:
                try:
                    from openbrep.revisions import copy_project_metadata

                    copy_project_metadata(previous_source, saved_root)
                except Exception:
                    pass
            self.session.source = "hsf"
            self.session.source_path = saved_root
            self.remember_project_path(saved_root)
            self.session.project = HSFProject.load_from_disk(str(saved_root))
        except Exception as exc:
            return {"ok": False, "error": f"Failed to export HSF project: {exc}"}

        return {"ok": True, "saved_to": str(saved_root), **self.session.snapshot()}

    def recent_projects(self) -> dict[str, Any]:
        return {
            "ok": True,
            "projects": [
                recent_project_to_api(path)
                for path in self.session.recent_project_paths
            ],
        }

    def remember_project_path(self, path: Path) -> None:
        normalized = str(path.expanduser().resolve())
        self.session.recent_project_paths = [
            normalized,
            *[item for item in self.session.recent_project_paths if item != normalized],
        ][:8]
        self.session.config.recent_projects = self.session.recent_project_paths
        save_workbench_config(self.session.config, self.session.config_path)

    def _drop_recent_project_path(self, path: Path) -> None:
        """从最近项目列表移除指定路径（rename 后旧路径失效；落盘随下一次 remember 一起）。"""
        normalized = str(path.expanduser().resolve())
        self.session.recent_project_paths = [
            item for item in self.session.recent_project_paths if item != normalized
        ]

    def choose_and_load_hsf_directory(self) -> dict[str, Any]:
        try:
            selected = self.session.directory_chooser()
        except Exception as exc:
            return {"ok": False, "error": f"Directory chooser failed: {exc}"}
        if not selected:
            return {"ok": False, "cancelled": True, "error": "Directory selection cancelled."}
        loaded = self.load_hsf_directory(selected)
        if loaded.get("ok"):
            loaded["path"] = str(Path(selected).expanduser().resolve())
        return loaded


def build_demo_project() -> HSFProject:
    project = HSFProject.create_new("Demo Bookshelf")
    project.parameters = [
        GDLParameter("A", "Length", "总宽", "1.2", is_fixed=True),
        GDLParameter("B", "Length", "总深", "0.36", is_fixed=True),
        GDLParameter("ZZYZX", "Length", "总高", "1.8", is_fixed=True),
        GDLParameter("shelf_count", "Integer", "层板数", "5"),
        GDLParameter("shelf_thickness", "Length", "层板厚度", "0.035"),
        GDLParameter("frame_thickness", "Length", "侧板厚度", "0.04"),
        GDLParameter("has_back_panel", "Boolean", "背板", "1"),
        GDLParameter("object_label", "String", "对象标签", "Bookshelf"),
    ]
    project.set_script(
        ScriptType.MASTER,
        """
inner_w = A - 2 * frame_thickness
gap = (ZZYZX - shelf_count * shelf_thickness) / (shelf_count + 1)
""".strip()
        + "\n",
    )
    project.set_script(
        ScriptType.SCRIPT_3D,
        """
! side panels
BLOCK frame_thickness, B, ZZYZX
ADDX A - frame_thickness
BLOCK frame_thickness, B, ZZYZX
DEL 1

! shelves
FOR i = 1 TO shelf_count
    ADDX frame_thickness
    ADDZ i * gap + (i - 1) * shelf_thickness
    BLOCK inner_w, B, shelf_thickness
    DEL 2
NEXT i

! optional back panel
IF has_back_panel = 1 THEN
    ADDY B - 0.018
    BLOCK A, 0.018, ZZYZX
    DEL 1
ENDIF
""".strip()
        + "\n",
    )
    return project


def demo_project() -> HSFProject:
    global _DEMO_PROJECT
    if _DEMO_PROJECT is None:
        _DEMO_PROJECT = build_demo_project()
    return _DEMO_PROJECT


def build_demo_snapshot() -> dict[str, Any]:
    return project_to_snapshot(demo_project())


def empty_project_snapshot() -> dict[str, Any]:
    return {
        "project": None,
        "parameters": [],
        "preview": {"meshes": [], "wires": [], "warnings": []},
        "warnings": [],
    }


def project_to_snapshot(
    project: HSFProject | None,
    *,
    source: str = "demo",
    source_path: str | None = None,
) -> dict[str, Any]:
    if project is None:
        return empty_project_snapshot()
    preview = preview_payload(project)
    return {
        "project": {
            "name": project.name,
            "source": source,
            **({"path": source_path} if source_path else {}),
        },
        "parameters": [parameter_to_dict(param) for param in project.parameters],
        "preview": preview,
        "warnings": preview.get("warnings", []),
    }


def validate_image_payload(body: dict[str, Any]) -> dict[str, Any]:
    """校验请求体中的图片负载。

    旧单图字段（image_b64 / image_mime）：原样保留、原逻辑、逐字节零变化——
    只要旧字段存在就走旧路径，不经过任何新逻辑。

    新多图通道（images 数组，仅当旧字段不存在时生效）：逐张校验
    （b64 合法 / mime ∈ png,jpeg,webp / 解码后 ≤5MB / path 存在且可读 / 总数 ≤4），
    路径不存在时 error 指名具体路径。
    """
    image_b64 = str(body.get("image_b64") or "").strip()
    image_mime = str(body.get("image_mime") or "image/png").strip().lower()

    if image_b64:
        if image_mime not in SUPPORTED_WORKBENCH_IMAGE_MIMES:
            supported = ", ".join(sorted(SUPPORTED_WORKBENCH_IMAGE_MIMES))
            return {"ok": False, "error": f"Unsupported image type: {image_mime}. Supported: {supported}."}

        try:
            raw = base64.b64decode(image_b64, validate=True)
        except (binascii.Error, ValueError):
            return {"ok": False, "error": "Invalid image data: expected base64 payload."}

        if len(raw) > MAX_WORKBENCH_IMAGE_BYTES:
            size_mb = len(raw) / (1024 * 1024)
            return {
                "ok": False,
                "error": f"Image is too large ({size_mb:.1f} MB). Please compress it to 5 MB or less.",
            }
        return {"ok": True, "image_b64": image_b64, "image_mime": image_mime, "images": []}

    # ── 新多图通道（旧字段不存在时才生效）──
    raw_images = body.get("images")
    if not isinstance(raw_images, list) or not raw_images:
        return {"ok": True, "image_b64": None, "image_mime": "image/png", "images": []}

    if len(raw_images) > MAX_WORKBENCH_IMAGES:
        return {
            "ok": False,
            "error": f"Too many images: {len(raw_images)}. Max {MAX_WORKBENCH_IMAGES} images per request.",
        }

    validated: list[dict[str, Any]] = []
    for index, item in enumerate(raw_images, start=1):
        if not isinstance(item, dict):
            return {"ok": False, "error": f"Invalid image entry #{index}: expected an object."}
        path = str(item.get("path") or "").strip()
        b64 = str(item.get("b64") or "").strip()
        mime = str(item.get("mime") or "image/png").strip().lower()

        if path:
            # 路径来源：webview 无 fs 权限，读取发生在 Python 侧；这里先做存在性/可读性校验
            img_path = Path(path).expanduser()
            if not img_path.exists():
                return {"ok": False, "error": f"Image path does not exist: {path}"}
            if not img_path.is_file():
                return {"ok": False, "error": f"Image path is not a file: {path}"}
            try:
                with img_path.open("rb") as fh:
                    fh.read(1)
            except OSError as exc:
                return {"ok": False, "error": f"Image path is not readable: {path} ({exc})"}
            validated.append({"token": f"图{index}", "path": path, "b64": "", "mime": mime})
        else:
            if not b64:
                return {"ok": False, "error": f"Image #{index}: missing both b64 and path."}
            if mime not in SUPPORTED_WORKBENCH_IMAGE_MIMES:
                supported = ", ".join(sorted(SUPPORTED_WORKBENCH_IMAGE_MIMES))
                return {"ok": False, "error": f"Unsupported image type: {mime}. Supported: {supported}."}
            try:
                raw = base64.b64decode(b64, validate=True)
            except (binascii.Error, ValueError):
                return {"ok": False, "error": f"Invalid image data for image #{index}: expected base64 payload."}
            if len(raw) > MAX_WORKBENCH_IMAGE_BYTES:
                size_mb = len(raw) / (1024 * 1024)
                return {
                    "ok": False,
                    "error": f"Image #{index} is too large ({size_mb:.1f} MB). Please compress it to 5 MB or less.",
                }
            validated.append({"token": f"图{index}", "path": None, "b64": b64, "mime": mime})

    return {"ok": True, "image_b64": None, "image_mime": "image/png", "images": validated}


def _image_refs_from_payload(validated_images: list[dict[str, Any]]) -> list:
    """校验后的 images 数组 → TaskRequest.images（ImageRef 列表）。"""
    from openbrep.runtime.pipeline import ImageRef

    return [
        ImageRef(token=str(img.get("token") or ""), path=img.get("path"), b64=str(img.get("b64") or ""), mime=str(img.get("mime") or "image/png"))
        for img in validated_images
    ]


def recent_project_to_api(path: str) -> dict[str, Any]:
    project_path = Path(path)
    return {
        "path": str(project_path),
        "name": project_path.name or str(project_path),
        "parent_dir": str(project_path.parent) if project_path.parent != Path(".") else "",
        "exists": project_path.is_dir(),
    }


def find_hsf_root(base: Path) -> Path | None:
    if not base.exists():
        return None
    if (base / "libpartdata.xml").exists() and (base / "scripts").is_dir():
        return base
    for candidate in base.rglob("libpartdata.xml"):
        root = candidate.parent
        if (root / "scripts").is_dir():
            return root
    return None
