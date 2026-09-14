#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{BufRead, BufReader};
use std::process::{Child, Command, Stdio};
use std::sync::{mpsc, Mutex};
use std::thread;
use std::time::{Duration, Instant};
use tauri::{Emitter, Manager, WindowEvent};
use tauri_plugin_updater::UpdaterExt;

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

/// Spawn the backend and return (Child, ready_url, api_url).
///
/// stderr is captured and relayed to our stderr so crash traces are visible
/// in the terminal (dev) or macOS Console.app (bundled).
///
/// Returns Err if the process fails to start OR does not emit OBR7_READY_URL
/// within the timeout — so the caller can surface a visible error instead of
/// opening a dead window.
fn spawn_backend(app: &tauri::App) -> Result<(Child, String, String), String> {
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

    // Relay Python stderr → our stderr (visible in terminal / Console.app).
    thread::spawn(move || {
        let reader = BufReader::new(stderr);
        for line in reader.lines().flatten() {
            eprintln!("[python] {line}");
        }
    });

    let (tx_ready, rx_ready) = mpsc::channel::<String>();
    let (tx_api, rx_api) = mpsc::channel::<String>();

    thread::spawn(move || {
        let reader = BufReader::new(stdout);
        for line in reader.lines().flatten() {
            eprintln!("[obr7] {line}");
            if let Some(url) = line.strip_prefix("OBR7_READY_URL=") {
                let _ = tx_ready.send(url.to_string());
            } else if let Some(url) = line.strip_prefix("OBR7_API_URL=") {
                let _ = tx_api.send(url.to_string());
            }
        }
    });

    // Hard fail on timeout: opening a window against a dead server is worse than
    // an explicit error message.
    let ready_url = rx_ready
        .recv_timeout(Duration::from_secs(60))
        .map_err(|_| {
            "OpenBrep backend did not start within 60 s.\n\
             Try running the bundled obr7-backend binary in a terminal to see the full error."
                .to_string()
        })?;

    // API URL follows immediately; allow a short extra window.
    let api_url = rx_api
        .recv_timeout(Duration::from_secs(5))
        .unwrap_or_else(|_| ready_url.clone());

    Ok((child, ready_url, api_url))
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

fn main() {
    tauri::Builder::default()
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
            let (child, ready_url, api_url) = spawn_backend(app).map_err(|msg| {
                // Log to stderr (terminal / Console.app) before the app exits.
                eprintln!("[openbrep] Fatal startup error: {msg}");
                // Propagate as a boxed error so Tauri exits cleanly.
                Box::<dyn std::error::Error>::from(msg)
            })?;

            let url: tauri::Url = ready_url
                .parse()
                .unwrap_or_else(|_| "http://127.0.0.1:8765".parse().unwrap());

            let win =
                tauri::WebviewWindowBuilder::new(app, "main", tauri::WebviewUrl::External(url))
                    .title("OpenBrep")
                    .inner_size(1400.0, 900.0)
                    .min_inner_size(900.0, 600.0)
                    .build()?;

            app.manage(BackendState {
                child: Mutex::new(Some(child)),
                api_url,
            });

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
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
