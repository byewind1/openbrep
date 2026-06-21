#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{BufRead, BufReader};
use std::process::{Child, Command, Stdio};
use std::sync::{mpsc, Mutex};
use std::thread;
use std::time::Duration;
use tauri::{Manager, WindowEvent};

struct BackendState {
    child: Mutex<Option<Child>>,
    api_url: String,
}

/// Locate the Python interpreter: prefer the one running this process, then "python3".
fn python_exe() -> String {
    std::env::var("OBR7_PYTHON").unwrap_or_else(|_| {
        // When bundled via PyInstaller the sidecar binary is used instead.
        // In dev/source mode, use the ambient python3.
        "python3".to_string()
    })
}

/// Find obr7.py: try relative to this binary, then relative to CWD.
fn find_obr7(app: &tauri::App) -> std::path::PathBuf {
    // In a packaged Tauri app, resources are in the resource dir.
    if let Ok(res) = app.path().resource_dir() {
        let candidate = res.join("scripts").join("obr7.py");
        if candidate.exists() {
            return candidate;
        }
    }
    // Dev fallback: src-tauri/ is one level below project root.
    let dev_path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap_or(std::path::Path::new("."))
        .join("scripts")
        .join("obr7.py");
    dev_path
}

/// Spawn the Python backend in Tauri mode and return (Child, ready_url, api_url).
fn spawn_backend(script: &std::path::Path) -> Result<(Child, String, String), String> {
    let python = python_exe();

    let mut child = Command::new(&python)
        .arg(script)
        .arg("--tauri")
        .arg("--no-open")
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|e| format!("Failed to start OpenBrep backend ({python}): {e}"))?;

    let stdout = child.stdout.take().expect("stdout should be piped");

    // Channel receives OBR7_READY_URL and OBR7_API_URL from stdout.
    let (tx_ready, rx_ready) = mpsc::channel::<String>();
    let (tx_api, rx_api) = mpsc::channel::<String>();

    thread::spawn(move || {
        let reader = BufReader::new(stdout);
        for line in reader.lines().flatten() {
            eprintln!("[obr7-tauri] {line}");
            if let Some(url) = line.strip_prefix("OBR7_READY_URL=") {
                let _ = tx_ready.send(url.to_string());
            } else if let Some(url) = line.strip_prefix("OBR7_API_URL=") {
                let _ = tx_api.send(url.to_string());
            }
        }
    });

    let timeout = Duration::from_secs(60);
    let ready_url = rx_ready
        .recv_timeout(timeout)
        .unwrap_or_else(|_| "http://127.0.0.1:8765".to_string());
    // API URL is expected right after READY_URL; use a short timeout.
    let api_url = rx_api
        .recv_timeout(Duration::from_secs(2))
        .unwrap_or_else(|_| ready_url.clone());

    Ok((child, ready_url, api_url))
}

/// Send shutdown request to the Python backend then kill the process.
fn shutdown_backend(state: &BackendState) {
    // Best-effort HTTP shutdown call.
    let shutdown_url = format!("{}/api/shutdown", state.api_url);
    let _ = ureq::post(&shutdown_url).call();

    // Give the backend a moment to shut down cleanly.
    thread::sleep(Duration::from_millis(800));

    if let Ok(mut guard) = state.child.lock() {
        if let Some(child) = guard.as_mut() {
            if child.try_wait().ok().flatten().is_none() {
                let _ = child.kill();
            }
        }
    }
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            let script = find_obr7(app);
            let (child, ready_url, api_url) = spawn_backend(&script)?;

            // Show the main window pointed at the backend URL.
            let url: tauri::Url = ready_url
                .parse()
                .unwrap_or_else(|_| "http://127.0.0.1:8765".parse().unwrap());

            let win = tauri::WebviewWindowBuilder::new(app, "main", tauri::WebviewUrl::External(url))
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
        .on_window_event(|_window, event| {
            // Extra safety: if the last window closes, the app exits normally via Tauri,
            // and the on_window_event above handles cleanup.
            if matches!(event, WindowEvent::Destroyed) {
                // Already handled per-window above.
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
