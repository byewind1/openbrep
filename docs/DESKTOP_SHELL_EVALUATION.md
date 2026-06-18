# 桌面壳评估：Tauri vs Electron（P4，只评估不实施）

> 日期：2026-06-11
> 前提：React Workbench Beta（P0–P3）已完成；当前启动方式是 `./obr7`（bash 起 Python API + Vite dev server）。
> 本文回答三个必答题并给出明确推荐。

## 现状盘点（影响选型的事实）

- 后端：`openbrep/workbench_api.py`，纯标准库 `ThreadingHTTPServer`，默认 `127.0.0.1:8765`，
  CLI 已支持 `--host/--port` 参数。
- **PyInstaller 打包基建已存在**（`openbrep.spec` + `packaging/openbrep_launcher.py`，分 free/pro edition）——
  后端可以打成单体可执行文件，这是"桌面壳 + Python sidecar"路线的最大底气。
- 前端：Vite + React + three.js（WebGL）+ Monaco；生产模式 `vite build` 出静态文件。
- 文件对话框：当前走 Python 的 `ui/local_file_dialog.py`（后端进程弹原生对话框）。

## 对比

| 维度 | Tauri 2.x | Electron |
|---|---|---|
| 安装包体积 | ~8–15 MB（系统 WebView） | ~90–120 MB（捆绑 Chromium） |
| 内存占用 | 低（共享系统 WebView） | 高（独立 Chromium 实例） |
| 渲染一致性 | macOS=WKWebView / Win=WebView2，**WebGL/Monaco 均可用**但版本随系统 | 捆绑 Chromium，三端像素级一致 |
| 外部进程管理 | **sidecar 一等公民**：声明式打包外部二进制、自动随 app 退出清理 | 需自己用 `child_process` 管理生命周期 |
| 原生文件对话框 | `@tauri-apps/plugin-dialog`，原生、异步 | `dialog.showOpenDialog`，原生 |
| 签名/公证 | macOS 公证 + Win 代码签名（两者成本相同，约 $99/年 Apple + 证书费） | 同左，但包大、公证上传慢 |
| 自动更新 | 内置 updater 插件 | electron-updater，生态更成熟 |
| 团队技术栈 | 配置为主，定制壳逻辑需 Rust | 全 JS，无新语言 |
| 主要风险 | 旧 Windows 需安装 WebView2 运行时（Win11 自带；安装器可捆绑） | 体积与内存被诟病；Chromium 安全更新跟进负担 |

## 三个必答题

### 1. 本地 Python API：启动 / 端口探测 / 进程关闭

两种壳方案相同，推荐流程：

1. **打包**：用现有 `openbrep.spec` 把 `workbench_api` 入口打成 sidecar 二进制
   （PyInstaller onedir 模式，避免 onefile 启动解压延迟）。
2. **启动 + 端口探测**：壳启动 sidecar 时传 `--port 0`（需给后端加约 5 行代码：
   bind 后把实际端口以 JSON 打到 stdout 首行）；壳读取 stdout 拿到端口，再轮询
   `/api/snapshot` 直到 200（超时 15s 报错引导用户）。固定端口 8765 作为 fallback。
3. **关闭**：Tauri sidecar 随 app 退出自动 kill；保险起见后端加 `POST /api/shutdown`
   （仅 127.0.0.1 接受），壳退出前先优雅调用再 kill。
4. **崩溃恢复**：壳监听 sidecar 退出事件 → 自动重启一次 + 提示。前端已有
   `session_id`/`restore_last_project()`，backend 重启后工作现场可恢复——Session v1
   就是为这一步铺的路。

### 2. 原生文件对话框迁移路径

- 终态：对话框由壳层提供（Tauri dialog 插件 / Electron dialog），前端拿到路径后
  调用现有的 `loadProjectPath(path)` / `importGdlFile(path)` 等 **已支持显式路径参数** 的 API——
  前端 store 接口不用改。
- 过渡：保留后端 `ui/local_file_dialog.py` chooser 路径作为 fallback（浏览器开发模式继续用），
  用一个运行时探测（`window.__TAURI__` 是否存在）决定走哪条。
- 工作量小的原因：当年把 chooser 做成"无路径时后端弹窗、有路径时直接用"的双模式，
  正好是为这次迁移留的缝。

### 3. 体积与签名成本

- Tauri：安装包 ~10 MB 壳 + sidecar（PyInstaller 后端约 80–150 MB，含 Python 运行时）≈ **100–160 MB**
- Electron：~110 MB 壳 + 同样的 sidecar ≈ **200–270 MB**
- 真正的体积大头是 Python sidecar，不是壳——所以壳的体积差异（10 vs 110 MB）是纯增量。
- 签名：两者都需要 Apple Developer（$99/年）+ macOS 公证；Windows 建议 Azure Trusted Signing
  （约 $10/月）或 EV 证书。成本与壳选型无关。

## 推荐：Tauri 2.x

理由按权重排序：

1. **sidecar 模型天然匹配**"壳 + Python 后端"架构，进程生命周期管理是声明式的，
   这正是 P4 最担心的复杂度所在；Electron 这块全要手写。
2. 壳本身体积/内存开销小一个数量级，叠加 Python sidecar 后总包仍比 Electron 小约 40%。
3. 文件对话框、updater、单实例锁都有官方插件，本项目壳层逻辑很薄，几乎不用写 Rust。
4. 渲染一致性风险可控：Monaco 和 three.js 在 WKWebView/WebView2 下均成熟；
   用户画像（Archicad 用户）的系统普遍较新。

**何时重新考虑 Electron**：如果实测发现 three.js 预览在 WebView2/WKWebView 有无法绕过的
渲染差异，或需要 Node 原生模块深度集成（目前没有）。

## 实施切口（未来动工时的第一步，本轮不做）

1. 后端加 `--port 0` 动态端口 + stdout 报告 + `/api/shutdown`（纯 Python，~30 行，可先行合入且不影响现状）
2. `npx create-tauri-app` 最小壳，指向 `vite build` 产物，sidecar 跑现有 PyInstaller 后端
3. 文件对话框探测切换
4. macOS 签名/公证流水线
