# OpenBrep 发布检查清单

日期：2026-04-27  
目标：发布前做最小但有效的质量确认。

## 自动检查

```bash
git status --short --branch
python -m pytest tests/ -q
```

## 文档与版本

- README 版本描述与 release note 一致。
- 重要架构变化已更新：
  - `docs/ARCHITECTURE.zh-CN.md`
  - `docs/AI_DEVELOPMENT_GUIDE.zh-CN.md`
  - `AGENTS.md`
- 新功能有最小使用说明或 release note。

## 功能 smoke

按 [MANUAL_TEST_CHECKLIST.zh-CN.md](MANUAL_TEST_CHECKLIST.zh-CN.md) 执行核心路径：

- 生成
- 修改
- 解释不改代码
- 导入 `.gdl`
- 导入 `.gsm`
- 加载 HSF
- preview
- 编译 GSM
- 可用时验证 Archicad/Tapir

## 发布前状态

```bash
git status --short --branch
git rev-parse main
git rev-parse origin/main
```

要求：

- 工作区干净。
- `main` 与 `origin/main` 指向同一提交。
- 若有发布 tag，tag 已 push。
