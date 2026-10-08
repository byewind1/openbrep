from __future__ import annotations

import tomllib
from pathlib import Path


def test_codex_mcp_example_declares_the_openbrep_stdio_entrypoint():
    example = Path(__file__).parents[1] / "examples/mcp-clients/codex.config.toml"

    config = tomllib.loads(example.read_text(encoding="utf-8"))

    server = config["mcp_servers"]["openbrep"]
    assert server["command"] == "obr"
    assert server["args"] == ["mcp-server"]
