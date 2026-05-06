#!/usr/bin/env python3
"""Cross-check local GDL knowledge against official Graphisoft sources.

Official GDL Center reference pages are treated as syntax authority. The
Graphisoft Community URL is recorded as a practice/case-study entry point only;
community posts must not override official syntax.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from openbrep.wiki_knowledge import WikiKnowledge  # noqa: E402


DEFAULT_OFFICIAL_INDEX = "https://gdl.graphisoft.com/reference-guide/index/"
DEFAULT_COMMUNITY_SEARCH = "https://community.graphisoft.com/t5/forums/searchpage/tab/message"
DEFAULT_COMMANDS = ("BLOCK", "PRISM_", "REVOLVE", "PROJECT2", "HOTSPOT2", "MATERIAL")


@dataclass(frozen=True)
class OfficialCommand:
    command: str
    signature: str
    url: str


@dataclass(frozen=True)
class VerificationRow:
    command: str
    status: str
    local_page: str
    local_title: str
    local_source: str
    official_exists: bool
    official_signatures: list[str]
    official_urls: list[str]
    local_mentions_command: bool
    local_has_syntax_section: bool
    community_search_url: str
    notes: list[str]


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href = ""
        self._text_parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href", "")
        self._href = href
        self._text_parts = []

    def handle_data(self, data):
        if self._href:
            self._text_parts.append(data)

    def handle_endtag(self, tag):
        if tag.lower() != "a" or not self._href:
            return
        text = html.unescape(" ".join(self._text_parts)).strip()
        text = re.sub(r"\s+", " ", text)
        if text:
            self.links.append((text, self._href))
        self._href = ""
        self._text_parts = []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--knowledge-dir", default=str(ROOT / "knowledge"))
    parser.add_argument("--official-index", default=DEFAULT_OFFICIAL_INDEX)
    parser.add_argument("--official-index-file", help="Use a local HTML file instead of fetching")
    parser.add_argument(
        "--offline-ok",
        action="store_true",
        help="Return a JSON error report instead of raising when the official index cannot be fetched",
    )
    parser.add_argument("--community-search", default=DEFAULT_COMMUNITY_SEARCH)
    parser.add_argument("--commands", nargs="*", default=list(DEFAULT_COMMANDS))
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--output", help="Write report to a file instead of stdout")
    args = parser.parse_args(argv)

    try:
        official_html = _read_official_index(args.official_index, args.official_index_file)
    except Exception as exc:
        if not args.offline_ok:
            raise
        payload = {
            "ok": False,
            "official_index": args.official_index_file or args.official_index,
            "error": f"Failed to load official index: {exc}",
            "hint": "Download the Graphisoft GDL index HTML and pass it with --official-index-file.",
            "rows": [],
        }
        rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            Path(args.output).write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
        return 0
    official = parse_official_index(official_html, base_url=args.official_index)
    rows = verify_commands(
        knowledge_dir=Path(args.knowledge_dir),
        official_commands=official,
        commands=args.commands,
        community_search=args.community_search,
    )
    payload = {
        "ok": all(row.status == "ok" for row in rows),
        "official_index": args.official_index_file or args.official_index,
        "authority_order": [
            "Graphisoft GDL Reference Guide",
            "Graphisoft GDL Center official docs",
            "Graphisoft Community GDL discussions",
            "OpenBrep local knowledge",
        ],
        "rows": [asdict(row) for row in rows],
    }

    rendered = (
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        if args.format == "json"
        else render_markdown(payload)
    )
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if payload["ok"] else 1


def parse_official_index(content: str, *, base_url: str = DEFAULT_OFFICIAL_INDEX) -> dict[str, list[OfficialCommand]]:
    parser = _LinkParser()
    parser.feed(content)

    commands: dict[str, list[OfficialCommand]] = {}
    for text, href in parser.links:
        command = normalize_command_text(text)
        if not command:
            continue
        url = urllib.parse.urljoin(base_url, href)
        commands.setdefault(command, []).append(OfficialCommand(command, text, url))
    return commands


def verify_commands(
    *,
    knowledge_dir: Path,
    official_commands: dict[str, list[OfficialCommand]],
    commands: list[str],
    community_search: str = DEFAULT_COMMUNITY_SEARCH,
) -> list[VerificationRow]:
    wiki = WikiKnowledge(str(knowledge_dir / "wiki"))
    wiki.load()

    rows: list[VerificationRow] = []
    for raw_command in commands:
        command = normalize_command_name(raw_command)
        page = wiki.get_by_slug(command)
        official_matches = official_commands.get(command, [])
        body = page.body if page is not None else ""
        local_mentions = bool(re.search(rf"\b{re.escape(command)}\b", body))
        has_syntax = bool(re.search(r"^##\s+Syntax\b", body, re.MULTILINE | re.IGNORECASE))
        notes: list[str] = []

        if page is None:
            status = "missing_local"
            notes.append("Local wiki page is missing.")
        elif not official_matches:
            status = "missing_official"
            notes.append("Official command index did not contain this command.")
        elif not local_mentions or not has_syntax:
            status = "needs_review"
            if not local_mentions:
                notes.append("Local page does not mention the command token in the body.")
            if not has_syntax:
                notes.append("Local page lacks a Syntax section.")
        else:
            status = "ok"

        rows.append(
            VerificationRow(
                command=command,
                status=status,
                local_page=page.filename if page is not None else "",
                local_title=page.title if page is not None else "",
                local_source=page.frontmatter.get("source", "") if page is not None else "",
                official_exists=bool(official_matches),
                official_signatures=[item.signature for item in official_matches],
                official_urls=[item.url for item in official_matches],
                local_mentions_command=local_mentions,
                local_has_syntax_section=has_syntax,
                community_search_url=community_search_url(community_search, command),
                notes=notes,
            )
        )
    return rows


def render_markdown(payload: dict) -> str:
    lines = [
        "# GDL Knowledge Source Verification",
        "",
        f"- Official index: {payload.get('official_index', '')}",
        f"- Overall: {'OK' if payload.get('ok') else 'NEEDS REVIEW'}",
        "",
        "## Authority Order",
        "",
    ]
    for item in payload.get("authority_order", []):
        lines.append(f"- {item}")

    lines.extend(
        [
            "",
            "## Command Cross-Check",
            "",
            "| Command | Status | Local Page | Official Signature | Community Search |",
            "|---|---|---|---|---|",
        ]
    )
    for row in payload.get("rows", []):
        signatures = "<br>".join(row.get("official_signatures") or [])
        community = row.get("community_search_url", "")
        lines.append(
            "| {command} | {status} | {local_page} | {signatures} | [search]({community}) |".format(
                command=row.get("command", ""),
                status=row.get("status", ""),
                local_page=row.get("local_page", ""),
                signatures=signatures,
                community=community,
            )
        )
    return "\n".join(lines).rstrip() + "\n"


def normalize_command_text(text: str) -> str:
    value = re.sub(r"\[[^\]]+\]", " ", text or "")
    value = value.strip()
    match = re.search(r"\b[A-Z][A-Z0-9_]*(?:\{\d+\})?\b", value)
    if not match:
        return ""
    return normalize_command_name(match.group(0))


def normalize_command_name(command: str) -> str:
    value = (command or "").strip().upper()
    value = re.sub(r"\{\d+\}$", "", value)
    return value


def community_search_url(base_url: str, command: str) -> str:
    query = urllib.parse.urlencode(
        {
            "advanced": "false",
            "allow_punctuation": "false",
            "q": f"GDL {command}",
        }
    )
    return f"{base_url}?{query}"


def _read_official_index(url: str, path: str | None) -> str:
    if path:
        return Path(path).read_text(encoding="utf-8")
    request = urllib.request.Request(url, headers={"User-Agent": "OpenBrep knowledge verifier"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
