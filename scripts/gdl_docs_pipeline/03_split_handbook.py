"""
Step 3（暂存版）：把 GDL Handbook 全量拆成可审查章节。

输出只进入 generated/gdl_pdf_knowledge/，不写现有 knowledge/。
同时把明显对象族相关章节复制到 archetypes/ 草稿目录，方便后续筛选。
"""

from __future__ import annotations

import re
from pathlib import Path


INPUT_FILE = Path("/tmp/gdl_raw/handbook.md")
OUTPUT_ROOT = Path("generated/gdl_pdf_knowledge")
SECTIONS_DIR = OUTPUT_ROOT / "handbook_sections"
ARCHETYPES_DIR = OUTPUT_ROOT / "archetypes"


ARCHETYPE_PATTERNS = {
    "door": re.compile(r"\bdoor\b|swing door|hinged|entrance", re.I),
    "window": re.compile(r"\bwindow\b|glazing|casement|sash", re.I),
    "furniture": re.compile(r"\bfurniture\b|shelf|bookcase|cabinet|desk|chair|table|wardrobe", re.I),
    "stair": re.compile(r"\bstair\b|step|riser|tread|landing", re.I),
    "railing": re.compile(r"\brailing\b|baluster|handrail|banister|parapet", re.I),
    "structural": re.compile(r"\bcolumn\b|\bbeam\b|structural|load.bearing", re.I),
    "profile": re.compile(r"\bprofile\b|\bsection\b|molding|extrusion path", re.I),
    "lamp": re.compile(r"\blamp\b|\blight\b|fixture|luminaire|pendant", re.I),
    "ui": re.compile(r"\buser interface\b|\bui_\w+|parameter interface", re.I),
    "workflow": re.compile(r"\bworkflow\b|library part|object making|planning", re.I),
}


def reset_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for old in path.glob("*.md"):
        old.unlink()


def slugify(text: str, fallback: str) -> str:
    text = re.sub(r"^#+\s*", "", text).strip().lower()
    text = re.sub(r"[^a-z0-9_]+", "_", text).strip("_")
    return (text[:70] or fallback).strip("_")


def split_sections(content: str) -> list[tuple[str, str]]:
    parts = re.split(r"\n(?=#{1,6} )", content)
    sections: list[tuple[str, str]] = []
    for index, part in enumerate(parts):
        part = part.strip()
        if len(part) < 180:
            continue
        first_line = part.splitlines()[0].strip()
        title = re.sub(r"^#+\s*", "", first_line) if first_line.startswith("#") else f"section_{index:04d}"
        sections.append((title, part))
    return sections


def classify_archetype(text: str) -> str | None:
    sample = text[:1600]
    for name, pattern in ARCHETYPE_PATTERNS.items():
        if pattern.search(sample):
            return name
    return None


def write_page(path: Path, frontmatter: dict[str, str | int | list[str]], body: str) -> None:
    lines = ["---"]
    for key, value in frontmatter.items():
        if isinstance(value, list):
            rendered = "[" + ", ".join(value) + "]"
        elif isinstance(value, int):
            rendered = str(value)
        else:
            rendered = str(value)
        lines.append(f"{key}: {rendered}")
    lines.append("---\n")
    path.write_text("\n".join(lines) + body.strip() + "\n", encoding="utf-8")


def main() -> int:
    reset_dir(SECTIONS_DIR)
    reset_dir(ARCHETYPES_DIR)

    content = INPUT_FILE.read_text(encoding="utf-8", errors="replace")
    sections = split_sections(content)

    archetype_count = 0
    for index, (title, body) in enumerate(sections, start=1):
        slug = slugify(title, f"section_{index:04d}")
        section_path = SECTIONS_DIR / f"{index:04d}_{slug}.md"
        archetype = classify_archetype(body)
        tags = ["handbook"]
        if archetype:
            tags.append(archetype)

        write_page(
            section_path,
            {
                "id": f"handbook.section.{index:04d}",
                "type": "handbook_section",
                "source": "GDL Handbook",
                "status": "draft",
                "tags": tags,
                "char_count": len(body),
            },
            body,
        )

        if archetype:
            archetype_path = ARCHETYPES_DIR / f"{archetype}_{index:04d}_{slug}.md"
            write_page(
                archetype_path,
                {
                    "id": f"archetype.generated.{archetype}.{index:04d}",
                    "type": "archetype",
                    "object_type": archetype,
                    "task_types": ["create", "modify"],
                    "priority": 55,
                    "source": "GDL Handbook (auto-generated)",
                    "status": "draft",
                    "char_count": len(body),
                },
                body,
            )
            archetype_count += 1

    print("Handbook 拆分完成：")
    print(f"  全量章节: {len(sections)} -> {SECTIONS_DIR}")
    print(f"  archetype 草稿: {archetype_count} -> {ARCHETYPES_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
