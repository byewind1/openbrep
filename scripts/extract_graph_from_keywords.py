#!/usr/bin/env python3
"""GDL 知识图谱自动化抽取脚本（无 LLM 版本）。

数据源（优先级从高到低）：
  1. knowledge/GDL_3d_commands.md — 含命令签名和说明的文档（最权威）
  2. knowledge/GDL_2d_commands.md — 2D 命令文档
  3. knowledge/GDL_functions.md  — 内置函数文档
  4. openbrep/gdl_keywords.py    — 全量命令枚举（补充无文档条目）
  5. openbrep/error_classifier.py — 7 类错误规则（阶段3：打通诊断对齐）

输出：更新 knowledge/gdl_graph.json
  - api_functions: 全量 API（目标 100+）
  - known_error_patterns: 合并文档中 2 个变量归因规则 + 7 类编译错误规则
  - bim_concepts: 保留不动

用法：
    python3 scripts/extract_graph_from_keywords.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))


# ── 文档签名抽取 ──────────────────────────────────────────

def _extract_signatures_from_3d_doc(content: str) -> dict[str, dict]:
    """从 GDL_3d_commands.md 抽取命令签名和说明。

    格式：
      ### N.N COMMAND_NAME
      **语法**
      ```gdl
      COMMAND_NAME param1, param2
      ```
      **说明**
      - 说明文字
    """
    results: dict[str, dict] = {}
    # 按 ### 分割章节
    sections = re.split(r'^###\s+', content, flags=re.MULTILINE)
    for section in sections[1:]:  # 跳过第一个（章节前的文本）
        lines = section.strip().splitlines()
        if not lines:
            continue
        # 第一行：数字前缀 + 命令名，如 "1.1 BLOCK"
        heading_m = re.match(r'[\d.]+\s+(\w[\w_]*)', lines[0])
        if not heading_m:
            continue
        heading_name = heading_m.group(1).upper()

        # 从语法代码块取签名（更准确）
        gdl_block_m = re.search(r'```gdl\s*\n(\w[\w_]*)\s*([^\n]*?)\s*\n', section)
        if gdl_block_m:
            code_name = gdl_block_m.group(1).upper()
            signature = gdl_block_m.group(2).strip()
            # 去掉签名里的注释
            signature = re.sub(r'\s*!.*$', '', signature).strip()
        else:
            code_name = heading_name
            signature = ""

        # 说明：第一个 **说明** 段落下的 - bullet
        desc_m = re.search(r'\*\*说明\*\*\s*\n((?:- [^\n]+\n?)+)', section)
        description = ""
        if desc_m:
            bullets = re.findall(r'- (.+)', desc_m.group(1))
            description = bullets[0].strip() if bullets else ""

        # 常见坑作为额外说明
        pit_m = re.search(r'\*\*常见坑\*\*\s*\n((?:- [^\n]+\n?)+)', section)
        pit_note = ""
        if pit_m:
            pits = re.findall(r'- (.+)', pit_m.group(1))
            pit_note = "常见坑：" + pits[0].strip() if pits else ""

        final_name = code_name or heading_name
        full_desc = " / ".join(p for p in [description, pit_note] if p)

        if final_name:
            results[final_name] = {
                "name": final_name,
                "signature": signature,
                "description": full_desc,
            }

    return results


def _extract_signatures_from_2d_doc(content: str) -> dict[str, dict]:
    """从 GDL_2d_commands.md 抽取命令签名（无结构化标题，从代码块提取）。"""
    results: dict[str, dict] = {}
    # 每个 gdl 代码块的第一行如 "RECT2 x1, y1, x2, y2"
    for m in re.finditer(r'```gdl\s*\n(\w[\w_]*)\s*([^\n!]*?)(?:\s*![^\n]*)?\s*\n', content):
        name = m.group(1).upper()
        signature = m.group(2).strip()
        if name and name not in results:
            results[name] = {"name": name, "signature": signature, "description": ""}
    return results


def _extract_signatures_from_functions_doc(content: str) -> dict[str, dict]:
    """从 GDL_functions.md 抽取内置函数签名。"""
    results: dict[str, dict] = {}
    for m in re.finditer(r'`([A-Z][A-Z0-9_]*)\(([^)]*)\)`', content):
        name = m.group(1).upper()
        params = m.group(2).strip()
        if name and name not in results:
            results[name] = {"name": name, "signature": f"({params})", "description": "内置函数"}
    return results


# ── gdl_keywords.py 全量枚举 ──────────────────────────────

def _load_keywords_by_category() -> dict[str, tuple[str, frozenset]]:
    """从 gdl_keywords.py 加载按分类的命令集合。"""
    from openbrep.gdl_keywords import (
        GEOMETRY_COMMANDS, TRANSFORM_COMMANDS, ATTRIBUTE_COMMANDS,
        PARAMETER_COMMANDS, TEXT_COMMANDS, TWO_D_COMMANDS, MISC_COMMANDS,
        BUILTIN_FUNCTIONS, CONTROL_FLOW, GROUP_COMMANDS,
        LOW_LEVEL_BODY_COMMANDS,
    )
    return {
        "geometry": ("geometry", GEOMETRY_COMMANDS),
        "transform": ("transform", TRANSFORM_COMMANDS),
        "attribute": ("attribute", ATTRIBUTE_COMMANDS),
        "parameter": ("parameter", PARAMETER_COMMANDS),
        "text": ("text", TEXT_COMMANDS),
        "2d": ("2d", TWO_D_COMMANDS),
        "misc": ("misc", MISC_COMMANDS),
        "function": ("function", BUILTIN_FUNCTIONS),
        "control": ("control", CONTROL_FLOW),
        "group": ("group", GROUP_COMMANDS),
        "body": ("body", LOW_LEVEL_BODY_COMMANDS),
    }


_CATEGORY_LABEL: dict[str, str] = {
    "geometry": "geometry",
    "transform": "transform",
    "attribute": "attribute",
    "parameter": "parameter",
    "text": "text",
    "2d": "2d",
    "misc": "misc",
    "function": "function",
    "control": "control",
    "group": "group",
    "body": "body",
}

_PARAM_COUNT_HINTS: dict[str, tuple[int, int]] = {
    "BLOCK": (3, 3), "BRICK": (3, 3), "CYLIND": (2, 2), "SPHERE": (1, 1),
    "CONE": (3, 5), "ELLIPS": (2, 2), "PRISM": (6, 0), "PRISM_": (5, 0),
    "CPRISM_": (8, 0), "ADD": (3, 3), "ADDX": (1, 1), "ADDY": (1, 1),
    "ADDZ": (1, 1), "ADD2": (2, 2), "ROT": (1, 1), "ROTX": (1, 1),
    "ROTY": (1, 1), "ROTZ": (1, 1), "MUL": (3, 3), "DEL": (1, 1),
    "PEN": (1, 1), "MATERIAL": (1, 1), "RECT": (4, 4), "RECT2": (4, 4),
    "LINE": (4, 4), "LINE2": (4, 4), "ARC": (5, 5), "ARC2": (5, 5),
    "CIRCLE2": (3, 3), "HOTSPOT": (3, 3), "HOTSPOT2": (2, 2),
    "PROJECT2": (2, 2), "POLY2": (3, 0), "PGON": (3, 0),
}


def _build_api_entries(
    doc_sigs: dict[str, dict],
    kw_categories: dict[str, tuple[str, frozenset]],
) -> list[dict]:
    """合并文档签名和 gdl_keywords 枚举，生成完整 api_functions 列表。"""
    # name_upper → {name, signature, description, category}
    merged: dict[str, dict] = {}

    # 1. 先从文档中填充（最权威）
    for name_upper, info in doc_sigs.items():
        merged[name_upper] = {
            "name": name_upper,
            "signature": info.get("signature", ""),
            "description": info.get("description", ""),
            "category": "",
            "return_type": "void",
        }

    # 2. 用 gdl_keywords 补充类别，并填充缺失条目
    for cat_key, (cat_label, cmd_set) in kw_categories.items():
        for cmd in sorted(cmd_set):
            name = cmd.upper()
            if name not in merged:
                merged[name] = {
                    "name": name,
                    "signature": "",
                    "description": "",
                    "category": cat_label,
                    "return_type": "function" if cat_label == "function" else "void",
                }
            else:
                if not merged[name]["category"]:
                    merged[name]["category"] = cat_label
                if cat_label == "function":
                    merged[name]["return_type"] = "varies"

    # 3. 填充参数数量提示
    for name, entry in merged.items():
        if name in _PARAM_COUNT_HINTS:
            entry["param_count_min"], entry["param_count_max"] = _PARAM_COUNT_HINTS[name]
        else:
            entry.setdefault("param_count_min", 0)
            entry.setdefault("param_count_max", 0)

    return sorted(merged.values(), key=lambda x: (x["category"], x["name"]))


# ── 阶段3：从 error_classifier.py 提取错误规则 ────────────

def _extract_error_patterns_from_classifier() -> list[dict]:
    """从 error_classifier.py 提取 7 类错误规则，转为图谱格式。

    采用代码导入而非字符串解析，保证与 ErrorClassifier 始终同步。
    """
    from openbrep.error_classifier import _RULES, ErrorCategory

    entries: list[dict] = []
    for category, _pattern, target_file, hint in _RULES:
        entries.append({
            "error_category": category.value,
            "match_via": "error_classifier",     # 标记：通过 ErrorClassifier 匹配
            "target_file": target_file or "",
            "diagnosis": _category_to_diagnosis(category),
            "fix_hint": hint,
            "concept": _category_to_concept(category),
        })
    return entries


def _category_to_diagnosis(cat) -> str:
    from openbrep.error_classifier import ErrorCategory
    return {
        ErrorCategory.MISSING_ENDIF:   "IF/ENDIF 块未正确闭合。",
        ErrorCategory.MISSING_NEXT:    "FOR/NEXT 循环未正确闭合。",
        ErrorCategory.MISSING_END:     "3D 脚本主流程末尾缺少 END。",
        ErrorCategory.UNDEFINED_VAR:   "脚本中引用了未定义的变量或参数。",
        ErrorCategory.WRONG_ARG_COUNT: "GDL 命令参数数量不正确。",
        ErrorCategory.ADD_DEL_MISMATCH:"变换栈 ADD/DEL 不配平。",
        ErrorCategory.XML_PARSE_ERROR: "paramlist.xml 无法解析。",
        ErrorCategory.UNKNOWN:         "未知错误类型。",
    }.get(cat, "")


def _category_to_concept(cat) -> str:
    from openbrep.error_classifier import ErrorCategory
    return {
        ErrorCategory.UNDEFINED_VAR:   "Variable",
        ErrorCategory.WRONG_ARG_COUNT: "APIFunction",
        ErrorCategory.ADD_DEL_MISMATCH:"Transform",
        ErrorCategory.XML_PARSE_ERROR: "Paramlist",
    }.get(cat, "")


# ── 主流程 ────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="GDL 知识图谱自动抽取（无 LLM）")
    parser.add_argument("--dry-run", action="store_true", help="只打印统计，不写文件")
    args = parser.parse_args()

    knowledge_dir = _ROOT / "knowledge"
    graph_path = knowledge_dir / "gdl_graph.json"

    # ── 读取现有图谱 ──────────────────────────────────────
    existing: dict = {}
    if graph_path.exists():
        existing = json.loads(graph_path.read_text(encoding="utf-8"))
    existing_concepts = existing.get("bim_concepts", [])
    existing_var_patterns = [
        p for p in existing.get("known_error_patterns", [])
        if p.get("match_via") != "error_classifier"
    ]

    # ── 抽取文档签名 ──────────────────────────────────────
    doc_sigs: dict[str, dict] = {}
    for fname, extractor in [
        ("GDL_3d_commands.md", _extract_signatures_from_3d_doc),
        ("GDL_2d_commands.md", _extract_signatures_from_2d_doc),
        ("GDL_functions.md",   _extract_signatures_from_functions_doc),
        ("GDL_command_index.md", _extract_signatures_from_2d_doc),  # 同格式
    ]:
        fpath = knowledge_dir / fname
        if fpath.exists():
            sigs = extractor(fpath.read_text(encoding="utf-8"))
            for k, v in sigs.items():
                if k not in doc_sigs:
                    doc_sigs[k] = v
            print(f"  文档 {fname}: 抽取 {len(sigs)} 个签名")

    # ── 加载 gdl_keywords 全量枚举 ────────────────────────
    kw_categories = _load_keywords_by_category()
    total_kw = sum(len(s) for _, s in kw_categories.values())
    print(f"  gdl_keywords.py: {total_kw} 个命令枚举")

    # ── 合并 API 条目 ────────────────────────────────────
    api_entries = _build_api_entries(doc_sigs, kw_categories)
    print(f"\n合并结果：{len(api_entries)} 个 API 条目")

    # 类别统计
    cat_counts: dict[str, int] = {}
    for e in api_entries:
        c = e.get("category", "?") or "?"
        cat_counts[c] = cat_counts.get(c, 0) + 1
    for cat, count in sorted(cat_counts.items()):
        print(f"  {cat:15s} {count} 个")

    # ── 提取错误规则（阶段3）────────────────────────────
    error_patterns_from_classifier = _extract_error_patterns_from_classifier()
    print(f"\n错误规则对齐：从 error_classifier.py 提取 {len(error_patterns_from_classifier)} 条规则")
    all_error_patterns = existing_var_patterns + error_patterns_from_classifier
    print(f"  变量归因规则（保留）: {len(existing_var_patterns)} 条")
    print(f"  编译错误规则（新增）: {len(error_patterns_from_classifier)} 条")
    print(f"  合计: {len(all_error_patterns)} 条")

    # ── 组装新图谱 ────────────────────────────────────────
    new_graph = {
        "version": "1.1",
        "description": (
            "GDL 领域知识图谱 — 自动抽取版（无 LLM）。"
            "数据源：knowledge/GDL_*.md + openbrep/gdl_keywords.py + error_classifier.py"
        ),
        "api_functions": api_entries,
        "bim_concepts": existing_concepts,
        "known_error_patterns": all_error_patterns,
    }

    if args.dry_run:
        print("\n[dry-run] 未写入文件。")
        return 0

    graph_path.write_text(
        json.dumps(new_graph, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n✅ 写入 {graph_path}")
    print(f"   API: {len(api_entries)} 个，BIM 概念: {len(existing_concepts)} 个，错误规则: {len(all_error_patterns)} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
