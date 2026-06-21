#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{BufRead, BufReader};
use std::process::{Child, Command, Stdio};
use std::sync::{mpsc, Mutex};
use std::thread;
use std::time::{Duration, Instant};
use tauri::{Manager, WindowEvent};

struct BackendState {
    child: Mutex<Option<Child>>,
    api_url: String,
}

/// Locate the Python interpreter: OBR7_PYTHON env → "python3".
fn python_exe() -> String {
    std::env::var("OBR7_PYTHON").unwrap_or_else(|_| "python3".to_string())
}

/// Find obr7.py: packaged resource dir → dev fallback via CARGO_MANIFEST_DIR.
fn find_obr7(app: &tauri::App) -> std::path::PathBuf {
    if let Ok(res) = app.path().resource_dir() {
        let candidate = res.join("scripts").join("obr7.py");
        if candidate.exists() {
            return candidate;
        }
    }
    std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap_or(std::path::Path::new("."))
        .join("scripts")
        .join("obr7.py")
}

/// Spawn the Python backend and return (Child, ready_url, api_url).
///
/// stderr is captured and relayed to our stderr so crash traces are visible
/// in the terminal (dev) or macOS Console.app (bundled).
///
/// Returns Err if the process fails to start OR does not emit OBR7_READY_URL
/// within the timeout — so the caller can surface a visible error instead of
/// opening a dead window.
fn spawn_backend(script: &std::path::Path) -> Result<(Child, String, String), String> {
    let python = python_exe();

    let mut child = Command::new(&python)
        .arg(script)
        .arg("--tauri")
        .arg("--no-open")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped()) // piped so we can relay lines; inherit() drops them in bundles
        .spawn()
        .map_err(|e| format!("Failed to start OpenBrep backend ({python}): {e}"))?;

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
             Check that Python 3 is installed and that config.toml is valid.\n\
             Run `python3 scripts/obr7.py --tauri` in a terminal to see the full error."
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
        .setup(|app| {
            let script = find_obr7(app);

            let (child, ready_url, api_url) = spawn_backend(&script).map_err(|msg| {
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
