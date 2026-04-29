from __future__ import annotations

import re


_FENCE_START_RE = re.compile(r"^```[a-zA-Z0-9_-]*\s*\n?", re.MULTILINE)
_FENCE_END_RE = re.compile(r"\n?```\s*$", re.MULTILINE)


def strip_md_fences(text: str) -> str:
    cleaned = _FENCE_START_RE.sub("", (text or "").strip())
    cleaned = _FENCE_END_RE.sub("", cleaned.strip())
    return cleaned.strip()


def sanitize_llm_script_output(text: str, path: str = "") -> str:
    """Remove markdown wrappers and trailing prose that should not enter script files."""
    cleaned = strip_md_fences(text)
    if not cleaned:
        return ""

    if _is_script_path(path):
        cleaned = _truncate_at_markdown_separator(cleaned)

    return cleaned.strip()


def _is_script_path(path: str) -> bool:
    lowered = (path or "").lower()
    return lowered.startswith("scripts/") or lowered.endswith(".gdl")


def _truncate_at_markdown_separator(text: str) -> str:
    kept: list[str] = []
    for line in text.splitlines():
        if line.strip() == "---":
            break
        kept.append(line)
    return "\n".join(kept)
