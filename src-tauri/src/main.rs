#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::collections::VecDeque;
use std::io::{BufRead, BufReader};
use std::process::{Child, Command, Stdio};
use std::sync::{mpsc, Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};
use tauri::{Emitter, Manager, WindowEvent};
use tauri_plugin_updater::UpdaterExt;

/// 首次启动（Gatekeeper 校验 + PyInstaller 解包 + 冷 Python import）在老
/// Intel 机器上可能远超 60 秒；超时只兜底"进程活着但挂住"的情况，子进程
/// 提前退出会被立即检出。
const BACKEND_READY_TIMEOUT: Duration = Duration::from_secs(120);
/// 启动失败时展示给用户的后端 stderr 尾部行数。
const STDERR_TAIL_LINES: usize = 40;

struct BackendState {
    child: Mutex<Option<Child>>,
    api_url: String,
}

/// Cached result of the last successful update check, so the download step
/// does not need a second network round-trip.
struct UpdaterState {
    update: Mutex<Option<tauri_plugin_updater::Update>>,
}

#[derive(serde::Serialize, Clone)]
struct UpdateInfo {
    version: String,
    current_version: String,
    notes: Option<String>,
}

#[derive(serde::Serialize, Clone)]
struct UpdateProgress {
    downloaded: u64,
    total: Option<u64>,
}

#[tauri::command]
fn app_version() -> String {
    env!("CARGO_PKG_VERSION").to_string()
}

/// Open the GitHub Releases page in the system browser (manual-download
/// fallback when in-place update is unavailable or fails).
#[tauri::command]
fn open_releases_page() -> Result<(), String> {
    tauri_plugin_opener::open_url(
        "https://github.com/byewind1/openbrep/releases/latest",
        None::<&str>,
    )
    .map_err(|e| e.to_string())
}

#[tauri::command]
async fn updater_check(app: tauri::AppHandle) -> Result<Option<UpdateInfo>, String> {
    let update_result = app.updater().map_err(|e| e.to_string())?.check().await;
    let update = match update_result {
        Ok(u) => u,
        Err(e) => {
            eprintln!("[updater] check failed: {e}");
            return Err(e.to_string());
        }
    };
    eprintln!("[updater] check ok: has_update={}", update.is_some());

    let info = update.as_ref().map(|u| UpdateInfo {
        version: u.version.to_string(),
        current_version: u.current_version.to_string(),
        notes: u.body.clone(),
    });

    let state = app.state::<UpdaterState>();
    *state.update.lock().unwrap() = update;

    Ok(info)
}

#[tauri::command]
async fn updater_download_and_install(app: tauri::AppHandle) -> Result<(), String> {
    // Prefer the cached Update from updater_check; re-check if the frontend
    // skipped the check step.
    let cached = {
        let state = app.state::<UpdaterState>();
        let taken = state.update.lock().unwrap().take();
        taken
    };
    let update = match cached {
        Some(u) => u,
        None => app
            .updater()
            .map_err(|e| e.to_string())?
            .check()
            .await
            .map_err(|e| e.to_string())?
            .ok_or_else(|| "no update available".to_string())?,
    };

    let mut downloaded: u64 = 0;
    let app_progress = app.clone();
    update
        .download_and_install(
            move |chunk_len, total| {
                downloaded += chunk_len as u64;
                let _ = app_progress.emit(
                    "updater-progress",
                    UpdateProgress { downloaded, total },
                );
            },
            || {},
        )
        .await
        .map_err(|e| e.to_string())?;

    // Required on macOS/Linux after install; on Windows the NSIS installer
    // has already taken over the process by this point.
    app.restart();
}

/// Locate the backend: bundled PyInstaller sidecar (Tauri externalBin
/// "binaries/obr7-backend") → dev fallback `python3 scripts/obr7.py`.
/// Returns a ready-to-spawn Command plus a human-readable description.
fn backend_command(app: &tauri::App) -> (Command, String) {
    let exe_name = if cfg!(windows) { "obr7-backend.exe" } else { "obr7-backend" };
    let mut candidates: Vec<std::path::PathBuf> = Vec::new();
    if let Ok(res) = app.path().resource_dir() {
        candidates.push(res.join(exe_name));
        candidates.push(res.join("binaries").join(exe_name));
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            candidates.push(dir.join(exe_name));
        }
    }
    for candidate in &candidates {
        if candidate.exists() {
            // mut 仅在 Windows 下用于 creation_flags；macOS/Linux 上允许 unused_mut
            #[allow(unused_mut)]
            let mut cmd = Command::new(candidate);
            // 避免 Windows 上 console 子系统 sidecar 弹出黑色控制台窗口
            #[cfg(windows)]
            {
                use std::os::windows::process::CommandExt;
                cmd.creation_flags(0x08000000); // CREATE_NO_WINDOW
            }
            return (cmd, candidate.display().to_string());
        }
    }

    // Dev fallback: repo checkout + system Python
    let script = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap_or(std::path::Path::new("."))
        .join("scripts")
        .join("obr7.py");
    let python = std::env::var("OBR7_PYTHON").unwrap_or_else(|_| "python3".to_string());
    let mut cmd = Command::new(&python);
    cmd.arg(&script);
    (cmd, format!("{python} {}", script.display()))
}

