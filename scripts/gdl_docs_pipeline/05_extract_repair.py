"""
Step 5（暂存版）：从全部 GDL PDF Markdown 中抽取错误、约束和规范片段。

输出只进入 generated/gdl_pdf_knowledge/repair/，不写现有 knowledge/。
"""

from __future__ import annotations

import re
from pathlib import Path


OUTPUT_DIR = Path("generated/gdl_pdf_knowledge/repair")
SOURCES = [
    ("reference_guide", Path("/tmp/gdl_raw/reference_guide.md"), "GDL Reference Guide 28"),
    ("handbook", Path("/tmp/gdl_raw/handbook.md"), "GDL Handbook"),
    ("cookbook_v3", Path("/tmp/gdl_raw/cookbook_v3.md"), "GDL Cookbook 3"),
    ("cookbook_v4", Path("/tmp/gdl_raw/cookbook_v4.md"), "GDL Cookbook 4"),
]

ERROR_KEYWORDS = [
    "error",
    "warning",
    "constraint",
    "limitation",
    "restriction",
    "note:",
    "caution",
    "important",
    "avoid",
    "must not",
    "cannot",
    "compile",
    "syntax",
    "stack",
    "undefined",
    "illegal",
    "invalid",
    "forbidden",
    "deprecated",
    "do not",
    "slow",
    "performance",
]


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
        if len(part) < 120:
            continue
        first_line = part.splitlines()[0].strip()
        title = re.sub(r"^#+\s*", "", first_line) if first_line.startswith("#") else f"section_{index:04d}"
        sections.append((title, part))
    return sections


def repair_score(text: str) -> int:
    lower = text.lower()
    return sum(lower.count(keyword) for keyword in ERROR_KEYWORDS)


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
    reset_dir(OUTPUT_DIR)
    total = 0

    for source_id, input_file, source_title in SOURCES:
        content = input_file.read_text(encoding="utf-8", errors="replace")
        sections = split_sections(content)
        source_count = 0
        for index, (title, body) in enumerate(sections, start=1):
            score = repair_score(body)
            if score < 2:
                continue
            slug = slugify(title, f"section_{index:04d}")
            out_path = OUTPUT_DIR / f"{source_id}_{index:04d}_{slug}.md"
            write_page(
                out_path,
                {
                    "id": f"repair.generated.{source_id}.{index:04d}",
                    "type": "repair",
                    "task_types": ["repair", "modify"],
                    "priority": 50 + min(score, 40),
                    "source": f"{source_title} (auto-generated)",
                    "status": "draft",
                    "char_count": len(body),
                    "repair_score": score,
                },
                body,
            )
            source_count += 1
        print(f"{source_id}: repair/constraint 片段 {source_count}")
        total += source_count

    print(f"Repair 草稿完成：{total} -> {OUTPUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
