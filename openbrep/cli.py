"""
Command-line interface for openbrep.

Provides: init, run, chat, decompile, config subcommands.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import click

from openbrep.config import GDLAgentConfig


# ── Rich console helpers ──────────────────────────────────────────────

def _get_console():
    try:
        from rich.console import Console
        return Console()
    except ImportError:
        return None


def _print(msg: str, style: str = ""):
    console = _get_console()
    if console:
        console.print(msg, style=style)
    else:
        click.echo(msg)


def _print_result(result):
    """Pretty-print an AgentResult."""
    try:
        from rich.panel import Panel
        from rich.table import Table
        from rich.console import Console

        console = Console()

        if result.success:
            console.print(Panel(
                f"[bold green]✅ Compiled successfully[/] in {result.attempts} attempt(s)\n"
                f"   Output: [cyan]{result.output_path}[/]\n"
                f"   Tokens: {result.total_tokens:,} | "
                f"Time: {result.total_duration_ms:,}ms",
                title="Result",
                border_style="green",
            ))
        else:
            console.print(Panel(
                f"[bold red]❌ {result.status.value}[/] after {result.attempts} attempt(s)\n"
                f"   {result.error_summary[:200]}",
                title="Result",
                border_style="red",
            ))

        # History table
        if result.history:
            table = Table(title="Execution History", show_lines=True)
            table.add_column("#", style="dim", width=3)
            table.add_column("Stage", width=16)
            table.add_column("Result", width=8)
            table.add_column("Details", ratio=1)

            for rec in result.history:
                status = "[green]✓[/]" if rec.success else "[red]✗[/]"
                detail = rec.error[:80] if rec.error else "OK"
                table.add_row(str(rec.attempt), rec.stage, status, detail)

            console.print(table)

    except ImportError:
        # Fallback without rich
        marker = "✅" if result.success else "❌"
        click.echo(f"\n{marker} {result.status.value} ({result.attempts} attempts)")
        if result.output_path:
            click.echo(f"   Output: {result.output_path}")
        if result.error_summary:
            click.echo(f"   Error: {result.error_summary[:200]}")


# ── Event handler for real-time feedback ──────────────────────────────

def _cli_event_handler(event: str, **kwargs):
    """Print agent events to the terminal."""
    try:
        from rich.console import Console
        console = Console()
        prefix = "  "

        match event:
            case "start":
                console.print(f"\n[bold cyan]🚀 GDL Agent[/]")
                console.print(f"{prefix}Task: {kwargs.get('instruction', '')[:80]}")
                console.print(f"{prefix}File: [dim]{kwargs.get('source', '')}[/]")
                console.print(f"{prefix}Max retries: {kwargs.get('max_iterations', '?')}\n")
            case "attempt_start":
                n, m = kwargs.get('attempt'), kwargs.get('max_attempts')
                console.print(f"{prefix}[dim]─── Attempt {n}/{m} ───[/]")
            case "llm_call":
                console.print(f"{prefix}🧠 Calling LLM...")
            case "validation_passed":
                console.print(f"{prefix}[green]✓[/] Validation passed")
            case "file_written":
                console.print(f"{prefix}[green]✓[/] Written {kwargs.get('size', 0)} bytes")
            case "compile_start":
                console.print(f"{prefix}🔧 Compiling...")
            case "compile_success":
                ms = kwargs.get('duration_ms', 0)
                console.print(f"{prefix}[bold green]✅ Compiled![/] ({ms}ms)")
            case "compile_failed":
                console.print(f"{prefix}[red]✗ Compile error:[/] {kwargs.get('error', '')[:80]}")
            case "xml_invalid":
                console.print(f"{prefix}[red]✗ XML invalid:[/] {kwargs.get('error', '')[:80]}")
            case "gdl_issues":
                for i in kwargs.get("issues", []):
                    console.print(f"{prefix}[yellow]⚠ {i}[/]")
            case "identical_retry":
                console.print(f"{prefix}[yellow]⚠ Identical to previous — stopping[/]")
            case "exhausted":
                console.print(f"{prefix}[red]⛔ Max retries reached[/]")
            case "compiler_unavailable":
                console.print(f"{prefix}[red]⛔ LP_XMLConverter not found[/]")
    except ImportError:
        pass  # Silent without rich


# ── CLI Commands ──────────────────────────────────────────────────────


@click.group()
@click.version_option(package_name="openbrep")
def cli():
    """🤖 openbrep — AI Agent for ArchiCAD GDL development."""
    pass


@cli.command()
@click.option("--dir", "-d", default=".", help="Directory to initialize")
def init(dir: str):
    """Initialize a new openbrep workspace."""
    workspace = Path(dir)

    config = GDLAgentConfig()

    # Auto-detect compiler
    from openbrep.config import _auto_detect_converter
    converter = _auto_detect_converter()
    if converter:
        config.compiler.path = converter
        _print(f"  [green]✓[/] Found LP_XMLConverter: {converter}")
    else:
        _print("  [yellow]⚠[/] LP_XMLConverter not found. Set CONVERTER_PATH later.")

    # Create directories
    for d in ["knowledge", "templates", "src", "output"]:
        (workspace / d).mkdir(parents=True, exist_ok=True)
        _print(f"  [green]✓[/] Created {d}/")

    # Write config
    config_path = workspace / "config.toml"
    if not config_path.exists():
        config_path.write_text(config.to_toml_string(), encoding="utf-8")
        _print(f"  [green]✓[/] Created config.toml")
    else:
        _print(f"  [dim]  config.toml already exists, skipping[/]")

    # Write system prompt
    prompt_dir = workspace / "prompts"
    prompt_dir.mkdir(exist_ok=True)
    system_prompt_path = prompt_dir / "system.md"
    if not system_prompt_path.exists():
        src = Path(__file__).parent / "prompts" / "system.md"
        if src.exists():
            system_prompt_path.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            _print(f"  [green]✓[/] Created prompts/system.md")

    # Write starter knowledge files
    _write_starter_knowledge(workspace / "knowledge")

    _print(f"\n  [bold green]✓ Workspace ready![/] Run [cyan]openbrep run \"your task\"[/] to start.\n")


@cli.command()
@click.argument("instruction")
@click.option("--file", "-f", default=None, help="XML source file path")
@click.option("--output", "-o", default=None, help="Output .gsm file path")
@click.option("--model", "-m", default=None, help="LLM model override")
@click.option("--config", "-c", "config_path", default=None, help="Config file path")
@click.option("--max-retries", "-r", default=None, type=int, help="Max retry attempts")
@click.option("--mock", is_flag=True, help="Use mock compiler (for testing)")
def run(instruction, file, output, model, config_path, max_retries, mock):
    """Run a single GDL development task."""
    # Load config
    overrides = {}
    if model:
        overrides["llm.model"] = model
    if max_retries:
        overrides["agent.max_iterations"] = max_retries

    config = GDLAgentConfig.load(config_path, **overrides)

    # Resolve paths
    source = file or str(Path(config.src_dir) / "current.xml")
    out = output or str(Path(config.output_dir) / (Path(source).stem + ".gsm"))

    # Create components
    llm = _create_llm(config)
    compiler = _create_compiler(config, mock=mock)
    knowledge = _create_knowledge(config)

    # Run agent
    from openbrep.core import GDLAgent
    agent = GDLAgent(config, llm, compiler, knowledge, on_event=_cli_event_handler)
    result = agent.run(instruction, source, out)

    _print_result(result)
    sys.exit(0 if result.success else 1)


@cli.command()
@click.option("--model", "-m", default=None, help="LLM model override")
@click.option("--config", "-c", "config_path", default=None, help="Config file path")
@click.option("--mock", is_flag=True, help="Use mock compiler (for testing)")
def chat(model, config_path, mock):
    """Interactive chat mode for iterative GDL development."""
    overrides = {}
    if model:
        overrides["llm.model"] = model

    config = GDLAgentConfig.load(config_path, **overrides)
    llm = _create_llm(config)
    compiler = _create_compiler(config, mock=mock)
    knowledge = _create_knowledge(config)

    _print("\n[bold cyan]🤖 GDL Agent Interactive Mode[/]")
    _print("[dim]Type your instructions. Use 'quit' or Ctrl+C to exit.[/]\n")

    from openbrep.core import GDLAgent

    while True:
        try:
            instruction = click.prompt("openbrep", prompt_suffix=" > ")
        except (click.Abort, EOFError, KeyboardInterrupt):
            _print("\n[dim]Bye![/]")
            break

        if instruction.lower() in ("quit", "exit", "q"):
            break

        if not instruction.strip():
            continue

        # Determine source file (allow specifying with --file prefix)
        source = str(Path(config.src_dir) / "current.xml")
        out = str(Path(config.output_dir) / "current.gsm")

        agent = GDLAgent(config, llm, compiler, knowledge, on_event=_cli_event_handler)
        result = agent.run(instruction, source, out)
        _print_result(result)
        _print("")


@cli.command()
@click.option("--config", "-c", "config_path", default=None, help="Config file path")
def show_config(config_path):
    """Display current configuration."""
    config = GDLAgentConfig.load(config_path)
    _print(f"\n[bold]Configuration[/]\n")
    _print(config.to_toml_string())

    # Show compiler status
    from openbrep.config import _auto_detect_converter
    converter = config.compiler.path or _auto_detect_converter()
    if converter and Path(converter).is_file():
        _print(f"[green]✓ LP_XMLConverter:[/] {converter}")
    else:
        _print(f"[red]✗ LP_XMLConverter:[/] Not found")


# ── Factory helpers ───────────────────────────────────────────────────


def _create_llm(config: GDLAgentConfig):
    """Create LLM adapter from config."""
    from openbrep.llm import LLMAdapter
    return LLMAdapter(config.llm)


def _create_compiler(config: GDLAgentConfig, mock: bool = False):
    """Create compiler from config."""
    if mock:
        from openbrep.compiler import MockCompiler
        return MockCompiler()
    from openbrep.compiler import Compiler
    return Compiler(config.compiler)


def _create_knowledge(config: GDLAgentConfig) -> Optional:
    """Create knowledge base from config."""
    from openbrep.knowledge import KnowledgeBase
    kb = KnowledgeBase(config.knowledge_dir)
    kb.load()
    return kb


def _write_starter_knowledge(knowledge_dir: Path):
    """Write starter knowledge files if they don't exist."""
    files = {
        "GDL_Reference_Guide.md": _STARTER_GDL_REFERENCE,
        "XML_Template.md": _STARTER_XML_TEMPLATE,
        "Common_Errors.md": _STARTER_COMMON_ERRORS,
    }
    for name, content in files.items():
        path = knowledge_dir / name
        if not path.exists():
            path.write_text(content, encoding="utf-8")
            _print(f"  [green]✓[/] Created knowledge/{name}")


