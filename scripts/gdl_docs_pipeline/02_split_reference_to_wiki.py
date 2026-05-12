"""
Step 2（修正版）：把 Reference Guide 按命令切割成 wiki 页。
输出到 /tmp/gdl_generated_wiki/，不覆盖现有 knowledge/wiki/
"""

import os
import re
from pathlib import Path

INPUT_FILE = "/tmp/gdl_raw/reference_guide.md"
OUTPUT_DIR = "generated/gdl_pdf_knowledge/wiki"
os.makedirs(OUTPUT_DIR, exist_ok=True)

for old_file in Path(OUTPUT_DIR).glob("*.md"):
    old_file.unlink()

with open(INPUT_FILE, "r", encoding="utf-8") as f:
    content = f.read()

# 实际标题是 ######（六级标题），不是 ###
sections = re.split(r"\n(?=#{4,7} )", content)
print(f"发现 {len(sections)} 个片段（按四级及以上标题切割）")

CATEGORY_MAP = {
    "3d": [
        "ADD",
        "ADDX",
        "ADDY",
        "ADDZ",
        "MUL",
        "MULX",
        "MULY",
        "MULZ",
        "ROT",
        "ROTX",
        "ROTY",
        "ROTZ",
        "DEL",
        "XFORM",
        "BLOCK",
        "BRICK",
        "CONE",
        "CYLIND",
        "SPHERE",
        "ELLIPS",
        "TUBE",
        "SWEEP",
        "REVOLVE",
        "PRISM",
        "BPRISM",
        "EXTRUDE",
        "PLANE",
        "POLY",
        "MESH",
        "BODY",
        "MASS",
        "VERT",
        "EDGE",
        "PGON",
        "COOR",
        "TEVE",
        "VOCA",
        "FILLA",
        "SFILL",
        "CUTPLANE",
        "CUTPOLY",
        "PYRAMID",
        "RULED",
        "CASING",
        "WALL_",
        "BEAM_",
        "COLUMN_",
        "SLAB_",
        "HOTSPOT",
        "LINE",
        "ARC",
        "CIRCLE",
    ],
    "2d": [
        "LINE2",
        "RECT2",
        "CIRCLE2",
        "ARC2",
        "SPLINE2",
        "POLY2",
        "HOTSPOT2",
        "TEXT2",
        "RICHTEXT2",
        "PICTURE2",
        "SYMBOL2",
        "FRAGMENT2",
        "DRAWING2",
        "WALLHOLE2",
        "WALLNICHE2",
        "PROJECT2",
        "SHADOW2",
    ],
    "control": [
        "FOR",
        "NEXT",
        "IF",
        "THEN",
        "ELSE",
        "ENDIF",
        "GOTO",
        "GOSUB",
        "RETURN",
        "EXIT",
        "END",
        "WHILE",
        "ENDWHILE",
        "DO",
        "UNTIL",
        "GROUP",
        "ENDGROUP",
        "CALL",
        "PRINT",
        "BREAKPOINT",
    ],
    "param": ["VALUES", "RANGE", "PARAMETERS", "LOCK", "HIDDEN", "REQUEST"],
    "string": [
        "STR",
        "STRSUB",
        "STRLEN",
        "STRSTR",
        "STRTOLOWER",
        "STRTOUPPER",
        "STRJOIN",
        "SPLIT",
        "FORMAT",
    ],
    "math": [
        "SIN",
        "COS",
        "TAN",
        "ATN",
        "ABS",
        "INT",
        "FRAC",
        "SQR",
        "EXP",
        "LGT",
        "LOG",
        "MAX",
        "MIN",
        "RND",
        "SIGN",
    ],
}


def get_category(title: str) -> str:
    cmd = extract_command(title) or ""
    for cat, cmds in CATEGORY_MAP.items():
        if any(cmd == c or cmd.startswith(c) for c in cmds):
            return cat
    return "other"


def extract_command(title: str) -> str | None:
    """Return the leading GDL command token from a heading.

    The PDF conversion often emits headings like "CYLIND h, r" or
    "FOR - TO - NEXT". For file names and frontmatter we only want the command
    token, not the argument list.
    """
    title = title.strip()
    match = re.match(r"^([A-Z][A-Z0-9_]*(?:\{\d+\})?)(?=\s|,|$|-)", title)
    if not match:
        return None
    return match.group(1)


def command_to_filename(command: str) -> str:
    safe = command.lower().replace("{", "_").replace("}", "")
    safe = re.sub(r"[^\w]", "_", safe).lstrip("_")
    return re.sub(r"_+", "_", safe)


candidate_sections = []
merged_duplicate_titles = 0
skipped_short = 0
skipped_not_cmd = 0

for section in sections:
    section = section.strip()
    if len(section) < 20:
        skipped_short += 1
        continue

    lines = section.split("\n")
    # 去掉标题前缀的 # 号
    title_line = re.sub(r"^#+\s*", "", lines[0]).strip()
    command = extract_command(title_line)
    if not command:
        skipped_not_cmd += 1
        continue

    if candidate_sections and candidate_sections[-1]["command"] == command:
        candidate_sections[-1]["section"] += "\n\n" + section
        merged_duplicate_titles += 1
        continue

    candidate_sections.append(
        {
            "command": command,
            "title": title_line,
            "section": section,
        }
    )

saved = 0

for item in candidate_sections:
    command = item["command"]
    title_line = item["title"]
    section = item["section"]
    if len(section) < 20:
        skipped_short += 1
        continue

    commands = [command]
    if " - " in title_line:
        commands = [c.strip() for c in title_line.split(" - ") if extract_command(c.strip())]

    category = get_category(command)

    # 文件名：取第一个命令名，去掉特殊字符
    safe_name = command_to_filename(command)[:50]
    out_path = os.path.join(OUTPUT_DIR, f"{safe_name}.md")

    # 如果文件已存在（同命令名重复），追加序号
    if os.path.exists(out_path):
        i = 2
        while os.path.exists(out_path):
            out_path = os.path.join(OUTPUT_DIR, f"{safe_name}_{i}.md")
            i += 1

    cmd_list = ", ".join(f'"{c}"' for c in commands[:3] if c)
    frontmatter = f"""---
id: wiki.generated.{safe_name}
type: wiki
category: {category}
commands: [{cmd_list}]
task_types: [create, modify, repair]
priority: 65
source: GDL Reference Guide 28 (auto-generated)
status: draft
---

"""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(frontmatter + section.strip())
    saved += 1

print("\n结果：")
print(f"  ✅ 保存: {saved} 个命令页")
print(f"  合并重复标题: {merged_duplicate_titles}")
print(f"  跳过（太短）: {skipped_short}")
print(f"  跳过（非命令）: {skipped_not_cmd}")
print(f"  输出目录: {OUTPUT_DIR}")
