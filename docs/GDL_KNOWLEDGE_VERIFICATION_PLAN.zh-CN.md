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
| P1 已完成 | 核心 3D 几何 | `PRISM_`、`CYLIND`、`CUTPLANE`、`BODY_EDGE_PGON` | 确认参数顺序、状态码、退化几何、布尔/裁切边界 |
| P2 已完成 | 变换与控制流 | `ADD_DEL`、`Transformation_Stack`、`FOR_NEXT`、`IF_ENDIF` | 防止 ADD/DEL、FOR/NEXT、IF/ENDIF 结构性错误 |
| P3 已完成 | 2D 表达 | `2D_Primitives`、`PROJECT2`、`HOTSPOT2` | 校对平面符号、热点编辑、投影策略 |
| P4 已完成 | 参数与属性 | `Paramlist_XML`、`DEFINE`、`MATERIAL`、`GLOBALS`、`Object_Types` | 校对参数类型、材质/属性、对象类型、全局变量 |
| P5 已完成 | Group / 高级几何 | `GROUP`、`SWEEP`、`REVOLVE`、`BODY_EDGE_PGON` | 确认高级命令只在有把握时用于生成 |
| P6 | 构件 archetype | `bookshelf`、`cabinet`、`table`、`door`、`window`、`profile_object` | 让构件知识只引用已校对命令，修正不合理建模策略 |

## 每批验收标准

- 本批 wiki 页至少包含官方语法签名。
- 明确 OpenBrep 推荐用法和不推荐用法。
- 明确 LLM 生成时最容易犯的错误。
- `knowledge_context_smoke.py` 不退化。
- `lint-knowledge.py` 通过。
- 全量测试通过。

## P1 完成记录

已校对：

```text
knowledge/wiki/PRISM_.md
knowledge/wiki/CYLIND.md
knowledge/wiki/CUTPLANE.md
knowledge/wiki/BODY_EDGE_PGON.md
```

完成结果：

- `PRISM_` 明确 `n, h, x, y, s` 顺序，并修正带洞示例的点数。
- `CYLIND` 修正为官方 `CYLIND h, r`，删除中心点、双半径、segments 的伪语法。
- `CUTPLANE` 修正为 3D 裁切命令，明确必须与 `CUTEND` 成对使用。
- `BODY_EDGE_PGON` 修正为 `VERT` / `EDGE` / `PGON` / `BODY` 低层 primitive 流程，并标注为 AI 非默认高级命令。
- `tests/test_knowledge_lint.py` 增加 P1 语法防回退断言。

Python 访问 Graphisoft index 时本地出现 SSL EOF，在线官方文档通过浏览器侧确认；自动验收以本地 lint、上下文 smoke、目标测试和全量测试为准。

## P2 完成记录

已校对：

```text
knowledge/wiki/ADD_DEL.md
knowledge/wiki/Transformation_Stack.md
knowledge/wiki/FOR_NEXT.md
knowledge/wiki/IF_ENDIF.md
```

完成结果：

- `ADD_DEL` 改成官方 `ADD dx, dy, dz` / `DEL n [, begin_with]` / `DEL TOP` / `NTR()`
- `Transformation_Stack` 补充局部删除、`DEL TOP`、`NTR()` 与变换栈语义。
- `FOR_NEXT` 改成官方 `FOR variable_name = initial_value TO end_value [ STEP step_value ] NEXT variable_name`
- `FOR_NEXT` 修正 `STEP` 默认值和 loop control 限制。
- `IF_ENDIF` 修正为官方允许的比较与逻辑表达式写法。
- `tests/test_knowledge_lint.py` 增加 P2 语法防回退断言。

## P3 完成记录

已校对：

```text
knowledge/wiki/2D_Primitives.md
knowledge/wiki/PROJECT2.md
knowledge/wiki/HOTSPOT2.md
```

完成结果：

- `2D_Primitives` 改成官方 `LINE2` / `RECT2` / `CIRCLE2` / `ARC2` / `POLY2` 语法。
- `PROJECT2` 修正为官方 3D-to-2D projection 语义，并去掉错误的 cut plane 解释。
- `HOTSPOT2` 对齐 graphical editing 用法，保留官方参数签名和图形编辑约束。
- `tests/test_knowledge_lint.py` 增加 P3 语法防回退断言。

P3 完成后再进入 P4，不并行铺开。

## P4 完成记录

已校对：

```text
knowledge/wiki/Paramlist_XML.md
knowledge/wiki/DEFINE.md
knowledge/wiki/MATERIAL.md
knowledge/wiki/GLOBALS.md
knowledge/wiki/Object_Types.md
```

完成结果：

- `Paramlist_XML` 改成 OpenBrep 实际 HSF `ParamSection` / `Parameters SectVersion="27"` 格式。
- `Paramlist_XML` 明确 `RealNum`、`PenColor`、`FillPattern`、`LineType` 等真实类型标签。
- `DEFINE` 修正为 inline attribute definition，不再误写成子程序机制。
- `GLOBALS` 改为现代 global variable 用法，删除伪 `GLOBALS SYMBOL` 声明链路。
- `Object_Types` 改为 subtype / host behavior 语义，不再靠硬编码 `SYMBOL` 表指导生成。
- `MATERIAL` 补充 HSF 中 material 参数值必须是整数 attribute index。
- `tests/test_knowledge_lint.py` 增加 P4 语法防回退断言。

P4 完成后再进入 P5，不并行铺开。

## P5 完成记录

已校对：

```text
knowledge/wiki/GROUP.md
knowledge/wiki/SWEEP.md
knowledge/wiki/REVOLVE.md
knowledge/wiki/BODY_EDGE_PGON.md
```

完成结果：

- `GROUP` 修正为 solid geometry operation 组，不再误写为可见的嵌套组织容器。
- `GROUP` 明确需要 `PLACEGROUP` 才会生成几何，并补充 `KILLGROUP`、唯一名称、不可嵌套等约束。
- `SWEEP` 标注为高级非默认命令，只有明确需要 profile along path 时使用。
- `REVOLVE` 标注为高级但可用于明确旋转体对象，简单圆柱仍优先用 `CYLIND`。
- `BODY_EDGE_PGON` 强化 primitive elements 非默认策略，普通构件不得用它替代高层实体命令。
- `tests/test_knowledge_lint.py` 增加 P5 语法防回退断言。

P5 完成后再进入 P6，不并行铺开。