/// Result of a successful backend spawn: the running child plus the URLs it
/// announced on stdout.
struct BackendStartup {
    child: Child,
    ready_url: String,
    api_url: String,
}

fn take_stderr_tail(tail: &Arc<Mutex<VecDeque<String>>>) -> String {
    let lines: Vec<String> = tail.lock().unwrap().iter().cloned().collect();
    if lines.is_empty() {
        "(no backend output captured)".to_string()
    } else {
        lines.join("\n")
    }
}

/// Spawn the backend and return a ready-to-run BackendStartup.
///
/// stderr is captured and relayed to our stderr so crash traces are visible
/// in the terminal (dev) or macOS Console.app (bundled); the last few lines
/// are kept in a ring buffer so a startup failure can show them to the user.
///
/// Returns Err if the process fails to start, exits early, OR does not emit
/// OBR7_READY_URL within the timeout — so the caller can surface a visible
/// error instead of opening a dead window. The child is always reaped on
/// the error path.
fn spawn_backend(app: &tauri::App) -> Result<BackendStartup, String> {
    let (mut cmd, desc) = backend_command(app);

    let mut child = cmd
        .arg("--tauri")
        .arg("--no-open")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped()) // piped so we can relay lines; inherit() drops them in bundles
        .spawn()
        .map_err(|e| format!("Failed to start OpenBrep backend ({desc}): {e}"))?;

    let stdout = child.stdout.take().expect("stdout piped");
    let stderr = child.stderr.take().expect("stderr piped");

    // Relay Python stderr → our stderr (visible in terminal / Console.app),
    // and keep a tail buffer for the startup-error page.
    let stderr_tail = Arc::new(Mutex::new(VecDeque::<String>::new()));
    let tail_writer = Arc::clone(&stderr_tail);
    thread::spawn(move || {
        let reader = BufReader::new(stderr);
        for line in reader.lines().map_while(Result::ok) {
            eprintln!("[python] {line}");
            let mut tail = tail_writer.lock().unwrap();
            if tail.len() >= STDERR_TAIL_LINES {
                tail.pop_front();
            }
            tail.push_back(line);
        }
    });

    let (tx_ready, rx_ready) = mpsc::channel::<String>();
    let (tx_api, rx_api) = mpsc::channel::<String>();

    thread::spawn(move || {
        let reader = BufReader::new(stdout);
        for line in reader.lines().map_while(Result::ok) {
            eprintln!("[obr7] {line}");
            if let Some(url) = line.strip_prefix("OBR7_READY_URL=") {
                let _ = tx_ready.send(url.to_string());
            } else if let Some(url) = line.strip_prefix("OBR7_API_URL=") {
                let _ = tx_api.send(url.to_string());
            }
        }
    });

    // Hard fail on timeout: opening a window against a dead server is worse
    // than an explicit error message. A child that exits before READY is
    // detected immediately instead of waiting out the full timeout.
    let deadline = Instant::now() + BACKEND_READY_TIMEOUT;
    let ready_url = loop {
        match rx_ready.recv_timeout(Duration::from_millis(200)) {
            Ok(url) => break url,
            Err(mpsc::RecvTimeoutError::Timeout) => {
                if let Ok(Some(status)) = child.try_wait() {
                    let tail = take_stderr_tail(&stderr_tail);
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err(format!(
                        "OpenBrep backend exited before it was ready (status: {status}).\n\nBackend log tail:\n{tail}"
                    ));
                }
                if Instant::now() >= deadline {
                    let tail = take_stderr_tail(&stderr_tail);
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err(format!(
                        "OpenBrep backend did not start within {} s.\n\nBackend log tail:\n{tail}",
                        BACKEND_READY_TIMEOUT.as_secs()
                    ));
                }
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(
                    "OpenBrep backend output stream closed before startup completed.".to_string(),
                );
            }
        }
    };

    // API URL follows immediately; allow a short extra window.
    let api_url = rx_api
        .recv_timeout(Duration::from_secs(5))
        .unwrap_or_else(|_| ready_url.clone());

    Ok(BackendStartup {
        child,
        ready_url,
        api_url,
    })
}

