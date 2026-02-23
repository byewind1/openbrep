# 🧪 openbrep 仿真测试完全指南（macOS版）

> **目标**：在一个全新的环境里，模拟普通建筑师的使用场景，验证工具能否正常工作。

---

## 🎯 测试原则

**我们要模拟的场景**：
- ❌ 不是测试"代码能否运行"
- ✅ 是测试"安装好的软件能否被用户使用"

**关键规则**：
- **绝对不要** Copy 整个源代码目录
- **只** Copy 必要的配置文件（API Key）
- 在一个"干净"的文件夹里测试

---

## 📋 准备工作清单

在开始前，确认以下条件：

- [ ] 你已经在开发目录执行过 `pip install -e .`
- [ ] 你的开发目录里有 `.env` 或 `config.toml` 文件（包含 API Key）
- [ ] 你知道自己用的是哪个 API（智谱/Claude/OpenAI）

---

## 🚀 第一步：找到你的 API Key

### 方案A：从 `.env` 文件复制（如果你用 .env）

1. 打开 **Finder（访达）**
2. 按 `Command + Shift + G`，输入你的源码目录路径，比如：
   ```
   ~/Projects/openbrep
   ```
3. 按 `Command + Shift + .`（显示隐藏文件）
4. 找到 `.env` 文件，**右键 → 打开方式 → 文本编辑**
5. 你会看到类似这样的内容：
   ```
   ZHIPU_API_KEY=your-key-here-xxxxxxxxxxxx
   ```
6. **复制整个文件内容**（`Cmd + A` 全选，`Cmd + C` 复制）

### 方案B：从 `config.toml` 文件复制（如果你用 toml）

1. 同样方法打开源码目录
2. 找到 `config.toml` 文件，右键打开
3. 找到 `api_key = "..."` 这一行
4. **记住这个 Key**（或者复制整个文件）

---

## 🧪 第二步：建立测试环境

### 2.1 创建测试文件夹

1. 打开 **Finder**
2. 在 **桌面** 上右键 → **新建文件夹**
3. 命名为 `Test_GDL_Agent`
4. **双击进入这个文件夹**（确保你在里面）

### 2.2 保存 API Key

**如果你用 `.env`**：

1. 右键空白处 → **新建文件**（或者按 `Command + N`）
2. 在弹出的窗口选 **文本文件**
3. 粘贴刚才复制的内容
4. **保存为** `.env`（注意前面有个点）
   - 文件名：`.env`
   - 位置：`Test_GDL_Agent` 文件夹
   - 格式：纯文本

**如果你用 `config.toml`**：

1. 同样方法新建文本文件
2. 粘贴 `config.toml` 的内容
3. 保存为 `config.toml`

### 2.3 确认文件存在

在 Finder 里按 `Command + Shift + .`，确保你能看到：
```
Test_GDL_Agent/
└── .env  (或 config.toml)
```

**只有这一个文件！如果看到其他文件，删掉！**

---

## 🔥 第三步：打开终端

1. 按 `Command + Space`
2. 输入 `Terminal`
3. 按回车，打开终端

---

## 🧭 第四步：进入测试目录

在终端窗口，**复制粘贴**以下命令并回车：

```bash
cd ~/Desktop/Test_GDL_Agent
```

**验证**：输入 `pwd` 并回车，应该显示：
```
/Users/你的用户名/Desktop/Test_GDL_Agent
```

---

## ⚡ 第五步：执行测试命令

### 测试1：最简单的立方体

复制粘贴这个命令并回车：

```bash
openbrep run "生成一个立方体，边长1米"
```

**预期输出**：
```
🚀 GDL Agent
   Task: 生成一个立方体，边长1米
   File: output/cube.xml
   Max retries: 5

─── Attempt 1/5 ───
🧠 Calling LLM...
✓ Validation passed
🔧 Compiling...
✅ Compiled! (320ms)

┌─ Result ─────────────────────────────────────────┐
│ ✅ Compiled successfully                          │
│    Output: output/cube.gsm                        │
└──────────────────────────────────────────────────┘
```

---

## 🔍 第六步：检查输出结果

### 6.1 回到 Finder

在 Finder 里刷新 `Test_GDL_Agent` 文件夹。

**你应该看到**：
```
Test_GDL_Agent/
├── .env (或 config.toml)
└── output/
    └── cube.gsm  (或 cube.xml)
```

### 6.2 验证文件大小

右键 `cube.gsm` → **显示简介**

**正常情况**：
- 文件大小：5KB - 50KB
- 类型：GSM 文档（或 XML 文档）

**异常情况**：
- 文件大小：0 字节 → 编译失败
- 文件不存在 → 命令没执行成功

---

## ✅ 成功的标志

如果你看到：
1. ✅ 终端显示 `✅ Compiled successfully`
2. ✅ `output/cube.gsm` 文件存在
3. ✅ 文件大小不是 0

**恭喜！测试通过！** 🎉

你的工具已经可以在任何目录下工作，不依赖源代码。

---

## ❌ 常见问题与解决方案

### 问题1：命令找不到

