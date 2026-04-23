"""Tests for openbrep.wiki_knowledge."""

import tempfile
from pathlib import Path

import pytest

from openbrep.wiki_knowledge import WikiKnowledge, WikiPage


# ── Fixtures ──────────────────────────────────────────────


@pytest.fixture
def wiki_dir():
    """Create a temporary wiki directory with test pages."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)

        # PRISM_ page
        (d / "PRISM_.md").write_text(
            "---\n"
            "type: concept\n"
            "status: stable\n"
            "tags: [prism, 3d, geometry, extrusion]\n"
            "aliases: [PRISM, prism]\n"
            "---\n"
            "# PRISM_\n\n"
            "PRISM_ is a GDL command for creating prisms. [[BLOCK]] is related.\n"
            "```\n"
            "PRISM_ 4, 0, 0,  100, 0,  100, 50,  0, 50,  10\n"
            "```\n"
        )

        # BLOCK page
        (d / "BLOCK.md").write_text(
            "---\n"
            "type: concept\n"
            "status: stable\n"
            "tags: [block, box, 3d, geometry]\n"
            "aliases: [block]\n"
            "---\n"
            "# BLOCK\n\n"
            "BLOCK creates a rectangular box.\n"
        )

        # FOR_NEXT page
        (d / "FOR_NEXT.md").write_text(
            "---\n"
            "type: concept\n"
            "status: stable\n"
            "tags: [loop, control-flow, iteration]\n"
            "aliases: []\n"
            "---\n"
            "# FOR_NEXT\n\n"
            "FOR/NEXT creates a loop in GDL.\n"
        )

        # A guide page
        (d / "Creating_a_Parametric_Object.md").write_text(
            "---\n"
            "type: guide\n"
            "status: stable\n"
            "tags: [guide, tutorial, parametric]\n"
            "aliases: [parametric tutorial]\n"
            "---\n"
            "# Creating a Parametric Object\n\n"
            "Step-by-step guide.\n"
        )

        yield d


# ── Tests ─────────────────────────────────────────────────


class TestWikiPage:
    def test_title_from_heading(self):
        page = WikiPage(slug="PRISM_", filename="PRISM_.md", frontmatter={}, body="# PRISM_\n\ncontent")
        assert page.title == "PRISM_"

    def test_title_fallback_to_slug(self):
        page = WikiPage(slug="PRISM_", filename="PRISM_.md", frontmatter={}, body="content")
        assert page.title == "PRISM_"

    def test_tags_parsing(self):
        page = WikiPage(slug="x", filename="x.md", frontmatter={"tags": "[prism, 3d, geometry]"}, body="")
        assert page.tags == ["prism", "3d", "geometry"]

    def test_aliases_parsing(self):
        page = WikiPage(slug="x", filename="x.md", frontmatter={"aliases": "[PRISM, prism]"}, body="")
        assert page.aliases == ["PRISM", "prism"]

    def test_empty_tags_aliases(self):
        page = WikiPage(slug="x", filename="x.md", frontmatter={}, body="")
        assert page.tags == []
        assert page.aliases == []

    def test_format_for_context(self):
        page = WikiPage(
            slug="PRISM_",
            filename="PRISM_.md",
            frontmatter={"type": "concept", "tags": "[prism, 3d]"},
            body="# PRISM_\n\nBody content.",
        )
        ctx = page.format_for_context()
        assert "## Wiki: PRISM_" in ctx
        assert "type: concept" in ctx
        assert "tags: prism, 3d" in ctx
        assert "Body content." in ctx


class TestWikiKnowledge:
    def test_load(self, wiki_dir):
        wk = WikiKnowledge(str(wiki_dir))
        wk.load()
        assert wk.page_count == 4

    def test_get_by_slug(self, wiki_dir):
        wk = WikiKnowledge(str(wiki_dir))
        wk.load()
        page = wk.get_by_slug("PRISM_")
        assert page is not None
        assert page.title == "PRISM_"

    def test_get_by_slug_missing(self, wiki_dir):
        wk = WikiKnowledge(str(wiki_dir))
        wk.load()
        assert wk.get_by_slug("NONEXISTENT") is None

    def test_list_slugs(self, wiki_dir):
        wk = WikiKnowledge(str(wiki_dir))
        wk.load()
        slugs = wk.list_slugs()
        assert "PRISM_" in slugs
        assert "BLOCK" in slugs
        assert "FOR_NEXT" in slugs

    def test_get_relevant_by_command_name(self, wiki_dir):
        wk = WikiKnowledge(str(wiki_dir))
        wk.load()
        pages = wk.get_relevant("how to use PRISM_ command?", max_pages=2)
        assert len(pages) >= 1
        assert pages[0].slug == "PRISM_"

    def test_get_relevant_by_keyword(self, wiki_dir):
        wk = WikiKnowledge(str(wiki_dir))
        wk.load()
        pages = wk.get_relevant("loop iteration in gdl", max_pages=2)
        assert len(pages) >= 1
        assert pages[0].slug == "FOR_NEXT"

    def test_get_relevant_by_body_keyword(self, wiki_dir):
        wk = WikiKnowledge(str(wiki_dir))
        wk.load()
        pages = wk.get_relevant("rectangular box", max_pages=2)
        assert len(pages) >= 1
        assert pages[0].slug == "BLOCK"

    def test_get_relevant_no_match(self, wiki_dir):
        wk = WikiKnowledge(str(wiki_dir))
        wk.load()
        pages = wk.get_relevant("zzzzyyyy unknown", max_pages=2)
        assert pages == []

    def test_format_relevant_context(self, wiki_dir):
        wk = WikiKnowledge(str(wiki_dir))
        wk.load()
        ctx = wk.format_relevant_context("PRISM_", max_pages=1)
        assert "Wiki: PRISM_" in ctx
        assert "GDL command" in ctx

    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            wk = WikiKnowledge(tmp)
            wk.load()
            assert wk.page_count == 0
            assert wk.list_slugs() == []

    def test_nonexistent_dir(self):
        wk = WikiKnowledge("/tmp/zzz_nonexistent_wiki_xxxx")
        wk.load()
        assert wk.page_count == 0

    def test_no_frontmatter_page(self, wiki_dir):
        # Add a page without frontmatter
        (Path(wiki_dir) / "NoFrontmatter.md").write_text("# Just a heading\n\nSome content.\n")
        wk = WikiKnowledge(str(wiki_dir))
        wk.load()
        page = wk.get_by_slug("NoFrontmatter")
        assert page is not None
        assert page.frontmatter == {}

    def test_get_relevant_by_tag(self, wiki_dir):
        wk = WikiKnowledge(str(wiki_dir))
        wk.load()
        pages = wk.get_relevant("extrusion geometry", max_pages=2)
        slugs = [p.slug for p in pages]
        assert "PRISM_" in slugs

    def test_aliases_matching(self, wiki_dir):
        wk = WikiKnowledge(str(wiki_dir))
        wk.load()
        pages = wk.get_relevant("prism command syntax", max_pages=2)
        slugs = [p.slug for p in pages]
        assert "PRISM_" in slugs
