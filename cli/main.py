"""
OpenBrep CLI — AI-driven GDL development command line tool.

Usage:
  python -m cli.main create "做一个宽600mm深400mm的书架"
  python -m cli.main --help
"""

from __future__ import annotations

import importlib.util
import logging
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime
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
        elif event_type == "status":
            message = data.get("message", "")
            if message:
                console.print(f"  [dim]{message}[/dim]")

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


def _persist_result_project(result_project, target_path: Path, project_name: Optional[str] = None) -> Path:
    """Re-root a result project to target_path and save it to disk."""
    project_name = project_name or target_path.name
    result_project.name = project_name
    result_project.work_dir = target_path.parent
    result_project.root = target_path
    return result_project.save_to_disk()


def _slugify_project_name(name: str) -> str:
    """Normalize a project/file stem into a filesystem-safe CLI name."""
    cleaned = re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]+", "-", (name or "").strip())
    cleaned = cleaned.strip("-_")
    return cleaned or "untitled"


def _extract_project_name_from_prompt(prompt: str) -> str:
    """Infer a stable project name from the user prompt without LLM calls."""
    prompt = (prompt or "").strip()
    english_patterns = [
        r"named?\s+([A-Za-z][A-Za-z0-9_-]{1,30})",
        r"called\s+([A-Za-z][A-Za-z0-9_-]{1,30})",
        r"名为\s*([A-Za-z][A-Za-z0-9_-]{1,30})",
        r"叫\s*([A-Za-z][A-Za-z0-9_-]{1,30})",
    ]
    for pattern in english_patterns:
        match = re.search(pattern, prompt, re.IGNORECASE)
        if match:
            return _slugify_project_name(match.group(1))

    cn_to_name = {
        "书架": "Bookshelf",
        "书柜": "Bookcase",
        "柜子": "Cabinet",
        "衣柜": "Wardrobe",
        "橱柜": "KitchenCabinet",
        "储物柜": "StorageUnit",
        "桌子": "Table",
        "桌": "Table",
        "书桌": "Desk",
        "餐桌": "DiningTable",
        "椅子": "Chair",
        "椅": "Chair",
        "沙发": "Sofa",
        "床": "Bed",
        "茶几": "CoffeeTable",
        "电视柜": "TVStand",
        "鞋柜": "ShoeRack",
        "窗框": "WindowFrame",
        "窗户": "Window",
        "百叶窗": "Louver",
        "窗": "Window",
        "门框": "DoorFrame",
        "推拉门": "SlidingDoor",
        "旋转门": "RevolvingDoor",
        "门": "Door",
        "墙板": "WallPanel",
        "隔墙": "Partition",
        "幕墙": "CurtainWall",
        "墙": "Wall",
        "楼梯": "Staircase",
        "台阶": "StairStep",
        "扶手": "Handrail",
        "栏杆": "Railing",
        "柱子": "Column",
        "柱": "Column",
        "梁": "Beam",
        "板": "Slab",
        "屋顶": "Roof",
        "天花": "Ceiling",
        "地板": "Floor",
        "灯具": "LightFixture",
        "管道": "Pipe",
        "风管": "Duct",
        "开关": "Switch",
        "插座": "Outlet",
        "空调": "AirConditioner",
        "花盆": "Planter",
        "树": "Tree",
        "围栏": "Fence",
        "长凳": "Bench",
    }
    for cn, en in sorted(cn_to_name.items(), key=lambda item: len(item[0]), reverse=True):
        if cn in prompt:
            return en

    candidate_patterns = [
        r'(?:生成|创建|制作|做一个|做个|建一个|建个)\s*(?:一个|个)?\s*([A-Za-z0-9_\-\u4e00-\u9fff]{1,40})',
        r'(?:生成|创建|制作)\s*([A-Za-z0-9_\-\u4e00-\u9fff]{1,40})',
    ]
    for pattern in candidate_patterns:
        match = re.search(pattern, prompt)
        if match:
            return _slugify_project_name(match.group(1))

    return "untitled"


