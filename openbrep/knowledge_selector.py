"""Request-aware GDL knowledge selection."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from openbrep.wiki_knowledge import WikiKnowledge


@dataclass(frozen=True)
class KnowledgeSelection:
    """Selected context for a single GDL request."""

    planner_context: str
    generation_context: str
    source_ids: list[str] = field(default_factory=list)


# ── 优化/审查触发词（AC-4，一处常量） ────────────────────────────────
# 口径：用户输入命中这些词 + 有打开项目 + 非 CREATE/IMAGE 意图时，
# MODIFY/EXPLAIN 路径**强制**注入规则文档全文，不走关键词命中漏斗——
# 因为这类请求用户明确要做选型审查（对照复杂度阶梯的评估），
# 而不是等着按对象类型碰运气命中。只做保守匹配：命中即强制注入，
# 宁多勿漏（注入的是只读知识，不产生副作用）。
REVIEW_TRIGGER_WORDS: tuple[str, ...] = (
    # 中文：优化/审查/检查/改进/精简
    "优化", "审查", "检查", "改进", "精简", "选型", "建模方式", "重构",
    # 英文：refactor / optimize / review / improve / simplify / streamline
    "refactor", "optimize", "review", "improve", "simplify",
    "streamline", "redo", "assess", "evaluate",
)


# 规则文档在 knowledge/core/ 下的文件名（正文内禁用 `--- 分隔符`，见 AC-1）
_COMMAND_SELECTION_DOC = "gdl_command_selection"


def _hit_review_trigger(instruction: str) -> bool:
    """保守命中：英文词按正则（re.IGNORECASE 在调用处），中文直接子串。"""
    text = instruction or ""
    low = text.lower()
    for word in REVIEW_TRIGGER_WORDS:
        if word.isascii():
            if re.search(word, low):
                return True
        elif word in text:
            return True
    return False


def load_command_selection_rules(knowledge_dir: str | Path) -> str:
    """读取规则文档正文（去 frontmatter），供 AC-4 强制注入与测试断言。

    文档缺失/损坏时返回空串（调用处可安全降级）。
    """
    fp = Path(knowledge_dir) / "core" / f"{_COMMAND_SELECTION_DOC}.md"
    try:
        raw = fp.read_text(encoding="utf-8")
    except OSError:
        return ""
    _, body = _split_frontmatter(raw)
    return body.strip()


_OBJECT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "bookshelf": ("书架", "书柜", "层板架", "bookshelf", "shelf", "bookcase"),
    "cabinet": ("柜", "柜体", "收纳柜", "鞋柜", "橱柜", "cabinet", "cupboard"),
    "table": ("桌", "桌子", "餐桌", "书桌", "会议桌", "table", "desk"),
    "door": ("门洞", "平开门", "推拉门", "门扇", "门框", "door"),
    "window": ("窗户", "外窗", "内窗", "窗洞", "window"),
    "profile_object": ("旋转体", "剖面", "放样", "异形板", "profile", "revolve", "sweep", "extrude"),
}

_ARCHETYPE_COMMANDS: dict[str, tuple[str, ...]] = {
    "bookshelf": ("BLOCK", "ADD_DEL", "FOR_NEXT", "PROJECT2", "HOTSPOT2"),
    "cabinet": ("BLOCK", "ADD_DEL", "FOR_NEXT", "PROJECT2", "HOTSPOT2", "PRISM_"),
    "table": ("BLOCK", "ADD_DEL", "FOR_NEXT", "PROJECT2", "HOTSPOT2", "CYLIND"),
    "door": ("BLOCK", "ADD_DEL", "PROJECT2", "HOTSPOT2", "Object_Types"),
    "window": ("BLOCK", "ADD_DEL", "PROJECT2", "HOTSPOT2", "Object_Types"),
    "profile_object": ("PRISM_", "REVOLVE", "SWEEP", "ADD_DEL", "PROJECT2", "HOTSPOT2"),
}

_INTENT_WIKI_HINTS: dict[str, tuple[str, ...]] = {
    "create": ("Paramlist_XML", "Transformation_Stack"),
    "image": ("Paramlist_XML", "Transformation_Stack"),
    "modify": ("Transformation_Stack", "ADD_DEL"),
    "debug": ("ADD_DEL", "FOR_NEXT", "IF_ENDIF"),
    "repair": ("ADD_DEL", "FOR_NEXT", "IF_ENDIF"),
}

# planner 侧 core/ 知识预算：需容纳 plan_contract + generation_discipline +
# parameter_rules + command_selection（HF6 新增）全部存活。
_CORE_MAX_CHARS = 8000


def select_gdl_knowledge(
    *,
    instruction: str,
    intent: str = "all",
    knowledge_dir: str | Path,
    base_context: str = "",
    project_context: str = "",
    project_knowledge: str = "",
) -> KnowledgeSelection:
    """Select compact planner and generation context for a request."""

    root = Path(knowledge_dir)
    task_type = (intent or "all").lower()
    object_keys = _detect_object_keys(instruction)

    source_ids: list[str] = []
    planner_parts: list[str] = []
    generation_parts: list[str] = []

    if project_context:
        planner_parts.append(project_context)
        generation_parts.append(project_context)
        source_ids.append("project.context")

    if project_knowledge:
        planner_parts.append(_section("Project Knowledge", project_knowledge))
        generation_parts.append(_section("Project Knowledge", project_knowledge))
        source_ids.append("project.knowledge")

    # HF6：core/ 知识同时进入 generation 上下文（此前只进 planner）。
    # gdl_command_selection.md 靠这里到达 CREATE 生成与 MODIFY 的 prompt，
    # 是 MODIFY/EXPLAIN agent-loop 的统一注入点（二者都消费
    # generation_context；不在各处散写）。
    planner_core_context, planner_core_sources = _load_core_context(
        root,
        task_type=task_type,
        stage="planner",
        max_chars=_CORE_MAX_CHARS,
    )
    if planner_core_context:
        planner_parts.append(planner_core_context)
        generation_parts.append(planner_core_context)
        source_ids.extend(planner_core_sources)

    archetype_context = _load_archetypes(root, object_keys)
    if archetype_context:
        planner_parts.append(archetype_context)
        generation_parts.append(archetype_context)
        source_ids.extend(f"archetype.{key}" for key in object_keys)

    if task_type in {"create", "image"}:
        wiki_context, wiki_sources = _load_wiki_context(root, instruction, task_type, object_keys)
        if wiki_context:
            planner_parts.append(wiki_context)
            generation_parts.append(wiki_context)
            source_ids.extend(wiki_sources)

    if task_type in {"create", "image", "modify"}:
        example_context, example_sources = _load_examples(root, instruction, task_type, object_keys)
        if example_context:
            generation_parts.append(example_context)
            source_ids.extend(example_sources)

    core_context = _compact_core_context(base_context, task_type=task_type)
    if core_context:
        generation_parts.append(core_context)
        source_ids.append("builtin.core")

    if not planner_parts and core_context:
        planner_parts.append(core_context)

    return KnowledgeSelection(
        planner_context=_join(planner_parts),
        generation_context=_join(generation_parts or [base_context]),
        source_ids=_dedupe(source_ids),
    )


def _detect_object_keys(instruction: str) -> list[str]:
    text = (instruction or "").lower()
    found: list[str] = []
    for key, words in _OBJECT_KEYWORDS.items():
        if any(word.lower() in text for word in words):
            found.append(key)
    return found


def _load_archetypes(root: Path, object_keys: list[str]) -> str:
    parts: list[str] = []
    for key in object_keys:
        fp = root / "archetypes" / f"{key}.md"
        if not fp.is_file():
            continue
        try:
            parts.append(_section(f"Archetype: {key}", fp.read_text(encoding="utf-8")))
        except Exception:
            continue
    return _join(parts)


def _load_examples(
    root: Path,
    instruction: str,
    task_type: str,
    object_keys: list[str],
    *,
    max_examples: int = 2,
) -> tuple[str, list[str]]:
    """按 object_types / commands 匹配 knowledge/examples/ 里的案例，注入生成上下文。

    评分：object_type 与检测到的 object key 相同 +3；object_type 直接出现在
    指令文本里 +2；指令中的大写命令与案例 commands 重叠 +2。零分不注入。
    """
    ex_dir = root / "examples"
    if not ex_dir.is_dir():
        return "", []

    text = (instruction or "").lower()
    instruction_cmds = set(re.findall(r"\b[A-Z][A-Z0-9_]{2,}\b", instruction or ""))
    scored: list[tuple[int, str, str]] = []

    for fp in sorted(ex_dir.glob("*.md")):
        try:
            raw = fp.read_text(encoding="utf-8")
        except Exception:
            continue
        frontmatter, body = _split_frontmatter(raw)
        if frontmatter and not _frontmatter_matches_task(frontmatter, task_type):
            continue
        object_types = [
            t.strip().lower()
            for t in frontmatter.get("object_types", "").strip("[]").split(",")
            if t.strip()
        ]
        commands = {
            c.strip()
            for c in frontmatter.get("commands", "").strip("[]").split(",")
            if c.strip()
        }
        score = 0
        for obj_type in object_types:
            if obj_type in object_keys:
                score += 3
            if obj_type and obj_type in text:
                score += 2
        score += 2 * len(commands & instruction_cmds)
        if score > 0:
            source_id = frontmatter.get("id") or f"example.{fp.stem}"
            scored.append((score, source_id, _section(f"Example: {source_id}", body or raw)))

    if not scored:
        return "", []
    scored.sort(key=lambda item: item[0], reverse=True)
    top = scored[:max_examples]
    return _join(content for _, _, content in top), [source_id for _, source_id, _ in top]


def _load_core_context(
    root: Path,
    *,
    task_type: str,
    stage: str,
    max_chars: int,
) -> tuple[str, list[str]]:
    core_dir = root / "core"
    if not core_dir.is_dir():
        return "", []

    candidates: list[tuple[int, str, str]] = []
    for fp in sorted(core_dir.glob("*.md")):
        try:
            raw = fp.read_text(encoding="utf-8")
        except Exception:
            continue
        frontmatter, body = _split_frontmatter(raw)
        if frontmatter and not _frontmatter_matches_task(frontmatter, task_type):
            continue
        priority = _parse_priority(frontmatter.get("priority", "0"))
        source_id = frontmatter.get("id") or f"core.{fp.stem}"
        candidates.append((priority, source_id, _section(f"Core: {source_id}", body or raw)))

    if not candidates:
        return "", []

    candidates.sort(key=lambda item: item[0], reverse=True)
    parts: list[str] = []
    sources: list[str] = []
    total = 0
    for _priority, source_id, content in candidates:
        if total and total + len(content) > max_chars:
            continue
        parts.append(content)
        sources.append(source_id)
        total += len(content)

    return _join(parts), sources


def _load_wiki_context(
    root: Path,
    instruction: str,
    task_type: str,
    object_keys: list[str],
    *,
    max_pages: int = 5,
    max_chars_per_page: int = 1200,
) -> tuple[str, list[str]]:
    wiki = WikiKnowledge(str(root / "wiki"))
    try:
        wiki.load()
    except Exception:
        return "", []

    slugs: list[str] = []
    for key in object_keys:
        slugs.extend(_ARCHETYPE_COMMANDS.get(key, ()))
    slugs.extend(_INTENT_WIKI_HINTS.get(task_type, ()))

    pages = []
    for slug in _dedupe(slugs):
        page = wiki.get_by_slug(slug)
        if page is not None:
            pages.append(page)

    existing = {page.slug for page in pages}
    for page in wiki.get_relevant(instruction, max_pages=max_pages):
        if page.slug not in existing:
            pages.append(page)
            existing.add(page.slug)

    selected = pages[:max_pages]
    if not selected:
        return "", []
    return (
        _join(_format_wiki_page_compact(page, max_chars=max_chars_per_page) for page in selected),
        [f"wiki.{page.slug}" for page in selected],
    )


def _format_wiki_page_compact(page, *, max_chars: int) -> str:
    formatted = page.format_for_context()
    if max_chars <= 0 or len(formatted) <= max_chars:
        return formatted
    head, advice = _split_wiki_head_and_advice(formatted)
    if not advice:
        return formatted[:max_chars].rstrip() + "\n\n[truncated]"
    # 预算内保留：头部（标题/元信息/语法）优先，选型建议段（页尾
    # Traps/Optimization/Recommended）整体追加——截断不误伤选择建议。
    head_budget = max(200, max_chars - len(advice) - 60)
    head = head[:head_budget].rstrip() or ""
    parts = [p for p in [head, advice] if p]
    return "\n\n".join(parts) + "\n\n[truncated]"


# 选型建议段标题关键词：命中即整体保留（大小写不敏感）。
# HF6：600 字符头截断会把页尾 Edge Cases & Traps / Optimization 段切掉，
# 而这些正是“选择建议”。
_WIKI_ADVICE_HEADING_TOKENS: tuple[str, ...] = (
    "edge cases", "traps", "optimization", "recommended", "选择", "边界",
    "陷阱", "优化", "建议", "何时", "when to", "use cases",
)


def _split_wiki_head_and_advice(formatted: str) -> tuple[str, str]:
    """把 wiki 页拆成 (头部, 选型建议段)。

    按 `## ` 二级标题切分；标题命中 _WIKI_ADVICE_HEADING_TOKENS 的段落
    归入建议段（保留原顺序），其余（含标题前的元信息块）归入头部。
    """
    lines = formatted.splitlines()
    head_lines: list[str] = []
    advice_lines: list[str] = []
    cur_is_advice = False
    cur: list[str] = []

    def flush():
        nonlocal cur, cur_is_advice
        if cur:
            if cur_is_advice:
                advice_lines.extend(cur)
            else:
                head_lines.extend(cur)
        cur = []

    for line in lines:
        m = re.match(r"^## (.+)$", line.strip())
        if m:
            flush()
            cur_is_advice = any(tok in m.group(1).lower() for tok in _WIKI_ADVICE_HEADING_TOKENS)
        cur.append(line)
    flush()
    return "\n".join(head_lines).strip(), "\n".join(advice_lines).strip()


def _split_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    text = raw or ""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    fm_text = text[3:end].strip()
    body = text[end + len("\n---"):].strip()
    frontmatter: dict[str, str] = {}
    for line in fm_text.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        frontmatter[key.strip()] = value.strip()
    return frontmatter, body


def _frontmatter_matches_task(frontmatter: dict[str, str], task_type: str) -> bool:
    raw = frontmatter.get("task_types", "")
    if not raw:
        return True
    items = [item.strip().lower() for item in raw.strip("[]").split(",") if item.strip()]
    return (task_type or "").lower() in items or "all" in items


def _parse_priority(raw: str) -> int:
    try:
        return int(str(raw).strip())
    except Exception:
        return 0


def _compact_core_context(base_context: str, *, task_type: str) -> str:
    if not base_context:
        return ""

    wanted = {
        "create": ("GDL_quick_reference", "GDL_parameters", "GDL_control_flow", "GDL_common_errors"),
        "image": ("GDL_quick_reference", "GDL_parameters", "GDL_control_flow", "GDL_common_errors"),
        "modify": ("GDL_parameters", "GDL_control_flow", "GDL_common_errors"),
        "debug": ("GDL_common_errors", "GDL_control_flow"),
        "repair": ("GDL_common_errors", "GDL_control_flow"),
    }.get(task_type, ("GDL_quick_reference", "GDL_common_errors"))

    sections = _split_markdown_sections(base_context)
    parts = [content for name, content in sections if any(token in name for token in wanted)]
    if not parts:
        return base_context[:12000]
    return _join(parts)[:16000]


def _split_markdown_sections(text: str) -> list[tuple[str, str]]:
    chunks = re.split(r"\n---\n", text or "")
    sections: list[tuple[str, str]] = []
    for chunk in chunks:
        stripped = chunk.strip()
        if not stripped:
            continue
        first = stripped.splitlines()[0] if stripped.splitlines() else ""
        sections.append((first, stripped))
    return sections


def _section(title: str, body: str) -> str:
    return f"## {title}\n\n{body.strip()}"


def _join(parts) -> str:
    return "\n\n---\n\n".join(str(part).strip() for part in parts if str(part).strip())


def _dedupe(items) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
