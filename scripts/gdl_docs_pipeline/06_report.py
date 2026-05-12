"""Report generated GDL PDF knowledge staging outputs."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path("generated/gdl_pdf_knowledge")
SUBDIRS = [
    "raw",
    "wiki",
    "handbook_sections",
    "archetypes",
    "cookbook_sections",
    "examples",
    "repair",
    "reports",
]


def count_files(path: Path) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    files = [p for p in path.rglob("*") if p.is_file()]
    size_kb = sum(p.stat().st_size for p in files) // 1024
    return len(files), size_kb


def frontmatter_count(path: Path) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    markdown = list(path.rglob("*.md"))
    with_frontmatter = 0
    for file in markdown:
        if file.read_text(encoding="utf-8", errors="replace").startswith("---"):
            with_frontmatter += 1
    return with_frontmatter, len(markdown)


def wiki_categories() -> dict[str, int]:
    categories: dict[str, int] = {}
    wiki_dir = ROOT / "wiki"
    for file in wiki_dir.glob("*.md"):
        text = file.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"^category:\s*(\w+)", text, re.M)
        category = match.group(1) if match else "unknown"
        categories[category] = categories.get(category, 0) + 1
    return categories


def main() -> int:
    ROOT.mkdir(parents=True, exist_ok=True)
    report_path = ROOT / "reports" / "gdl_pdf_knowledge_staging_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# GDL PDF Knowledge Staging Report",
        "",
        "本报告只统计暂存整理区，不代表已经合并进 OpenBrep 正式知识库。",
        "",
        "## 文件统计",
        "",
        "| 目录 | 文件数 | 大小 | frontmatter |",
        "|---|---:|---:|---:|",
    ]

    for subdir in SUBDIRS:
        path = ROOT / subdir
        count, size_kb = count_files(path)
        fm_count, md_count = frontmatter_count(path)
        fm = f"{fm_count}/{md_count}" if md_count else "-"
        lines.append(f"| `{subdir}` | {count} | {size_kb} KB | {fm} |")

    lines.extend(["", "## Wiki 分类", ""])
    for category, count in sorted(wiki_categories().items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"- `{category}`: {count}")

    lines.extend(
        [
            "",
            "## 目录说明",
            "",
            "- `raw/`: 四个 PDF 的完整 Markdown 转换归档，保留转换器生成的资源目录。",
            "- `wiki/`: Reference Guide 自动拆出的命令页草稿。",
            "- `handbook_sections/`: Handbook 全量章节拆分。",
            "- `archetypes/`: Handbook 中疑似对象族/构件族相关章节的草稿副本。",
            "- `cookbook_sections/`: Cookbook v3/v4 全量章节拆分。",
            "- `examples/`: Cookbook 中疑似包含 GDL 代码案例的章节草稿副本。",
            "- `repair/`: 四本文档中疑似错误、约束、警告、性能和规范片段。",
        ]
    )

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\n报告已写入: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