def _next_available_path(target_path: Path) -> tuple[Path, bool]:
    """Return a non-overwriting path using -2/-3 suffixes when needed."""
    candidate = target_path.resolve()
    if not candidate.exists():
        return candidate, False

    parent = candidate.parent
    stem = candidate.stem if candidate.suffix else candidate.name
    suffix = candidate.suffix if candidate.suffix else ""
    index = 2
    while True:
        aliased = parent / f"{stem}-{index}{suffix}"
        if not aliased.exists():
            return aliased, True
        index += 1


def _resolve_create_target(output_root: str, prompt: str) -> tuple[Path, str, bool]:
    """Resolve final non-overwriting project directory under the output root."""
    root = Path(output_root).resolve()
    project_name = _extract_project_name_from_prompt(prompt)
    final_path, was_aliased = _next_available_path(root / project_name)
    return final_path, final_path.name, was_aliased


def _resolve_compile_target(output_root: str, project_name: str) -> tuple[Path, bool]:
    """Resolve final non-overwriting .gsm output path under the output root."""
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    final_path, was_aliased = _next_available_path(root / f"{_slugify_project_name(project_name)}.gsm")
    return final_path, was_aliased


def _is_supported_image_file(path: Path) -> bool:
    if not path.is_file():
        return False
    suffix = path.suffix.lower()
    return suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _guess_image_mime(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    if mime and mime.startswith("image/"):
        return mime
    suffix = path.suffix.lower()
    fallback = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }
    return fallback.get(suffix, "image/png")


def _extract_image_reference_from_text(text: str) -> Optional[str]:
    if not text:
        return None
    alias_match = re.search(r"\b(img\d+)\b", text, re.IGNORECASE)
    if alias_match:
        return alias_match.group(1).lower()

    quoted_matches = re.findall(r'"([^"\n]+)"|\'([^\'\n]+)\'', text)
    for double_quoted, single_quoted in quoted_matches:
        candidate = (double_quoted or single_quoted).strip()
        if candidate and ("/" in candidate or "\\" in candidate) and "*" not in candidate:
            return candidate

    path_match = re.search(r"((?:\.{1,2}[\\/]|~[\\/]|/[\w\-\u4e00-\u9fff./\\ ]+|[A-Za-z]:[\\/][^\s]+)[^\s]*)", text)
    if path_match:
        candidate = path_match.group(1).strip().strip('"\'')
        if candidate and "*" not in candidate:
            return candidate
    return None


def _resolve_chat_image_reference(
    text: str,
    registry_by_alias: dict[str, Path],
    next_alias_index: int,
) -> tuple[Optional[Path], Optional[str], int, Optional[str]]:
    """Resolve explicit image reference from chat text.

    Returns: (image_path, image_mime, next_alias_index, notice)
    """
    ref = _extract_image_reference_from_text(text)
    if not ref:
        return None, None, next_alias_index, None

    if re.fullmatch(r"img\d+", ref, re.IGNORECASE):
        alias = ref.lower()
        hit = registry_by_alias.get(alias)
        if hit is None:
            return None, None, next_alias_index, f"未找到别名 {alias}，请先粘贴图片文件路径。"
        return hit, _guess_image_mime(hit), next_alias_index, None

    if "*" in ref:
        return None, None, next_alias_index, "不支持通配符批量上传，请粘贴具体图片文件路径。"

    path = Path(ref).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.resolve()

    if path.is_dir():
        return None, None, next_alias_index, "检测到目录路径，请粘贴具体图片文件路径。"
    if not path.exists():
        return None, None, next_alias_index, f"图片不存在：{path}"
    if not _is_supported_image_file(path):
        return None, None, next_alias_index, "仅支持图片文件：png/jpg/jpeg/webp/gif"

    for alias, existing in registry_by_alias.items():
        if existing == path:
            return path, _guess_image_mime(path), next_alias_index, f"复用参考图 {alias}"

    alias = f"img{next_alias_index}"
    registry_by_alias[alias] = path
    return path, _guess_image_mime(path), next_alias_index + 1, f"已记录参考图 {alias} -> {path}"


