"""Workspace-level learning memory for recurring GDL errors.

This module separates two layers:

- workspace memory: user chats, learned lessons and compacted skills under
  ``<work_dir>/.openbrep/memory``;
- developer baseline: built-in lessons shipped with source code.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MEMORY_DIR = ".openbrep/memory"
LEARNINGS_DIR = f"{MEMORY_DIR}/learnings"
CHATS_DIR = f"{MEMORY_DIR}/chats"
SKILLS_DIR = f"{MEMORY_DIR}/skills"
LEGACY_LEARNINGS_DIR = ".openbrep/learnings"
ERROR_LESSONS_FILE = "error_lessons.jsonl"
CHAT_TRANSCRIPT_FILE = "chat_transcript.jsonl"
LEARNED_SKILL_FILE = "learned_skill.md"
_SEED_FIRST_SEEN = "2026-04-28T00:00:00"


@dataclass
class ErrorLesson:
    fingerprint: str
    category: str
    summary: str
    guidance: str
    example: str
    count: int
    first_seen: str
    last_seen: str
    source: str = ""
    project_name: str = ""
    raw_excerpt: str = ""


@dataclass(frozen=True)
class LearningSummary:
    ok: bool
    lesson_count: int
    path: Path
    message: str


@dataclass(frozen=True)
class ChatTranscriptEntry:
    role: str
    content: str
    timestamp: str
    source: str = ""
    project_name: str = ""


@dataclass(frozen=True)
class MemoryStatus:
    memory_root: Path
    chat_count: int
    lesson_count: int
    has_learned_skill: bool
    total_bytes: int


class ErrorLearningStore:
    """Append/update recurring GDL error lessons for a personal workspace."""

    def __init__(self, work_dir: str | Path):
        self.work_dir = Path(work_dir)
        self.memory_root = self.work_dir / MEMORY_DIR
        self.root = self.work_dir / LEARNINGS_DIR
        self.chats_root = self.work_dir / CHATS_DIR
        self.skills_root = self.work_dir / SKILLS_DIR
        self.legacy_root = self.work_dir / LEGACY_LEARNINGS_DIR
        self.error_lessons_path = self.root / ERROR_LESSONS_FILE
        self.legacy_error_lessons_path = self.legacy_root / ERROR_LESSONS_FILE
        self.chat_transcript_path = self.chats_root / CHAT_TRANSCRIPT_FILE
        self.legacy_chat_transcript_path = self.legacy_root / CHAT_TRANSCRIPT_FILE
        self.learned_skill_path = self.skills_root / LEARNED_SKILL_FILE
        self.legacy_learned_skill_path = self.legacy_root / LEARNED_SKILL_FILE

    def record_error(
        self,
        raw_error: str,
        *,
        source: str,
        project_name: str = "",
        instruction: str = "",
    ) -> ErrorLesson | None:
        raw = _clean(raw_error)
        if not raw:
            return None

        now = _dt.datetime.now().isoformat(timespec="seconds")
        category = classify_error(raw)
        summary = summarize_error(raw, category)
        guidance = guidance_for_category(category)
        fingerprint = error_fingerprint(raw, category)
        raw_excerpt = raw[:1200]

        lessons = self.list_error_lessons()
        existing = next((lesson for lesson in lessons if lesson.fingerprint == fingerprint), None)
        if existing:
            existing.count += 1
            existing.last_seen = now
            existing.source = source or existing.source
            existing.project_name = project_name or existing.project_name
            existing.raw_excerpt = raw_excerpt
            lesson = existing
        else:
            lesson = ErrorLesson(
                fingerprint=fingerprint,
                category=category,
                summary=summary,
                guidance=guidance,
                example=_clean(instruction)[:500],
                count=1,
                first_seen=now,
                last_seen=now,
                source=source,
                project_name=project_name,
                raw_excerpt=raw_excerpt,
            )
            lessons.append(lesson)

        self._write_lessons(lessons)
        return lesson

    def list_error_lessons(self, *, include_seed: bool = False) -> list[ErrorLesson]:
        lessons: list[ErrorLesson] = developer_error_lessons() if include_seed else []
        paths = _current_then_legacy_paths(
            self.error_lessons_path,
            self.legacy_error_lessons_path,
        )
        if not paths:
            return lessons

        try:
            for path in paths:
                for line in path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    _merge_lesson(lessons, _lesson_from_dict(json.loads(line)))
        except Exception:
            return lessons
        return lessons

    def build_skill_prompt(self, *, project_name: str = "", limit: int = 8) -> str:
        compacted = self.load_learned_skill()
        workspace_recent = build_error_learning_skill(
            self.list_error_lessons(include_seed=False),
            project_name=project_name,
            limit=limit,
            layer_name="workspace_gdl_error_avoidance",
            layer_description="这些规则来自当前 OpenBrep 个人工作空间的真实聊天、编译和纠错记录。",
        )
        developer_baseline = build_error_learning_skill(
            developer_error_lessons(),
            project_name="",
            limit=limit,
            layer_name="developer_gdl_error_baseline",
            layer_description="这些规则来自 OpenBrep 源码随附的开发者基线经验，作为所有用户的兜底约束。",
        )
        return "\n\n---\n\n".join(
            part for part in (compacted, workspace_recent, developer_baseline) if part
        )

    def load_learned_skill(self) -> str:
        for path in (self.learned_skill_path, self.legacy_learned_skill_path):
            if not path.exists():
                continue
            try:
                return path.read_text(encoding="utf-8").strip()
            except Exception:
                return ""
        return ""

    def append_chat_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        project_name: str = "",
        source: str = "ui_chat",
    ) -> int:
        entries: list[ChatTranscriptEntry] = []
        now = _dt.datetime.now().isoformat(timespec="seconds")
        for message in messages:
            content = _message_content_to_text(message.get("content", ""))
            if not content:
                continue
            entries.append(
                ChatTranscriptEntry(
                    role=str(message.get("role", "")),
                    content=content,
                    timestamp=now,
                    source=source,
                    project_name=project_name,
                )
            )
        if not entries:
            return 0

        self.chats_root.mkdir(parents=True, exist_ok=True)
        with self.chat_transcript_path.open("a", encoding="utf-8") as fh:
            for entry in entries:
                fh.write(json.dumps(_chat_entry_to_dict(entry), ensure_ascii=False, sort_keys=True))
                fh.write("\n")
        return len(entries)

    def rewrite_chat_transcript(
        self,
        messages: list[dict[str, Any]] | list[ChatTranscriptEntry],
        *,
        project_name: str = "",
        source: str = "ui_chat",
    ) -> int:
        entries: list[ChatTranscriptEntry] = []
        now = _dt.datetime.now().isoformat(timespec="seconds")
        for message in messages or []:
            if isinstance(message, ChatTranscriptEntry):
                content = _message_content_to_text(message.content)
                role = message.role
                timestamp = message.timestamp or now
                entry_source = message.source or source
                entry_project = message.project_name or project_name
            else:
                content = _message_content_to_text(message.get("content", ""))
                role = str(message.get("role", ""))
                timestamp = now
                entry_source = source
                entry_project = project_name
            if not content:
                continue
            entries.append(
                ChatTranscriptEntry(
                    role=role,
                    content=content,
                    timestamp=timestamp,
                    source=entry_source,
                    project_name=entry_project,
                )
            )

        self.chats_root.mkdir(parents=True, exist_ok=True)
        with self.chat_transcript_path.open("w", encoding="utf-8") as fh:
            for entry in entries:
                fh.write(json.dumps(_chat_entry_to_dict(entry), ensure_ascii=False, sort_keys=True))
                fh.write("\n")
        return len(entries)

    def list_chat_transcript(self) -> list[ChatTranscriptEntry]:
        entries: list[ChatTranscriptEntry] = []
        paths = _current_then_legacy_paths(
            self.chat_transcript_path,
            self.legacy_chat_transcript_path,
        )
        if not paths:
            return entries
        try:
            for path in paths:
                for line in path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    entries.append(_chat_entry_from_dict(json.loads(line)))
        except Exception:
            return entries
        return entries

    def memory_status(self) -> MemoryStatus:
        """Return a privacy-facing summary of persisted workspace memory."""
        return MemoryStatus(
            memory_root=self.memory_root,
            chat_count=len(self.list_chat_transcript()),
            lesson_count=len(self.list_error_lessons(include_seed=False)),
            has_learned_skill=bool(self.load_learned_skill()),
            total_bytes=_directory_size(self.memory_root) + _directory_size(self.legacy_root),
        )

    def export_memory(self, target_dir: str | Path, *, overwrite: bool = False) -> Path:
        """Copy persisted workspace memory to ``target_dir`` for user review or backup."""
        target = Path(target_dir)
        if target.exists():
            if not overwrite:
                raise FileExistsError(f"Target already exists: {target}")
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()

        target.mkdir(parents=True, exist_ok=True)
        copied = False
        if self.memory_root.exists():
            _copy_tree_contents(self.memory_root, target)
            copied = True
        if self.legacy_root.exists():
            legacy_target = target / "legacy_learnings"
            _copy_tree_contents(self.legacy_root, legacy_target)
            copied = True

        status = self.memory_status()
        manifest = {
            "schema_version": 1,
            "source_work_dir": str(self.work_dir),
            "memory_root": str(self.memory_root),
            "chat_count": status.chat_count,
            "lesson_count": status.lesson_count,
            "has_learned_skill": status.has_learned_skill,
            "exported_at": _dt.datetime.now().isoformat(timespec="seconds"),
            "copied_files": copied,
        }
        (target / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return target

    def clear_memory(self) -> MemoryStatus:
        """Remove persisted workspace learning memory while preserving projects and revisions."""
        before = self.memory_status()
        for root in (self.memory_root, self.legacy_root):
            if root.exists():
                shutil.rmtree(root)
        return before

    def learn_from_chat_transcript(self, *, project_name: str = "") -> int:
        learned = 0
        for entry in self.list_chat_transcript():
            if project_name and entry.project_name and entry.project_name != project_name:
                continue
            if not looks_like_error_report(entry.content):
                continue
            lesson = self.record_error(
                entry.content,
                source="chat_transcript_scan",
                project_name=entry.project_name or project_name,
                instruction="从持久化聊天记录中整理出的脚本错误线索。",
            )
            if lesson is not None:
                learned += 1
        return learned

    def summarize_to_skill(
        self,
        *,
        project_name: str = "",
        limit: int = 12,
        scan_chat: bool = True,
    ) -> LearningSummary:
        scanned_count = self.learn_from_chat_transcript(project_name=project_name) if scan_chat else 0
        lessons = self.list_error_lessons(include_seed=True)
        skill = build_compacted_learning_skill(lessons, project_name=project_name, limit=limit)
        if not skill:
            return LearningSummary(
                ok=False,
                lesson_count=0,
                path=self.learned_skill_path,
                message="暂无可整理的错题记录",
            )

        self.skills_root.mkdir(parents=True, exist_ok=True)
        self.learned_skill_path.write_text(skill + "\n", encoding="utf-8")
        selected_count = min(
            len(_select_relevant_lessons(lessons, project_name=project_name)),
            limit,
        )
        return LearningSummary(
            ok=True,
            lesson_count=selected_count,
            path=self.learned_skill_path,
            message=f"已整理 {selected_count} 条错题约束，扫描聊天命中 {scanned_count} 条",
        )

    def _write_lessons(self, lessons: list[ErrorLesson]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(_lesson_to_dict(lesson), ensure_ascii=False, sort_keys=True) for lesson in lessons]
        self.error_lessons_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def build_error_learning_skill(
    lessons: list[ErrorLesson],
    *,
    project_name: str = "",
    limit: int = 8,
    layer_name: str = "learned_gdl_error_avoidance",
    layer_description: str = "这些规则来自本机真实 Archicad/LP_XMLConverter 错误与用户纠错记录。",
) -> str:
    if not lessons:
        return ""

    selected = _select_relevant_lessons(lessons, project_name=project_name)[:limit]
    if not selected:
        return ""

    lines = [
        f"## Skill: {layer_name}",
        "",
        layer_description,
        "生成或修复 GDL 时必须优先规避这些已发生过的问题。",
        "",
        "### Recurring Lessons",
    ]
    for idx, lesson in enumerate(selected, 1):
        summary = _sanitize_learning_text(lesson.summary)
        lines.append(f"{idx}. **{lesson.category}**（出现 {lesson.count} 次）: {summary}")
        lines.append(f"   - 约束: {lesson.guidance}")
        if lesson.raw_excerpt:
            excerpt = _sanitize_learning_text(lesson.raw_excerpt)[:220]
            if excerpt:
                lines.append(f"   - 错误模式摘录: `{excerpt}`")

    lines.extend([
        "",
        "### Required Behavior",
        "- 对照上述错题先做自检，再输出脚本。",
        "- 如果用户贴出真实 Archicad 错误，先分类，再做最小修复。",
        "- 不要为了修一个错误大面积重写无关脚本。",
    ])
    return "\n".join(lines)


def build_compacted_learning_skill(
    lessons: list[ErrorLesson],
    *,
    project_name: str = "",
    limit: int = 12,
) -> str:
    selected = _select_relevant_lessons(lessons, project_name=project_name)[:limit]
    if not selected:
        return ""

    generated_at = _dt.datetime.now().isoformat(timespec="seconds")
    categories = sorted({lesson.category for lesson in selected})
    lines = [
        "# Skill: learned_gdl_error_avoidance_compacted",
        "",
        f"- Generated: {generated_at}",
        f"- Project: {project_name or 'workspace'}",
        f"- Source lessons: {len(selected)}",
        f"- Categories: {', '.join(categories)}",
        "",
        "## Success Criteria",
        "",
        "- 生成、修改或修复 GDL 前，必须先按本 skill 做一次错题自检。",
        "- 遇到用户粘贴的 Archicad/GDL Copilot 真实错误，"
        "优先抽象成可复用约束，而不是只修当前行。",
        "- 修复策略以最小变更为主，"
        "避免为单个错误重写无关脚本、参数或项目结构。",
        "",
        "## Hard Constraints",
    ]
    for lesson in selected:
        lines.append(f"- **{lesson.category}**（{lesson.count} 次）: {lesson.guidance}")

    lines.extend([
        "",
        "## Representative Lessons",
    ])
    for idx, lesson in enumerate(selected, 1):
        lines.append(f"{idx}. {_sanitize_learning_text(lesson.summary)}")
        if lesson.raw_excerpt:
            excerpt = _sanitize_learning_text(lesson.raw_excerpt)[:260]
            if excerpt:
                lines.append(f"   - Error pattern: `{excerpt}`")

    return "\n".join(lines)


def _select_relevant_lessons(
    lessons: list[ErrorLesson],
    *,
    project_name: str = "",
) -> list[ErrorLesson]:
    relevant = [
        lesson
        for lesson in lessons
        if not project_name or not lesson.project_name or lesson.project_name == project_name
    ]
    relevant.sort(key=lambda item: (-item.count, item.last_seen, item.category))
    return relevant


def _merge_lesson(lessons: list[ErrorLesson], lesson: ErrorLesson) -> None:
    existing = next((item for item in lessons if item.fingerprint == lesson.fingerprint), None)
    if existing:
        existing.count += lesson.count
        existing.last_seen = max(existing.last_seen, lesson.last_seen)
        existing.source = lesson.source or existing.source
        existing.project_name = lesson.project_name or existing.project_name
        existing.raw_excerpt = lesson.raw_excerpt or existing.raw_excerpt
        existing.example = lesson.example or existing.example
        return
    lessons.append(lesson)


def _current_then_legacy_paths(current: Path, legacy: Path) -> list[Path]:
    if current.exists():
        return [current]
    if legacy.exists() and legacy != current:
        return [legacy]
    return []


def _directory_size(root: Path) -> int:
    if not root.exists():
        return 0
    total = 0
    for path in root.rglob("*"):
        if path.is_file():
            try:
                total += path.stat().st_size
            except OSError:
                continue
    return total


def _copy_tree_contents(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for child in source.iterdir():
        destination = target / child.name
        if child.is_dir():
            shutil.copytree(child, destination, dirs_exist_ok=True)
        elif child.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(child, destination)


def developer_error_lessons() -> list[ErrorLesson]:
    """Built-in developer baseline lessons shipped with OpenBrep source code."""
    return [
        ErrorLesson(
            fingerprint="missing_call_keyword:developer-000001",
            category="missing_call_keyword",
            summary=(
                "开发者基线错题：GDL Copilot/Archicad 检查发现 3D 与 Master 脚本中"
                "标签式调用存在“缺少 CALL 关键字(不推荐写法)”问题。"
            ),
            guidance=guidance_for_category("missing_call_keyword"),
            example="文件《钢结构节点_v4.gsm》3D脚本与Master脚本多行出现“缺少CALL关键字(不推荐写法)”。",
            count=1,
            first_seen=_SEED_FIRST_SEEN,
            last_seen=_SEED_FIRST_SEEN,
            source="openbrep_developer_baseline",
            project_name="",
            raw_excerpt=(
                "文件《钢结构节点_v4.gsm》存在两类问题:3D脚本出现"
                "“缺少CALL关键字(不推荐写法)”;Master脚本出现类似问题"
            ),
        )
    ]


def seed_error_lessons() -> list[ErrorLesson]:
    """Backward-compatible name for developer baseline lessons."""
    return developer_error_lessons()


def classify_error(raw_error: str) -> str:
    text = raw_error.lower()
    if _looks_like_user_summary(raw_error):
        if "call" in text and ("缺少" in raw_error or "不推荐写法" in raw_error):
            return "missing_call_keyword"
        return "user_summarized_archicad_issue"
    if any(term in text for term in ("not enough parameters", "too few parameters")):
        return "command_arguments"
    if any(term in text for term in ("endif", "end if", "next expected", "end expected", "unexpected end")):
        return "control_flow_closure"
    if any(term in text for term in ("wrong number of", "missing parameter", "too few parameters", "too many parameters", "argument")):
        return "command_arguments"
    if any(term in text for term in ("undefined variable", "uninitialized variable", "not initialized", "未初始化", "未定义")):
        return "variable_mapping"
    if any(term in text for term in ("division by zero", "zero divide", "除以零")):
        return "numeric_guard"
    if any(term in text for term in ("paramlist", "parameter", "参数", "xml parse", "not well-formed")):
        return "parameter_xml"
    if any(term in text for term in ("add", "del", "transformation", "transform", "坐标")):
        return "transform_balance"
    if any(term in text for term in ("project2", "2d script", "symbol", "hotspot2")):
        return "2d_symbol"
    return "general_compile_error"


def summarize_error(raw_error: str, category: str) -> str:
    summarized = _summarize_user_error_report(raw_error, category)
    if summarized:
        return summarized
    first_line = next((line.strip() for line in raw_error.splitlines() if line.strip()), "")
    if first_line:
        return _sanitize_learning_text(first_line)[:180]
    return category.replace("_", " ")


def guidance_for_category(category: str) -> str:
    guidance = {
        "control_flow_closure": "逐段核对 IF/ENDIF、FOR/NEXT、GOSUB/RETURN；单行 IF THEN 不额外添加 ENDIF。",
        "command_arguments": "查命令签名，尤其 PRISM_/TUBE/REVOLVE 等命令的数量、顺序、mask 与高度参数。",
        "variable_mapping": "所有变量必须来自 paramlist.xml 或先在 1d.gdl 中赋值；禁止 width/height/depth 等语义别名漂移。",
        "numeric_guard": "所有除法、比例和数组/循环边界先做最小值保护，分母不能为 0。",
        "parameter_xml": "参数名、类型和值必须与脚本一致；XML 只用结构化生成，不手写破坏标签。",
        "transform_balance": "每个 ADD/ROT/MUL 变换块必须有匹配 DEL，嵌套变换按栈成对关闭。",
        "2d_symbol": "2D 脚本至少提供 PROJECT2 或基础绘图/热点，且不要依赖 3D 中未定义的局部变量。",
        "missing_call_keyword": "出现子程序、宏或标签式调用时显式使用 CALL/规范调用形式；不要让脚本依赖 Archicad 可编译但不推荐的省略写法。",
        "user_summarized_archicad_issue": "用户总结的真实 Archicad 检查结果视为高优先级约束；抽取错误类型、脚本范围和错误短语，生成前逐条自检。",
        "general_compile_error": "先定位文件和行号，做最小修复，再回归检查参数、结构闭合和变换平衡。",
    }
    return guidance.get(category, guidance["general_compile_error"])


def error_fingerprint(raw_error: str, category: str) -> str:
    normalized = _normalize_for_fingerprint(raw_error)
    digest = hashlib.sha1(f"{category}\n{normalized}".encode("utf-8")).hexdigest()[:12]
    return f"{category}:{digest}"


def looks_like_error_report(text: str) -> bool:
    raw = text or ""
    lower = raw.lower()
    return (
        "archicad gdl 错误报告" in raw
        or "错误日志" in raw
        or _looks_like_user_summary(raw)
        or "compile failed" in lower
        or "lp_xmlconverter" in lower
        or _looks_like_script_error_fragment(raw)
        or bool(re.search(r"(error|warning)\s+in\s+\w[\w\s]*script[,\s]+line\s+\d+", raw, re.IGNORECASE))
    )


def _normalize_for_fingerprint(raw: str) -> str:
    text = raw.lower()
    text = re.sub(r"《[^》]+\.gsm》", "《<gsm>》", text)
    text = re.sub(r"第[\d、,，\s]+行", "第<n>行", text)
    text = re.sub(r"/[\w./\- ]+", "<path>", text)
    text = re.sub(r"\bline\s+\d+\b", "line <n>", text)
    text = re.sub(r"\b\d+\b", "<n>", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:500]


def _clean(text: str) -> str:
    return re.sub(r"\s+\n", "\n", str(text or "").strip())


def _sanitize_learning_text(text: str) -> str:
    """Remove volatile source locations before text is injected into prompts."""
    cleaned = str(text or "").replace("\n", " ")
    cleaned = re.sub(r"第[\d、,，\s]+行", "", cleaned)
    cleaned = re.sub(r"\bat\s+line\s+\d+\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bline\s+\d+\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*,\s*in\s+the\s+", " in the ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.replace("脚本出现", "脚本出现")
    cleaned = re.sub(r"脚本\s*[:：,，;；]\s*", "脚本: ", cleaned)
    return cleaned.strip(" ;；,，")


def _looks_like_user_summary(raw: str) -> bool:
    return bool(
        re.search(r"文件《[^》]+\.gsm》存在", raw)
        or (
            re.search(r"(3D|2D|Master|参数|UI)\s*脚本第[\d、,，\s]+行", raw, re.IGNORECASE)
            and any(marker in raw for marker in ("出现", "存在", "缺少", "错误", "不推荐写法"))
        )
    )


def _looks_like_script_error_fragment(raw: str) -> bool:
    text = raw or ""
    lower = text.lower()
    has_script = bool(re.search(r"\b(3d|2d|master|ui)\s*(script|脚本)\b", lower, re.IGNORECASE))
    has_line = bool(re.search(r"\bline\s+\d+\b|第[\d、,，\s]+行", text, re.IGNORECASE))
    has_error = any(term in lower for term in (
        "error",
        "not enough parameters",
        "wrong number of",
        "undefined variable",
        "warning",
        "错误",
        "报错",
        "缺少",
        "未定义",
        "未初始化",
    ))
    has_gsm = ".gsm" in lower
    return has_error and has_script and (has_line or has_gsm)


def _summarize_user_error_report(raw: str, category: str) -> str:
    if not _looks_like_user_summary(raw):
        return ""

    file_match = re.search(r"文件《([^》]+)》", raw)
    file_part = f"{file_match.group(1)}: " if file_match else ""
    script_matches = re.findall(
        r"((?:3D|2D|Master|参数|UI)\s*脚本)第[\d、,，\s]+行",
        raw,
        flags=re.IGNORECASE,
    )
    phrase_match = re.search(r"“([^”]+)”", raw)
    phrase = phrase_match.group(1) if phrase_match else ""

    script_names = []
    for script_name in script_matches[:3]:
        normalized_script = re.sub(r"\s+", "", script_name)
        if normalized_script not in script_names:
            script_names.append(normalized_script)

    category_text = {
        "missing_call_keyword": "缺少 CALL 关键字/不推荐调用写法",
        "user_summarized_archicad_issue": "用户总结的 Archicad 检查问题",
    }.get(category, "用户总结的 Archicad 检查问题")
    detail = f"；错误短语：{phrase}" if phrase else ""
    script_text = f"；脚本范围：{'、'.join(script_names)}" if script_names else ""
    return _sanitize_learning_text(f"{file_part}{category_text}{script_text}{detail}")[:220]


def _lesson_from_dict(data: dict[str, Any]) -> ErrorLesson:
    return ErrorLesson(
        fingerprint=str(data.get("fingerprint", "")),
        category=str(data.get("category", "general_compile_error")),
        summary=str(data.get("summary", "")),
        guidance=str(data.get("guidance", "")),
        example=str(data.get("example", "")),
        count=int(data.get("count", 1) or 1),
        first_seen=str(data.get("first_seen", "")),
        last_seen=str(data.get("last_seen", "")),
        source=str(data.get("source", "")),
        project_name=str(data.get("project_name", "")),
        raw_excerpt=str(data.get("raw_excerpt", "")),
    )


def _lesson_to_dict(lesson: ErrorLesson) -> dict[str, Any]:
    return {
        "fingerprint": lesson.fingerprint,
        "category": lesson.category,
        "summary": lesson.summary,
        "guidance": lesson.guidance,
        "example": lesson.example,
        "count": lesson.count,
        "first_seen": lesson.first_seen,
        "last_seen": lesson.last_seen,
        "source": lesson.source,
        "project_name": lesson.project_name,
        "raw_excerpt": lesson.raw_excerpt,
    }


def _chat_entry_from_dict(data: dict[str, Any]) -> ChatTranscriptEntry:
    return ChatTranscriptEntry(
        role=str(data.get("role", "")),
        content=str(data.get("content", "")),
        timestamp=str(data.get("timestamp", "")),
        source=str(data.get("source", "")),
        project_name=str(data.get("project_name", "")),
    )


def _chat_entry_to_dict(entry: ChatTranscriptEntry) -> dict[str, str]:
    return {
        "role": entry.role,
        "content": entry.content,
        "timestamp": entry.timestamp,
        "source": entry.source,
        "project_name": entry.project_name,
    }


def _message_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return _clean(content)
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                value = item.get("text") or item.get("content") or item.get("caption") or ""
                if value:
                    parts.append(str(value))
            elif item:
                parts.append(str(item))
        return _clean("\n".join(parts))
    if content is None:
        return ""
    return _clean(str(content))
