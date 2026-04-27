# ADR 0002: AI 生成写入由 generation service 边界承接

日期：2026-04-27  
状态：Accepted

## 背景

OpenBrep 的生成链路同时涉及 LLM、知识库、GDL 解析、pending diff、项目快照、编译验证和 Streamlit UI 状态。如果这些逻辑继续堆在 `ui/app.py` 或 view 模块里，后续 AI 开发工具很容易破坏写入顺序、取消状态或测试替身。

## 决策

AI 生成、修复、解释和结果应用的应用级流程归属 `ui/generation_service.py`，底层 pipeline 语义归属 `openbrep/runtime/pipeline.py`。`ui/app.py` 只保留薄 wrapper 和依赖装配，Streamlit view 只负责显示和收集输入。

## 成功标准

- View 模块不直接实例化 LLM、compiler 或 pipeline。
- `ui/app.py` 不新增实质生成逻辑，只保留兼容 wrapper 或依赖注入。
- 生成写入前捕获项目快照。
- AI 修改默认进入 pending diff 或明确的应用结果路径。
- 取消、过期 generation id、redo、bridge follow-up 等路径不互相覆盖。
- 相关行为有 controller/service/domain 测试保护。

## 后果

这个边界让生成链路可以独立测试，也让 AI 工具更容易判断代码应该放在哪里。代价是新增功能时需要先选择层级，不能把 UI 点击事件、业务规则和 pipeline 调用混写在一起。

## 对 AI 开发工具的要求

新增生成能力时按以下顺序判断归属：

```text
用户输入和按钮
  ui/views/*

聊天/生成编排
  ui/chat_controller.py
  ui/generation_service.py

核心 AI pipeline 语义
  openbrep/runtime/pipeline.py

GDL/HSF 规则
  openbrep/*
```

如果必须保留旧函数名，先在 `ui/app.py` 放薄 wrapper，再把真实逻辑放入 service 或 domain，并补测试。
