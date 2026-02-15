# knowledge/ — GDL Knowledge Base

## What This Directory Does / 这个目录的作用

This directory contains GDL reference documents that get injected into the LLM's context when generating code. **The quality of these documents directly determines whether the Agent produces compilable GDL.**

这个目录存放 GDL 参考文档，在生成代码时注入 LLM 的上下文。**这些文档的质量直接决定 Agent 能否产出可编译的 GDL。**

## How It Works / 工作原理

```
User instruction: "Create a parametric bookshelf"
                    │
                    ▼
        ┌─ knowledge/ ──────────────────┐
        │ GDL_syntax.md         (loaded)│
        │ XML_structure.md      (loaded)│
        │ common_errors.md      (loaded)│
        │ naming_conventions.md (loaded)│
        └───────────────────────────────┘
                    │
                    ▼
          System Prompt + Knowledge
                    │
                    ▼
               LLM generates GDL
```

The Agent loads all `.md` files from this directory and includes them in the system prompt. Files are scored by relevance to the user's instruction — higher-relevance docs are prioritized when context window is limited.

Agent 会加载此目录下所有 `.md` 文件并注入 system prompt。文件按与用户指令的相关性评分——上下文窗口有限时，优先注入高相关性文档。

## Required Documents / 必要文档

At minimum, you need these files for the Agent to produce usable GDL:

至少需要以下文件，Agent 才能产出可用的 GDL：

### 1. `GDL_syntax.md` — GDL Language Reference

Core GDL syntax that LLMs don't reliably know:

```markdown
# GDL Syntax Reference

## 3D Geometry Commands
- BLOCK a, b, c
- PRISM_ n, h, x1, y1, ..., xn, yn
- CYLIND h, r
- ...

## Transformation Stack
- ADD dx, dy, dz    ! Push translation
- DEL n             ! Pop n transformations
- ADDX dx           ! Shorthand
- ...

## Control Flow
- IF condition THEN ... ENDIF
- FOR var = start TO end ... NEXT var
- Single-line IF: IF x THEN y = z  (no ENDIF needed)
- ...
```

### 2. `XML_structure.md` — LP_XMLConverter XML Format

The XML structure that LP_XMLConverter expects:

```markdown
# LP_XMLConverter XML Structure

## Root Element
<?xml version="1.0" encoding="UTF-8"?>
<Symbol>
  <Parameters>...</Parameters>
  <Script_1D><![CDATA[ ... ]]></Script_1D>   <!-- Master Script -->
  <Script_2D><![CDATA[ ... ]]></Script_2D>
  <Script_3D><![CDATA[ ... ]]></Script_3D>
  <Script_PR><![CDATA[ ... ]]></Script_PR>   <!-- Parameter Script -->
  <Script_UI><![CDATA[ ... ]]></Script_UI>
</Symbol>

## Parameter Types
- Length, Integer, Boolean, RealNum, Angle
- String, Material, FillPattern, LineType, Pencolor

## CDATA Rules
- Every script MUST be wrapped in <![CDATA[ ... ]]>
- CDATA sections CANNOT be nested
- ]]> inside script content will break XML parsing
```

### 3. `common_errors.md` — Mistakes LLMs Make

Patterns that LLMs consistently get wrong:

```markdown
# Common LLM Mistakes in GDL

## 1. Missing ENDIF for multi-line IF blocks
Wrong: IF bOption THEN\n  BLOCK ...\n(no ENDIF)
Right: IF bOption THEN\n  BLOCK ...\nENDIF

## 2. PRISM_ without height parameter
Wrong: PRISM_ 4, 0,0, 1,0, 1,1, 0,1
Right: PRISM_ 4, 0.1, 0,0, 1,0, 1,1, 0,1

## 3. ADD/DEL mismatch
Every ADD must have a corresponding DEL.
...
```

### 4. `naming_conventions.md` — Parameter Naming Rules

```markdown
# GDL Naming Conventions

## Hungarian Notation (recommended)
- bXxx    Boolean     bHasGlass, bShowFrame
- iXxx    Integer     iSegments, iFloorCount
- rXxx    RealNum     rAngle, rOffset
- sXxx    String      sLabel, sDescription
- A, B    Length      (reserved: object width, depth)
- ZZYZX   Length      (reserved: object height)
...
```

## File Format / 文件格式

- All files must be **UTF-8 encoded Markdown** (`.md`)
- Use clear headings (`##`) — the Agent uses headings for relevance matching
- Include **concrete code examples** — LLMs learn from examples, not abstract rules
- Keep each file focused on one topic
- Chinese and English are both supported

所有文件必须是 **UTF-8 编码的 Markdown**（`.md`）。使用清晰的标题（`##`）——Agent 通过标题匹配相关性。包含**具体代码示例**——LLM 从示例学习，而非抽象规则。

## Customization / 定制

You should customize these documents based on:

- Your ArchiCAD version (syntax differences between AC25 and AC28)
- Your firm's coding standards
- Your target object types (doors, windows, furniture, MEP...)
- Common mistakes you've observed in LLM output

**The knowledge base is your competitive advantage.** The gdl-agent code is the engine; these documents are the fuel. Better documents = better GDL output.

**知识库是你的核心竞争力。** gdl-agent 代码是引擎，这些文档是燃料。文档越好，GDL 输出质量越高。

## Tips / 建议

1. **Start small.** One good `common_errors.md` with 10 real error patterns is worth more than a 50-page reference manual.

2. **Iterate.** When the Agent makes a mistake, add that pattern to your knowledge base. Over time, the knowledge base becomes a curated error correction library.

3. **Use real examples.** Instead of "PRISM_ takes n vertices and a height", show the actual code: `PRISM_ 4, 0.1, 0,0, 1,0, 1,1, 0,1`.

4. **Separate by topic.** One file per topic (syntax, errors, naming, XML format) is better than one giant file — allows better relevance matching.
