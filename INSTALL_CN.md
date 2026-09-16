# openbrep v0.10.8 安装指南（中文）

> 针对设计师用户的分步骤安装教程
> 当前正式版本：v0.10.8
> 难度：⭐️ 小白可用

---

## 先选安装方式

OpenBrep 现在按用户类型分三条安装路径。普通用户不要从 `git clone` 开始。

| 用户类型 | 推荐方式 | 适合原因 |
|---|---|---|
| 建筑师 / 设计师 | 下载 GitHub Release 桌面包 | 不需要先学 Git、Python、pip |
| 命令行用户 | `pipx` / `uv tool` | 隔离依赖，升级清晰 |
| 开发者 / 贡献者 | `git clone` + `bash install.sh` | 方便改源码、跑测试、提交 PR |

### 推荐方式：GitHub Release 桌面包

访问：

```text
https://github.com/byewind1/openbrep/releases/latest
```

下载对应系统的安装包（v0.9.0 起为 Tauri 桌面安装包，具体文件名以 Release 页面为准）：

- macOS：`OpenBrep_0.10.8_aarch64.dmg`（Apple Silicon）或 `OpenBrep_0.10.8_x64.dmg`（Intel）
- Windows：`OpenBrep_0.10.8_x64_en-US.msi` 或 `OpenBrep_0.10.8_x64-setup.exe`

v0.9.1 起安装包内嵌 Python 后端（PyInstaller sidecar），下载安装即可用，不需要本机 Python 环境或源码。

当前 macOS 包兼容性：Apple Silicon 包（`arm64`，M1/M2/M3/M4）与 Intel 包（`x86_64`）均提供，需要 macOS 11 Big Sur 或更高版本（按发布二进制实测 `minos 11.0`）；Intel 构建基于 GitHub `macos-15-intel` runner（官方支持到 2027-08）。

macOS 打开 dmg 后把 OpenBrep 拖入「应用程序」；Windows 运行 msi / setup.exe 按向导安装。

macOS 第一次运行如果提示来自未知开发者，请在「应用程序」里右键 OpenBrep，选择“打开”，再在系统提示里确认。

如果仍然打不开，通常是因为当前 macOS 包还没有完成 Developer ID 签名和 Apple 公证，Gatekeeper 会把浏览器下载的包标记为隔离。临时处理办法：

```bash
xattr -dr com.apple.quarantine /Applications/OpenBrep.app
```

我们会准备正式签名并公证的 macOS 包，届时不再需要这个手动步骤。

### 命令行方式：pipx / uv

正式发布到 PyPI 后，推荐：

```bash
pipx install "openbrep[ui]"
obr
```

或：

```bash
uv tool install "openbrep[ui]"
obr
```

升级：

```bash
pipx upgrade openbrep
# 或
uv tool upgrade openbrep
```

### 开发者方式：源码安装

如果你需要修改 OpenBrep 本身、跑测试、提交 PR，再使用后文的源码安装方式。

---

## 前置准备：你需要什么

如果你使用 GitHub Release 桌面包，可以先跳过 Python / Git 相关准备；如果桌面包启动失败，再回来看本节排查。

### 1. Python 3.10+ 环境

**不用害怕"Python"这个词。** 它就像 ArchiCAD 一样，是个工具软件。

**检查你是否已装 Python：**

打开你的终端（Mac 叫"终端"，Windows 叫"命令提示符"或"PowerShell"），输入：

```bash
python --version
```

如果看到类似 `Python 3.11.0` 或更高版本，**恭喜，你已经装过了。跳过这一步。**

如果看到 `command not found` 或 `不是内部或外部命令`，说明你需要装 Python。

**装 Python（5 分钟）：**

访问官网下载：https://www.python.org/downloads/

1. 选择最新版本（3.12.x 或 3.11.x）
2. 点击 "Download"
3. **重要**：安装时 **一定要勾选 "Add Python to PATH"**（否则后续命令找不到 Python）
4. 一路 Next → Install

装完后重新打开终端，再试一遍 `python --version` 验证。

---

### 2. 你需要能访问 GitHub（国内特别注意）

**问题：** 国内可能无法直接访问 GitHub

**解决方案 3 选 1：**

#### 方案 A：使用代理（推荐，最简单）

如果你有 VPN 或代理工具（如 Clash, V2rayN 等）：

1. **启动你的 VPN/代理**
2. 在终端里配置 git 使用代理：

