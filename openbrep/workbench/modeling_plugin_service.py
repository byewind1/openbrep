"""Local management for versioned, data-only GDL domain Skill packages."""
from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from openbrep.domain_skill_selection import (
    PACKAGE_RELATIVE_PATH,
    SELECTION_RELATIVE_PATH,
    load_project_skill_selections,
    package_hash,
    select_project_skill,
    set_project_skill_enabled,
)
from openbrep.domain_skills import DomainSkillRegistry

_VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


class ModelingPluginService:
    """Management APIs; package content is always data and is never executed."""

    def __init__(self, session: Any):
        self.session = session
        self.personal_root = Path.home() / ".openbrep" / "domain_skills"

    def route(self, method: str, route: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = body or {}
        if method == "GET" and route == "/api/settings/modeling-plugins":
            return self._with_personal_lock(self.list_plugins)
        if method == "GET" and route == "/api/settings/modeling-tools":
            return self.list_tools()
        if method == "POST" and route == "/api/settings/modeling-plugins/install":
            return self._with_personal_lock(lambda: self.install(payload))
        if method == "POST" and route == "/api/settings/modeling-plugins/personalize":
            return self._with_personal_lock(lambda: self.personalize(payload))
        if method == "POST" and route == "/api/settings/modeling-plugins/adopt":
            return self.adopt(payload)
        if method == "POST" and route == "/api/settings/modeling-plugins/enabled":
            return self.set_enabled(payload)
        if method == "POST" and route == "/api/settings/modeling-plugins/methodology":
            return self._with_personal_lock(lambda: self.save_methodology(payload))
        if method == "POST" and route == "/api/settings/modeling-plugins/restore":
            return self._with_personal_lock(lambda: self.restore(payload))
        return {"ok": False, "code": "method_not_found", "error": "未知建模插件路由"}

    def _roots(self) -> list[tuple[str, Path]]:
        example_root = Path(__file__).resolve().parents[2] / "examples" / "chinese-architecture" / "skills"
        roots = [("builtin", DomainSkillRegistry.builtin().root), ("example", example_root), ("personal", self.personal_root)]
        project = self.session.project
        if project is not None:
            roots.append(("project", Path(project.root) / PACKAGE_RELATIVE_PATH))
        return roots

    def _with_personal_lock(self, action):
        from openbrep.project_write_lock import project_write_lock
        with project_write_lock(self.personal_root):
            return action()

    def list_plugins(self) -> dict[str, Any]:
        project = self.session.project
        try:
            selected = load_project_skill_selections(project.root) if project else None
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return self._error("selection_unreadable", f"项目插件绑定无法读取：{exc}")
        bindings = {row.skill_id: row for row in selected.records} if selected else {}
        usage = self._recent_plugin_usage(project.root) if project else {}
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        issues: list[dict[str, str]] = []
        for scope, root in self._roots():
            registry = DomainSkillRegistry(root)
            for skill_id in registry.skill_ids:
                loaded = registry.load(skill_id)
                if not loaded.ok or loaded.skill is None:
                    issues.extend({"skill_id": skill_id, "code": issue.code, "message": issue.message} for issue in loaded.issues)
                    continue
                skill = loaded.skill
                try:
                    digest = package_hash(skill.package_path)
                except (OSError, ValueError) as exc:
                    issues.append({"skill_id": skill_id, "code": "PACKAGE_UNSAFE", "message": str(exc)})
                    continue
                binding = bindings.get(skill_id)
                duplicate = skill_id in seen
                seen.add(skill_id)
                version_root = self.personal_root / ".versions" / skill_id
                archived_versions = sorted(
                    (path.name for path in version_root.iterdir() if path.is_dir()),
                    reverse=True,
                ) if scope == "personal" and version_root.is_dir() else []
                items.append({
                    "skill_id": skill_id,
                    "name": str(skill.manifest.get("name") or skill.manifest.get("domain") or skill_id),
                    "domain": skill.manifest.get("domain", ""),
                    "version": skill.version,
                    "status": skill.status,
                    "content_hash": digest,
                    "source": scope,
                    "intents": skill.manifest.get("intents", []),
                    "aliases": skill.manifest.get("aliases", []),
                    "capabilities": ["methodology"] + (["typed_observation"] if skill.manifest.get("observation", {}).get("fields") else []) + (["registered_checks"] if any(r.get("check_id") for r in skill.manifest.get("requirements", [])) else []),
                    "methodology": skill.prompt_text,
                    "versions": sorted(set([skill.version, *archived_versions]), reverse=True) if scope == "personal" else [skill.version],
                    "installed": scope in {"personal", "project"},
                    "selected": binding is not None,
                    "enabled": binding.enabled if binding else False,
                    "pinned_version": binding.version if binding else None,
                    "pinned_hash": binding.content_hash if binding else None,
                    "update_available": bool(binding and (binding.version != skill.version or binding.content_hash != digest)),
                    "shadowed": duplicate,
                    "recent_usage": usage.get(skill_id, []),
                })
        return {"ok": True, "has_project": project is not None, "plugins": items, "issues": issues,
                "selection_issues": [issue.__dict__ for issue in (selected.issues if selected else ())]}

    def install(self, body: dict[str, Any]) -> dict[str, Any]:
        source = Path(str(body.get("source_path") or "")).expanduser().resolve()
        single_markdown = source if source.is_file() and source.suffix.casefold() == ".md" else None
        if single_markdown is not None:
            source = source.parent
        if not source.is_dir():
            return self._error("package_not_found", "请选择一个领域插件或 SKILL.md 包目录")
        manifest_path = source / "manifest.json"
        if single_markdown is None and any(path.is_symlink() for path in source.rglob("*")):
            return self._error("package_symlink", "插件包不能包含符号链接")
        staging_root = self.personal_root / f".import-{uuid.uuid4().hex}"
        try:
            if manifest_path.is_file() and single_markdown is None:
                raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                skill_id = str(raw_manifest.get("skill_id") or "") if isinstance(raw_manifest, dict) else ""
                if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", skill_id):
                    shutil.rmtree(staging_root, ignore_errors=True)
                    return self._error("package_invalid", "manifest.json 中的 skill_id 格式无效")
                stage_package = staging_root / skill_id
                shutil.copytree(source, stage_package, symlinks=False)
            else:
                skill_file = single_markdown or (source / "SKILL.md")
                if not skill_file.is_file():
                    shutil.rmtree(staging_root, ignore_errors=True)
                    return self._error("unsupported_package", "目录中没有 manifest.json 或 SKILL.md")
                content = skill_file.read_text(encoding="utf-8")
                frontmatter = content.split("---", 2)[1] if content.startswith("---") and content.count("---") >= 2 else ""
                name_match = re.search(r"(?m)^name:\s*['\"]?([^\s'\"]+)['\"]?\s*$", frontmatter)
                slug = (name_match.group(1) if name_match else source.name).casefold()
                skill_id = re.sub(r"[^a-z0-9_-]+", "-", slug).strip("-_")
                if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", skill_id):
                    shutil.rmtree(staging_root, ignore_errors=True)
                    return self._error("package_invalid", "SKILL.md name 不能转换成安全的插件 ID")
                version_match = re.search(r"(?m)^\s*version:\s*['\"]?([0-9]+\.[0-9]+\.[0-9]+)", frontmatter)
                version = version_match.group(1) if version_match else "0.1.0"
                intent_match = re.search(r"(?m)^intents:\s*\[([^\]]*)\]", frontmatter)
                intents = [item.strip().strip("'\" ") for item in intent_match.group(1).split(",")] if intent_match else ["create", "modify"]
                intents = [item for item in intents if item in {"create", "modify", "debug", "image"}] or ["create", "modify"]
                stage_package = staging_root / skill_id
                if single_markdown is None:
                    shutil.copytree(source, stage_package, symlinks=False)
                else:
                    stage_package.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(skill_file, stage_package / "SKILL.md")
                generated = {
                    "schema_version": 1, "skill_id": skill_id, "version": version,
                    "status": "proposed", "domain": skill_id, "intents": intents,
                    "aliases": [skill_id], "observation": {"fields": {}},
                    "plan_policy": {"unobserved_values": "preserve_unknown", "conflict_policy": "preserve_unknown", "requirement_mapping": "retain_source_and_field_path"},
                    "requirements": [], "allowed_variations": [], "fixtures": [],
                    "methodology_path": "SKILL.md",
                }
                (stage_package / "manifest.json").write_text(json.dumps(generated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            checked = DomainSkillRegistry(staging_root).load(skill_id)
            if not checked.ok or checked.skill is None:
                shutil.rmtree(staging_root, ignore_errors=True)
                return {"ok": False, "code": "package_invalid", "error": "插件包校验失败", "issues": [i.__dict__ for i in checked.issues]}
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            shutil.rmtree(staging_root, ignore_errors=True)
            return self._error("package_invalid", str(exc))
        destination = self.personal_root / skill_id
        if destination.exists():
            shutil.rmtree(staging_root, ignore_errors=True)
            return self._error("already_installed", "个人库中已存在同 ID 插件；请先查看版本或使用编辑副本")
        try:
            self._atomic_copy(staging_root / skill_id, destination)
        except OSError as exc:
            return self._error("install_failed", str(exc))
        finally:
            shutil.rmtree(staging_root, ignore_errors=True)
        return {"ok": True, "skill_id": skill_id, "version": checked.skill.version, "content_hash": package_hash(destination)}

    def personalize(self, body: dict[str, Any]) -> dict[str, Any]:
        skill_id = str(body.get("skill_id") or "")
        source_scope = str(body.get("source") or "builtin")
        roots = dict(self._roots())
        source_root = roots.get(source_scope)
        if source_root is None or source_scope not in {"builtin", "example"}:
            return self._error("invalid_source", "只允许复制内置或随应用提供的示例包")
        source = DomainSkillRegistry(source_root).load(skill_id)
        if not source.ok or source.skill is None:
            return self._error("skill_unavailable", "内置插件不可用")
        destination = self.personal_root / skill_id
        if destination.exists():
            return self._error("already_installed", "个人库已有该插件")
        try:
            self._atomic_copy(source.skill.package_path, destination)
        except OSError as exc:
            return self._error("install_failed", str(exc))
        return {"ok": True, "skill_id": skill_id, "version": source.skill.version, "content_hash": package_hash(destination)}

    def adopt(self, body: dict[str, Any]) -> dict[str, Any]:
        project = self.session.project
        if project is None:
            return self._error("project_required", "请先打开 HSF 项目")
        skill_id = str(body.get("skill_id") or "")
        source_name = str(body.get("source") or "personal")
        roots = dict(self._roots())
        source_root = roots.get(source_name)
        if source_root is None:
            return self._error("invalid_source", "插件来源无效")
        result = DomainSkillRegistry(source_root).load(skill_id)
        if not result.ok or result.skill is None:
            return {"ok": False, "code": "skill_unavailable", "error": "插件不可用", "issues": [i.__dict__ for i in result.issues]}
        try:
            record = select_project_skill(project.root, result.skill, source=source_name)
        except (OSError, ValueError) as exc:
            return self._error("adopt_failed", str(exc))
        return {"ok": True, "selection": record.to_dict()}

    def set_enabled(self, body: dict[str, Any]) -> dict[str, Any]:
        project = self.session.project
        if project is None:
            return self._error("project_required", "请先打开 HSF 项目")
        if not isinstance(body.get("enabled"), bool):
            return self._error("invalid_request", "enabled 必须是布尔值")
        try:
            set_project_skill_enabled(project.root, str(body.get("skill_id") or ""), body["enabled"])
        except KeyError:
            return self._error("skill_not_selected", "该插件尚未在本项目采用")
        except (OSError, ValueError) as exc:
            return self._error("save_failed", str(exc))
        return {"ok": True, "skill_id": body["skill_id"], "enabled": body["enabled"]}

    def save_methodology(self, body: dict[str, Any]) -> dict[str, Any]:
        skill_id = str(body.get("skill_id") or "")
        text = body.get("methodology")
        expected_hash = str(body.get("expected_hash") or "")
        if not isinstance(text, str) or not text.strip():
            return self._error("invalid_methodology", "方法内容不能为空")
        registry = DomainSkillRegistry(self.personal_root)
        result = registry.load(skill_id)
        if not result.ok or result.skill is None:
            return self._error("personal_copy_required", "请先安装或创建个人副本再编辑")
        skill = result.skill
        try:
            current_hash = package_hash(skill.package_path)
        except (OSError, ValueError) as exc:
            return self._error("package_unsafe", str(exc))
        if current_hash != expected_hash:
            return self._error("content_conflict", "插件内容已变化，请刷新后再保存", current_hash=current_hash)
        rel = skill.manifest.get("methodology_path")
        if not rel:
            return self._error("methodology_unavailable", "该包未声明 methodology_path")
        target = (skill.package_path / str(rel)).resolve()
        if not target.is_relative_to(skill.package_path) or not target.is_file():
            return self._error("unsafe_path", "方法文件路径无效")
        version = skill.version.split(".")
        if not _VERSION.fullmatch(skill.version):
            return self._error("invalid_version", "只有语义版本号可自动递增")
        next_version = f"{version[0]}.{version[1]}.{int(version[2]) + 1}"
        versions_root = self.personal_root / ".versions" / skill_id
        updated = self.personal_root / f".{skill_id}.{uuid.uuid4().hex}.tmp"
        try:
            versions_root.mkdir(parents=True, exist_ok=True)
            archive = versions_root / skill.version
            if not archive.exists():
                self._atomic_copy(skill.package_path, archive)
            shutil.copytree(skill.package_path, updated, symlinks=False)
            (updated / str(rel)).write_text(text, encoding="utf-8")
            manifest = json.loads((updated / "manifest.json").read_text(encoding="utf-8"))
            manifest["version"] = next_version
            (updated / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            self._replace_directory(updated, skill.package_path)
        except (OSError, json.JSONDecodeError) as exc:
            return self._error("save_failed", str(exc))
        finally:
            shutil.rmtree(updated, ignore_errors=True)
        refreshed = DomainSkillRegistry(self.personal_root).load(skill_id).skill
        return {"ok": True, "skill_id": skill_id, "version": next_version,
                "content_hash": package_hash(refreshed.package_path) if refreshed else ""}

    def restore(self, body: dict[str, Any]) -> dict[str, Any]:
        skill_id, version = str(body.get("skill_id") or ""), str(body.get("version") or "")
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", skill_id) or not _VERSION.fullmatch(version):
            return self._error("invalid_request", "插件 ID 或版本号格式无效")
        archive = self.personal_root / ".versions" / skill_id / version
        if not archive.is_dir() or not (archive / "manifest.json").is_file():
            return self._error("version_not_found", "找不到该插件版本")
        try:
            manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
            if manifest.get("skill_id") != skill_id or manifest.get("version") != version:
                return self._error("version_invalid", "版本存档与插件身份不匹配")
            self._replace_directory(archive, self.personal_root / skill_id, copy=True)
        except (OSError, json.JSONDecodeError) as exc:
            return self._error("restore_failed", str(exc))
        current = DomainSkillRegistry(self.personal_root).load(skill_id).skill
        adopted = False
        if self.session.project is not None and body.get("adopt") is True and current is not None:
            select_project_skill(self.session.project.root, current, source="personal")
            adopted = True
        return {"ok": True, "skill_id": skill_id, "version": version, "adopted": adopted}

    def list_tools(self) -> dict[str, Any]:
        project = self.session.project
        tool_names: list[str] = []
        try:
            from openbrep.runtime.modify_agent_tools import ModifyToolRegistry
            tool_names = [item.name for item in ModifyToolRegistry.__new__(ModifyToolRegistry).definitions()]
        except Exception:
            pass
        available = bool(project)
        tapir_status = self.session.tapir_service.status_response().get("tapir", {})
        tools = [
            {"id": "source.read", "name": "读取与编辑 GDL 源码", "provider": "HSF 工作区", "status": "available" if available else "project_required"},
            {"id": "source.parameters", "name": "参数读写", "provider": "HSF 工作区", "status": "available" if available else "project_required"},
            {"id": "compiler", "name": "编译 GSM", "provider": "HSFCompiler", "status": "available" if self.session.compiler_mode == "lp" and self.session.converter_path and Path(self.session.converter_path).is_file() else "mock"},
            {"id": "preview", "name": "2D/3D 预览", "provider": "OpenBrep Preview", "status": "available" if available else "project_required"},
            {"id": "knowledge", "name": "知识查询", "provider": "KnowledgeBase", "status": "available"},
            {"id": "archicad.tapir", "name": "Archicad/Tapir", "provider": "Tapir", "status": "available" if tapir_status.get("archicad_connected") else "not_connected"},
            {"id": "modify_agent", "name": "AI 修改工具", "provider": "ModifyToolRegistry", "status": "channel_limited" if available else "project_required", "tools": tool_names},
        ]
        return {"ok": True, "tools": tools}

    @staticmethod
    def _recent_plugin_usage(project_root: str | Path) -> dict[str, list[dict[str, Any]]]:
        runs = Path(project_root) / ".openbrep" / "quality" / "runs"
        result: dict[str, list[dict[str, Any]]] = {}
        if not runs.is_dir():
            return result
        for path in sorted(runs.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                provenance = record.get("provenance", {})
                used = provenance.get("domain_skills_used", []) if isinstance(provenance, dict) else []
                for item in used if isinstance(used, list) else []:
                    if not isinstance(item, dict) or not item.get("skill_id"):
                        continue
                    rows = result.setdefault(str(item["skill_id"]), [])
                    if len(rows) < 3:
                        rows.append({"run_id": str(record.get("run_id") or path.stem),
                                     "ts": str(record.get("ts") or ""),
                                     "version": str(item.get("version") or ""),
                                     "content_hash": str(item.get("content_hash") or ""),
                                     "stage": str(item.get("stage") or ""),
                                     "consumed": str(item.get("consumed") or ""),
                                     "tools_used": list(provenance.get("tools_used") or []) if isinstance(provenance, dict) else [],
                                     "outcome": str(record.get("outcome") or "")})
            except (OSError, ValueError, TypeError):
                continue
        return result

    @staticmethod
    def _atomic_copy(source: Path, destination: Path) -> None:
        from openbrep.project_write_lock import project_write_lock
        with project_write_lock(destination.parent):
            destination.parent.mkdir(parents=True, exist_ok=True)
            temp = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
            try:
                shutil.copytree(source, temp, symlinks=False)
                if destination.exists():
                    raise FileExistsError(destination)
                temp.replace(destination)
            finally:
                shutil.rmtree(temp, ignore_errors=True)

    @staticmethod
    def _replace_directory(source: Path, destination: Path, *, copy: bool = False) -> None:
        from openbrep.project_write_lock import project_write_lock
        with project_write_lock(destination.parent):
            temp = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
            backup = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.old")
            try:
                if copy:
                    shutil.copytree(source, temp, symlinks=False)
                else:
                    source.replace(temp)
                if destination.exists():
                    destination.replace(backup)
                try:
                    temp.replace(destination)
                except Exception:
                    if backup.exists():
                        backup.replace(destination)
                    raise
                shutil.rmtree(backup, ignore_errors=True)
            finally:
                shutil.rmtree(temp, ignore_errors=True)

    @staticmethod
    def _error(code: str, error: str, **extra: Any) -> dict[str, Any]:
        return {"ok": False, "code": code, "error": error, **extra}