def _provider_key_name(provider: str) -> Optional[str]:
    mapping = {
        "zhipu": "zhipu",
        "deepseek": "deepseek",
        "anthropic": "anthropic",
        "openai": "openai",
        "google": "google",
        "aliyun": "aliyun",
        "kimi": "kimi",
    }
    return mapping.get(provider)


def _backup_config_file(config_path: Path) -> Optional[Path]:
    if not config_path.exists():
        return None
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = config_path.with_name(f"{config_path.name}.bak.{timestamp}")
    shutil.copy2(config_path, backup_path)
    return backup_path


def _mask_secret(current: str) -> str:
    if not current:
        return ""
    return f"{current[:4]}...{current[-4:]}" if len(current) > 8 else "已配置"


def _prompt_secret(label: str, current: str = "") -> str:
    prompt = label
    if current:
        prompt = f"{label}（留空保留当前：{_mask_secret(current)}）"
    return typer.prompt(prompt, default=current, hide_input=True, show_default=False).strip()


def _prompt_model(current_model: str) -> str:
    from openbrep.config import ALL_MODELS

    choices = ", ".join(ALL_MODELS)
    return typer.prompt(
        f"模型（可选：{choices}）",
        default=current_model,
        show_default=True,
    ).strip()


def _configure_builtin_provider(config, provider: str) -> None:
    key_name = _provider_key_name(provider)
    if not key_name:
        raise typer.BadParameter(f"不支持的 provider: {provider}")
    current = config.llm.provider_keys.get(key_name, "")
    value = _prompt_secret(f"输入 {provider} API Key", current)
    if value:
        config.llm.provider_keys[key_name] = value


def _configure_custom_provider(config, model: str) -> None:
    matched = None
    for provider in config.llm.custom_providers:
        models = provider.get("models", []) or []
        if model in models:
            matched = provider
            break

    provider_name = typer.prompt(
        "自定义 provider 名称",
        default=(matched or {}).get("name", "custom-provider"),
        show_default=True,
    ).strip()
    base_url = typer.prompt(
        "base_url",
        default=(matched or {}).get("base_url", "https://your-proxy.com/v1"),
        show_default=True,
    ).strip()
    api_key = _prompt_secret("api_key", (matched or {}).get("api_key", ""))
    protocol_value = typer.prompt(
        "protocol",
        default=(matched or {}).get("protocol", "openai"),
        show_default=True,
    ).strip().lower()
    if protocol_value not in {"openai", "anthropic"}:
        raise typer.BadParameter("protocol 仅支持 openai 或 anthropic")

    new_provider = {
        "name": provider_name,
        "base_url": base_url,
        "api_key": api_key,
        "models": [model],
        "protocol": protocol_value,
    }

    remaining = [p for p in config.llm.custom_providers if model not in (p.get("models", []) or [])]
    if matched:
        remaining = [p for p in remaining if p.get("name") != matched.get("name")]
    remaining.append(new_provider)
    config.llm.custom_providers = remaining


def _maybe_configure_compiler(config) -> None:
    from openbrep.config import _auto_detect_converter

    detected = _auto_detect_converter()
    current = config.compiler.path or ""
    if typer.confirm("自动检测并写入 LP_XMLConverter 路径？", default=True):
        if detected:
            config.compiler.path = detected
            console.print(f"[green]✓ 已检测到编译器：[/green] {detected}")
        elif current:
            console.print(f"[yellow]⚠ 未自动检测到，保留当前配置：[/yellow] {current}")
        else:
            console.print("[yellow]⚠ 未自动检测到 LP_XMLConverter[/yellow]")


