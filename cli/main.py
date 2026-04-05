"""
OpenBrep CLI — AI-driven GDL development command line tool.

Usage:
  python -m cli.main create "做一个宽600mm深400mm的书架"
  python -m cli.main --help
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

app = typer.Typer(
    name="openbrep",
    help="OpenBrep: AI-driven GDL development CLI",
    add_completion=False,
)
console = Console()
err_console = Console(stderr=True)

logging.basicConfig(level=logging.WARNING)


# ── Helpers ───────────────────────────────────────────────

def _load_pipeline(work_dir: str, trace_dir: str):
    """Load config and initialize pipeline, with friendly error on missing config."""
    from openbrep.runtime.pipeline import TaskPipeline
    from openbrep.config import GDLAgentConfig

    try:
        config = GDLAgentConfig.load()
    except Exception as exc:
        err_console.print(f"[red]❌ 配置加载失败：{exc}[/red]")
        raise typer.Exit(1)

    api_key = config.llm.resolve_api_key()
    if not api_key:
        err_console.print(
            "[yellow]⚠️  未找到 API Key。\n"
            "请编辑 config.toml，在 [llm.provider_keys] 或 [llm] 中填入有效的 API Key。[/yellow]"
        )
        raise typer.Exit(1)

    return TaskPipeline(config=config, trace_dir=trace_dir)


def _make_on_event(show_progress: bool):
    """Build a Rich-based on_event callback for GDLAgent."""
    if not show_progress:
        return None

    def on_event(event_type: str, data: dict):
        if event_type == "analyze":
            scripts = data.get("affected_scripts", [])
            console.print(f"  [dim]🔍 分析脚本: {', '.join(scripts)}[/dim]")
        elif event_type == "attempt":
            console.print(f"  [dim]🧠 调用 AI...[/dim]")
        elif event_type == "llm_response":
            console.print(f"  [dim]✏️  收到 {data.get('length', 0)} 字符[/dim]")
        elif event_type == "validate":
            errors = data.get("errors", [])
            warnings = data.get("warnings", [])
            if errors:
                console.print(f"  [yellow]⚠️  发现 {len(errors)} 个错误，AI 修复中...[/yellow]")
            elif warnings:
                console.print(f"  [dim]📋 {len(warnings)} 条建议[/dim]")
        elif event_type == "rewrite":
            round_num = data.get("round", 2)
            console.print(f"  [dim]🔄 第 {round_num} 轮修复中...[/dim]")

    return on_event


def _print_scripts(scripts: dict[str, str]) -> None:
    """Print generated scripts with syntax highlighting."""
    if not scripts:
        return
    for fpath, code in scripts.items():
        label = fpath.replace("scripts/", "").replace(".gdl", "").upper()
        if "paramlist" in fpath:
            label = "PARAMLIST"
        console.print(
            Panel(
                Syntax(code, "gdl", theme="monokai", word_wrap=True),
                title=f"[bold cyan]{label}[/bold cyan]",
                border_style="dim",
            )
        )


# ── Commands ──────────────────────────────────────────────

@app.command()
def create(
    prompt: str = typer.Argument(..., help="自然语言描述，例如：\"做一个宽600mm的书架\""),
    output: str = typer.Option("./workdir", "--output", "-o", help="工作目录"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="覆盖 config.toml 中的模型"),
    no_progress: bool = typer.Option(False, "--no-progress", help="不显示进度信息"),
    trace_dir: str = typer.Option("./traces", "--trace-dir", help="Trace 输出目录"),
):
    """从自然语言描述创建 GDL 对象"""
    from openbrep.runtime.pipeline import TaskRequest

    console.print(f"\n[bold]OpenBrep[/bold] — 创建 GDL 对象")
    console.print(f"指令: [cyan]{prompt}[/cyan]\n")

    pipeline = _load_pipeline(work_dir=output, trace_dir=trace_dir)

    # Optional model override
    if model:
        pipeline.config.llm.model = model

    request = TaskRequest(
        user_input=prompt,
        intent="CREATE",
        work_dir=output,
        gsm_name=None,   # pipeline will derive from prompt
        on_event=_make_on_event(not no_progress),
    )

    with console.status("[bold green]生成中...[/bold green]"):
        result = pipeline.execute(request)

    if not result.success:
        err_console.print(f"\n[red]❌ 生成失败：{result.error}[/red]")
        raise typer.Exit(1)

    console.print("\n[green]✅ 生成成功[/green]\n")

    if result.plain_text:
        console.print(Panel(result.plain_text, title="AI 说明", border_style="dim"))

    _print_scripts(result.scripts)

    if result.trace_path:
        console.print(f"\n[dim]Trace: {result.trace_path}[/dim]")


@app.command()
def modify(
    project_dir: str = typer.Argument(..., help="HSF 项目目录路径"),
    prompt: str = typer.Argument(..., help="修改指令"),
    no_progress: bool = typer.Option(False, "--no-progress"),
    trace_dir: str = typer.Option("./traces", "--trace-dir"),
):
    """修改现有 GDL 对象"""
    from openbrep.runtime.pipeline import TaskRequest
    from openbrep.hsf_project import HSFProject

    pipeline = _load_pipeline(work_dir=str(Path(project_dir).parent), trace_dir=trace_dir)

    try:
        project = HSFProject.load_from_disk(project_dir)
    except Exception as exc:
        err_console.print(f"[red]❌ 无法加载项目 '{project_dir}'：{exc}[/red]")
        raise typer.Exit(1)

    console.print(f"\n修改项目: [cyan]{project.name}[/cyan]")
    console.print(f"指令: [cyan]{prompt}[/cyan]\n")

    request = TaskRequest(
        user_input=prompt,
        intent="MODIFY",
        project=project,
        work_dir=str(Path(project_dir).parent),
        on_event=_make_on_event(not no_progress),
    )

    with console.status("[bold green]修改中...[/bold green]"):
        result = pipeline.execute(request)

    if not result.success:
        err_console.print(f"\n[red]❌ 修改失败：{result.error}[/red]")
        raise typer.Exit(1)

    console.print("\n[green]✅ 修改成功[/green]\n")

    if result.plain_text:
        console.print(Panel(result.plain_text, title="AI 说明", border_style="dim"))

    _print_scripts(result.scripts)


@app.command()
def compile(
    project_dir: str = typer.Argument(..., help="HSF 项目目录路径"),
    output: str = typer.Option("./output", "--output", "-o", help="输出目录"),
    mock: bool = typer.Option(False, "--mock", help="Mock 编译（无需 ArchiCAD）"),
):
    """编译 HSF → .gsm"""
    from openbrep.hsf_project import HSFProject
    from openbrep.compiler import HSFCompiler, MockHSFCompiler

    try:
        project = HSFProject.load_from_disk(project_dir)
    except Exception as exc:
        err_console.print(f"[red]❌ 无法加载项目：{exc}[/red]")
        raise typer.Exit(1)

    out_dir = Path(output)
    out_dir.mkdir(parents=True, exist_ok=True)
    gsm_path = str(out_dir / f"{project.name}.gsm")

    hsf_dir = project.save_to_disk()

    if mock:
        compiler = MockHSFCompiler()
    else:
        from openbrep.config import GDLAgentConfig
        config = GDLAgentConfig.load()
        if not config.compiler.path:
            err_console.print("[red]❌ 未找到 LP_XMLConverter。请在 config.toml 的 [compiler] path 中配置路径，或使用 --mock。[/red]")
            raise typer.Exit(1)
        compiler = HSFCompiler(converter_path=config.compiler.path)

    with console.status("编译中..."):
        result = compiler.hsf2libpart(str(hsf_dir), gsm_path)

    if result.success:
        console.print(f"[green]✅ 编译成功：{gsm_path}[/green]")
    else:
        err_console.print(f"[red]❌ 编译失败：\n{result.stderr}[/red]")
        raise typer.Exit(1)


@app.command()
def repair(
    project_dir: str = typer.Argument(..., help="HSF 项目目录路径"),
    error_log: Optional[str] = typer.Option(None, "--error-log", "-e", help="编译错误日志文本"),
    no_progress: bool = typer.Option(False, "--no-progress"),
    trace_dir: str = typer.Option("./traces", "--trace-dir"),
):
    """修复 GDL 脚本编译错误"""
    from openbrep.runtime.pipeline import TaskRequest
    from openbrep.hsf_project import HSFProject

    pipeline = _load_pipeline(work_dir=str(Path(project_dir).parent), trace_dir=trace_dir)

    try:
        project = HSFProject.load_from_disk(project_dir)
    except Exception as exc:
        err_console.print(f"[red]❌ 无法加载项目：{exc}[/red]")
        raise typer.Exit(1)

    instruction = "修复脚本中的编译错误"
    if error_log:
        instruction = f"[DEBUG:editor]{instruction}\n\n错误日志：\n{error_log}"

    console.print(f"\n修复项目: [cyan]{project.name}[/cyan]\n")

    request = TaskRequest(
        user_input=instruction,
        intent="DEBUG",
        project=project,
        work_dir=str(Path(project_dir).parent),
        on_event=_make_on_event(not no_progress),
    )

    with console.status("[bold green]分析并修复中...[/bold green]"):
        result = pipeline.execute(request)

    if not result.success:
        err_console.print(f"\n[red]❌ 修复失败：{result.error}[/red]")
        raise typer.Exit(1)

    console.print("\n[green]✅ 修复完成[/green]\n")

    if result.plain_text:
        console.print(Panel(result.plain_text, title="AI 分析", border_style="dim"))

    _print_scripts(result.scripts)


@app.command()
def chat(
    project_dir: Optional[str] = typer.Option(None, "--project", "-p", help="HSF 项目目录（可选）"),
):
    """交互式多轮对话模式"""
    from openbrep.runtime.pipeline import TaskRequest, TaskPipeline
    from openbrep.config import GDLAgentConfig

    try:
        config = GDLAgentConfig.load()
    except Exception as exc:
        err_console.print(f"[red]❌ 配置加载失败：{exc}[/red]")
        raise typer.Exit(1)

    pipeline = TaskPipeline(config=config)

    project = None
    if project_dir:
        from openbrep.hsf_project import HSFProject
        try:
            project = HSFProject.load_from_disk(project_dir)
            console.print(f"[dim]已加载项目: {project.name}[/dim]")
        except Exception as exc:
            err_console.print(f"[yellow]⚠️  无法加载项目，继续无项目模式：{exc}[/yellow]")

    console.print("\n[bold]OpenBrep Chat[/bold] — 输入 'exit' 退出\n")

    while True:
        try:
            user_input = typer.prompt("> ")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]再见[/dim]")
            break

        if user_input.strip().lower() in ("exit", "quit", "q"):
            console.print("[dim]再见[/dim]")
            break

        request = TaskRequest(
            user_input=user_input,
            project=project,
            work_dir="./workdir",
        )

        with console.status(""):
            result = pipeline.execute(request)

        if not result.success:
            console.print(f"[red]❌ {result.error}[/red]")
            continue

        if result.plain_text:
            console.print(Panel(result.plain_text, border_style="dim"))

        if result.scripts:
            _print_scripts(result.scripts)
            # Update project state for next turn
            if result.project:
                project = result.project


@app.command()
def benchmark(
    suite: str = typer.Option("create", "--suite", "-s", help="测试套件: create / modify / repair"),
    work_dir: str = typer.Option("./benchmark/workdir", "--work-dir"),
    trace_dir: str = typer.Option("./benchmark/traces", "--trace-dir"),
):
    """运行 benchmark 测试套件"""
    console.print(f"[yellow]benchmark 命令暂未实现（suite={suite}）[/yellow]")
    console.print("请直接运行 tests/ 目录下的测试文件。")


if __name__ == "__main__":
    app()
