# Wiki Operation Log

## 2026-04-23

- Initialized wiki structure (CLAUDE.md, index.md, log.md)
- Moved `ccgdl_dev_doc/` to `raw/ccgdl_dev_doc/` as source reference
- Created `scripts/lint-wiki.py` for dead link and orphan checking
- Created first batch of wiki concept pages

## 2026-04-23 (续)

- Created `openbrep/wiki_knowledge.py` — wiki retrieval module (frontmatter parsing, keyword/command-name scoring, top-N context formatting)
- Created `tests/test_wiki_knowledge.py` — 20 tests covering WikiPage, WikiKnowledge retrieval, edge cases
- Created 10 wiki concept pages: PRISM_, BLOCK, Transformation_Stack, ADD_DEL, BODY_EDGE_PGON, IF_ENDIF, FOR_NEXT, HOTSPOT2, PROJECT2, Paramlist_XML
- All pages include frontmatter, code examples, edge cases/traps, and wikilinks (3+ per page)
- Lint check: all pages pass (no dead links, no orphans, no missing frontmatter)
