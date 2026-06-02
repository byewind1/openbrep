# React Workbench 文件流设计

日期：2026-06-02

## 目标

让 React workbench 像专业 GDL IDE，而不是启动后默认进入一个示例书架对象。

核心流程：

```text
启动 -> 空工作台 -> New/Open/Import/AI create -> 编辑 -> Save 或 Save As HSF
```

## 当前问题

本地 API 启动时创建 `Demo Bookshelf`，关闭/重置项目时也回到这个 demo。结果是：

- 用户分不清自己是在编辑真实源对象还是示例对象。
- `Cmd+Shift+R` 语义变成“回到书架”，不是“关闭当前项目/回到空工作台”。
- AI 生成可以写盘，但 UI 没有清楚暴露项目名和保存目录。
- `Save` 主要保存活动脚本，默认假设已经存在 HSF 项目路径。

## 设计决策

采用 IDE 式“未保存项目”模型。

`New` 创建一个内存中的空 GDL 项目，命名为 `Untitled GDL Object`，不会立刻写盘。第一次需要持久化时进入 Save As HSF 流程，要求用户选择项目名和父目录。保存后，后续保存直接写回当前 HSF 目录。

## 状态模型

### 空工作台

无活动项目，无 source path。

允许：

- New
- Open HSF
- Import GDL
- Import GSM
- AI create
- Settings

禁用或隐藏：

- 保存活动脚本
- Compile
- 需要项目路径的 revision / memory 操作

### 未保存项目

内存中存在 `HSFProject`，但 `source_path` 为空，`source` 为 `untitled`。

允许：

- 编辑脚本和参数
- AI generate/modify
- 预览
- Save As HSF

保存行为：

- 如果所有脚本为空，且没有有意义的参数变更，提示“空项目无需保存”，不创建 HSF 目录。
- 如果已有代码或生成内容，要求输入项目名和父目录，再保存到 `parent/project_name`。

### 已保存 HSF 项目

存在 `HSFProject`，且 `source_path` 指向真实 HSF 目录。

保存行为：

- `Save` 保存活动脚本，若有项目级变更则写回当前 HSF。
- `Save As HSF` 另存到指定父目录，并把另存后的 HSF 作为当前项目。

## 启动

启动后返回空工作台 snapshot，不再返回 `Demo Bookshelf`。

demo 对象可以保留为测试夹具或未来显式 sample 命令，但不能再作为默认项目或 reset 目标。

## New

顶层 `New` 创建未保存项目：

```text
name: Untitled GDL Object
source: untitled
path: empty
scripts: 标准 GDL script slots，内容为空
parameters: 最小固定尺寸 A, B, ZZYZX
preview: 空预览或最小非错误预览
```

如果当前有 dirty scripts 或参数草稿，`New` 需要确认是否丢弃未保存变更。

## AI Create

如果当前没有活动项目，AI create 应生成一个新项目，并让保存位置对用户显式可见。

第一阶段允许复用现有后端生成链路，但 UI 必须把结果标记为新建项目，并提示用户可 Save As HSF 到工作空间目录。长期目标是：

```text
AI create -> 生成未保存项目 -> Save As HSF
```

这避免静默把 HSF source 写到 `./output`。

## Open 与 Import

`Open HSF` 加载已有 HSF 源目录。

`Import GDL` / `Import GSM` 从外部输入构造项目。导入结果应视为新建/未保存项目，直到用户保存到指定工作空间目录。

## 顶部工具栏

顶部文件流应提供紧凑但完整的工作台动作：

- New
- Open
- Recent
- Import GDL
- Import GSM
- Save
- Save As
- Compile
- Settings

状态应清楚显示：

- Empty
- Unsaved
- Saved
- Dirty
- 当前项目路径（如有）

## 后端边界

不要继续膨胀 `workbench_api.py`。

新增行为放在 `openbrep/workbench/project_session_service.py`，`WorkbenchSession` 只保留薄 wrapper。

新增 API：

- `POST /api/project/new`
- `POST /api/project/save`

已有 `POST /api/project/export-hsf` 可继续作为 Save As。

## 前端边界

`WorkbenchApp.tsx` 只做组合。

新增行为放在：

- `frontend/src/state/actions/projectActions.ts`
- `frontend/src/workbench/project/ProjectOpenControls.tsx`
- `frontend/src/components/TopMenu.tsx`
- 必要时拆小 helper，不把文件生命周期逻辑堆进主组件

## 错误处理

- 打开不存在的 HSF 目录时显示明确错误，并保留当前项目。
- 未保存空项目执行保存时提示无内容可保存。
- Save As 不覆盖非当前项目且非空的 HSF 目录。
- Compile 在空工作台和未保存项目下禁用。

## 测试

后端测试：

- 启动 snapshot 表示空工作台。
- New 创建 `source=untitled`，不写盘。
- Close/Reset 回到空工作台，不回 demo。
- 未保存空项目保存返回 no-op 错误。
- Save As 写 HSF 到选择的父目录。

前端测试：

- 顶部暴露 New 和 Save As。
- New 调用新建 project action。
- 未保存 dirty 项目 Save 路由到 Save As。
- 空/未保存项目禁用 Compile。
- 项目状态显示 Empty / Unsaved / Saved。

## 非目标

- 不使用浏览器 File System Access API。OpenBrep 已有本地 Python API，文件对话框和写盘应由本地 API 负责。
- 本轮不删除 demo project 代码，可保留为测试夹具或未来显式 sample。
- 不重做三栏主布局。