```bash
# 如果你用的是 HTTP 代理（如 localhost:7890）
git config --global http.proxy http://127.0.0.1:7890
git config --global https.proxy https://127.0.0.1:7890

# 后续操作完成后，可以取消代理：
git config --global --unset http.proxy
git config --global --unset https.proxy
```

> **不知道代理端口？** 打开你的代理工具，通常会显示 "http 代理: 127.0.0.1:xxxx"，把 xxxx 替换成那个端口号。

#### 方案 B：用国内镜像（无需 VPN）

用 gitee（国内平台）的镜像：

```bash
git clone https://gitee.com/byewind/openbrep.git
cd openbrep
```

这样完全不需要 VPN。

#### 方案 C：直接从 GitHub 下载 ZIP（无需 git）

如果上面都觉得麻烦，可以：

1. 开启 VPN
2. 访问 https://github.com/byewind1/openbrep
3. 点绿色 "Code" 按钮 → "Download ZIP"
4. 解压到你喜欢的文件夹
5. 跳过下面的 `git clone` 步骤

---

## 源码分步安装（开发者 / 备用方案）

### Step 1：获取代码（3 种方法选 1）

**选方案 A 或 B（推荐，用 git 克隆）：**

打开终端，输入：

```bash
git clone https://github.com/byewind1/openbrep.git
cd openbrep
```

或国内镜像：

```bash
git clone https://gitee.com/byewind/openbrep.git
cd openbrep
```

**选方案 C（下载 ZIP）：**

1. 解压 ZIP 文件到你喜欢的文件夹（如 `~/Documents/openbrep`）
2. 在终端里进入这个文件夹：

```bash
cd ~/Documents/openbrep
```

> **"cd" 是什么意思？** 它的意思是"进入这个文件夹"。后面所有命令都要在这个文件夹里运行。

---

### Step 2：安装依赖（2 分钟，最容易出错的步骤）

在 openbrep 文件夹内，运行：

```bash
bash install.sh
```

**会发生什么：**

- 终端会显示一堆文字，下载 Streamlit、litellm 等工具
- 这很正常，**不要关闭终端**，耐心等待（2-3 分钟）
- 最后会显示 `Successfully installed ...`

**常见问题 1：pip 找不到？**

试试：

```bash
python -m pip install -e ".[ui]"
```

**常见问题 2：国内网络太慢？**

使用清华源加速（添加 `-i` 参数）：

```bash
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -e ".[ui]"
```

**常见问题 3：streamlit-ace 安装失败？**

这个包有时候在某些系统上比较挑，可以先装基础版本（去掉代码高亮，功能不受影响）：

```bash
pip install -e ".[ui]" --no-deps
pip install streamlit litellm click rich tomli
```

---

### Step 3：配置 LLM（API Key）

openbrep 需要调用 AI（如 Claude、GPT-4）。

**选一个 LLM 服务：**

| 服务 | 费用 | 中国用户 | 推荐度 |
|---|---|---|---|
| Claude（Anthropic） | 按量付费，$0.003/1K token | 需梯子但稳定 | ⭐️⭐️⭐️⭐️⭐️ |
| GPT-4o（OpenAI） | 按量付费 | 需梯子 | ⭐️⭐️⭐️⭐️ |
| 智谱 GLM-4 | 免费额度 + 按量 | 无需梯子✓ | ⭐️⭐️⭐️ |
| DeepSeek | 便宜 | 无需梯子✓ | ⭐️⭐️⭐️ |

**推荐方案（无需梯子）：用 智谱 GLM-4**

1. 访问 https://open.bigmodel.cn/
2. 注册账户，实名认证
3. 点击 "API 密钥" → 创建新密钥
4. 复制那个长密钥（保管好，不要分享）

---

### Step 4：启动应用

#### 稳定入口：React 工作台

在 openbrep 文件夹内，运行：

```bash
obr
```

**会发生什么：**

1. 终端同时启动本地 Python API 和 React 工作台，并自动打开浏览器
2. 终端会打印实际地址，例如：

```text
[obr7] OpenBrep Workbench: http://127.0.0.1:5174
[obr7] API: http://127.0.0.1:8765
```

3. 如果浏览器没自动打开，手动访问终端里打印的 Workbench 地址

`obr` 适合直接打开 HSF 项目、编辑 `3d.gdl` / `2d.gdl` / XML、保存回磁盘、
运行编译、查看诊断和预览。Streamlit 界面已从 v0.9.0 起完全移除。

