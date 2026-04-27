# ADR 0001: HSF 项目目录是 OpenBrep 的源格式

日期：2026-04-27  
状态：Accepted

## 背景

ArchiCAD 库对象的可交付结果通常是 `.gsm`，但 `.gsm` 是编译产物，不适合直接作为 AI 修改、版本管理和协作审查的源格式。单个 `.gdl` 文件也不足以表示完整库对象，因为参数、脚本、元数据和宏引用分散在 HSF 目录结构中。

## 决策

OpenBrep 将 HSF 项目目录作为可编辑源格式和版本管理对象。

```text
workspace/<object-name>/
  libpartdata.xml
  paramlist.xml
  ancestry.xml
  calledmacros.xml
  libpartdocs.xml
  scripts/
    1d.gdl
    2d.gdl
    3d.gdl
    vl.gdl
    ui.gdl
    pr.gdl
```

`.gsm` 只作为编译输出和 ArchiCAD 交付物处理。导入 `.gsm` 时可以创建稳定的 HSF 源目录；后续修改应更新当前 HSF 目录或生成 pending diffs 等待确认。编译动作不得为同一个对象创建新的源目录。

## 成功标准

- 修改 GDL 对象时，源状态可以通过 HSF 目录审查、提交和恢复。
- `paramlist.xml` 与 `scripts/*.gdl` 被视为同一个源单元。
- `.gsm` 不被当成可编辑源文件。
- AI 写入前可以捕获项目快照，写入后可以通过 revision 元数据追溯。
- 编译验证生成 `.gsm`，但不改变源目录身份。

## 后果

这个决策让 OpenBrep 可以使用 Git、diff、revision metadata 和 AI pending diff 工作流来管理库对象。代价是导入、加载、编译和恢复逻辑必须始终围绕 HSFProject 边界实现，不能用单文件 `.gdl` 或 `.gsm` 快捷路径绕过。

## 对 AI 开发工具的要求

涉及项目状态、导入、保存、恢复、编译的修改时，优先阅读：

- `openbrep/hsf_project.py`
- `ui/project_service.py`
- `ui/project_io.py`
- `ui/revision_controller.py`

如果一个实现需要直接编辑 `.gsm`，通常说明方向错了。正确做法是回到 HSF 源目录，修改源，再编译验证。
