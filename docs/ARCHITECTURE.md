# openbrep 技术架构

# Technical Architecture

---

## 数据流 / Data Flow

```
用户输入（自然语言 / .gdl 文件 / .gsm 文件）
         │
         ▼
    ┌─────────┐     ┌────────────┐
    │ GDL     │     │ LP_XML     │
    │ Parser  │     │ Converter  │
    │(.gdl→)  │     │(.gsm→HSF)  │
    └────┬────┘     └─────┬──────┘
         │               │
         ▼               ▼
    ┌─────────────────────────┐
    │      HSFProject         │  ← 核心数据模型
    │  ┌───────────────────┐  │
    │  │ libpartdata.xml   │  │  身份信息 (GUID)
    │  │ paramlist.xml     │  │  参数定义 (强类型)
    │  │ ancestry.xml      │  │  对象分类
    │  │ scripts/          │  │  GDL 脚本 (分离)
    │  │   1d/2d/3d/vl/ui  │  │
    │  └───────────────────┘  │
    └────────────┬────────────┘
                 │
    ┌────────────▼────────────┐
    │     Agent Core Loop     │
    │                         │
    │  1. ANALYZE             │  确定影响哪些脚本
    │  2. GENERATE            │  LLM + Knowledge 生成代码
    │  3. COMPILE             │  hsf2libpart → .gsm
    │  4. VERIFY              │  成功? → 输出 / 失败? → 重试
    │                         │
    └────────────┬────────────┘
                 │
                 ▼
            📦 .gsm 文件
```

## HSF 格式详解

LP_XMLConverter `libpart2hsf` 解压 .gsm 后的真实目录结构：

```
ObjectName/
├── libpartdata.xml       # 对象元数据
│   └── <LibpartData Owner="..." Signature="..." Version="46">
│       └── <Identification>
│           ├── <MainGUID>...</MainGUID>
│           └── <IsPlaceable>true</IsPlaceable>
│
├── paramlist.xml         # 参数定义
│   └── <ParamSection>
│       ├── <ParamSectHeader>...</ParamSectHeader>
│       └── <Parameters SectVersion="27" ...>
│           ├── <Length Name="A"><Fix/><Value>1.0</Value></Length>
│           ├── <Boolean Name="bTest"><Value>1</Value></Boolean>
│           └── <Material Name="mat"><Value>52</Value></Material>
│                                          ↑ 必须是整数索引
│
├── ancestry.xml          # 分类 (subtype GUID 链)
│   └── <Ancestry>
│       └── <MainGUID>F938E33A-...</MainGUID>  ← General GDL Object
│
├── calledmacros.xml      # CALL 宏引用
├── libpartdocs.xml       # 版权/关键词
│
└── scripts/              # GDL 脚本 (分离存储)
    ├── 1d.gdl            # Master Script
    ├── 2d.gdl            # 2D Symbol
    ├── 3d.gdl            # 3D Model
    ├── vl.gdl            # Parameter Logic
    └── ui.gdl            # Interface
```

### 关键编码规则

| 规则 | 说明 |
|:---|:---|
| **UTF-8 BOM** | 所有文件必须使用 `utf-8-sig` 编码写入 |
| **Material 值** | 必须是整数索引，不能是字符串名称 |
| **Description** | 必须用 `<![CDATA["text"]]>` 包裹（注意内部有引号） |
| **ancestry GUID** | `F938E33A-329D-4A36-BE3E-85E126820996` = General GDL Object |
| **保留参数** | A / B / ZZYZX 必须是 Length 类型且有 `<Fix/>` |

## Context Surgery（上下文手术）

HSF 的核心优势：每个脚本是独立文件，AI 只需要加载相关脚本。

```python
# 用户说 "修改三维几何"
affected = project.get_affected_scripts("修改三维几何")
# → [ScriptType.MASTER, ScriptType.SCRIPT_3D]
# 只加载 1d.gdl + 3d.gdl 到 LLM 上下文
# 省掉 2d.gdl / ui.gdl / vl.gdl 的 token 消耗
```

## Anti-Hallucination 机制

| 层级 | 机制 | 实现 |
|:---|:---|:---|
| 参数类型 | LLM 写 `Float` → 自动纠正为 `RealNum` | `PARAM_TYPE_CORRECTIONS` |
| 参数校验 | Boolean 值必须是 0/1，保留参数必须是 Length | `validate_paramlist()` |
| 结构检查 | IF/ENDIF 配对、FOR/NEXT 配对 | `MockHSFCompiler._check_gdl_basic()` |
| 编译验证 | LP_XMLConverter 真实编译 | `HSFCompiler.hsf2libpart()` |
| 反循环 | 连续相同输出 → 停止重试 | `output_hash` 比对 |

## LLM 路由

```python
# litellm 统一接口，根据模型名自动路由
"claude-opus-4-6"           → Anthropic API (直接)
"glm-4.7"                   → openai/glm-4.7 + bigmodel.cn base URL
"deepseek-chat"              → deepseek/deepseek-chat
"ollama/qwen2.5:14b"        → Ollama 本地
"gemini/gemini-2.5-flash"   → Google Gemini API
```
