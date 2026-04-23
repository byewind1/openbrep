"""
User knowledge — custom flat Markdown files for generation context injection.

Users place their own GDL patterns, conventions, and reference snippets
in a user-configured directory (default: ./user_knowledge/).  Each .md file
is loaded and injected into the generation prompt alongside the built-in
knowledge base, giving the LLM project-specific context.
"""

from __future__ import annotations

from pathlib import Path


def load_user_knowledge(user_dir: str = "./user_knowledge") -> str:
    """
    Load all Markdown files from the user knowledge directory.

    Returns a concatenated string ready for prompt injection, or an empty
    string if the directory does not exist or contains no .md files.
    """
    root = Path(user_dir)
    if not root.is_dir():
        return ""

    parts: list[str] = []
    for md_file in sorted(root.glob("*.md")):
        if md_file.stem.upper() == "README":
            continue
        try:
            content = md_file.read_text(encoding="utf-8")
            parts.append(f"## 用户知识：{md_file.stem}\n\n{content}")
        except Exception:
            continue

    return "\n\n---\n\n".join(parts)
