# skills/ — Prompt Engineering Skills

## What This Directory Does / 这个目录的作用

Skills are **task-specific prompt strategies** that tell the LLM *how* to approach different types of GDL tasks. While `knowledge/` provides *what* (reference facts), `skills/` provides *how* (methodology).

Skills 是**任务特定的 prompt 策略**，告诉 LLM *如何*处理不同类型的 GDL 任务。`knowledge/` 提供"是什么"（参考知识），`skills/` 提供"怎么做"（方法论）。

## knowledge/ vs skills/

```
knowledge/                          skills/
─────────                          ──────
WHAT the LLM needs to know         HOW the LLM should work
├── GDL syntax reference            ├── Creating objects from scratch
├── XML structure rules             ├── Modifying existing parameters
├── Common error patterns           ├── Debugging compile errors
└── Naming conventions              └── Optimizing 3D geometry
```

**Analogy / 类比：** `knowledge/` is the textbook. `skills/` is the teacher's methodology.

## How It Works / 工作原理

```
User: "Create a parametric door"
          │
          ▼
    Preflight detects: "create from scratch" task
          │
          ▼
    Loads: skills/create_object.md
          │
          ▼
    System prompt now includes:
    - knowledge/ docs (what GDL syntax to use)
    - skills/ doc (step-by-step creation strategy)
          │
          ▼
    LLM follows the skill's methodology
```

The Agent selects skills based on task type detected during preflight analysis. Multiple skills can be loaded for complex tasks.

Agent 在预检阶段根据任务类型选择 skills。复杂任务可以同时加载多个 skills。

## Skill File Format / Skill 文件格式

Each skill is a Markdown file with a specific structure:

```markdown
# Skill: [Task Type]

## When to Use / 适用场景
[Describe when this skill should be activated]

## Strategy / 策略
[Step-by-step methodology for the LLM to follow]

## Rules / 规则
[Hard constraints the LLM must obey]

## Examples / 示例
[Concrete before/after examples]

## Common Pitfalls / 常见陷阱
[What goes wrong and how to avoid it]
```

## Skill Categories / Skill 分类

### Creation Skills (创建类)

For building new GDL objects from scratch.

**`create_object.md`** — Full object creation strategy:

```markdown
# Skill: Create GDL Object from Scratch

## Strategy
1. Start with Parameters section — define ALL parameters before writing any script
2. Write Master Script — parameter validation (IF < min THEN = min)
3. Write Script_3D — geometry, always use ADD/DEL pairs
4. Write Script_2D — plan view with HOTSPOT2 for stretch
5. Write Script_PR — VALUES constraints matching Master Script
6. Write Script_UI — only if explicitly requested

## Rules
- EVERY IF block MUST have ENDIF (except single-line IF THEN)
- EVERY ADD MUST have matching DEL
- PRISM_ ALWAYS needs height parameter: PRISM_ n, h, x1,y1,...
- Parameters A, B, ZZYZX are RESERVED (width, depth, height)
- Use Hungarian notation: bXxx (Boolean), iXxx (Integer), rXxx (Real)

## Common Pitfalls
- Forgetting height parameter in PRISM_ (most frequent LLM error)
- Using ENDIF for single-line IF THEN (causes compile error)
- ...
```

### Modification Skills (修改类)

For changing existing objects.

**`modify_parameter.md`** — Adding/changing parameters:

```markdown
# Skill: Modify Parameters

## Strategy
1. Read existing Parameters section
2. Add new parameter with correct Type and default Value
3. Add validation in Master Script (IF < min / IF > max)
4. Add VALUES constraint in Parameter Script
5. Use new parameter in relevant Script (3D/2D/UI)
6. Output FULL XML (not just the changed section)

## Rules
- Never remove existing parameters without explicit instruction
- Match parameter Type to usage (Material for SET MATERIAL, etc.)
- ...
```

### Debug Skills (调试类)

For fixing compile errors.

**`fix_compile_error.md`** — Error diagnosis and repair:

```markdown
# Skill: Fix Compile Error

## Strategy
1. Parse error message — identify error type and location
2. Map to known error pattern (IF/ENDIF, CDATA, PRISM_)
3. Apply minimal fix (don't rewrite working code)
4. Verify fix doesn't introduce new errors

## Error Patterns
- "Mismatched IF/ENDIF" → count blocks, add missing ENDIF
- "XML Parse Error" → check CDATA boundaries
- "Unknown identifier" → check parameter name spelling
- ...
```

### Optimization Skills (优化类)

**`optimize_geometry.md`** — 3D performance:

```markdown
# Skill: Optimize 3D Geometry

## Strategy
- Replace multiple BLOCK calls with single PRISM_ where possible
- Use GROUP for repeated elements instead of copy-paste
- Minimize transformation stack depth (ADD/DEL)
- ...
```

## Loading Mechanism / 加载机制

Skills are loaded by the Agent based on:

1. **Task type** detected by preflight analysis (`create` / `modify` / `debug`)
2. **Keyword matching** in the user's instruction
3. **Error type** during retry (loads debug skill with specific error pattern)

```python
# In core.py (simplified)
if analysis.complexity == "complex" and "create" in instruction:
    load_skill("create_object.md")
elif error and "IF/ENDIF" in error:
    load_skill("fix_compile_error.md")
```

## Creating Your Own Skills / 编写自己的 Skills

### Principles / 原则

1. **Be specific.** "Always use ADD/DEL pairs" is better than "manage transformations properly".

2. **Show, don't tell.** Include concrete code examples in every skill. LLMs follow examples more reliably than abstract instructions.

3. **Test empirically.** Write a skill → run Agent → check output → refine skill. This is prompt engineering — iteration is the process.

4. **One skill, one task type.** Don't combine creation and debugging in one file.

5. **Include failure modes.** The "Common Pitfalls" section is often more valuable than the "Strategy" section.

### Iteration Workflow / 迭代流程

```
1. Agent produces wrong GDL
        │
        ▼
2. Identify what went wrong
   (missing ENDIF? wrong PRISM_ args? bad parameter type?)
        │
        ▼
3. Add pattern to relevant skill
   (or create new skill if no existing one covers it)
        │
        ▼
4. Re-run Agent with same instruction
        │
        ▼
5. Verify fix → repeat if needed
```

Over time, your skills become a curated knowledge base of "how to make LLMs write correct GDL". **This is the real IP — not the code.**

随着迭代，你的 skills 会成为"如何让 LLM 写出正确 GDL"的精炼知识库。**这才是真正的 IP——不是代码。**

## Notes / 备注

- Skills files are **not included** in the open-source distribution. The project ships with this README and skeleton templates only.
- Users are expected to develop their own skills based on their ArchiCAD version, coding standards, and observed LLM behavior.
- The quality of skills has a **multiplicative effect** on output quality — a well-written skill can turn a mediocre model into a reliable GDL generator.

Skills 文件**不包含在开源发行版中**。项目只附带本 README 和骨架模板。用户需要根据自己的 ArchiCAD 版本、编码规范和观察到的 LLM 行为来开发自己的 skills。
