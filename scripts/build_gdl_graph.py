#!/usr/bin/env python3
"""GDL 知识图谱构建与验证脚本。

功能：
1. 加载 knowledge/gdl_graph.json，验证结构完整性
2. 打印统计摘要
3. 验证 API 查询和错误诊断接口可用

用法：
    python3 scripts/build_gdl_graph.py
    python3 scripts/build_gdl_graph.py --query BLOCK
    python3 scripts/build_gdl_graph.py --diagnose "Undefined variable 'mat_01'"
    python3 scripts/build_gdl_graph.py --suggest "书架"
"""

import argparse
import sys
from pathlib import Path

# 确保项目根目录在 path 里
_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="GDL 知识图谱工具")
    parser.add_argument("--query", metavar="API_NAME", help="查询某个 API 的签名")
    parser.add_argument("--diagnose", metavar="ERROR_MSG", help="从错误消息诊断根因")
    parser.add_argument("--suggest", metavar="INTENT", help="根据意图推荐 API")
    parser.add_argument("--constraint", metavar="INTENT", help="构建 CREATE 路径约束 prompt")
    args = parser.parse_args()

    from openbrep.knowledge_graph import GDLGraphManager
    from openbrep.graph_schema import APIFunction

    graph_path = _PROJECT_ROOT / "knowledge" / "gdl_graph.json"
    if not graph_path.exists():
        print(f"[ERROR] 图谱文件不存在: {graph_path}", file=sys.stderr)
        return 1

    manager = GDLGraphManager(graph_path=graph_path)
    manager.load()

    # ── 摘要统计 ──────────────────────────────────────────
    print(f"\n=== GDL 知识图谱摘要 ===")
    print(f"API 函数：{len(manager.api_map)} 个")
    print(f"BIM 概念：{len(manager.concept_map)} 个")
    print(f"别名映射：{len(manager.alias_map)} 个")
    print(f"错误归因规则：{len(manager.error_patterns)} 条")

    # ── 类别统计 ──────────────────────────────────────────
    categories: dict[str, int] = {}
    for api in manager.api_map.values():
        categories[api.category] = categories.get(api.category, 0) + 1
    if categories:
        print("\nAPI 分类：")
        for cat, count in sorted(categories.items()):
            print(f"  {cat or '(未分类)':15s} {count} 个")

    # ── 概念列表 ──────────────────────────────────────────
    if manager.concept_map:
        print("\nBIM 概念：")
        for name, concept in manager.concept_map.items():
            apis = ", ".join(concept.required_apis) if concept.required_apis else "—"
            print(f"  {name:15s} → APIs: {apis}")

    # ── 按需执行查询 ──────────────────────────────────────
    if args.query:
        print(f"\n=== 查询 API: {args.query} ===")
        result = manager.query_api(args.query)
        if result:
            print(f"  名称：{result.name}")
            print(f"  签名：{result.signature}")
            print(f"  说明：{result.description}")
            print(f"  参数数量：min={result.param_count_min}, max={result.param_count_max}")
        else:
            print(f"  [NOT FOUND] 图谱中不存在 '{args.query}'")

    if args.diagnose:
        print(f"\n=== 错误诊断 ===")
        print(f"输入：{args.diagnose}")
        result = manager.diagnose_error(args.diagnose)
        print(f"诊断结果：\n{result or '(无图谱诊断)'}")

    if args.suggest:
        print(f"\n=== API 推荐：{args.suggest} ===")
        apis = manager.suggest_apis(args.suggest)
        if apis:
            for api in apis:
                print(f"  {api.name:20s} {api.signature}")
        else:
            print("  (无匹配概念)")

    if args.constraint:
        print(f"\n=== CREATE 约束 prompt：{args.constraint} ===")
        prompt = manager.build_constraint_prompt(args.constraint)
        print(prompt or "(无约束)")

    # ── 自检：验证几个核心 API 可查到 ──────────────────────
    required_apis = ["BLOCK", "PRISM_", "ADD", "DEL", "MATERIAL"]
    missing = [name for name in required_apis if not manager.query_api(name)]
    if missing:
        print(f"\n[WARN] 以下核心 API 在图谱中缺失: {missing}", file=sys.stderr)
        return 1

    print(f"\n✅ 图谱完整性检查通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
