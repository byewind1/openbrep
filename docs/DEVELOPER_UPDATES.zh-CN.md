# OpenBrep 更新通道与开发者源码更新

OpenBrep 提供三层更新方式，适用对象不同：

1. **稳定版**：默认通道，只安装带 `v*` 标签并经过发布门禁的签名版本。
2. **开发版（nightly）**：桌面端在“设置 → 软件更新”中显式切换。每次 `main`
   更新由 CI 构建并签名，适合内部测试；可随时显式切回稳定版。
3. **本地源码 checkout**：仅面向开发者，不替换已安装的桌面 App。它会快进本地
   `main`、同步 Python/npm 依赖并重建 `frontend/dist`：

   ```bash
   obr source-update --repo /path/to/openbrep --dry-run
   obr source-update --repo /path/to/openbrep
   ```

   如果本机以 `obr serve` 运行 Archicad Copilot 后台，可显式要求构建后重启：

   ```bash
   obr source-update --repo /path/to/openbrep --restart-service
   ```

源码更新只允许干净的 `main` checkout，并使用 `git merge --ff-only`。存在已跟踪的
本地改动、当前不在 `main`、缺少 Git/npm 或目录不是 OpenBrep 仓库时会停止，不会
自动 stash、reset 或覆盖用户文件。

普通用户不需要安装 Git、Python、Node 或 Rust；稳定版与开发版桌面通道下载的都是
CI 预构建产物，并由 Tauri updater 验签后安装。
