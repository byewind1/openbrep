from __future__ import annotations

import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from openbrep import mcp_server
from openbrep.hsf_project import GDLParameter, HSFProject, ScriptType


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
    assert result.structuredContent["contract_version"] == "1.5"


def test_business_error_is_structured_tool_result(monkeypatch):
    monkeypatch.setitem(
        mcp_server._TOOLS_BY_NAME["compile_hsf"],
        "fn",
        lambda **_kwargs: {"ok": False, "error": {"code": "invalid_mode"}},
    )
    result = mcp_server._call_tool_result("compile_hsf", {"path": "unused"})
    assert result.isError is False
    assert result.structuredContent == {"ok": False, "error": {"code": "invalid_mode"}}


def test_stdio_server_initializes_lists_tools_and_calls_capabilities_without_model_config():
    async def exercise_server():
        server = StdioServerParameters(command=sys.executable, args=["-m", "cli.main", "mcp-server"])
        async with stdio_client(server) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await asyncio.wait_for(session.initialize(), timeout=5)
                listed = await asyncio.wait_for(session.list_tools(), timeout=5)
                capabilities = await asyncio.wait_for(session.call_tool("capabilities", {}), timeout=5)
                return initialized, listed, capabilities

    initialized, listed, capabilities = asyncio.run(exercise_server())

    assert initialized.serverInfo.name == "openbrep"
    assert {tool.name for tool in listed.tools} == set(mcp_server._MCP_TOOL_NAMES)
    assert capabilities.isError is False
    assert capabilities.structuredContent["contract_version"] == "1.5"
    assert capabilities.structuredContent["availability"]["archicad_host"]["available_via_mcp"] is False


def test_stdio_server_supports_typed_edit_idempotent_retry_and_rollback(tmp_path):
    project = HSFProject.create_new("McpStdio", str(tmp_path))
    project.add_parameter(GDLParameter(name="hasBack", type_tag="Boolean", value="0"))
    project.add_parameter(GDLParameter(name="pattern", type_tag="String", value='"plain"'))
    project.scripts[ScriptType.SCRIPT_3D] = "BLOCK A, B, ZZYZX\n"
    root = project.save_to_disk()
    original = HSFProject.load_from_disk(str(root))
    original_a = original.get_parameter("A").value
    operation_id = "stdio-typed-edit-001"
    spec = {
        "type": "set_parameters",
        "values": {"A": 1.7, "hasBack": True, "pattern": "回纹"},
    }

    async def exercise_server():
        server = StdioServerParameters(command=sys.executable, args=["-m", "cli.main", "mcp-server"])
        async with stdio_client(server) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await asyncio.wait_for(session.initialize(), timeout=5)
                edited = await asyncio.wait_for(
                    session.call_tool(
                        "apply_edit",
                        {
                            "path": str(root),
                            "spec": spec,
                            "mode": "apply",
                            "operation_id": operation_id,
                        },
                    ),
                    timeout=20,
                )
                replayed = await asyncio.wait_for(
                    session.call_tool(
                        "apply_edit",
                        {
                            "path": str(root),
                            "spec": spec,
                            "mode": "apply",
                            "operation_id": operation_id,
                        },
                    ),
                    timeout=20,
                )
                revision_id = edited.structuredContent["revision_id"]
                restored = await asyncio.wait_for(
                    session.call_tool("rollback", {"path": str(root), "revision_id": revision_id}),
                    timeout=20,
                )
                return edited, replayed, restored

    edited, replayed, restored = asyncio.run(exercise_server())

    assert edited.isError is False
    assert edited.structuredContent["ok"] is True
    assert replayed.structuredContent["operation_replayed"] is True
    assert restored.structuredContent["ok"] is True
    final = HSFProject.load_from_disk(str(root))
    assert final.get_parameter("A").value == original_a
    assert final.get_parameter("hasBack").value == "0"
    assert final.get_parameter("pattern").value == '"plain"'
