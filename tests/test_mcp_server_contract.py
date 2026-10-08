from __future__ import annotations

import json

from openbrep import mcp_server


def test_server_tool_registry_and_structured_result_contract():
    listed = mcp_server._list_tools_result()
    listed_names = {tool.name for tool in listed.tools}
    assert listed_names == set(mcp_server._MCP_TOOL_NAMES)
    assert set(mcp_server._TOOLS_BY_NAME) == listed_names

    apply_schema = next(tool for tool in listed.tools if tool.name == "apply_edit")
    assert "operation_id" in apply_schema.inputSchema["properties"]

    result = mcp_server._call_tool_result("capabilities", {})
    text_payload = json.loads(result.content[0].text)
    assert result.isError is False
    assert result.structuredContent == text_payload
    assert result.structuredContent["contract_version"] == "1.4"


def test_business_error_is_structured_tool_result(monkeypatch):
    monkeypatch.setitem(
        mcp_server._TOOLS_BY_NAME["compile_hsf"],
        "fn",
        lambda **_kwargs: {"ok": False, "error": {"code": "invalid_mode"}},
    )
    result = mcp_server._call_tool_result("compile_hsf", {"path": "unused"})
    assert result.isError is False
    assert result.structuredContent == {"ok": False, "error": {"code": "invalid_mode"}}
