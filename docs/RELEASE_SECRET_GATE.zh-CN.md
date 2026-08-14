# 发布秘密泄漏门禁（Release Secret Gate）

> D7（Codex BYOA 派单）交付物。目标：机器可执行地证明「开发机账号不会进入
> 安装包，subscription provider 不会从开发者环境变量或 home cache 隐式登录」。

## 背景与威胁模型

OpenBrep 打包产物（PyInstaller zip / Tauri bundle / dmg / msi）在任何情况下
都不得携带：

- 开发者本机 Codex 登录态：`~/.codex/auth.json`、`~/.openbrep/codex/auth.json`；
- 个人配置与密钥：`config.toml`、`.env*`、`id_rsa*`、`*.p12` / `*.pfx` / `*.key`；
- 内嵌秘密：Bearer token、JWT、真实 OpenAI API key（`sk-...`）、
  `CODEX_ACCESS_TOKEN` / `OPENAI_API_KEY` 的字面量赋值；
- 构建/测试注入的 canary 字符串（用于自证泄漏）。

D1 运行时侧已保证：Codex 登录只发生在独立用户级 `CODEX_HOME`
（默认 `~/.openbrep/codex`），绝不读取 `~/.codex`；`api_mode=codex_app_server`
的 provider 永不 fallback 到 API-key / 环境变量。本门禁是发布侧的独立防线：
即使未来构建脚本误复制了 home/config/cache，gate 会在发布前红灯。

## 扫描器：`scripts/secret_scan.py`

标准库实现（CI 无需额外安装），命令式可复用：

```bash
# 目录树（frontend/dist、Tauri bundle 目录、诊断包目录…）
python scripts/secret_scan.py --tree frontend/dist
python scripts/secret_scan.py --tree src-tauri/target/release/bundle

# zip 归档（PyInstaller zip / 诊断 zip）
python scripts/secret_scan.py --archive release/OpenBrep-free-macOS.zip

# git staged source（pre-commit 语义：文件名级 + 新增行内容级）
python scripts/secret_scan.py --staged

# 注入 canary：出现即失败（也可用环境变量 OPENBREP_RELEASE_CANARY）
python scripts/secret_scan.py --tree dist --canary obr-canary-abc123
```

退出码：`0` 通过；`1` 有发现；`2` 用法/目标错误（如路径不存在、无目标）。
`--report json` 输出机器可读结果。

### 检测规则

| 规则 | 类型 | 说明 |
|---|---|---|
| `auth.json` / `config.toml` | `auth_file` / `config_toml` | 按文件名，**不读内容** |
| `.env*`（`.env.example` 除外） | `env_file` | 按文件名，**不读内容** |
| `id_rsa*`、`*.p12/.pfx/.key` | `private_key` | 按文件名，**不读内容** |
| 路径含 `.codex` / `.openbrep` 段 | `codex_dir` / `openbrep_home` | 按路径，**不读内容** |
| `Bearer <token>` | `bearer` | 内容级，≥12 字符 |
| `eyJ…` JWT | `jwt` | 内容级 |
| `sk-…` / `sk-proj-…`（≥16 字符） | `openai_api_key` | 内容级，任意上下文 |
| `CODEX_ACCESS_TOKEN=` / `OPENAI_API_KEY=` 裸赋值 | `codex_access_token` / `openai_api_key_assignment` | 内容级，值须为字面量 |
| `--canary` / `OPENBREP_RELEASE_CANARY` | `canary` | 内容级，出现即失败 |

安全契约：

- **报告绝不回显秘密值**：只报 `类型 + 目标 + 文件（+ 行号）`；
  text/json 两种报告均不回显。
- **凭据文件只按名报**：`auth.json` / `.env` / 私钥 / `.codex` / `.openbrep`
  下的文件**内容永远不会被打开**——即使误把扫描目标指向开发者 HOME，
  也不会读取真实 auth 材料。
- 树扫描**不跟随符号链接**，避免链回 HOME；跳过 `.git`、`node_modules`、
  `target`、`__pycache__`、`.venv`、`.worktrees` 等目录。
- 二进制嗅探：内容级扫描跳过含 NUL 的二进制文件（凭据文件名级仍会报）。

### 已知边界（有意为之）

- `dmg` / `msi` / `pkg` 对标准库不可解析：扫描器对这类归档给出 `INFO`
  （不失败、不静默通过），正确用法是扫描它们**来源的 staging 目录**
  （macOS `.app` bundle、NSIS/MSI staging）再扫产物 zip。
