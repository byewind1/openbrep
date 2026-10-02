# ADR 0005: benchmark 黄金语料密封化与"prompt 变化即重录"判据

日期：2026-07-28  
状态：Accepted

## 背景

benchmark 用真实 LLM 验证生成/修改效果，但 hosted LLM 即使 temperature=0 也非
确定——同一任务两次运行结果可以不同。这带来两个问题：

- **不可复现**：效果回归无法作为门禁，"这次变差是代码退化了还是模型抖动"
  无法回答。
- **环境泄漏**：runner 若从开发者本机 `config.toml` 读模型、凭据、Codex 登录
  态，本地与 CI 跑的根本不是同一实验，且凭据有泄漏面。

需要一个确定性机制：把真实 LLM 交互录制一次，之后离线逐字节重放。

## 决策

**黄金语料 record/replay**：

- `benchmark/llm_replay.py` 的 `ReplayLLM` 顶替真实 LLM；语料入库为
  `benchmark/fixtures/llm_corpus/{create,modify}.jsonl`（录制用
  `benchmark.runner --llm-record`）。
- **密封化**：`benchmark/check_baseline.py` 与 `benchmark/update_baseline.py`
  显式传入仓内 fixture 配置（`replay_config_create.toml` 锚定录制时的模型/
  端点结构、`replay_config_modify.toml` 锚定普通 chat-completions provider），
  绝不读取仓根 `config.toml`，配置不含凭据；回放不能触网。
- **编译器一致**：回放统一使用 mock 编译器，保证本地（装了 LP_XMLConverter）
  与 CI（没装）产出同一份基线语义；modify 语料自 S4 起在 agent loop 路径录制
  /回放，fixture 的项目记忆（如 Stair 的 `.openbrep/memory/`）属于录制环境的
  一部分。
- **基线只许变好**：`benchmark/baseline.json` 由 `update_baseline.py` 维护，
  任何退化方向（PASS→FAIL、criteria_failures 增加、pass 数下降）拒绝写入；
  写盘必须显式 `--confirm`。
- **判据成文**："改动会不会改变送给 LLM 的 prompt"是唯一重录判据，写入
  `AGENTS.md` benchmark 黄金语料规范：`knowledge/`、任务 description、prompt
  拼装逻辑、model/provider、学习记忆注入策略变更必须重录；生成后处理
  （linter、static checker、naming alignment、semantic verifier、修复接受
  判定、编译链路、断言逻辑）prompt 不变，直接回放验证。
- 回放未命中（prompt 流与语料不一致）报错并提示重录——这是特性：拦住
  "悄悄改变 prompt 却不重录"。

## 成功标准

- CI 的 `benchmark-replay` job 用入库语料离线回放 create + modify 套件，与
  `baseline.json` 比较，不触网、不依赖本机配置或登录态。
- 同一语料回放结果确定，本地与 CI 一致。
- prompt 无关的重构/修复随时可回放验证，零 token 成本。

## 后果

- 正面：效果验证确定化、CI 化；开发者模型/凭据与测试环境彻底隔离；回归
  归因从"猜"变成"回放对比"。
- 代价：prompt 侧改动成本变高——必须用真实 LLM 重录语料（消耗 token）；
  fixture 内的记忆与配置属于录制环境，改动它们等于改动 prompt。因此
  `benchmark/fixtures/`、`benchmark/baseline.json` 进入禁改清单（G2），
  语料重录需维护者决策。

## 对 AI 开发工具的要求

- 动手前先按 `AGENTS.md` "benchmark 黄金语料规范" 判断改动是否影响 prompt。
- 若发现自己正在做会改变 prompt 的改动（指令拼装、knowledge 选择、
  model/provider、`include_learned_skills` 等），立即停止并报告，不得自行
  重录语料。
- 相关代码：`benchmark/check_baseline.py`、`benchmark/update_baseline.py`、
  `benchmark/llm_replay.py`、`benchmark/fixtures/replay_config_*.toml`。
