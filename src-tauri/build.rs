// 窗口加载的是 sidecar 的 localhost 外部 URL（remote origin），Tauri v2 对
// 非本地来源的自定义命令默认拒绝（"not allowed by ACL"）。这里为 app 命令
// 自动生成 allow-<command> 权限，capabilities/default.json 再逐个点名放行。
fn main() {
    tauri_build::try_build(
        tauri_build::Attributes::new().app_manifest(
            tauri_build::AppManifest::new().commands(&[
                "app_version",
                "open_releases_page",
                "updater_check",
                "updater_download_and_install",
            ]),
        ),
    )
    .expect("failed to run tauri-build");
}
