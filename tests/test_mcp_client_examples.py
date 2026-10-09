from __future__ import annotations

from pathlib import Path
import json

import tomllib


def test_codex_mcp_example_declares_the_openbrep_stdio_entrypoint():
    example = Path(__file__).parents[1] / "examples/mcp-clients/codex.config.toml"

    config = tomllib.loads(example.read_text(encoding="utf-8"))

    server = config["mcp_servers"]["openbrep"]
    assert server["command"] == "obr"
    assert server["args"] == ["mcp-server"]


def test_claude_code_mcp_example_declares_the_same_openbrep_stdio_entrypoint():
    example = Path(__file__).parents[1] / "examples/mcp-clients/claude-code.mcp.json"

    config = json.loads(example.read_text(encoding="utf-8"))

    server = config["mcpServers"]["openbrep"]
    assert server["type"] == "stdio"
    assert server["command"] == "obr"
    assert server["args"] == ["mcp-server"]