**错误提示**：
```
zsh: command not found: openbrep
```

**原因**：pip 安装没生效或路径没刷新。

**解决方法**：

```bash
# 方法1：刷新 shell
rehash

# 方法2：重新安装
cd ~/Projects/openbrep  # 回到源码目录
pip install -e .

# 方法3：检查安装位置
which openbrep
```

如果 `which openbrep` 没输出，说明没装成功。

---

### 问题2：找不到 API Key

**错误提示**：
```
Error: No ZHIPU_API_KEY found in environment
```

**原因**：`.env` 文件没被读取。

**解决方法**：

**临时解决**（立即测试）：
```bash
export ZHIPU_API_KEY="你的-api-key-这里"
openbrep run "生成一个立方体"
```

**永久解决**（修复代码）：
1. 检查你的代码是否有 `load_dotenv()`
2. 或者在测试目录创建 `config.toml`：
   ```toml
   [llm]
   api_key = "你的-api-key"
   model = "glm-4-flash"
   ```

---

### 问题3：编译失败

**错误提示**：
```
❌ Compile error: LP_XMLConverter not found
```

**原因**：找不到 ArchiCAD 的编译器。

**解决方法**：

```bash
# 检查编译器是否存在
which LP_XMLConverter

# 如果没有，手动指定路径（在 config.toml 里）
[compiler]
path = "/Applications/GRAPHISOFT/ARCHICAD 27/LP_XMLConverter"
```

---

### 问题4：权限被拒绝

**错误提示**：
```
PermissionError: [Errno 13] Permission denied: 'output/'
```

**解决方法**：

```bash
# 给测试目录赋权
chmod -R 755 ~/Desktop/Test_GDL_Agent

# 或者换个目录重新测试
cd ~/Documents
mkdir Test_GDL_Agent_v2
cd Test_GDL_Agent_v2
```

---

## 🧪 进阶测试：复杂案例

如果简单测试通过了，试试这个：

```bash
openbrep run "创建一个参数化门，宽度A，高度B，厚度50mm，包含门框和门板"
```

**预期时间**：30秒 - 2分钟（取决于模型速度）

**验证标准**：
1. 生成的 `.gsm` 文件大于 10KB
2. 用 ArchiCAD 打开能看到门的 3D 模型
3. 参数界面有 A、B 参数

---

## 📸 测试截图保存

如果测试成功，建议保存截图：

1. 终端的成功输出（`Cmd + Shift + 4` 截图）
2. Finder 里的文件列表
3. ArchiCAD 里打开的效果（如果有）

这些截图可以用在：
- GitHub README
- 公众号文章
- 演示视频

---

## 🎯 测试完成后清理

```bash
# 回到测试目录
cd ~/Desktop/Test_GDL_Agent

# 查看生成了什么
ls -la

# 如果要重新测试，删除 output 文件夹
rm -rf output

# 如果测试完成，删除整个测试目录
cd ~/Desktop
rm -rf Test_GDL_Agent
```

---

## 📝 测试报告模板

测试完成后，记录以下信息：

```
【测试环境】
- macOS 版本: 14.2
- Python 版本: 3.11.5
- openbrep 版本: 0.1.0
- 测试时间: 2026-02-09

【测试结果】
✅ 命令能找到
✅ API Key 识别正常
✅ 生成立方体成功（8KB）
✅ 生成参数化门成功（23KB）

【遇到的问题】
- 初次运行时 LP_XMLConverter 路径报错
- 解决方法：在 config.toml 手动指定路径

【建议改进】
- 自动检测 ArchiCAD 安装路径
- 如果没有 .env 文件，给出明确提示
```

---

## 🆚 Windows 用户注意事项

如果你用 Windows，主要区别：

### 文件路径
```bash
# macOS
cd ~/Desktop/Test_GDL_Agent

# Windows
cd %USERPROFILE%\Desktop\Test_GDL_Agent
```

### 显示隐藏文件
- macOS: `Command + Shift + .`
- Windows: 文件夹选项 → 查看 → 显示隐藏文件

### 编译器路径
```toml
# macOS
[compiler]
path = "/Applications/GRAPHISOFT/ARCHICAD 27/LP_XMLConverter"

# Windows
[compiler]
path = "C:\\Program Files\\GRAPHISOFT\\ARCHICAD 27\\LP_XMLConverter.exe"
```

---

## 🎓 学到了什么？

通过这个测试，你验证了：

1. ✅ **独立性**：工具不依赖源代码目录
2. ✅ **可移植性**：可以在任何文件夹运行
3. ✅ **配置正确**：API Key 和编译器路径配置有效
4. ✅ **完整性**：从输入到输出的完整流程可用

**这就是真实用户的使用体验！**

---

## 📚 延伸阅读

- [openbrep 完整文档](../README.md)
- [配置文件说明](../docs/configuration.md)
- [常见问题 FAQ](../docs/faq.md)
- [贡献指南](../CONTRIBUTING.md)

---

**最后提醒**：如果测试通过，记得在 GitHub 项目 README 里添加一个 ✅ Tested on macOS 的标识！
