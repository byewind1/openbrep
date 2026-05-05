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


_OBJECT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "bookshelf": ("书架", "书柜", "层板架", "bookshelf", "shelf", "bookcase"),
    "cabinet": ("柜", "柜体", "收纳柜", "鞋柜", "橱柜", "cabinet", "cupboard"),
}

_ARCHETYPE_COMMANDS: dict[str, tuple[str, ...]] = {
    "bookshelf": ("BLOCK", "ADD_DEL", "FOR_NEXT", "PROJECT2", "HOTSPOT2"),
    "cabinet": ("BLOCK", "ADD_DEL", "FOR_NEXT", "PROJECT2", "HOTSPOT2", "PRISM_"),
}

_INTENT_WIKI_HINTS: dict[str, tuple[str, ...]] = {
    "create": ("Paramlist_XML", "Transformation_Stack"),
    "image": ("Paramlist_XML", "Transformation_Stack"),
    "modify": ("Transformation_Stack", "ADD_DEL"),
    "debug": ("ADD_DEL", "FOR_NEXT", "IF_ENDIF"),
    "repair": ("ADD_DEL", "FOR_NEXT", "IF_ENDIF"),
}


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

    archetype_context = _load_archetypes(root, object_keys)
    if archetype_context:
        planner_parts.append(archetype_context)
        generation_parts.append(archetype_context)
        source_ids.extend(f"archetype.{key}" for key in object_keys)

    wiki_context, wiki_sources = _load_wiki_context(root, instruction, task_type, object_keys)
    if wiki_context:
        planner_parts.append(wiki_context)
        generation_parts.append(wiki_context)
        source_ids.extend(wiki_sources)

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


def _load_wiki_context(
    root: Path,
    instruction: str,
    task_type: str,
    object_keys: list[str],
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
    for page in wiki.get_relevant(instruction, max_pages=4):
        if page.slug not in existing:
            pages.append(page)
            existing.add(page.slug)

    selected = pages[:8]
    if not selected:
        return "", []
    return (
        _join(page.format_for_context() for page in selected),
        [f"wiki.{page.slug}" for page in selected],
    )


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
