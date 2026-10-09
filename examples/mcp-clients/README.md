# MCP client examples

OpenBrep exposes a local stdio MCP server. The server owns deterministic HSF
operations and evidence; the calling Agent owns the model loop. OpenBrep model
credentials are not needed for the tools in this workflow.

## Codex

Install OpenBrep in an environment visible to the Codex process and make sure
`obr` is on that process's `PATH`. Merge [`codex.config.toml`](codex.config.toml)
into a trusted project's `.codex/config.toml`, or into `~/.codex/config.toml`.
Project-scoped Codex MCP configuration is only loaded for trusted projects.
Restart Codex, run `/mcp`, and confirm that the `openbrep` tools appear.

In the agent session, use the portable
[`openbrep-gdl-development` Skill](../agent-skills/openbrep-gdl-development/SKILL.md).
Begin with `capabilities` and `load_project`; use draft edits before apply, and
report mock compilation and local preview as such. The example is a client
configuration, not a claim that a live Codex-to-OpenBrep project task was run.

## Claude Code

Install OpenBrep in an environment visible to Claude Code and make sure `obr`
is on that process's `PATH`. Copy [`claude-code.mcp.json`](claude-code.mcp.json)
to the project root as `.mcp.json`, or add the same `mcpServers.openbrep` entry
with `claude mcp add-json`. Start Claude Code in the project, approve the
project MCP server when prompted, then run `/mcp` and confirm that the OpenBrep
tools appear. Use the same portable
[`openbrep-gdl-development` Skill](../agent-skills/openbrep-gdl-development/SKILL.md)
and workflow described above; only the MCP connection configuration is
client-specific.

Claude Code project MCP configuration and approval behavior follow the
official [Claude Code MCP documentation](https://docs.anthropic.com/en/docs/claude-code/mcp).

## DeepSeek Harness

See [`../dsh/README.md`](../dsh/README.md) for the optional DSH stdio overlay.
That file is a configuration sample; full DSH task-chain validation remains a
separate integration task.

Codex configuration syntax and project trust behavior follow the official
[Codex MCP documentation](https://learn.chatgpt.com/docs/extend/mcp).
