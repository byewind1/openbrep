# Debug 工作流

## UI 问题排查顺序

1. 功能失效但无报错
   - 先检查是否是 Streamlit 废弃参数（width='stretch' 等）
   - 再检查编辑器缓冲区是否同步（_sync_visible_editor_buffers）
   - 最后检查 session_state 读写是否正确

2. 预览失效
   - python3 直接测试 gdl_previewer 是否正常
   - 检查 _run_preview 拿到的脚本是否为空
   - 检查 plotly_chart 是否用了废弃参数

3. 配置不生效
   - 确认 config.toml 是新格式（custom_providers 是数组）
   - 确认字段名：compiler 用 path 不用 lp_converter_path
   - python3 验证配置加载是否正确

## 代码编辑原则

- 精细编辑必须用 claude 系列模型，GLM 系列不适合
- 改前先 grep 确认变量/函数的所有引用位置
- 改后必须 python3 -m py_compile 语法检查
- 出现循环报错超过2次，停下来让用户用终端直接修

## 回滚策略

- 出问题先 git diff 看改了什么
- 单个提交回滚：git revert <hash>
- 多个提交回滚：git revert hash1 hash2 hash3 --no-edit
- 不要用 git reset（会丢失提交历史）