# ── Starter Knowledge Content ─────────────────────────────────────────

_STARTER_GDL_REFERENCE = """\
# GDL Quick Reference

## Common 3D Commands

| Command | Description | Syntax |
|---------|-------------|--------|
| `PRISM_` | Prism with n vertices | `PRISM_ n, h, x1,y1, ..., xn,yn` |
| `REVOLVE` | Revolution solid | `REVOLVE n, alpha, mask, x1,y1,s1, ...` |
| `EXTRUDE` | Extrusion along path | `EXTRUDE n, dx,dy,dz, mask, x1,y1,s1, ...` |
| `TUBE` | Tube along polyline | `TUBE n, m, mask, ...` |
| `SLAB_` | Horizontal slab | `SLAB_ n, h, x1,y1,z1, ..., xn,yn,zn` |
| `CONE` | Cone/cylinder | `CONE h, r_bottom, r_top, alpha1, alpha2` |
| `SPHERE` | Sphere | `SPHERE r` |

## Transformation Commands

| Command | Description |
|---------|-------------|
| `ADD x, y, z` | Translate coordinate system |
| `MUL x, y, z` | Scale coordinate system |
| `ROT x, y, z` | Rotate (angles in degrees) |
| `DEL n` | Remove last n transformations |
| `ADDX dx` / `ADDY dy` / `ADDZ dz` | Single-axis translate |

## Control Flow

```gdl
IF condition THEN
    ...
ENDIF

IF condition THEN
    ...
ELSE
    ...
ENDIF

FOR i = 1 TO n
    ...
NEXT i

WHILE condition
    ...
ENDWHILE
```

## Parameter Types

| Type | GDL Keyword | Prefix |
|------|------------|--------|
| Boolean | `Boolean` | `b` |
| Integer | `Integer` | `i` |
| Real Number | `RealNum` | `r` |
| Length | `Length` | `r` |
| Angle | `Angle` | `r` |
| String | `String` | `s` |
| Material | `Material` | `mat` |
| Fill Pattern | `FillPattern` | `fill` |
| Line Type | `LineType` | `lt` |
| Pen Color | `Pencolor` | `pen` |
"""

