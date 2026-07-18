"""wiki→图谱派生管道（Phase 2a）。

从 knowledge/wiki/（549 页）与 knowledge/archetypes/ 自动抽取：
- API 函数节点（名称、签名、参数数量、分类、描述）
- BIM 概念节点（名称、别名、必需 API、初始化模式）

产出 knowledge/gdl_graph_derived.json，由 GDLGraphManager 加载后再用
手工 knowledge/gdl_graph.json 作为 override 覆盖层合并（手工优先）。

用法：
    python3 -m openbrep.graph_derivation            # 重新生成 derived JSON 并打印统计
    python3 -m openbrep.graph_derivation --check    # 只打印统计，不写文件
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

_KNOWLEDGE_DIR = Path(__file__).parent.parent / "knowledge"
_WIKI_DIR = _KNOWLEDGE_DIR / "wiki"
_ARCHETYPES_DIR = _KNOWLEDGE_DIR / "archetypes"
DERIVED_GRAPH_PATH = _KNOWLEDGE_DIR / "gdl_graph_derived.json"

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
# GDL 命令名：大写字母开头，可含数字/下划线，如 PRISM_、ADD2、HOTSPOT2
_COMMAND_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")
# 参数标识符：小写开头（GDL 惯例签名参数），如 h, r1, dx, mat_name
_PARAM_TOKEN_RE = re.compile(r"^[a-z_][a-zA-Z0-9_]*$")


# ── frontmatter 解析 ──────────────────────────────────────


def _split_frontmatter(raw: str) -> tuple[dict, str]:
    """拆分 frontmatter 与正文。list 值（JSON 或 [a, b] 形式）解析为 Python list。"""
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        return {}, raw
    fm: dict = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if val.startswith("[") and val.endswith("]"):
            try:
                fm[key] = json.loads(val)
            except json.JSONDecodeError:
                fm[key] = [v.strip().strip('"').strip("'") for v in val[1:-1].split(",") if v.strip()]
        else:
            fm[key] = val
    return fm, raw[m.end():].strip()


# ── 签名抽取 ──────────────────────────────────────────────


@dataclass
class ParsedSignature:
    """从 wiki 正文抽出的命令签名。"""

    params: list[str] = field(default_factory=list)
    variadic: bool = False
    description: str = ""

    @property
    def signature(self) -> str:
        sig = ", ".join(self.params)
        if self.variadic:
            sig += ", ..." if sig else "..."
        return sig


def _parse_signature_chunks(text: str) -> ParsedSignature | None:
    """解析命令名之后的文本，提取逗号分隔的参数序列。

    生成页三种形态都归一到这里：
      - "h, r1, r2, alpha1, alpha2"                     → 独立签名行
      - "r A sphere with its center at the origin..."   → 签名与描述同行
      - "n, m, mask,"（行尾逗号，多行继续，含 "..."）    → 变参命令

    规则：逐个逗号块取首个 token，必须形如参数标识符；一旦某块 token 之后
    还跟着散文（或 token 不合法），在该处截断。全局变量页（如
    "volume of the beam"）因首 token 是长英文单词而被拒绝。
    """
    params: list[str] = []
    variadic = False
    description = ""
    chunks = text.split(",")
    for i, chunk in enumerate(chunks):
        chunk = chunk.strip()
        if not chunk:
            continue
        if chunk.startswith("..."):
            variadic = True
            # "..." 之后是变参重复段，不再逐个记录
            break
        tokens = chunk.split()
        head = tokens[0] if tokens else ""
        if not _PARAM_TOKEN_RE.match(head):
            break
        # 单 chunk（无逗号）且后随文字：真命令的描述以大写句首开头（"SPHERE r A sphere..."），
        # 全局变量页是小写短语接续（"BEAM_VOLUME volume of the beam"）→ 拒绝
        if len(chunks) == 1 and len(tokens) > 1 and not tokens[1][0].isupper():
            break
        params.append(head)
        if len(tokens) > 1:
            # 本块 token 后跟散文 → 签名到此为止，余下是描述
            description = " ".join(tokens[1:])
            break
    if not params and not variadic:
        return None
    return ParsedSignature(params=params, variadic=variadic, description=description)


def _extract_signature(body: str, command: str) -> ParsedSignature | None:
    """在正文中定位命令签名。支持独立行、标题行内、多行续行三种形态。"""
    lines = body.splitlines()
    for idx, line in enumerate(lines):
        stripped = line.strip().lstrip("#").strip()
        if not stripped.startswith(command):
            continue
        rest = stripped[len(command):]
        # 命令名后必须是空白或行尾，避免 PRISM 匹配到 PRISM_
        if rest and not rest[0].isspace():
            continue
        rest = rest.strip()
        # 多行签名：行尾逗号则拼接后续行（最多 6 行，防失控）
        joined = rest
        j = idx
        while joined.rstrip().endswith(",") and j + 1 < len(lines) and j - idx < 6:
            j += 1
            joined = joined.rstrip() + " " + lines[j].strip()
        parsed = _parse_signature_chunks(joined)
        if parsed:
            if not parsed.description:
                parsed.description = _first_prose_after(lines, j)
            return parsed
    return None


def _first_prose_after(lines: list[str], start_idx: int) -> str:
    """取签名行之后第一段像描述的散文（≥4 个单词），截断到 200 字符。"""
    for line in lines[start_idx + 1:start_idx + 15]:
        text = line.strip()
        if text.startswith(("#", "|", "```", "-")):
            continue
        if len(text.split()) >= 4:
            return text[:200]
    return ""


# ── wiki 页派生 ───────────────────────────────────────────


def _derive_from_generated_page(fm: dict, body: str, slug: str) -> tuple[list[dict], list[dict]]:
    """处理一张自动生成页（type: wiki），返回 (api 条目列表, 概念条目列表)。"""
    apis: list[dict] = []
    concepts: list[dict] = []
    category = fm.get("category", "")
    commands = [c for c in (fm.get("commands") or []) if _COMMAND_NAME_RE.match(str(c))]
    for cmd in commands:
        parsed = _extract_signature(body, cmd)
        api: dict = {
            "name": cmd,
            "signature": parsed.signature if parsed else "",
            "return_type": "void",
            "description": (parsed.description if parsed else "")[:200],
            "param_count_min": 0,
            "param_count_max": 0,
            "category": category,
            "source": f"wiki/{slug}.md",
        }
        if parsed and not parsed.variadic:
            api["param_count_min"] = len(parsed.params)
            api["param_count_max"] = len(parsed.params)
        elif parsed and parsed.variadic:
            api["param_count_min"] = len(parsed.params)
            api["param_count_max"] = 0  # 0 = 可变参数数量
        apis.append(api)

        # 只有几何/2D 命令且签名解析成功的才升级为概念（可被意图匹配命中）。
        # ASCII 别名最小长度 4：避免 "add"/"del"/"rot" 等短词误命中用户意图散文。
        if parsed and category in {"3d", "2d"}:
            aliases = {cmd.lower()}
            if "_" in slug:
                aliases.add(slug.replace("_", " "))
            kept_aliases = sorted(a for a in aliases if len(a) >= 4)
            if kept_aliases:
                concepts.append({
                    "name": cmd.title().replace("_", ""),
                    "aliases": kept_aliases,
                    "description": (parsed.description or f"GDL {cmd} 命令")[:200],
                    "required_apis": [cmd],
                    "init_pattern": f"{cmd} {parsed.signature}".strip(),
                    "source": f"wiki/{slug}.md",
                })
    return apis, concepts


_GDL_BLOCK_RE = re.compile(r"```gdl\s*\n(.*?)```", re.DOTALL)
_ERROR_DOC_PATH = _KNOWLEDGE_DIR / "GDL_common_errors.md"
_ERROR_SECTION_RE = re.compile(r"^## \d+\.\s*(.+)$", re.MULTILINE)
_BACKTICK_PHRASE_RE = re.compile(r"`([^`]{6,80})`")


def _derive_error_patterns(doc_path: Path = _ERROR_DOC_PATH) -> list[dict]:
    """从 knowledge/GDL_common_errors.md 派生 known_error_patterns。

    每节的「现象」行里反引号包住的报错短语 → pattern（诊断走子串匹配，小写）；
    「原因」→ diagnosis；「修复」→ fix_hint。没有可匹配报错短语的节
    （纯几何/语义问题，编译日志抓不到）跳过。
    """
    if not doc_path.exists():
        return []
    try:
        text = doc_path.read_text(encoding="utf-8")
    except Exception:
        return []

    patterns: list[dict] = []
    sections = _ERROR_SECTION_RE.split(text)
    # split 结果：[前言, title1, body1, title2, body2, ...]
    for title, body in zip(sections[1::2], sections[2::2]):
        symptom = diagnosis = fix_hint = ""
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("**现象**"):
                symptom = stripped
            elif stripped.startswith("**原因**"):
                diagnosis = stripped.split("：", 1)[-1].strip()
            elif stripped.startswith("**修复**"):
                fix_hint = stripped.split("：", 1)[-1].strip()
        for phrase in _BACKTICK_PHRASE_RE.findall(symptom):
            # 只收报错消息短语（含空格的英文句式），跳过纯命令名如 `ENDIF`
            if " " not in phrase:
                continue
            patterns.append({
                "pattern": phrase.lower(),
                "diagnosis": f"{title.strip()}：{diagnosis}",
                "fix_hint": fix_hint,
                "source": "GDL_common_errors.md",
            })
    return patterns


def _derive_from_concept_page(
    fm: dict, body: str, slug: str, known_apis: set[str]
) -> tuple[dict | None, dict | None]:
    """处理一张手写概念页（type: concept），返回 (概念条目, 补充 API 条目或 None)。

    required_apis 从正文代码块中出现的、已知 API 集合内的命令推断。
    页名本身是命令（全大写、正文可解析出签名）时置顶，并在生成页未覆盖该命令时
    补一条 API 条目（macOS 文件系统大小写不敏感，同名手写页会挤掉生成页）。
    """
    aliases_raw = fm.get("aliases") or []
    if isinstance(aliases_raw, str):
        aliases_raw = [aliases_raw]
    aliases = {str(a).strip().lower() for a in aliases_raw if str(a).strip()}
    aliases.add(slug.lower().replace("_", " "))

    code_blocks = _GDL_BLOCK_RE.findall(body)
    used_apis: list[str] = []
    seen: set[str] = set()
    for block in code_blocks:
        for token in re.findall(r"\b[A-Z][A-Z0-9_]{2,}\b", block):
            if token in known_apis and token not in seen:
                seen.add(token)
                used_apis.append(token)
    # 页名本身是命令时置顶（slug 原样全大写 + 正文能解析出签名才算命令，
    # 避免 Transformation_Stack 这类页名被误判）
    extra_api: dict | None = None
    if _COMMAND_NAME_RE.match(slug):
        parsed = _extract_signature(body, slug)
        if parsed:
            if slug in seen:
                used_apis.remove(slug)
            used_apis.insert(0, slug)
            if slug not in known_apis:
                extra_api = {
                    "name": slug,
                    "signature": parsed.signature,
                    "return_type": "void",
                    "description": parsed.description[:200],
                    "param_count_min": 0 if parsed.variadic else len(parsed.params),
                    "param_count_max": 0 if parsed.variadic else len(parsed.params),
                    "category": "",
                    "source": f"wiki/{slug}.md",
                }

    init_pattern = code_blocks[0].strip() if code_blocks else ""
    # 描述：第一段散文
    description = _first_prose_after(body.splitlines(), -1)

    concept = {
        "name": slug,
        "aliases": sorted(a for a in aliases if len(a) >= 3),
        "description": description,
        "required_apis": used_apis[:8],
        "init_pattern": init_pattern[:400],
        "source": f"wiki/{slug}.md",
    }
    return concept, extra_api


def _derive_from_archetype(fm: dict, slug: str) -> dict | None:
    """处理一张 archetype 页，返回概念条目（object_types 即别名，含中文）。"""
    object_types = fm.get("object_types") or []
    commands = [c for c in (fm.get("commands") or []) if _COMMAND_NAME_RE.match(str(c))]
    if not object_types:
        return None
    name = str(fm.get("id", f"archetype.{slug}")).split(".")[-1].title()
    aliases = sorted({str(a).strip().lower() for a in object_types if str(a).strip()})
    return {
        "name": name,
        "aliases": aliases,
        "description": str(fm.get("title", "")).strip(),
        "required_apis": commands,
        "init_pattern": "",
        "source": f"archetypes/{slug}.md",
    }


# ── 主入口 ────────────────────────────────────────────────


def derive_graph(
    wiki_dir: Path = _WIKI_DIR,
    archetypes_dir: Path = _ARCHETYPES_DIR,
) -> dict:
    """扫描 wiki 与 archetypes，产出派生图谱 dict。

    概念优先级（后写入者在同名/同别名时覆盖前者）：
      生成命令页 < 手写概念页 < archetype
    手工 gdl_graph.json 的 override 合并发生在 GDLGraphManager 加载阶段，不在这里。
    """
    api_entries: dict[str, dict] = {}
    generated_concepts: list[dict] = []
    concept_pages: list[tuple[dict, str, str]] = []  # (fm, body, slug) 延后处理，需要完整 API 集合

    if wiki_dir.is_dir():
        for md_file in sorted(wiki_dir.glob("*.md")):
            try:
                raw = md_file.read_text(encoding="utf-8")
            except Exception:
                continue
            fm, body = _split_frontmatter(raw)
            page_type = fm.get("type", "")
            if page_type == "wiki":
                apis, concepts = _derive_from_generated_page(fm, body, md_file.stem)
                for api in apis:
                    existing = api_entries.get(api["name"])
                    # 同名命令出现在多页时保留签名更完整的那个
                    if existing is None or (not existing["signature"] and api["signature"]):
                        api_entries[api["name"]] = api
                generated_concepts.extend(concepts)
            elif page_type == "concept":
                concept_pages.append((fm, body, md_file.stem))

    known_apis = set(api_entries.keys())

    concepts_by_name: dict[str, dict] = {}
    for concept in generated_concepts:
        concepts_by_name[concept["name"]] = concept
    for fm, body, slug in concept_pages:
        concept, extra_api = _derive_from_concept_page(fm, body, slug, known_apis)
        if extra_api:
            api_entries[extra_api["name"]] = extra_api
        if concept:
            concepts_by_name[concept["name"]] = concept
    if archetypes_dir.is_dir():
        for md_file in sorted(archetypes_dir.glob("*.md")):
            try:
                raw = md_file.read_text(encoding="utf-8")
            except Exception:
                continue
            fm, _ = _split_frontmatter(raw)
            if fm.get("type") != "archetype":
                continue
            concept = _derive_from_archetype(fm, md_file.stem)
            if concept:
                concepts_by_name[concept["name"]] = concept

    return {
        "version": "1.0",
        "description": "由 openbrep/graph_derivation.py 从 knowledge/wiki/ 与 knowledge/archetypes/ 自动派生。"
                       "请勿手工编辑；手工条目放 gdl_graph.json（override 覆盖层）。",
        "api_functions": [api_entries[k] for k in sorted(api_entries)],
        "bim_concepts": list(concepts_by_name.values()),
        "known_error_patterns": _derive_error_patterns(),
    }


def write_derived_graph(output_path: Path = DERIVED_GRAPH_PATH) -> dict:
    """派生并写盘，返回图谱 dict。"""
    graph = derive_graph()
    output_path.write_text(
        json.dumps(graph, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    return graph


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="wiki→图谱派生管道")
    parser.add_argument("--check", action="store_true", help="只打印统计，不写文件")
    args = parser.parse_args()

    graph = derive_graph() if args.check else write_derived_graph()
    apis = graph["api_functions"]
    concepts = graph["bim_concepts"]
    with_sig = sum(1 for a in apis if a["signature"])
    print("=== wiki→图谱派生统计 ===")
    print(f"API 函数：{len(apis)} 个（含签名 {with_sig} 个）")
    print(f"BIM 概念：{len(concepts)} 个")
    alias_total = sum(len(c["aliases"]) for c in concepts)
    print(f"概念别名：{alias_total} 个")
    print(f"错误模式：{len(graph['known_error_patterns'])} 条")
    if not args.check:
        print(f"已写入：{DERIVED_GRAPH_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
