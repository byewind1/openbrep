from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path


DEFAULT_API_PORT = 8765
DEFAULT_WEB_PORT = 5174
FALLBACK_API_PORT = 19065
FALLBACK_WEB_PORT = 19074
HOST = "127.0.0.1"


def is_port_available(port: int, host: str = HOST) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def find_available_port(
    preferred: int,
    host: str = HOST,
    *,
    max_attempts: int = 50,
    fallback_start: int | None = None,
    fallback_attempts: int = 50,
) -> int:
    for port in range(preferred, preferred + max_attempts):
        if is_port_available(port, host):
            return port
    if fallback_start is not None:
        for port in range(fallback_start, fallback_start + fallback_attempts):
            if is_port_available(port, host):
                return port
        raise RuntimeError(
            "No available port found from "
            f"{preferred} to {preferred + max_attempts - 1}, "
            f"or from {fallback_start} to {fallback_start + fallback_attempts - 1}."
        )
    raise RuntimeError(f"No available port found from {preferred} to {preferred + max_attempts - 1}.")


def choose_port(
    *,
    explicit: int | None,
    env_name: str,
    default: int,
    fallback_start: int | None = None,
    host: str = HOST,
) -> tuple[int, bool]:
    raw_env = os.environ.get(env_name, "").strip()
    fixed = explicit is not None or bool(raw_env)
    port = explicit if explicit is not None else int(raw_env) if raw_env else default
    if is_port_available(port, host):
        return port, False
    if fixed:
        raise RuntimeError(f"{env_name or 'port'} {port} is already in use.")
    return find_available_port(port + 1, host, fallback_start=fallback_start), True


def wait_for_url(url: str, *, timeout: float = 12.0, waiting_msg: str | None = None) -> bool:
    """Poll url until it responds with a non-5xx status or timeout expires.

    Args:
        waiting_msg: If given, printed every ~2 s so the terminal doesn't look frozen.
    """
    deadline = time.time() + timeout
    _last_print = 0.0
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:
                if response.status < 500:
                    return True
        except OSError:
            pass
        now = time.time()
        if waiting_msg is not None and now - _last_print >= 2.0:
            print(f"[obr7] {waiting_msg}", flush=True)
            _last_print = now
        time.sleep(0.25)
    return False


def build_api_command(api_port: int, static_dir: str | None = None) -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        "openbrep.workbench_api",
        "--host",
        HOST,
        "--port",
        str(api_port),
    ]
    if static_dir:
        cmd += ["--static-dir", static_dir]
    return cmd


def build_web_command(web_port: int) -> list[str]:
    return ["npm", "run", "dev", "--", "--host", HOST, "--port", str(web_port), "--strictPort"]


def resolve_shared_config_path(root: Path) -> Path | None:
    env_path = os.environ.get("GDL_AGENT_CONFIG", "").strip()
    if env_path:
        return Path(env_path).expanduser()

    candidates: list[Path] = []
    try:
        common_dir = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "--git-common-dir"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        if common_dir:
            common_path = Path(common_dir)
            if not common_path.is_absolute():
                common_path = root / common_path
            if common_path.name == ".git":
                candidates.append(common_path.parent / "config.toml")
    except Exception:
        pass

    candidates.append(root / "config.toml")
    return next((path for path in candidates if path.exists()), None)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the OpenBrep React workbench.")
    parser.add_argument("--api-port", type=int, default=None, help="Workbench API port. Overrides OBR7_API_PORT.")
    parser.add_argument("--web-port", type=int, default=None, help="Workbench web port. Overrides OBR7_WEB_PORT.")
    parser.add_argument("--no-open", action="store_true", help="Do not open the browser automatically.")
    parser.add_argument("--tauri", action="store_true", help="Tauri desktop mode: single-port, serve built frontend, no browser launch.")
    parser.add_argument("--static-dir", default=None, help="Override frontend static files directory (Tauri mode).")
    parser.add_argument("--daemon", action="store_true", help="后台运行（脱离终端进程组），日志写 ~/.openbrep/logs/obr7.log")
    parser.add_argument("--daemon-child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--status", action="store_true", help="查看后台运行状态")
    parser.add_argument("--stop", action="store_true", help="停止后台运行")
    parser.add_argument("--restart", action="store_true", help="重启后台运行")
    return parser.parse_args(argv)


# ── Daemon 模式：脱离终端存活 ────────────────────────────────────────────────
# 根因：obr7 前台跑在终端进程组里，终端关闭时 SIGHUP 灭掉整个进程组
# （API + vite 同时死，前端只剩 "local API is not available"）。
# --daemon 用 start_new_session 让监督进程脱离终端，日志落盘，
# --status/--stop/--restart 通过 ~/.openbrep/run/obr7.json 状态文件管理。