常用选项：

```bash
./obr7 --no-open
./obr7 --api-port 8770 --web-port 5180
OBR7_API_PORT=8770 OBR7_WEB_PORT=5180 ./obr7
```

默认端口被占用时，`obr7` 会自动选择附近可用端口；但如果你显式设置
`--api-port`、`--web-port`、`OBR7_API_PORT` 或 `OBR7_WEB_PORT`，这些端口就是
严格指定，端口被占用时会直接报错。遇到端口占用，可以换一个显式端口，或先用：

```bash
lsof -nP -iTCP:8765 -sTCP:LISTEN
kill <PID>
```

#### 后台服务模式：`obr serve`（Archicad Copilot 面板依赖）

```bash
obr serve             # 后台常驻：单端口 8765，服务 frontend/dist 构建产物
obr serve --status    # 查看后台运行状态
obr serve --stop      # 停止后台服务
```

`obr serve` 是 `scripts/obr7.py --tauri --daemon` 的薄封装（单端口、静态前端、
后台常驻，日志 `~/.openbrep/logs/obr7.log`）。Archicad 的 Copilot 面板通过
`http://localhost:8765/?mode=copilot` 加载此服务，打开面板前请先执行 `obr serve`；
若提示前端构建产物缺失，先执行 `cd frontend && npm run build`。

---

## 首次使用：3 分钟上手

### 1. 在侧边栏选择 LLM 模型

下拉菜单选 "glm-4.7"（或你申请的服务对应的模型）

### 2. 填入 API Key

把刚才复制的密钥粘到 "API Key" 输入框，按回车保存

### 3. 测试一下

在右侧对话框输入：

```
做一个简单的立方体。宽 500mm，高 500mm，深 500mm。材质默认 Wood。
```

按 Enter，看看 AI 是否生成了 GDL 代码。

**如果成功，你会看到：**

- 编辑器栏的脚本框自动填充了代码
- 参数表出现了 w, h, d, mat 几个参数

**恭喜！你已经可以用了！**

---

## 常见问题排查

### Q：启动时报错端口 8765 已被占用

**A：** 说明有旧的 `obr` / `obr7` 进程还在占着端口。先检查：

```bash
lsof -iTCP:8765 -sTCP:LISTEN
```

找到 PID 后关闭旧进程，再重新运行：

```bash
kill <PID>
obr
```

---

### Q：启动时报错 "ModuleNotFoundError"

**A：** 说明依赖没装成功。回到 Step 2，再跑一遍：

```bash
pip install -e "."
```

如果还是失败，用清华源：

```bash
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -e "."
```

---

### Q：侧边栏找不到模型下拉菜单

**A：** 页面没有完全加载。刷新浏览器（Cmd+R / Ctrl+R）。

---

### Q：输入提示词后，AI 没反应或报错 "API Key invalid"

**A：** 检查：

1. 侧边栏的 API Key 是否正确粘贴了？（不要有多余的空格）
2. 是否选了正确的模型？（用 Claude 的 Key 要选 Claude 模型）
3. 网络是否正常？（如果用 Claude，需要 VPN）

---

### Q：代码生成了，但编译 GSM 失败

**A：** 这说明你还没配置 LP_XMLConverter（ArchiCAD 的编译工具）。

**目前可以先用 Mock 模式测试**（不需要 ArchiCAD）。真实编译的设置见 [用户手册第 9.2 节](docs/manual.md#92-lp_xmlconverter-配置)。

---

### Q：streamlit-ace 编辑框显示为空白

**A：** streamlit-ace 包没装对。运行：

```bash
pip install streamlit-ace
```

重启应用。如果还是不行，功能会自动降级到普通文本框，不影响使用。

---

## 下一步

安装成功后，建议：

1. **读一遍 [README.md](README.md)** 了解功能全景
2. **按照 [用户手册](docs/manual.md) 的"工作流 A"** 尝试从零创建一个简单构件
3. **遇到问题时，查阅手册的"常见问题"章节**

---

## 需要帮助？

- **技术问题**：在 GitHub 提 Issue：https://github.com/byewind1/openbrep/issues
- **功能建议**：用"差评"反馈（每个 AI 消息下方的 👎 按钮）

---

**祝你用得愉快！**

这是一个为建筑师设计师而做的工具。如果遇到什么奇怪的地方，说明可能是我们考虑不周，反馈给我们。
