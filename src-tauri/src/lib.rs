// Tauri mobile entry point (used by iOS/Android targets).
// Desktop functionality lives in main.rs.
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // Mobile build is not yet supported; this stub satisfies the build system.
    unimplemented!("mobile not supported yet")
}
