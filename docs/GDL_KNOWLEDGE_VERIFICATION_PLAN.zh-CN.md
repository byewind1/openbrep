# GDL 知识库分批校对计划

日期：2026-05-06

## 目标

用最小批次完成高价值校对：先修会直接导致生成错误的 GDL 命令语法，再校验参数、2D、项目上下文，最后校对构件 archetype。

权威顺序：

```text
Graphisoft GDL Reference Guide
  > Graphisoft GDL Center 官方文档
  > Graphisoft Community GDL 讨论
  > OpenBrep 本地知识库
  > LLM 推断
```

社区只用于发现实践案例和常见坑，不用于覆盖官方语法。

## 每批固定动作

1. 用官方 GDL Reference Guide 校对本地 wiki 语法。
2. 修正文档中的伪语法、错误默认值、危险简写。
3. 给关键错误加回归测试。
4. 跑知识检查和全量测试。
5. 提交并推送。

固定命令：

```bash
python knowledge/scripts/lint-knowledge.py knowledge
python scripts/knowledge_context_smoke.py --json
python -m pytest tests/test_knowledge_lint.py tests/test_verify_gdl_knowledge_sources.py tests/test_knowledge_context_smoke.py -q
python -m pytest tests/ -q
```

官方 index 可联网时：

```bash
python scripts/verify_gdl_knowledge_sources.py \
  --commands BLOCK PRISM_ REVOLVE PROJECT2 HOTSPOT2 MATERIAL \
  --format markdown \
  --output /tmp/openbrep-gdl-knowledge-verify.md
```

官方 index 需要离线时：

```bash
python scripts/verify_gdl_knowledge_sources.py \
  --official-index-file /tmp/openbrep-gdl-verify/graphisoft-gdl-index.html \
  --commands BLOCK PRISM_ REVOLVE PROJECT2 HOTSPOT2 MATERIAL \
  --format markdown \
  --output /tmp/openbrep-gdl-knowledge-verify.md
```

## 分批计划

| 批次 | 范围 | 文件 | 目标 |
|---|---|---|---|
| P0 已完成 | 最高风险生成命令 | `BLOCK`、`PROJECT2`、`HOTSPOT2`、`MATERIAL`、`REVOLVE`、`SWEEP` | 修掉伪语法和误导性简写 |
| P1 | 核心 3D 几何 | `PRISM_`、`CYLIND`、`CUTPLANE`、`BODY_EDGE_PGON` | 确认参数顺序、状态码、退化几何、布尔/裁切边界 |
| P2 | 变换与控制流 | `ADD_DEL`、`Transformation_Stack`、`FOR_NEXT`、`IF_ENDIF` | 防止 ADD/DEL、FOR/NEXT、IF/ENDIF 结构性错误 |
| P3 | 2D 表达 | `2D_Primitives`、`PROJECT2`、`HOTSPOT2` | 校对平面符号、热点编辑、投影策略 |
| P4 | 参数与属性 | `Paramlist_XML`、`DEFINE`、`MATERIAL`、`GLOBALS`、`Object_Types` | 校对参数类型、材质/属性、对象类型、全局变量 |
| P5 | Group / 高级几何 | `GROUP`、`SWEEP`、`REVOLVE`、`BODY_EDGE_PGON` | 确认高级命令只在有把握时用于生成 |
| P6 | 构件 archetype | `bookshelf`、`cabinet`、`table`、`door`、`window`、`profile_object` | 让构件知识只引用已校对命令，修正不合理建模策略 |

## 每批验收标准

- 本批 wiki 页至少包含官方语法签名。
- 明确 OpenBrep 推荐用法和不推荐用法。
- 明确 LLM 生成时最容易犯的错误。
- `knowledge_context_smoke.py` 不退化。
- `lint-knowledge.py` 通过。
- 全量测试通过。

## P1 立即执行清单

优先校对：

```text
knowledge/wiki/PRISM_.md
knowledge/wiki/CYLIND.md
knowledge/wiki/CUTPLANE.md
knowledge/wiki/BODY_EDGE_PGON.md
```

重点查：

- `PRISM_` 的 `n, h, x, y, s` 顺序和状态码说明。
- `CYLIND` 的 `h, r`、零高/零半径退化行为。
- `CUTPLANE` / `CUTEND` 是否被写成错误的裁切流程。
- `BODY_EDGE_PGON` 是否适合作为 AI 默认生成命令；若不适合，应标注为高级/非默认。

P1 完成后再进入 P2，不并行铺开。