- `"KEY": "value"` 引号键形式不按赋值报（避免源码字典 key 误报）；
  真实 OpenAI key 的 `sk-…` 形态无论在什么上下文都会被内容级规则捕获。
- 秘密脱敏模块自身（`openbrep/codex/redact.py`，其源码随 openbrep/ 镜像
  进 PyInstaller 包）不做内容级扫描——docstring 必然出现 Bearer 示例词；
  文件名级检查对其仍生效，其他文件的真实秘密不受影响。
- 对「讨论秘密形态的源码」（如脱敏模块的 docstring、测试里的 fake token）
  做整树扫描会得到误报——门禁面向**产物与 staged 变更**，不面向 dev checkout
  全树。

## 构建脚本接线

- `scripts/build_macos.sh`：PyInstaller 后、zip 前扫描 `dist` 树；zip 后扫描
  `release/OpenBrep-*-macOS.zip`。失败即退出（`set -euo pipefail`）。
- `scripts/build_windows.ps1`：`Compress-Archive` 后扫描 `dist/OpenBrep` 树 +
  zip，`$LASTEXITCODE != 0` 抛错。
- 构建脚本本身不递归复制用户 home/config/cache：`openbrep.spec` 只打包
  `ui` / `openbrep` / `skills` / `config.example.toml` / README / knowledge。

## CI 接线

- `.github/workflows/build-installers.yml`：macOS / Windows 产物 zip 各加
  `Secret gate` 步骤，在 smoke 之前红灯即停。
- `.github/workflows/release-tauri.yml`：Tauri 构建后扫描
  `frontend/dist` 与 `src-tauri/target/release/bundle`（dmg/msi 的 staging）。

## 干净 HOME + 清空 env 的 package smoke

`scripts/package_smoke.py` 默认（`--clean-env`）在全新临时 HOME 下启动打包
产物，并剥离 OpenAI / Codex / 本机配置变量：

```text
OPENAI_API_KEY OPENAI_ORG_ID OPENAI_API_BASE OPENAI_BASE_URL
OPENAI_API_KEY_PATH OPENAI_CHATGPT_AUTH OPENAI_SKIP_PROXY OPENAI_LOG_LEVEL
CODEX_ACCESS_TOKEN CODEX_HOME GDL_AGENT_CONFIG GDL_AGENT_API_KEY
GDL_AGENT_API_BASE OBR7_API_PORT OBR7_WEB_PORT OBR7_TAURI_MODE
OPENBREP_RELEASE_CANARY
```

`--no-clean-env` 可关闭（调试用）。构建安装器 workflow 的 smoke 沿用默认。

## 黑盒回归：初装 openai-codex 仍 signed out

`tests/test_release_secret_gate.py` 用**子进程 fake app-server**（模拟真实
codex CLI 的 auth.json 行为：登录态只存在于 `CODEX_HOME`）黑盒验证：

- 外部环境存在 `CODEX_ACCESS_TOKEN=sk-canary-…`、`OPENAI_API_KEY=sk-proj-…`
  **且**开发机 `~/.codex/auth.json` 已注入 canary 登录态时，初装
  （隔离 `CODEX_HOME`）`status()` 仍为 `signed_out`；开发机 auth 文件字节级
  未动。
- transport 子进程实际收到的 `CODEX_HOME` 是隔离目录（黑盒观察
  `initialize` 回显），绝不指向 `~/.codex`；canary env 确实存在于子进程
  环境（证明被忽略而非被抹除）。
- 两个隔离 `CODEX_HOME` 状态互不可见：H1 有登录态、H2 初装仍 signed_out；
  H1 登录产物不会复制进 H2，A 的 auth 文件保持原样。
- 全部离线：只与 fake app-server 通信，不依赖真实账号/网络/本机登录态。

## 验证

```bash
python -m pytest tests/test_package_smoke.py tests/test_obr7_launcher.py -q
python -m pytest tests/test_release_secret_gate.py -q
python -m pytest tests/ -q
cd frontend && npx vitest run
cd frontend && npx tsc --noEmit -p tsconfig.app.json
```

修改 workflow 后另做 YAML 语法验证：

```bash
ruby -e 'require "yaml"; YAML.load_file(".github/workflows/build-installers.yml"); YAML.load_file(".github/workflows/release-tauri.yml"); puts "yaml ok"'
```
