#!/usr/bin/env python3
"""Deterministic wiki lint checks: dead wikilinks, orphan pages, missing frontmatter."""

import os
import re
import sys

WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)


def find_wiki_files(wiki_dir):
    files = {}
    for fn in os.listdir(wiki_dir):
        if fn.endswith(".md"):
            slug = os.path.splitext(fn)[0]
            files[slug] = fn
    return files


def extract_links(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    # Skip content inside code blocks
    code_blocks = re.findall(r"```.*?```", content, re.DOTALL)
    for block in code_blocks:
        content = content.replace(block, "")
    return WIKILINK_RE.findall(content)


def extract_frontmatter(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    m = FRONTMATTER_RE.match(content)
    if not m:
        return {}
    fm = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            fm[key.strip()] = val.strip()
    return fm


def lint(wiki_dir):
    if not os.path.isdir(wiki_dir):
        print(f"Error: {wiki_dir} is not a directory", file=sys.stderr)
        return 1

    files = find_wiki_files(wiki_dir)
    issues = []
    inbound = {slug: set() for slug in files}

    for slug, fn in files.items():
        filepath = os.path.join(wiki_dir, fn)
        if not os.path.isfile(filepath):
            continue

        # Check frontmatter
        fm = extract_frontmatter(filepath)
        if not fm:
            issues.append(f"MISSING_FRONTMATTER: {fn}")
        elif "type" not in fm:
            issues.append(f"MISSING_TYPE: {fn}")

        # Extract wikilinks
        links = extract_links(filepath)
        for target in links:
            target_slug = target.strip()
            if target_slug in files:
                inbound[target_slug].add(slug)
            else:
                issues.append(f"DEAD_LINK: [[{target}]] in {fn}")

    # Check orphans (pages with no inbound links)
    skip = {"index", "log"}
    for slug in files:
        if slug in skip:
            continue
        if not inbound.get(slug):
            issues.append(f"ORPHAN: {files[slug]} has no inbound links")

    if not issues:
        print("OK: No issues found")
        return 0

    for issue in sorted(issues):
        print(issue)
    print(f"\nTotal: {len(issues)} issue(s)")
    return 0


if __name__ == "__main__":
    wiki_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(__file__), "..", "wiki"
    )
    sys.exit(lint(os.path.abspath(wiki_dir)))