def _collect_config_issues(config) -> list[str]:
    from openbrep.config import model_to_provider

    issues: list[str] = []
    model = (config.llm.model or "").strip()
    if not model:
        issues.append("未配置 llm.model")
        return issues

    provider = model_to_provider(model)
    if provider == "custom":
        matched = None
        for custom in config.llm.custom_providers:
            if model in (custom.get("models", []) or []):
                matched = custom
                break
        if not matched:
            issues.append(f"当前模型 {model} 未匹配任何 custom_providers")
        else:
            if not matched.get("base_url"):
                issues.append(f"自定义 provider {matched.get('name', 'custom')} 缺少 base_url")
            if not matched.get("api_key"):
                issues.append(f"自定义 provider {matched.get('name', 'custom')} 缺少 api_key")
            if not matched.get("protocol"):
                issues.append(f"自定义 provider {matched.get('name', 'custom')} 缺少 protocol")
    else:
        key_name = _provider_key_name(provider)
        if key_name and not config.llm.provider_keys.get(key_name) and not config.llm.api_key:
            issues.append(f"当前模型 {model} 对应 provider key 未配置：{key_name}")

    compiler_path = config.compiler.path
    if compiler_path and not Path(compiler_path).is_file():
        issues.append(f"compiler.path 不存在：{compiler_path}")
    return issues


def _has_streamlit() -> bool:
    return importlib.util.find_spec("streamlit") is not None



def _resolve_ui_app_path() -> Path:
    return Path(__file__).resolve().parent.parent / "ui" / "app.py"



