---
type: concept
status: stable
tags: [3d, geometry, define, macro, reuse, sub-geometry]
aliases: [DEFINE, define, sub, gosub, call, reuseable geometry]
source: raw/ccgdl_dev_doc/docs/GDL_06_Macro_UI_Perf.md
---

# DEFINE

`DEFINE` ... `END` creates a **reusable sub-program** within a GDL script. Defined sub-programs can be called multiple times with `GOSUB`, reducing code duplication for repeated geometry patterns.

## Syntax

```gdl
DEFINE name [param1, param2, ...]
  ... geometry or logic ...
END
```

| Param   | Type          | Description                                  |
|---------|---------------|----------------------------------------------|
| `name`  | identifier    | Sub-program name (invoked via `GOSUB name`)  |
| `param` | variable name | Optional parameters passed by reference      |

## Calling Syntax

```gdl
GOSUB name [arg1, arg2, ...]
```

## How Parameters Work

- Parameters are passed **by reference** — the sub-program can modify variables in the caller's scope.
- The sub-program sees the **current values** of all global variables and parameters.
- Arguments map to parameters positionally: first arg → first param, etc.
- Parameters act as **local aliases**: inside `DEFINE`, `param1` refers to whatever variable/expression was passed as `arg1`.

## Examples

### Reusable column cap

```gdl
DEFINE column_cap w, h
  ADD -w/2, -w/2, h
    PRISM_ 4, 0.15,
        0, 0, 1,
        w, 0, 1,
        w, w, 1,
        0, w, 1
  DEL 1
END

! Usage
GOSUB column_cap 0.8, 3.0
ADD 2.5, 0, 0
  GOSUB column_cap 0.8, 3.0
DEL 1
```

### Sub-program with local variables

```gdl
DEFINE frame a, b, depth
  ! Creates a rectangular frame with given outer dimensions
  BLOCK -a/2, -b/2, 0, a, b, depth
  BLOCK -a/2 + 0.05, -b/2 + 0.05, 0, a - 0.1, b - 0.1, depth
END
```

## Scope Rules

| Scope        | Variables access                      |
|--------------|---------------------------------------|
| Before CALL  | Normal script scope                   |
| Inside DEFINE| Parameters + all global variables     |
| After RETURN | Parameters have caller's final values |

## Static Behavior

`DEFINE` blocks are processed **once at compile time**, not at runtime in the traditional sense. Each `GOSUB` results in the defined code being executed in the current transformation context:

```gdl
DEFINE knob
  CYLIND 0, 0, 0.03, 0.03, 0.05
END

! Written once, used twice in different positions
ADD 0.5, 0, 0
  GOSUB knob
DEL 1

ADD 1.5, 0, 0
  GOSUB knob
DEL 1
```

## Edge Cases & Traps

- **No forward reference**: `DEFINE` must appear before any `GOSUB` that calls it (linear order).
- **Recursion**: GDL does not support recursive `GOSUB` — the sub-program cannot call itself, directly or indirectly.
- **Parameter count mismatch**: passing more args than defined params — excess args are evaluated but the return values are lost. Passing fewer args — unmatched params remain at their **current value** (not zero).
- **Nested calls**: `GOSUB` inside a `DEFINE` is supported. There is no hard limit on nesting depth, but avoid deep chains for readability.
- **Unused parameters**: defining params that the sub-program never references is legal but misleading.
- **No local variables**: all variables in GDL are effectively global. Use unique prefixes in DEFINE blocks to avoid name collisions: `_knob_r`, `_knob_h`.

## Related

- [[FOR_NEXT]] — looping over repeated geometry (alternative for repeating patterns)
- [[GROUP]] — logical sub-division within a body (for organization, not reuse)
- [[Transformation_Stack]] — positioning repeated sub-geometry
