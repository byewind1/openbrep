# GitHub CI 失败分析与修复方案

> 生成时间：2026-08-04  
> 状态：部分已修复，剩余 1 项待处理

---

## 1. 当前 CI 状态

| Job | 状态 | 说明 |
|---|---|---|
| `pytest` | ✅ 已通过 | 1106 个测试全过 |
| `react-workbench` | ✅ 已通过 | 前端测试与类型检查通过 |
| `scorecard-mock` | ✅ 已通过 | 不受影响 |
| `benchmark-replay` | ❌ 仍失败 | create 黄金语料过期，C14/C17 退化 |

---

## 2. 已修复的问题

### 2.1 benchmark-replay 直接崩溃

**现象：**  
MODIFY/DEBUG/REPAIR 任务默认走 `agent_loop=True`，但 `ReplayLLM` 未实现 `generate_with_tools`，导致 benchmark-replay job 直接崩溃。

**修复：**  
`benchmark/runner.py` 中，当 `llm_source` 为 replay 模式时，显式设置 `request.agent_loop = False`，强制走旧路径。

**提交：** `b175c29 fix(benchmark): force agent_loop=False for replay in MODIFY tasks`

### 2.2 pytest 在 CI 上 15 分钟超时

**现象：**  
CI 上 pytest 运行到约 60% 时被 SIGTERM 终止，本地也能复现 hang。

**根因：**  
`tests/test_pipeline_semantic_repair.py` 和 `tests/test_vision.py` 中，MODIFY/DEBUG 任务默认 `agent_loop=True`，但测试只 mock 了 `generate()`，未 mock `generate_with_tools()`。`run_modify_agent_loop` 中的 `response.tool_calls` 是 `MagicMock`，迭代时产生无限序列，导致测试 hang。

**修复：**  
两个测试文件中显式关闭 `agent_loop`：

```python
def _execute_with_agent_loop_off(request):
    request.agent_loop = False
    return original_execute(request)
pipeline.execute = _execute_with_agent_loop_off
```

**提交：** `75bc570 fix(tests): close agent_loop in semantic repair and vision pipeline tests`

### 2.3 LLM 空响应无清晰报错

**现象：**  
部分 deepseek-v4-flash 端点在复杂请求下只输出 `reasoning_content`，`message.content` 为空，导致代码生成静默失败。

**修复：**  
`openbrep/llm.py` 中检测空 content + 非空 reasoning_content 的情况，抛出带 actionable hint 的 `RuntimeError`。

**提交：** `0cea973 fix(llm): detect reasoning-only empty responses and raise clear error`

---

## 3. 未修复的问题：benchmark-replay 语料过期

### 3.1 现象

`python -m benchmark.check_baseline` 输出：

```
benchmark 回放出现退化：
  ✗ create/C14: criteria_failures 8 → 9
  ✗ create/C17: criteria_failures 6 → 8
```

大量 CREATE 任务在 auto-repair / semantic-repair 阶段报：

```
replay 语料未命中（sha256:xxx…）：当前 prompt 流与录制不一致，请用 --llm-record 重新录制
```

### 3.2 根因

- **7月28日**：提交 `b0fa090` 首次录入 `benchmark/fixtures/llm_corpus/create.jsonl`，当时 prompt 流与语料匹配。
- **8月2日**：提交 `78cd89b`（previewer 支持 PUT/GET stack、GOSUB/RETURN、TUBE_），改变了 CREATE 任务的 prompt 构建与内部处理逻辑。
- **结果**：当前代码生成的 prompt 与旧语料中的 prompt 不一致，replay 时 repair 阶段语料未命中，C14/C17 的 repair 未执行，导致 criteria_failures 比基线差。

### 3.3 为什么必须重录

黄金语料的设计原则是：**LLM 响应是确定的，只有 prompt 变化才需要重录**。当前 prompt 流已变化，旧语料无法代表当前代码行为，必须重新录制。

### 3.4 重录阻塞

尝试本地重录时，当前配置的 provider 均不可用：

| Provider | 简单请求 | 复杂 GDL 生成 | 结论 |
|---|---|---|---|
| `deepseek-v4-flash` (opencode-go) | ✅ 正常 | ❌ 只输出 `reasoning_content`，`content` 为空 | 无法录到有效代码 |
| `deepseek-v4-pro` (opencode-go) | — | ❌ 多次超时 | 不可用 |
| `qwen3.8-max-preview` (token-plan) | ✅ 正常 | ❌ 多次超时 | 不可用 |
| `qwen3.8-max` (opencode-go) | — | ❌ 多次超时 | 不可用 |
| `kimi-k2.5` (opencode-go) | ✅ 正常 | ❌ 输出大量思考过程，不遵循 `[FILE:]` 格式 | 不可用 |
| Kimi 官方 API (moonshot-v1-8k) | — | ❌ 账户余额不足 | 需充值 |