def _is_tcp_port_available(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return sock.connect_ex((host, port)) != 0


def _kill_process_on_port(port: int, host: str = "127.0.0.1") -> bool:
    """Kill any process listening on the given TCP port. Returns True if killed."""
    import signal
    try:
        result = subprocess.run(
            ["lsof", "-ti", f"{host}:{port}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return False
        pids = [int(pid) for pid in result.stdout.strip().splitlines()]
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                continue
        # Wait briefly for processes to exit
        time.sleep(0.5)
        return True
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False



def _launch_ui() -> int:
    if not _has_streamlit():
        err_console.print("[red]❌ 未安装 UI 依赖 streamlit。[/red]")
        err_console.print("请先安装： pip install openbrep[ui]", markup=False)
        return 1

    ui_app_path = _resolve_ui_app_path()
    if not ui_app_path.is_file():
        err_console.print(f"[red]❌ 未找到 UI 入口文件：{ui_app_path}[/red]")
        return 1

    port = 8501
    if not _is_tcp_port_available(port):
        console.print("[dim]旧进程占用端口 8501，正在清理...[/dim]")
        if _kill_process_on_port(port):
            time.sleep(0.5)
        if not _is_tcp_port_available(port):
            err_console.print(f"[red]❌ OpenBrep UI 默认端口 {port} 已被占用。[/red]")
            err_console.print("请先关闭旧的 Streamlit/obr 进程，再重新运行 obr。")
            return 1

    console.print(f"[dim]OpenBrep UI 已启动：http://localhost:{port}[/dim]")
    console.print("[dim]已关闭自动打开浏览器，请在常用浏览器中手动访问或使用已收藏地址。[/dim]")
    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(ui_app_path),
        "--server.headless",
        "true",
        "--server.address",
        "127.0.0.1",
        "--server.port",
        str(port),
    ]
    return subprocess.call(cmd)


def _run_chat_repl(project_dir: Optional[str] = None) -> None:
    from openbrep.runtime.pipeline import TaskRequest, TaskPipeline
    from openbrep.config import GDLAgentConfig

    try:
        config = GDLAgentConfig.load()
    except Exception as exc:
        err_console.print(f"[red]❌ 配置加载失败：{exc}[/red]")
        raise typer.Exit(1)

    pipeline = TaskPipeline(config=config)
    history: list[dict[str, str]] = []
    image_registry: dict[str, Path] = {}
    next_image_alias_index = 1
    interrupt_armed = False

    project = None
    if project_dir:
        from openbrep.hsf_project import HSFProject
        try:
            project = HSFProject.load_from_disk(project_dir)
            console.print(f"[dim]已加载项目: {project.name}[/dim]")
        except Exception as exc:
            err_console.print(f"[yellow]⚠️  无法加载项目，继续无项目模式：{exc}[/yellow]")

    console.print("\n[bold]OpenBrep Chat[/bold]")
    console.print("[dim]发送消息：回车  |  中断/取消：Ctrl+C  |  退出：连续两次 Ctrl+C 或输入 exit[/dim]\n")

    while True:
        try:
            user_input = typer.prompt("> ")
        except KeyboardInterrupt:
            if interrupt_armed:
                console.print("\n[dim]再见[/dim]")
                break
            interrupt_armed = True
            console.print("\n[dim]已取消本次输入（再次 Ctrl+C 退出）[/dim]")
            continue
        except EOFError:
            console.print("\n[dim]再见[/dim]")
            break

        interrupt_armed = False

        if user_input.strip().lower() in ("exit", "quit", "q"):
            console.print("[dim]再见[/dim]")
            break

        image_path, image_mime, next_image_alias_index, image_notice = _resolve_chat_image_reference(
            user_input,
            image_registry,
            next_image_alias_index,
        )
        if image_notice:
            console.print(f"[dim]{image_notice}[/dim]")

        history.append({"role": "user", "content": user_input})
        request = TaskRequest(
            user_input=user_input,
            project=project,
            work_dir="./workdir",
            history=history[-6:],
            assistant_settings=config.llm.assistant_settings,
            image_path=str(image_path) if image_path else None,
            image_mime=image_mime or "image/png",
        )

        try:
            with console.status(""):
                result = pipeline.execute(request)
        except KeyboardInterrupt:
            if interrupt_armed:
                console.print("\n[dim]再见[/dim]")
                break
            interrupt_armed = True
            console.print("\n[dim]已中断本轮生成（再次 Ctrl+C 退出）[/dim]")
            continue

        if not result.success:
            console.print(f"[red]❌ {result.error}[/red]")
            continue

        if result.plain_text:
            console.print(Panel(result.plain_text, border_style="dim"))
            history.append({"role": "assistant", "content": result.plain_text})

        if result.scripts:
            _print_scripts(result.scripts)
            if result.project:
                project = result.project


def obrcli_entry(argv: Optional[list[str]] = None) -> None:
    forwarded = list(argv or [])
    if not forwarded:
        _run_chat_repl(None)
        return
    app(prog_name="obrcli", args=["cli", *forwarded])


# ── Commands ──────────────────────────────────────────────

@app.callback(invoke_without_command=True)
def main(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        raise typer.Exit(_launch_ui())


@app.command()
def create(
    prompt: str = typer.Argument(..., help="自然语言描述，例如：\"做一个宽600mm的书架\""),
    output: str = typer.Option("./output", "--output", "-o", help="输出根目录，如 ./output"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="覆盖 config.toml 中的模型"),
    no_progress: bool = typer.Option(False, "--no-progress", help="不显示进度信息"),
    trace_dir: str = typer.Option("./traces", "--trace-dir", help="Trace 输出目录"),
):
    """从自然语言描述创建 GDL 对象，并将 HSF 项目写入磁盘"""
    from openbrep.runtime.pipeline import TaskRequest

    output_root = Path(output).resolve()
    target_path, project_name, was_aliased = _resolve_create_target(output, prompt)
    work_dir = str(output_root.parent)

    console.print(f"\n[bold]OpenBrep[/bold] — 创建 GDL 对象")
    console.print(f"指令: [cyan]{prompt}[/cyan]")
    console.print(f"输出根目录: [dim]{output_root}[/dim]")
    if was_aliased:
        console.print(f"[yellow]⚠️  检测到同名目录，已改名为 {project_name}[/yellow]")
    console.print(f"最终项目目录: [dim]{target_path}[/dim]\n")

    pipeline = _load_pipeline(work_dir=work_dir, trace_dir=trace_dir)

    if model:
        pipeline.config.llm.model = model

    request = TaskRequest(
        user_input=prompt,
        intent="CREATE",
        work_dir=work_dir,
        gsm_name=project_name,
        output_dir=str(output_root),
        on_event=_make_on_event(not no_progress),
    )

    with console.status("[bold green]生成中...[/bold green]"):
        result = pipeline.execute(request)

    if not result.success:
        err_console.print(f"\n[red]❌ 生成失败：{result.error}[/red]")
        raise typer.Exit(1)

    console.print("\n[green]✅ 生成成功[/green]\n")

    if result.project:
        saved_path = _persist_result_project(result.project, target_path, project_name)
        console.print(f"[green]📁 项目目录：{saved_path}[/green]")
        console.print(f"[green]📄 项目名：{project_name}[/green]")
        console.print(f"[dim]结构: {saved_path}/scripts/  +  paramlist.xml  +  libpartdata.xml[/dim]\n")
    else:
        err_console.print("[yellow]⚠️  无项目对象，跳过写入磁盘。[/yellow]")

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

    if result.project:
        saved_path = _persist_result_project(result.project, Path(project_dir).resolve(), project.name)
        console.print(f"[green]📁 已写回项目目录 {saved_path}[/green]\n")

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

    output_root = Path(output).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    gsm_path, was_aliased = _resolve_compile_target(output, project.name)

    console.print(f"\n编译项目: [cyan]{project.name}[/cyan]")
    console.print(f"输出根目录: [dim]{output_root}[/dim]")
    if was_aliased:
        console.print(f"[yellow]⚠️  检测到同名文件，已改名为 {gsm_path.name}[/yellow]")
    console.print(f"最终输出文件: [dim]{gsm_path}[/dim]\n")

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
        result = compiler.hsf2libpart(str(hsf_dir), str(gsm_path))

    if result.success:
        console.print(f"[green]✅ 编译成功[/green]")
        console.print(f"[green]📄 文件名：{gsm_path.name}[/green]")
        console.print(f"[green]📁 完整路径：{gsm_path}[/green]")
    else:
        err_console.print(f"[red]❌ 编译失败：\n{result.stderr}[/red]")
        raise typer.Exit(1)


@app.command()
def configure(
    config: Optional[str] = typer.Option(None, "--config", help="Config file path"),
):
    """交互式配置向导"""
    from openbrep.config import GDLAgentConfig, model_to_provider

    config_file = Path(config or "config.toml")
    loaded_config = GDLAgentConfig.load(config)

    console.print("\n[bold]OpenBrep 配置向导[/bold]\n")
    model = _prompt_model(loaded_config.llm.model)
    loaded_config.llm.model = model
    provider = model_to_provider(model)
    console.print(f"检测到 provider: [cyan]{provider}[/cyan]")

    if provider == "custom":
        _configure_custom_provider(loaded_config, model)
    else:
        _configure_builtin_provider(loaded_config, provider)

    _maybe_configure_compiler(loaded_config)

    console.print("\n[bold]配置预览[/bold]\n")
    console.print(loaded_config.to_toml_string())

    if not typer.confirm("确认写入配置？", default=True):
        console.print("[yellow]已取消，未写入任何配置。[/yellow]")
        return

    backup_path = _backup_config_file(config_file)
    loaded_config.save(str(config_file))
    reloaded = GDLAgentConfig.load(str(config_file))
    issues = _collect_config_issues(reloaded)

    console.print(f"[green]✓ 已写入配置：[/green] {config_file}")
    if backup_path:
        console.print(f"[green]✓ 已备份旧配置：[/green] {backup_path}")
    if issues:
        for issue in issues:
            console.print(f"[yellow]⚠ {issue}[/yellow]")
    else:
        console.print("[green]✓ 配置自检通过[/green]")


@app.command()
def doctor(
    config: Optional[str] = typer.Option(None, "--config", help="Config file path"),
):
    """诊断当前配置"""
    from openbrep.config import GDLAgentConfig, _auto_detect_converter, model_to_provider

    config_file = Path(config or "config.toml")
    console.print("\n[bold]OpenBrep 配置诊断[/bold]\n")

    if config_file.exists():
        console.print(f"[green]✓ 配置文件：[/green] {config_file}")
    else:
        console.print(f"[red]✗ 配置文件不存在：[/red] {config_file}")

    loaded_config = GDLAgentConfig.load(config)
    model = (loaded_config.llm.model or "").strip()
    provider = model_to_provider(model) if model else "unknown"

    console.print(f"当前模型: [cyan]{model or '(未配置)'}[/cyan]")
    console.print(f"推断 provider: [cyan]{provider}[/cyan]")

    api_key = loaded_config.llm.resolve_api_key()
    if api_key:
        console.print("[green]✓ 已解析到 API Key[/green]")
    else:
        console.print("[yellow]⚠ 未解析到 API Key[/yellow]")

    compiler_path = loaded_config.compiler.path
    if compiler_path:
        if Path(compiler_path).is_file():
            console.print(f"[green]✓ 编译器路径有效：[/green] {compiler_path}")
        else:
            console.print(f"[yellow]⚠ 编译器路径不存在：[/yellow] {compiler_path}")
    else:
        detected = _auto_detect_converter()
        if detected:
            console.print(f"[yellow]⚠ 未配置 compiler.path，可检测到：[/yellow] {detected}")
        else:
            console.print("[yellow]⚠ 未配置 compiler.path，且未检测到 LP_XMLConverter[/yellow]")

    issues = _collect_config_issues(loaded_config)
    if issues:
        console.print("\n[bold yellow]发现问题[/bold yellow]\n")
        for issue in issues:
            console.print(f"[yellow]⚠ {issue}[/yellow]")
        raise typer.Exit(1)

    console.print("\n[bold green]✓ 未发现配置问题[/bold green]\n")


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

    console.print(f"\n修复项目: [cyan]{project.name}[/cyan]\n")

    request = TaskRequest(
        user_input="修复脚本中的编译错误",
        intent="REPAIR",
        project=project,
        work_dir=str(Path(project_dir).parent),
        error_log=error_log or "",
        on_event=_make_on_event(not no_progress),
    )

    with console.status("[bold green]分析并修复中...[/bold green]"):
        result = pipeline.execute(request)

    if not result.success:
        err_console.print(f"\n[red]❌ 修复失败：{result.error}[/red]")
        raise typer.Exit(1)

    console.print("\n[green]✅ 修复完成[/green]\n")

    if result.project:
        saved_path = _persist_result_project(result.project, Path(project_dir).resolve(), project.name)
        console.print(f"[green]📁 已写回项目目录 {saved_path}[/green]\n")

    if result.plain_text:
        console.print(Panel(result.plain_text, title="AI 分析", border_style="dim"))

    _print_scripts(result.scripts)


@app.command("cli")
def cli_chat():
    """进入终端对话模式"""
    _run_chat_repl(None)


@app.command()
def help():
    """显示常用命令速查"""
    table = Table(title="OpenBrep 命令速查", show_header=True, header_style="bold cyan")
    table.add_column("命令", style="green")
    table.add_column("说明")
    table.add_row("obr", "启动 UI")
    table.add_row("obr cli", "进入终端对话模式")
    table.add_row("obrcli", "直接进入终端对话模式")
    table.add_row("obr configure", "交互式配置向导")
    table.add_row("obr doctor", "配置诊断")
    table.add_row("obr create <prompt>", "从描述生成对象项目")
    table.add_row("obr modify <project_dir> <prompt>", "修改现有项目")
    table.add_row("obr compile <project_dir>", "编译为 .gsm")
    table.add_row("obr repair <project_dir>", "按错误日志修复脚本")
    table.add_row("obr chat", "交互式聊天（可选带 --project）")
    table.add_row("obr --help", "查看完整参数帮助")
    console.print(table)


@app.command()
def chat(
    project_dir: Optional[str] = typer.Option(None, "--project", "-p", help="HSF 项目目录（可选）"),
):
    """交互式多轮对话模式"""
    _run_chat_repl(project_dir)


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
