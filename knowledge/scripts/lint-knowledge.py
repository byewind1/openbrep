#!/usr/bin/env python3
"""Lint OpenBrep GDL knowledge docs."""

from __future__ import annotations

import os
import re
import sys


FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
REQUIRED_ARCHETYPE_FIELDS = {
    "id",
    "title",
    "type",
    "task_types",
    "object_types",
    "commands",
    "script_types",
    "priority",
    "tags",
}


def lint(root_dir: str) -> int:
    if not os.path.isdir(root_dir):
        print(f"Error: {root_dir} is not a directory", file=sys.stderr)
        return 1

    issues: list[str] = []
    wiki_slugs = _wiki_slugs(os.path.join(root_dir, "wiki"))
    archetype_dir = os.path.join(root_dir, "archetypes")
    if os.path.isdir(archetype_dir):
        for filename in sorted(os.listdir(archetype_dir)):
            if not filename.endswith(".md"):
                continue
            path = os.path.join(archetype_dir, filename)
            fm = extract_frontmatter(path)
            if not fm:
                issues.append(f"MISSING_FRONTMATTER: archetypes/{filename}")
                continue

            missing = sorted(REQUIRED_ARCHETYPE_FIELDS - set(fm))
            for field in missing:
                issues.append(f"MISSING_ARCHETYPE_FIELD: archetypes/{filename} field={field}")
            if fm.get("type") != "archetype":
                issues.append(f"INVALID_ARCHETYPE_TYPE: archetypes/{filename} type={fm.get('type')}")
            if not str(fm.get("id", "")).startswith("archetype."):
                issues.append(f"INVALID_ARCHETYPE_ID: archetypes/{filename} id={fm.get('id')}")

            for command in _parse_inline_list(fm.get("commands", "")):
                if command in {"ADD", "ADDX", "ADDY", "ADDZ", "DEL", "FOR", "NEXT", "ROTZ"}:
                    continue
                if command not in wiki_slugs:
                    issues.append(f"MISSING_COMMAND_WIKI: archetypes/{filename} command={command}")

    if issues:
        for issue in sorted(issues):
            print(issue)
        print(f"\nTotal: {len(issues)} issue(s)")
        return 1

    print("OK: knowledge lint passed")
    return 0


def extract_frontmatter(path: str) -> dict[str, str]:
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    match = FRONTMATTER_RE.match(content)
    if not match:
        return {}
    data: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        data[key.strip()] = value.strip()
    return data


def _wiki_slugs(wiki_dir: str) -> set[str]:
    if not os.path.isdir(wiki_dir):
        return set()
    return {
        os.path.splitext(filename)[0]
        for filename in os.listdir(wiki_dir)
        if filename.endswith(".md")
    }


def _parse_inline_list(raw: str) -> list[str]:
    text = (raw or "").strip()
    if not text:
        return []
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    return [item.strip().strip("'\"") for item in text.split(",") if item.strip()]


if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..")
    sys.exit(lint(os.path.abspath(root)))
