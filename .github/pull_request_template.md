## Checklist

- [ ] 本 PR 未改动 prompt 影响面（`knowledge/`、`user_knowledge/`、`skills/`、`openbrep/prompts/`、prompt 构建逻辑、LLM model/provider）。
      若命中以上路径，必须用真实 LLM 重录黄金语料，否则 CI 的 `benchmark-replay` 会变红；
      重录命令与判定规则见 AGENTS.md「benchmark 黄金语料规范」。
