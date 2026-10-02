# ADR 0006: Codex 双入口（local / managed）

日期：2026-09-17  
状态：Accepted

## 背景

OpenBrep 通过 Codex CLI 链路使用 ChatGPT 系模型。最初只有托管入口（managed）：
OpenBrep 自己的 `~/.openbrep/codex` 承载 ChatGPT 登录，连接向导、login/logout
都指向它。

D1（BYOA，bring your own account）落地过程中明确了两个约束：

- 已有 ChatGPT/Codex 订阅的用户希望直接复用自己机器上已配置的 Codex
  （`CODEX_HOME` 或 `~/.codex`），不想再登一次托管账号。
- Codex CLI 的 app-server 是互斥资源，两个 Codex home 不能同时各持一把锁；
  且用户的 Codex home 里有 auth 凭据，绝不能被应用读写或泄入 payload/日志。

## 决策

`openbrep/codex/entry.py` 承载双入口，二者长期共存：

- **local**：只读消费用户自己的 Codex 配置（`CODEX_HOME` 或 `~/.codex`），
  模型/目录解析在 `openbrep/codex/local_config.py`（`model_catalog_json` →
  配置 `model` → `models_cache.json`），**零写入**——绝不修改用户的 Codex
  home，哪怕一把空锁文件也不行。
- **managed**：OpenBrep 托管的 ChatGPT 登录（`~/.openbrep/codex`），保持
  默认（`DEFAULT_CODEX_ENTRY = "managed"`），旧配置与连接向导继续工作。

配套规则：

- app-server 互斥锁移出 Codex home，固定到
  `~/.openbrep/run/codex-app-server-<sha12>.lock`（环境变量
  `OPENBREP_CODEX_LOCK_DIR` 可覆盖）；local 入口以 `create_home=False` 启动。
- local 状态机为 `no_cli` / `unconfigured` / `signed_out` / `ready`，payload
  只携带符号化字段（`entry`、`codex_home_kind`、`auth_source`），**auth 路径
  不进任何 payload**（D1 红线，脱敏集中在 `openbrep/codex/redact.py`）。
- 登录类操作（login/logout/cancel/rate-limits）managed 独占
  （`codex_entry_managed_only`）；local 入口收到这类请求直接报错。
- 入口经 `llm.codex_entry` 配置选择（仅在非默认值时落盘）+
  `GET/POST /api/settings/llm/codex/entry`；所有调用方经
  `bind_codex_entry(provider, config)` 绑定共享 provider。
- `codex` 二进制解析在 PATH 之外回退到用户登录 shell（结果缓存），保证打包
  应用能找到只存在于用户 shell PATH 的 codex。

## 成功标准

- local 入口对用户 Codex home 零写入；卸载 OpenBrep 后用户 Codex 配置原样。
- 双入口共存，切换入口不改写旧配置；不配置 `llm.codex_entry` 时行为与历史
  版本完全一致（managed）。
- 任何 payload / 日志 / 测试断言中不出现 auth 路径或凭据。
- 锁文件集中在 `~/.openbrep/run/`，不再散落在 Codex home 内。

## 后果

- 正面：用户可自带账号（BYOA），托管登录继续可用；打包应用对用户 shell
  PATH 的依赖被显式化、可缓存；auth 处理面收敛、可审计。
- 代价：app-server 互斥仍然意味着同机同一时刻只能跑一个 Codex 链路会话
  （按 home 维度加锁）；双入口让"哪个 home 在服务"成为需要显式报告的状态
  字段，而不是隐含事实。

## 对 AI 开发工具的要求

- 相关代码：`openbrep/codex/entry.py`、`openbrep/codex/local_config.py`、
  `openbrep/codex/app_server.py`、`openbrep/codex/redact.py`。
- 不要在 payload、日志或测试断言中出现 auth 路径或凭据；新增输出字段前先过
  `redact.py` 的脱敏约定。
- 不要让 local 入口写用户 Codex home；新增登录类操作必须挂
  `codex_entry_managed_only` 检查。
- 新增 Codex 相关路由时保持 payload 只携带符号化状态（`entry` /
  `codex_home_kind` / `auth_source`）。