**关键发现：** `deepseek-v4-flash` 在 opencode.ai 端点下的空 content 问题不是 openbrep 的 bug，而是该端点 harness/代理层的已知问题（参考 [OpenCode Go DeepSeek V4 Flash 工具调用报错](https://github.com/AAswordman/Operit/issues/621)）。官方 DeepSeek API 不存在此问题。

---

## 4. 修复方案

### 4.1 短期：重录 create 语料

需要一个能稳定完成复杂 GDL 生成的 LLM provider。推荐优先级：

1. **官方 DeepSeek API**（platform.deepseek.com）
   - model: `deepseek-chat` 或 `deepseek-reasoner`
   - 优点：与项目历史语料同源，行为可预期
2. **OpenAI API**
   - model: `gpt-4o` 或 `gpt-4o-mini`
   - 优点：稳定，代码生成能力强
3. **Claude API**
   - model: `claude-3-5-sonnet`
   - 优点：代码生成质量高
4. **Kimi 官方 API**（platform.moonshot.cn）
   - model: `moonshot-v1-8k` / `kimi-k2`
   - 缺点：当前账户余额不足，需充值

**重录命令：**

```bash
# 1. 在 config.toml 中配置可用 provider
# 2. 重录 create 语料
python -m benchmark.runner \
  --suite benchmark/tasks/create/ \
  --mode auto \
  --jobs 4 \
  --llm-record benchmark/fixtures/llm_corpus/create.jsonl

# 3. 本地验证
python -m benchmark.check_baseline

# 4. 若通过，更新基线
python -m benchmark.update_baseline --confirm

# 5. 提交并推送
git add benchmark/fixtures/llm_corpus/create.jsonl benchmark/baseline.json
git commit -m "chore(benchmark): re-record create golden corpus and update baseline"
git push
```

### 4.2 长期：防止语料过期漏更新

**问题：** 修改 prompt 相关代码时，开发者容易忘记同步重录语料。

**方案：**

1. **CI 新增 prompt-fingerprint job**  
   在 `tests.yml` 中增加一个轻量 job，计算当前代码的 prompt 指纹（如 knowledge 文件 hash + prompt 模板 hash），与语料文件头部记录的指纹对比。不一致时直接红灯，提醒需要重录。

2. **PR 模板增加检查项**  
   在 `.github/pull_request_template.md` 中增加：
   ```
   - [ ] 如果修改了 knowledge/、prompt 构建、object_planner、skills_loader，已同步重录黄金语料
   ```

3. **AGENTS.md 强化提醒**  
   在「benchmark 黄金语料规范」章节增加显眼提示：
   > ⚠️ 任何影响 prompt 的提交必须同步重录语料，否则 benchmark-replay 会红灯。

---

## 5. 需要用户确认的事项

1. **提供可用 LLM provider/key**  
   用于重录 create 语料。首选官方 DeepSeek 或 OpenAI。

2. **是否接受当前 CI 状态**  
   如果暂时无法提供 provider，CI 将保持 `benchmark-replay` 红、其余绿的状态。

3. **是否启用长期预防机制**  
   是否需要我实现 prompt-fingerprint CI job 和 PR 模板检查项。

---

## 6. 附录：已验证的 provider 行为

### 6.1 deepseek-v4-flash 空 content 复现

```python
import litellm

resp = litellm.completion(
    model="openai/deepseek-v4-flash",
    api_key="...",
    api_base="https://opencode.ai/zen/go/v1",
    messages=[{"role": "user", "content": "复杂 GDL 生成 prompt..."}],
    max_tokens=16384,
    temperature=0,
    stream=False,
)
# resp.choices[0].message.content == ""
# resp.choices[0].message.reasoning_content 非空
# resp.usage.completion_tokens == 16384 (全部用于 reasoning)
```

### 6.2 相关链接

- [OpenCode Go DeepSeek V4 Flash 工具调用报错](https://github.com/AAswordman/Operit/issues/621)
- [DeepSeek V4 Flash via OpenCode Zen provider fails](https://github.com/openclaw/openclaw/issues/87575)
- [reasoning_content is missing when using DeepSeek V4 Flash](https://github.com/anomalyco/opencode/issues/29618)
