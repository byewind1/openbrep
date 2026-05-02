# OpenBrep 手工测试清单

日期：2026-04-27  
目标：覆盖自动测试难以覆盖的 Streamlit、LP_XMLConverter、Archicad/Tapir 路径。

## 启动

```bash
python -m pytest tests/ -q
streamlit run ui/app.py
```

## 必测路径

- 启动 UI，无首页报错。
- 通过"打开文件或 HSF 文件夹"打开 `.gdl` 或 `.txt`，确认脚本进入编辑器。
- 有 LP_XMLConverter 时通过同一入口打开 `.gsm`，确认生成 HSF 项目。
- 通过同一入口打开已有 HSF 目录，确认参数和脚本可见。
- 通过同一入口打开非 HSF 文件夹，确认出现明确提示。
- 输入自然语言生成简单对象，确认首次写入编辑器。
- 对已有对象要求修改，确认自动写入当前 HSF 项目并刷新编辑器。
- 输入“解释一下这个 3D 脚本”，确认只解释、不改代码。
- 运行本地脚本检查。
- 运行 2D/3D preview。
- 编译 GSM，确认输出 `workspace/output/ObjectName_vN.gsm`。

## 有 Archicad/Tapir 时加测

- 点击读取选中对象，确认 GUID/参数可见。
- 高亮选中对象。
- 修改一个安全参数并写回。
- 触发 reload/test，确认失败时 UI 有明确提示。

## 通过标准

- 无 Streamlit traceback。
- 不会把 `.gsm` 当源文件修改。
- 编译不会创建新的 HSF 源目录。
- 解释类请求不写入脚本。
- 失败路径有可理解错误提示。
