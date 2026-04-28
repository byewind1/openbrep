"""Project-level learning memory for recurring GDL errors.

This module stores raw compile/runtime failures as structured lessons under
``<work_dir>/.openbrep/learnings`` and turns the most relevant lessons into a
small dynamic skill section for prompt injection.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


LEARNINGS_DIR = ".openbrep/learnings"
ERROR_LESSONS_FILE = "error_lessons.jsonl"


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


class ErrorLearningStore:
    """Append/update recurring GDL error lessons for a workspace."""

    def __init__(self, work_dir: str | Path):
        self.work_dir = Path(work_dir)
        self.root = self.work_dir / LEARNINGS_DIR
        self.error_lessons_path = self.root / ERROR_LESSONS_FILE

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

    def list_error_lessons(self) -> list[ErrorLesson]:
        if not self.error_lessons_path.exists():
            return []

        lessons: list[ErrorLesson] = []
        try:
            for line in self.error_lessons_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                data = json.loads(line)
                lessons.append(_lesson_from_dict(data))
        except Exception:
            return lessons
        return lessons

    def build_skill_prompt(self, *, project_name: str = "", limit: int = 8) -> str:
        return build_error_learning_skill(
            self.list_error_lessons(),
            project_name=project_name,
            limit=limit,
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
) -> str:
    if not lessons:
        return ""

    relevant = [
        lesson
        for lesson in lessons
        if not project_name or not lesson.project_name or lesson.project_name == project_name
    ]
    relevant.sort(key=lambda item: (-item.count, item.last_seen, item.category))
    selected = relevant[:limit]
    if not selected:
        return ""

    lines = [
        "## Skill: learned_gdl_error_avoidance",
        "",
        "这些规则来自本机真实 Archicad/LP_XMLConverter 错误与用户纠错记录。",
        "生成或修复 GDL 时必须优先规避这些已发生过的问题。",
        "",
        "### Recurring Lessons",
    ]
    for idx, lesson in enumerate(selected, 1):
        lines.append(f"{idx}. **{lesson.category}**（出现 {lesson.count} 次）: {lesson.summary}")
        lines.append(f"   - 约束: {lesson.guidance}")
        if lesson.raw_excerpt:
            excerpt = lesson.raw_excerpt.replace("\n", " ")[:220]
            lines.append(f"   - 最近错误摘录: `{excerpt}`")

    lines.extend([
        "",
        "### Required Behavior",
        "- 对照上述错题先做自检，再输出脚本。",
        "- 如果用户贴出真实 Archicad 错误，先分类，再做最小修复。",
        "- 不要为了修一个错误大面积重写无关脚本。",
    ])
    return "\n".join(lines)


def classify_error(raw_error: str) -> str:
    text = raw_error.lower()
    if _looks_like_user_summary(raw_error):
        if "call" in text and ("缺少" in raw_error or "不推荐写法" in raw_error):
            return "missing_call_keyword"
        return "user_summarized_archicad_issue"
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
        return first_line[:180]
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
        "user_summarized_archicad_issue": "用户总结的真实 Archicad 检查结果视为高优先级约束；抽取文件、脚本、行号和错误短语，生成前逐条自检。",
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


def _looks_like_user_summary(raw: str) -> bool:
    return bool(
        re.search(r"文件《[^》]+\.gsm》存在", raw)
        or (
            re.search(r"(3D|2D|Master|参数|UI)\s*脚本第[\d、,，\s]+行", raw, re.IGNORECASE)
            and any(marker in raw for marker in ("出现", "存在", "缺少", "错误", "不推荐写法"))
        )
    )


def _summarize_user_error_report(raw: str, category: str) -> str:
    if not _looks_like_user_summary(raw):
        return ""

    file_match = re.search(r"文件《([^》]+)》", raw)
    file_part = f"{file_match.group(1)}: " if file_match else ""
    script_matches = re.findall(r"((?:3D|2D|Master|参数|UI)\s*脚本)第([\d、,，\s]+)行", raw, flags=re.IGNORECASE)
    phrase_match = re.search(r"“([^”]+)”", raw)
    phrase = phrase_match.group(1) if phrase_match else ""

    locations = []
    for script_name, lines in script_matches[:3]:
        normalized_lines = re.sub(r"\s+", "", lines)
        locations.append(f"{script_name}第{normalized_lines}行")

    category_text = {
        "missing_call_keyword": "缺少 CALL 关键字/不推荐调用写法",
        "user_summarized_archicad_issue": "用户总结的 Archicad 检查问题",
    }.get(category, "用户总结的 Archicad 检查问题")
    detail = f"；错误短语：{phrase}" if phrase else ""
    where = "；".join(locations)
    where_text = f"；位置：{where}" if where else ""
    return f"{file_part}{category_text}{where_text}{detail}"[:220]


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
