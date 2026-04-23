"""
Knowledge management for GDL Agent.

Loads reference documents from the knowledge/ directory and injects them
into LLM prompts. Supports:
- Layered loading (by task type)
- Keyword-based relevance filtering
- Premium knowledge base support (ccgdl_dev_doc)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional


class KnowledgeBase:
    """
    Manages GDL reference documentation for RAG-style prompt injection.

    The knowledge base is a directory of Markdown files that contain:
    - GDL syntax reference
    - Control flow and parameters
    - Common error patterns and fixes
    - 2D commands and functions

    These are loaded into the LLM's system prompt to compensate for the
    scarcity of GDL training data.
    """

    def __init__(self, knowledge_dir: str = "./knowledge"):
        self.knowledge_dir = Path(knowledge_dir)
        self._docs: dict[str, str] = {}
        self._loaded = False

        # Pro docs (ccgdl_dev_doc) take priority when available
        _pro = ["pro_GDL_01_Basics", "pro_GDL_02_Shapes", "pro_GDL_03_Attributes",
                "pro_GDL_07_Examples"]
        _pro_debug = ["pro_GDL_04_Debug_Compat", "pro_GDL_09_Error_Dictionary"]
        _pro_adv   = ["pro_GDL_05_Globals_Request", "pro_GDL_06_Macro_UI_Perf",
                      "pro_GDL_11_2D_Advanced", "pro_GDL_12_Examples_Verified"]
        _free = ["GDL_quick_reference", "GDL_parameters", "GDL_control_flow",
                 "GDL_2d_commands", "GDL_functions"]

        self._layers = {
            "create": _pro + _pro_adv + _free,
            "modify": _pro + ["GDL_parameters", "GDL_control_flow"],
            "debug":  _pro_debug + ["GDL_common_errors", "GDL_control_flow"],
            "all":    _pro + _pro_debug + _pro_adv + _free + ["GDL_common_errors"],
        }

    def load(self) -> None:
        """
        Load knowledge docs from two tiers:
        - Free:  knowledge/*.md          (public, on GitHub)
        - Pro:   knowledge/ccgdl_dev_doc/docs/*.md  (gitignored, loaded with 'pro_' prefix)
        Pro docs override free docs for the same topic when both exist.
        """
        self._docs.clear()

        if not self.knowledge_dir.exists():
            self._loaded = True
            return

        # Free tier: top-level *.md (skip README / index noise)
        _skip = {"README", "CHANGELOG"}
        for md_file in sorted(self.knowledge_dir.glob("*.md")):
            if md_file.stem in _skip:
                continue
            try:
                self._docs[md_file.stem] = md_file.read_text(encoding="utf-8")
            except Exception:
                continue

        # Pro tier: ccgdl_dev_doc/docs/*.md  (gitignored — only present locally)
        pro_dir = self.knowledge_dir / "raw" / "ccgdl_dev_doc" / "docs"
        if pro_dir.exists():
            for md_file in sorted(pro_dir.glob("*.md")):
                if md_file.stem in _skip:
                    continue
                try:
                    self._docs[f"pro_{md_file.stem}"] = md_file.read_text(encoding="utf-8")
                except Exception:
                    continue

        self._loaded = True

    @property
    def has_pro(self) -> bool:
        """True if pro (ccgdl_dev_doc) docs are loaded."""
        if not self._loaded:
            self.load()
        return any(k.startswith("pro_") for k in self._docs)

    def get_by_task_type(self, task_type: str) -> str:
        """
        Get knowledge documents relevant to task type.

        Args:
            task_type: One of 'create', 'modify', 'debug', 'all'

        Returns:
            Concatenated relevant documents.
        """
        if not self._loaded:
            self.load()

        if not self._docs:
            return ""

        # "all" → 直接返回全部已加载文档，不走写死列表
        # 确保用户 copy 进 knowledge/ 的任意 .md 文件都能被加载
        if task_type == "all":
            return self.get_all()

        doc_names = self._layers.get(task_type, [])
        parts = []
        for name in doc_names:
            if name in self._docs:
                parts.append(f"## {name}\n\n{self._docs[name]}")

        # 没有匹配到任何文档时降级到全部
        if not parts:
            return self.get_all()

        result = "\n\n---\n\n".join(parts)

        MAX_TOKENS = 12000
        if len(result) // 3 > MAX_TOKENS:
            # 优先保留 pro 层，裁剪 free 层
            pro_parts = [
                p for p in parts
                if "## pro_" in p
            ]
            if pro_parts:
                result = "\n\n---\n\n".join(pro_parts)

        return result

    def get_all(self) -> str:
        """
        Get all knowledge documents concatenated.

        Suitable for models with large context windows (128k+).
        """
        if not self._loaded:
            self.load()

        if not self._docs:
            return ""

        parts = []
        for name, content in self._docs.items():
            parts.append(f"## {name}\n\n{content}")

        return "\n\n---\n\n".join(parts)

    def _build_command_index(self) -> None:
        """
        Build command -> doc mapping from knowledge/GDL_command_index.md.

        Index format:
        | 命令 | 所属 Pro 文件 | task_type |
        | PRISM_ | GDL_10_3D_Commands_Full | create |
        """
        self._command_index: dict[str, list[str]] = {}

        index_file = self.knowledge_dir / "GDL_command_index.md"
        if not index_file.exists():
            return

        try:
            content = index_file.read_text(encoding="utf-8")
        except Exception:
            return

        cmd_map: dict[str, set[str]] = {}
        for line in content.splitlines():
            line = line.strip()
            if not line.startswith("|"):
                continue
            # skip table header/separator lines
            if "命令" in line or "---" in line:
                continue

            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) < 2:
                continue

            cmd = cells[0]
            pro_file = cells[1]
            if not cmd or not pro_file:
                continue

            doc_name = f"pro_{pro_file}"
            if doc_name not in self._docs:
                continue

            if cmd not in cmd_map:
                cmd_map[cmd] = set()
            cmd_map[cmd].add(doc_name)

        self._command_index = {k: sorted(v) for k, v in cmd_map.items()}

    def get_relevant(self, query: str, max_docs: int = 3) -> str:
        """
        Get knowledge documents relevant to a query.

        Uses simple keyword matching. For production use, consider
        replacing with embedding-based retrieval.

        Args:
            query: The user's instruction or error message.
            max_docs: Maximum number of documents to return.

        Returns:
            Concatenated relevant documents.
        """
        if not self._loaded:
            self.load()

        if not self._docs:
            return ""

        query_lower = query.lower()

        # score_map: doc_name -> score
        score_map: dict[str, int] = {}

        # 1) Base keyword score (existing behavior)
        for name, content in self._docs.items():
            score = 0
            content_lower = content.lower()
            name_lower = name.lower()

            # Name/content match
            for word in query_lower.split():
                if len(word) > 2:
                    if word in name_lower:
                        score += 10
                    if word in content_lower:
                        score += 1

            # Special keyword boosts
            error_keywords = ["error", "bug", "fix", "fail", "wrong", "错误", "报错", "失败"]
            if any(kw in query_lower for kw in error_keywords):
                if "error" in name_lower or "common" in name_lower:
                    score += 20

            syntax_keywords = ["prism", "revolve", "extrude", "tube", "命令", "语法", "syntax"]
            if any(kw in query_lower for kw in syntax_keywords):
                if "reference" in name_lower or "guide" in name_lower:
                    score += 20

            template_keywords = ["xml", "template", "structure", "结构", "模板"]
            if any(kw in query_lower for kw in template_keywords):
                if "template" in name_lower or "xml" in name_lower:
                    score += 20

            if score > 0:
                score_map[name] = score_map.get(name, 0) + score

        # 2) Command-level exact match boost (highest weight)
        if not hasattr(self, "_command_index"):
            self._build_command_index()

        cmd_candidates = re.findall(r"\b[A-Z_]{3,}(?:\{\d+\})?\b", query)
        for cmd in cmd_candidates:
            if cmd in self._command_index:
                for doc_name in self._command_index[cmd]:
                    score_map[doc_name] = score_map.get(doc_name, 0) + 30

        # Convert to sortable tuples
        scored = []
        for name, score in score_map.items():
            content = self._docs.get(name, "")
            if score > 0 and content:
                scored.append((score, name, content))

        # If no matches, return all docs (better safe than sorry)
        if not scored:
            return self.get_all()

        # Sort by score descending
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:max_docs]

        parts = []
        for _, name, content in top:
            parts.append(f"## {name}\n\n{content}")

        return "\n\n---\n\n".join(parts)

    @property
    def doc_count(self) -> int:
        if not self._loaded:
            self.load()
        return len(self._docs)

    @property
    def doc_names(self) -> list[str]:
        if not self._loaded:
            self.load()
        return list(self._docs.keys())