DAEMON_STATE_PATH = Path.home() / ".openbrep" / "run" / "obr7.json"
DAEMON_LOG_PATH = Path.home() / ".openbrep" / "logs" / "obr7.log"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _tcp_open(port: int, host: str = HOST) -> bool:
    if not port:
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, int(port))) == 0


def _read_state() -> dict | None:
    try:
        return json.loads(DAEMON_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_state(**fields) -> None:
    DAEMON_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    DAEMON_STATE_PATH.write_text(
        json.dumps(fields, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _remove_state() -> None:
    try:
        DAEMON_STATE_PATH.unlink()
    except FileNotFoundError:
        pass


def _daemon_running(state: dict | None) -> bool:
    return bool(state) and _pid_alive(int(state.get("pid", -1)))


def daemon_status() -> int:
    state = _read_state()
    print("OpenBrep 状态")
    print("─" * 25)
    if not _daemon_running(state):
        print("  ❌ 未运行")
        print("     启动：obr7 --daemon")
        _remove_state()  # 清掉可能残留的过期状态文件
        return 1

    def line(ok: bool, name: str, pid, url: str) -> None:
        mark = "✅" if ok else "⚠️"
        text = "运行中" if ok else "进程存活但端口未响应"
        print(f"  {mark} {name:<8} {text}  PID {pid}  {url}")

    api_port = state.get("api_port")
    web_port = state.get("web_port")
    line(_tcp_open(api_port), "API", state.get("api_pid"), f"http://{HOST}:{api_port}")
    if web_port:
        line(_tcp_open(web_port), "Web", state.get("web_pid"), f"http://{HOST}:{web_port}")
    print(f"  📄 配置     {state.get('config') or '(默认)'}")
    print(f"  📋 日志     {DAEMON_LOG_PATH}")
    return 0


def daemon_stop() -> int:
    state = _read_state()
    if not _daemon_running(state):
        print("[obr7] 未在运行。")
        _remove_state()
        return 0
    pid = int(state["pid"])
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        pgid = pid
    print(f"[obr7] 正在停止 PID {pid}（进程组 {pgid}）…")
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.time() + 5
    while time.time() < deadline and _pid_alive(pid):
        time.sleep(0.2)
    if _pid_alive(pid):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    _remove_state()
    print("[obr7] 已停止。")
    return 0


def daemon_spawn(args: argparse.Namespace) -> int:
    existing = _read_state()
    if _daemon_running(existing):
        print(
            f"[obr7] 已在运行：PID {existing['pid']}  "
            f"API http://{HOST}:{existing.get('api_port')}  "
            f"Web http://{HOST}:{existing.get('web_port')}"
        )
        print("[obr7] 如需重启：obr7 --restart；查看状态：obr7 --status")
        return 0

    _remove_state()  # 清过期状态，让父进程只认本次启动写出的状态
    DAEMON_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log_fh = DAEMON_LOG_PATH.open("a", encoding="utf-8")
    root = Path(__file__).resolve().parents[1]

    child_argv = [sys.executable, str(root / "scripts" / "obr7.py"), "--daemon-child"]
    for flag, attr in (("--api-port", "api_port"), ("--web-port", "web_port")):
        value = getattr(args, attr, None)
        if value is not None:
            child_argv += [flag, str(value)]
    if args.no_open:
        child_argv.append("--no-open")
    if args.tauri:
        child_argv.append("--tauri")
    if args.static_dir:
        child_argv += ["--static-dir", args.static_dir]

    proc = subprocess.Popen(
        child_argv,
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        start_new_session=True,  # 关键：脱离终端进程组，终端关闭不影响
    )

    # 等子进程确认就绪（状态文件由子进程在端口就绪后写出）
    deadline = time.time() + 25
    state = None
    while time.time() < deadline:
        state = _read_state()
        if state and state.get("ready"):
            break
        if proc.poll() is not None:
            break
        time.sleep(0.3)

    if proc.poll() is not None or not (state and state.get("ready")):
        print(f"[obr7] 后台启动失败，日志见 {DAEMON_LOG_PATH}", file=sys.stderr)
        return 1

    print(f"[obr7] 后台运行中  PID {proc.pid}")
    print(f"  API   http://{HOST}:{state.get('api_port')}")
    if state.get("web_port"):
        print(f"  Web   http://{HOST}:{state.get('web_port')}")
    print(f"  日志  {DAEMON_LOG_PATH}")
    print("  状态  obr7 --status    停止  obr7 --stop")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.status:
        return daemon_status()
    if args.stop:
        return daemon_stop()
    if args.restart:
        daemon_stop()
        return daemon_spawn(args)
    if args.daemon:
        return daemon_spawn(args)

    root = Path(__file__).resolve().parents[1]
    frontend_dir = root / "frontend"
    if not frontend_dir.exists():
        print(f"[obr7] frontend directory not found: {frontend_dir}", file=sys.stderr)
        return 1

    try:
        api_port, api_shifted = choose_port(
            explicit=args.api_port,
            env_name="OBR7_API_PORT",
            default=DEFAULT_API_PORT,
            fallback_start=FALLBACK_API_PORT,
        )
        web_port, web_shifted = choose_port(
            explicit=args.web_port,
            env_name="OBR7_WEB_PORT",
            default=DEFAULT_WEB_PORT,
            fallback_start=FALLBACK_WEB_PORT,
        )
    except RuntimeError as exc:
        print(f"[obr7] {exc}", file=sys.stderr)
        return 1

    api_url = f"http://{HOST}:{api_port}"
    web_url = f"http://{HOST}:{web_port}"
    if api_shifted:
        print(f"[obr7] API port {DEFAULT_API_PORT} is in use; using {api_port}.")
    if web_shifted:
        print(f"[obr7] Web port {DEFAULT_WEB_PORT} is in use; using {web_port}.")

    env = os.environ.copy()
    env["VITE_OPENBREP_API"] = api_url
    config_path = resolve_shared_config_path(root)
    if config_path and not env.get("GDL_AGENT_CONFIG"):
        env["GDL_AGENT_CONFIG"] = str(config_path)
        print(f"[obr7] Config: {config_path}")

    processes: list[subprocess.Popen[bytes]] = []

    def stop_processes(*_: object) -> None:
        for proc in processes:
            if proc.poll() is None:
                proc.terminate()
        for proc in processes:
            try:
                proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                proc.kill()

    signal.signal(signal.SIGINT, stop_processes)
    signal.signal(signal.SIGTERM, stop_processes)

    tauri_mode = args.tauri or os.environ.get("OBR7_TAURI_MODE", "").strip() in {"1", "true", "yes"}

    try:
        if tauri_mode:
            # Tauri desktop mode: single port, serve built frontend, skip browser launch
            static_dir = args.static_dir or str(frontend_dir / "dist")
            print(f"[obr7] Starting API (Tauri mode): {api_url}")
            processes.append(subprocess.Popen(build_api_command(api_port, static_dir=static_dir), cwd=root, env=env))
            if not wait_for_url(
                f"{api_url}/api/snapshot",
                waiting_msg="正在等待后端 API 就绪…",
            ):
                print("[obr7] 后端启动超时，请检查环境配置。", file=sys.stderr)
                stop_processes()
                return 1
            print(f"[obr7] OpenBrep ready: {api_url}")
            print(f"OBR7_READY_URL={api_url}", flush=True)
            print(f"OBR7_API_URL={api_url}", flush=True)
            if args.daemon_child:
                _write_state(
                    pid=os.getpid(), ready=True,
                    api_port=api_port, web_port=None,
                    api_pid=processes[0].pid,
                    config=str(config_path or ""),
                )
        else:
            print(f"[obr7] Starting API: {api_url}")
            processes.append(subprocess.Popen(build_api_command(api_port), cwd=root, env=env))
            if not wait_for_url(
                f"{api_url}/api/snapshot",
                waiting_msg="正在等待后端 API 就绪…",
            ):
                print("[obr7] 后端启动超时，请检查环境配置。", file=sys.stderr)
                stop_processes()
                return 1

            print(f"[obr7] Starting React workbench: {web_url}")
            processes.append(subprocess.Popen(build_web_command(web_port), cwd=frontend_dir, env=env))
            # Wait for Vite dev server before opening the browser — eliminates the
            # "site can't be reached" flash caused by the browser racing the dev server.
            if not wait_for_url(
                web_url,
                timeout=30.0,
                waiting_msg="正在等待前端开发服务器就绪…",
            ):
                print("[obr7] 前端服务器启动超时，请检查 Node 环境。", file=sys.stderr)
                stop_processes()
                return 1
            print(f"[obr7] OpenBrep Workbench: {web_url}")
            print(f"[obr7] API: {api_url}")
            print("[obr7] Press Ctrl+C to stop.")
            # Machine-readable signal for Tauri desktop shell
            print(f"OBR7_READY_URL={web_url}", flush=True)
            print(f"OBR7_API_URL={api_url}", flush=True)
            if args.daemon_child:
                _write_state(
                    pid=os.getpid(), ready=True,
                    api_port=api_port, web_port=web_port,
                    api_pid=processes[0].pid, web_pid=processes[1].pid,
                    config=str(config_path or ""),
                )

        if not tauri_mode and not args.no_open and os.environ.get("OBR7_NO_OPEN", "").strip() not in {"1", "true", "yes"}:
            webbrowser.open(web_url)

        while all(proc.poll() is None for proc in processes):
            time.sleep(0.5)
        return next((proc.returncode or 1 for proc in processes if proc.poll() is not None), 0)
    finally:
        stop_processes()


if __name__ == "__main__":
    raise SystemExit(main())
