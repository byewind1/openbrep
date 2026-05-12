"""
Step 4（暂存版）：把 GDL Cookbook v3/v4 全量拆成可审查章节。

输出只进入 generated/gdl_pdf_knowledge/，不写现有 knowledge/。
所有可读章节进入 cookbook_sections/；疑似代码案例章节另存到 examples/。
"""

from __future__ import annotations

import re
from pathlib import Path


OUTPUT_ROOT = Path("generated/gdl_pdf_knowledge")
SECTIONS_DIR = OUTPUT_ROOT / "cookbook_sections"
EXAMPLES_DIR = OUTPUT_ROOT / "examples"

SOURCES = [
    ("cookbook_v3", Path("/tmp/gdl_raw/cookbook_v3.md"), "GDL Cookbook 3"),
    ("cookbook_v4", Path("/tmp/gdl_raw/cookbook_v4.md"), "GDL Cookbook 4"),
]

GDL_COMMANDS = [
    "ADD",
    "ADDX",
    "ADDY",
    "ADDZ",
    "DEL",
    "BLOCK",
    "BRICK",
    "CYLIND",
    "PRISM",
    "PRISM_",
    "REVOLVE",
    "SWEEP",
    "TUBE",
    "FOR",
    "NEXT",
    "IF",
    "THEN",
    "ENDIF",
    "GOSUB",
    "RETURN",
    "PROJECT2",
    "LINE2",
    "POLY2",
    "HOTSPOT",
    "HOTSPOT2",
    "PARAMETERS",
    "VALUES",
    "MATERIAL",
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
        if len(part) < 180:
            continue
        first_line = part.splitlines()[0].strip()
        title = re.sub(r"^#+\s*", "", first_line) if first_line.startswith("#") else f"section_{index:04d}"
        sections.append((title, part))
    return sections


def code_signal(text: str) -> int:
    score = 0
    for cmd in GDL_COMMANDS:
        score += len(re.findall(rf"\b{re.escape(cmd)}\b", text))
    score += text.count("!")
    score += len(re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]*\s*=", text))
    return score


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
    reset_dir(EXAMPLES_DIR)

    total_sections = 0
    total_examples = 0
    for source_id, input_file, source_title in SOURCES:
        content = input_file.read_text(encoding="utf-8", errors="replace")
        sections = split_sections(content)
        source_examples = 0

        for index, (title, body) in enumerate(sections, start=1):
            slug = slugify(title, f"section_{index:04d}")
            signal = code_signal(body)
            section_path = SECTIONS_DIR / f"{source_id}_{index:04d}_{slug}.md"
            write_page(
                section_path,
                {
                    "id": f"cookbook.section.{source_id}.{index:04d}",
                    "type": "cookbook_section",
                    "source": source_title,
                    "status": "draft",
                    "char_count": len(body),
                    "code_signal": signal,
                },
                body,
            )

            if signal >= 8:
                example_path = EXAMPLES_DIR / f"{source_id}_{index:04d}_{slug}.md"
                write_page(
                    example_path,
                    {
                        "id": f"example.generated.{source_id}.{index:04d}",
                        "type": "example",
                        "task_types": ["create", "modify", "repair"],
                        "priority": 50,
                        "source": f"{source_title} (auto-generated)",
                        "status": "draft",
                        "char_count": len(body),
                        "code_signal": signal,
                    },
                    body,
                )
                source_examples += 1

        print(f"{source_id}: 全量章节 {len(sections)}，疑似案例 {source_examples}")
        total_sections += len(sections)
        total_examples += source_examples

    print("Cookbook 拆分完成：")
    print(f"  全量章节: {total_sections} -> {SECTIONS_DIR}")
    print(f"  example 草稿: {total_examples} -> {EXAMPLES_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
