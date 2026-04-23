# GDL Wiki Schema

This wiki follows the llm-wiki pattern for GDL knowledge management.

## Entity Types

| Type | Description | Frontmatter Required |
|------|-------------|---------------------|
| `concept` | A GDL command, concept, or pattern | type, tags, aliases, status |
| `guide` | Tutorial or how-to | type, tags, status |
| `reference` | Quick reference / cheatsheet | type, tags, status |

## Frontmatter Fields

```yaml
---
type: concept                  # concept | guide | reference
status: stable                 # stable | draft | deprecated
tags: [prism, 3d, geometry]   # lowercase, for search
aliases: [PRISM_, prism]      # alternative names for matching
source: raw/ccgdl_dev_doc/...  # optional, link to source material
---
```

## Content Guidelines

- **Concepts**: explain WHY first, then syntax, then examples
- **Include** wikilinks `[[RelatedConcept]]` for cross-references
- **Include** code examples that are compilable GDL
- **800-2000 words** per page (enough depth, not a novel)
- At least **3 wikilinks** per page to maintain connectivity

## Wikilink Format

Use `[[PageName]]` or `[[PageName|Display Text]]`. PageName matches filenames in `wiki/` without `.md` extension.