_STARTER_XML_TEMPLATE = """\
# GDL XML Structure Template

## Basic Object Structure

```xml
<?xml version="1.0" encoding="UTF-8"?>
<Symbol>
  <Parameters>
    <Parameter>
      <n>paramName</n>
      <Type>Length</Type>
      <Value>1.0</Value>
      <Description>Parameter description</Description>
    </Parameter>
  </Parameters>

  <Script_2D><![CDATA[
! 2D representation
RECT2 0, 0, A, B
  ]]></Script_2D>

  <Script_3D><![CDATA[
! 3D geometry
PRISM_ 4, ZZYZX,
  0, 0,
  A, 0,
  A, B,
  0, B
  ]]></Script_3D>

  <Script_UI><![CDATA[
! Parameter panel UI (optional)
  ]]></Script_UI>

  <Script_PR><![CDATA[
! Property script (optional)
  ]]></Script_PR>
</Symbol>
```

## Key Variables

| Variable | Meaning |
|----------|---------|
| `A` | Object width (X dimension) |
| `B` | Object depth (Y dimension) |
| `ZZYZX` | Object height (Z dimension) |
"""

_STARTER_COMMON_ERRORS = """\
# Common LP_XMLConverter Errors

## XML Structure Errors

| Error | Cause | Fix |
|-------|-------|-----|
| `XML Parse Error` | Malformed XML (unclosed tags, bad CDATA) | Validate with xmllint first |
| `Root element must be Symbol` | Wrong root tag | Ensure `<Symbol>` is root |
| `Missing Script_3D` | No 3D script section | Add `<Script_3D>` even if empty |

## GDL Script Errors

| Error | Cause | Fix |
|-------|-------|-----|
| `Mismatched IF/ENDIF` | Unclosed IF block | Count IF and ENDIF, ensure matching |
| `Mismatched FOR/NEXT` | Unclosed FOR loop | Count FOR and NEXT, ensure matching |
| `Unknown command` | Typo or invalid GDL command | Check spelling in GDL Reference |
| `Type mismatch` | Parameter used with wrong type | Check Parameter type definition |

## Tips

- Always wrap scripts in `<![CDATA[...]]>` to avoid XML escaping issues
- Use `!` for GDL comments inside scripts
- Parameter names are case-sensitive
- PRISM_ requires exactly 2*n coordinate values after the vertex count and height
"""


if __name__ == "__main__":
    cli()
