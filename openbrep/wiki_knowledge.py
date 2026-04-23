"""
Wiki knowledge retrieval for GDL Q&A.

Loads wiki/ pages with frontmatter, matches user questions to pages
by keyword/command-name scoring, returns relevant context for LLM
answer synthesis.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class WikiPage:
    """A single wiki page with parsed frontmatter and body."""

    slug: str
    filename: str
    frontmatter: dict[str, str]
    body: str

    @property
    def title(self) -> str:
        """Page title (first # heading or slug)."""
        for line in self.body.splitlines():
            if line.startswith("# "):
                return line.lstrip("# ").strip()
        return self.slug

    @property
    def tags(self) -> list[str]:
        raw = self.frontmatter.get("tags", "")
        return [t.strip().lower() for t in raw.strip("[]").split(",") if t.strip()]

    @property
    def aliases(self) -> list[str]:
        raw = self.frontmatter.get("aliases", "")
        return [a.strip() for a in raw.strip("[]").split(",") if a.strip()]

    def format_for_context(self) -> str:
        """Format page as injectable context block."""
        header = f"## Wiki: {self.title} (type: {self.frontmatter.get('type', '?')})"
        tags_str = ", ".join(self.tags) if self.tags else ""
        meta = f"tags: {tags_str}" if tags_str else ""
        parts = [header, meta, self.body] if meta else [header, self.body]
        return "\n\n".join(p for p in parts if p)


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
_COMMAND_RE = re.compile(r"\b[A-Z_]{3,}(?:\{\d+\})?\b")


class WikiKnowledge:
    """
    Retrieves GDL wiki pages for LLM-based Q&A.

    Loads markdown files from knowledge/wiki/, parses frontmatter,
    and supports keyword / command-name matching for relevant page retrieval.
    """

    def __init__(self, wiki_dir: str = "./knowledge/wiki"):
        self.wiki_dir = Path(wiki_dir)
        self._pages: dict[str, WikiPage] = {}
        self._loaded = False

    # ── Loading ────────────────────────────────────────────

    def load(self) -> None:
        """Load all wiki .md files with frontmatter parsing."""
        self._pages.clear()

        if not self.wiki_dir.is_dir():
            self._loaded = True
            return

        for md_file in sorted(self.wiki_dir.glob("*.md")):
            slug = md_file.stem
            try:
                raw = md_file.read_text(encoding="utf-8")
            except Exception:
                continue

            fm, body = self._split_frontmatter(raw)
            self._pages[slug] = WikiPage(
                slug=slug,
                filename=md_file.name,
                frontmatter=fm,
                body=body,
            )

        self._loaded = True

    @staticmethod
    def _split_frontmatter(raw: str) -> tuple[dict[str, str], str]:
        """Split raw markdown into (frontmatter_dict, body)."""
        m = _FRONTMATTER_RE.match(raw)
        if not m:
            return {}, raw

        fm: dict[str, str] = {}
        for line in m.group(1).splitlines():
            if ":" in line:
                key, _, val = line.partition(":")
                fm[key.strip()] = val.strip()

        body = raw[m.end():].strip()
        return fm, body

    # ── Retrieval ──────────────────────────────────────────

    def get_relevant(self, query: str, max_pages: int = 3) -> list[WikiPage]:
        """
        Return top-N wiki pages relevant to query.

        Scoring factors (cumulative):
        - Command name match in page slug / aliases  (+30)
        - Query keyword in page title / tags / aliases (+10)
        - Query keyword in body                        (+2)
        """
        if not self._loaded:
            self.load()

        if not self._pages:
            return []

        query_lower = query.lower()
        # 也尝试大写版本，让 "cylinder" → 匹配 "CYLIND"
        query_commands = set(_COMMAND_RE.findall(query)) | set(_COMMAND_RE.findall(query.upper()))

        # split() 对中英混排无效（如 "cylinder命令的语法" 会变成一个 token）
        # 改为分别提取英文单词和中文字符序列
        eng_words = re.findall(r"[a-z]+", query_lower)
        chn_chars = re.findall(r"[一-鿿]+", query_lower)
        tokens = {w for w in eng_words if len(w) > 2} | {c for c in chn_chars if len(c) > 1}

        scored: list[tuple[int, WikiPage]] = []

        for page in self._pages.values():
            score = 0
            title_lower = page.title.lower()
            body_lower = page.body.lower()
            slug_lower = page.slug.lower()

            # Command name match (highest weight)
            for cmd in query_commands:
                if cmd == page.slug:
                    score += 30
                elif any(cmd == alias for alias in page.aliases):
                    score += 25
                elif cmd.lower() == slug_lower:
                    score += 30

            # Keyword overlap in title / tags / aliases
            title_tokens = {w for w in title_lower.split() if len(w) > 2}
            overlap = tokens & title_tokens
            score += len(overlap) * 10

            tag_overlap = tokens & set(page.tags)
            score += len(tag_overlap) * 8

            alias_tokens = set(a.lower() for a in page.aliases)
            alias_overlap = tokens & alias_tokens
            score += len(alias_overlap) * 10

            # Keyword in body
            body_hits = sum(1 for w in tokens if w in body_lower)
            score += body_hits * 2

            if score > 0:
                scored.append((score, page))

        if not scored:
            return []

        scored.sort(key=lambda x: x[0], reverse=True)
        return [page for _, page in scored[:max_pages]]

    def get_by_slug(self, slug: str) -> Optional[WikiPage]:
        """Get a single page by its slug (filename stem)."""
        if not self._loaded:
            self.load()
        return self._pages.get(slug)

    def list_slugs(self) -> list[str]:
        """List all available wiki page slugs."""
        if not self._loaded:
            self.load()
        return sorted(self._pages.keys())

    @property
    def page_count(self) -> int:
        if not self._loaded:
            self.load()
        return len(self._pages)

    # ── Context formatting ────────────────────────────────

    def format_relevant_context(self, query: str, max_pages: int = 3) -> str:
        """
        Get relevant wiki pages formatted as injectable context.

        Returns empty string if no matches.
        """
        pages = self.get_relevant(query, max_pages=max_pages)
        if not pages:
            return ""

        parts = []
        for page in pages:
            parts.append(page.format_for_context())

        return "\n\n---\n\n".join(parts)