/// Gracefully stop the Python backend then ensure the process is dead.
///
/// POST /api/shutdown → wait up to 2 s for clean exit → SIGKILL → wait up
/// to 3 s for the kernel to reclaim the PID.  This prevents orphan Python
/// processes after the Tauri window closes.
fn shutdown_backend(state: &BackendState) {
    // Best-effort HTTP shutdown so the server can flush state.
    let shutdown_url = format!("{}/api/shutdown", state.api_url);
    let _ = ureq::post(&shutdown_url).call();

    if let Ok(mut guard) = state.child.lock() {
        if let Some(child) = guard.as_mut() {
            // Wait up to 2 s for clean HTTP-triggered shutdown.
            let deadline = Instant::now() + Duration::from_millis(2000);
            while Instant::now() < deadline {
                if child.try_wait().ok().flatten().is_some() {
                    return; // exited cleanly
                }
                thread::sleep(Duration::from_millis(100));
            }

            // Force-kill if still alive.
            let _ = child.kill();

            // Wait up to 3 s for the OS to reap the process (prevents zombie/orphan).
            let deadline = Instant::now() + Duration::from_secs(3);
            while Instant::now() < deadline {
                if child.try_wait().ok().flatten().is_some() {
                    break;
                }
                thread::sleep(Duration::from_millis(100));
            }
        }
    }
}

/// Persist the startup failure detail where a non-technical user can find it
/// (the backend-error page references this file). Best-effort.
fn write_startup_error_log(app: &tauri::App, msg: &str) {
    if let Ok(dir) = app.path().app_log_dir() {
        if std::fs::create_dir_all(&dir).is_ok() {
            let _ = std::fs::write(
                dir.join("startup-error.log"),
                format!("OpenBrep 启动失败 / startup failure\n\n{msg}\n"),
            );
        }
    }
}

fn main() {
    let result = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_opener::init())
        .manage(UpdaterState {
            update: Mutex::new(None),
        })
        .invoke_handler(tauri::generate_handler![
            app_version,
            open_releases_page,
            updater_check,
            updater_download_and_install
        ])
        .setup(|app| {
            // 后端启动失败不能再向上传播 Err：run() 失败会触发 panic，
            // 用户看到的是一份"openbrep 意外退出"的崩溃报告，真实原因被掩盖
            // （v0.9.5 Intel 首启崩溃即此类）。改为打开一个静态错误页。
            let (win, backend) = match spawn_backend(app) {
                Ok(startup) => {
                    let url: tauri::Url = startup
                        .ready_url
                        .parse()
                        .unwrap_or_else(|_| "http://127.0.0.1:8765".parse().unwrap());
                    let win = tauri::WebviewWindowBuilder::new(
                        app,
                        "main",
                        tauri::WebviewUrl::External(url),
                    )
                    .title("OpenBrep")
                    .inner_size(1400.0, 900.0)
                    .min_inner_size(900.0, 600.0)
                    .build()?;
                    (win, Some(startup))
                }
                Err(msg) => {
                    eprintln!("[openbrep] Fatal startup error: {msg}");
                    write_startup_error_log(app, &msg);
                    let win = tauri::WebviewWindowBuilder::new(
                        app,
                        "main",
                        tauri::WebviewUrl::App("backend-error.html".into()),
                    )
                    .title("OpenBrep — 启动失败")
                    .inner_size(760.0, 560.0)
                    .build()?;
                    (win, None)
                }
            };

            if let Some(startup) = backend {
                app.manage(BackendState {
                    child: Mutex::new(Some(startup.child)),
                    api_url: startup.api_url,
                });
            }

            let handle = app.handle().clone();
            win.on_window_event(move |event| {
                if matches!(event, WindowEvent::Destroyed) {
                    if let Some(state) = handle.try_state::<BackendState>() {
                        shutdown_backend(&state);
                    }
                }
            });

            Ok(())
        })
        .on_window_event(|_window, _event| {})
        .run(tauri::generate_context!());

    if let Err(err) = result {
        // 不用 expect：panic 会生成系统崩溃报告；启动失败应干净退出并留下可读日志。
        eprintln!("[openbrep] error while running tauri application: {err}");
        std::process::exit(1);
    }
}
