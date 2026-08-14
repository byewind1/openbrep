# 发布秘密泄漏门禁（Release Secret Gate）

> D7（Codex BYOA 派单）交付物。目标：机器可执行地证明「开发机账号不会进入
> 安装包，subscription provider 不会从开发者环境变量或 home cache 隐式登录」。
> 门禁全部 fail-closed：任何形态的秘密/注入 canary 出现在产物中都必须红灯。

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
# 目录树（frontend/dist、Tauri bundle staging、诊断包目录…）
python scripts/secret_scan.py --tree frontend/dist
python scripts/secret_scan.py --tree src-tauri/target/release/bundle

# zip 归档（PyInstaller zip / 诊断 zip；dmg/msi/exe 传 --archive 会失败）
python scripts/secret_scan.py --archive release/OpenBrep-free-macOS.zip

# git staged source（pre-commit 语义：文件名级 + 新增行内容级）
python scripts/secret_scan.py --staged

# 注入 canary：出现即失败（也可用环境变量 OPENBREP_RELEASE_CANARY）
python scripts/secret_scan.py --tree dist --canary obr-canary-abc123
```

退出码：`0` 通过；`1` 有发现；`2` 用法/目标错误（如路径不存在、无目标）。
`--report json` 输出机器可读结果。

### 检测规则（全部 fail-closed）

| 规则 | 类型 | 说明 |
|---|---|---|
| `auth.json` / `config.toml` | `auth_file` / `config_toml` | 按文件名，**绝不 open** |
| `.env*`（`.env.example` 除外） | `env_file` | 按文件名，**绝不 open** |
| `id_rsa*`、`*.p12/.pfx/.key` | `private_key` | 按文件名，**绝不 open** |
| 路径含 `.codex` / `.openbrep` 段（含空目录） | `codex_dir` / `openbrep_home` | 按路径，**绝不 open** |
| `Bearer <token>`（≥12 字符，尾随词边界） | `bearer` | 原始字节扫描 |
| `eyJ…` JWT | `jwt` | 原始字节扫描 |
| `sk-…` / `sk-proj-…`（≥16 字符，词边界） | `openai_api_key` | 原始字节扫描 |
| `CODEX_ACCESS_TOKEN=` / `OPENAI_API_KEY=` 裸赋值 | `codex_access_token` / `openai_api_key_assignment` | 值须为字面量 |
| `--canary` / `OPENBREP_RELEASE_CANARY` | `canary` | **原始字节、最高优先级，永不豁免** |
| 不可读文件 | `unreadable` | 无法读取的普通文件 → gate 失败 |
| symlink（可疑：凭据名/目标含秘密段/绝对逃逸） | `symlink*` | 绝不跟随；可疑者失败，良性 in-tree 相对链接 INFO |
| dmg/msi/exe/pkg 传 `--archive` | `opaque_archive` | 不可解析 → **失败**（须配 staging 树扫描） |

安全契约：

- **凭据文件先分类后打开**：`auth.json` / `.env` / 私钥 / `.codex` / `.openbrep`
  下的文件在任何 open/read 之前按路径分类并报告，内容**永远不会被打开**——
  即使误把扫描目标指向开发者 HOME，也读不到真实 auth 材料（有
  instrumented-open 回归测试钉死）。
- **canary 最高优先级**：按原始字节搜索，不被源码豁免、NUL、文件类型、
  chunk 边界或目录忽略绕过。
- **全量流式扫描**：完整文件与完整 zip entry 分 chunk 扫描（chunk 间保留
  重叠，跨边界匹配不丢），2 MiB 之后不藏秘密。
- **二进制一视同仁**：含 NUL 的二进制同样对 canary / JWT / Bearer / `sk-*` /
  环境 token 做原始字节扫描，不整体跳过。
- **release 树不忽略任何目录**：`.git` / `node_modules` / `.venv` / `target` /
  `.worktrees` 若意外进入产物，正应被扫描并红灯；开发 checkout 的 staged
  模式只盯 git 暂存变更，二者策略分离。
- **树内嵌套 zip 也扫描**（如 PyInstaller 的 `base_library.zip`）。
- **symlink 绝不跟随**：按 link 名与 link target 分类；凭据名/目标含秘密段/
  绝对目标逃逸 → 失败；相对 in-tree 的良性链接（如 dylib 版本链接，真实
  macOS dist 有 128 个）→ INFO 可见不失败。
- **报告零秘密回显**：正文、文件名、目录名、target、archive entry 名中的
  秘密形态与 canary 一律脱敏为 `<redacted>`，只保留类型与（脱敏后的）位置。

### 精确误报过滤（匹配级，绝不整文件豁免）

- `Bearer plain-secret-value`：脱敏模块 docstring/注释的已知安全样例
  （匹配级过滤，容忍二进制 marshal 尾部 1-2 字节）。
- SSH 算法名：`sk-ecdsa-*` / `sk-ssh-*`（libssh2 等内嵌）按已知算法标识排除；
  同文件内的 canary / 真实 `sk-` 值仍然命中。
- 散文：bearer 后的全小写单词（"bearer authorization" 等 HuggingFace
  transformers docstring）不是 token。
- 引号键 `"KEY": "value"` 不按赋值报（避免源码字典 key 误报）；
  真实 OpenAI key 的 `sk-…` 形态无论在什么上下文都会被内容级规则捕获。

## 构建脚本与 CI 接线（gate 必须先于一切上传）

- `scripts/build_macos.sh`：PyInstaller 后、zip 前扫描 `dist` 树；zip 后扫描
  `release/OpenBrep-*-macOS.zip`。失败即退出（`set -euo pipefail`）。
- `scripts/build_windows.ps1`：`Compress-Archive` 后扫描 `dist/OpenBrep` 树 +
  zip，`$LASTEXITCODE != 0` 抛错。
- `.github/workflows/build-installers.yml`：macOS / Windows 产物 zip 各加
  `Secret gate` 步骤，在 smoke 与 upload 之前红灯即停。
- `.github/workflows/release-tauri.yml`：
  - `tauri-action` **纯构建**（不传 `tagName`/`releaseName`/`GITHUB_TOKEN`，
    不在 gate 前创建或上传任何 Release）；
  - gate 步骤扫描 `frontend/dist` 与 `src-tauri/target/release/bundle`
    （macOS `.app` staging + dmg 原始字节）；
  - Windows 额外用 7-Zip 展开 `.msi` / NSIS `.exe` 后做内容级门禁
    （压缩安装器内容必须展开才可扫描）；
  - `publish-release` 独立 job，`needs: build-tauri`，gate 通过后才创建
    GitHub Release。
- 有 workflow 静态契约测试（`tests/test_release_secret_gate.py`）证明：
  gate 步骤位于所有 `upload-artifact` 与 release 操作之前。
- 构建脚本本身不递归复制用户 home/config/cache：`openbrep.spec` 只打包
  `ui` / `openbrep` / `skills` / `config.example.toml` / README / knowledge
  （有静态守卫测试）。

## 干净 HOME + 清空 env 的 package smoke

`scripts/package_smoke.py` 默认（`--clean-env`）在全新临时 HOME 下启动打包
产物，并剥离 OpenAI / Codex / 本机配置变量：

```text
显式名单：OPENAI_API_KEY OPENAI_ORG_ID OPENAI_API_BASE OPENAI_BASE_URL
OPENAI_API_KEY_PATH OPENAI_CHATGPT_AUTH OPENAI_SKIP_PROXY OPENAI_LOG_LEVEL
CODEX_ACCESS_TOKEN CODEX_HOME GDL_AGENT_CONFIG GDL_AGENT_API_KEY
GDL_AGENT_API_BASE OBR7_API_PORT OBR7_WEB_PORT OBR7_TAURI_MODE
OPENBREP_RELEASE_CANARY
前缀剥离：OPENAI_* / CODEX_* / GDL_AGENT_*（覆盖未来新增变量）
```

同时把 `XDG_CONFIG/DATA/CACHE_HOME` 重定向到临时 HOME 下；Windows 的
`APPDATA` / `LOCALAPPDATA` 重定向（非 Windows 剥离），避免干净 HOME 下仍
指向开发机 cache。`--no-clean-env` 可关闭（调试用）。

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
