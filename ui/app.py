"""
gdl-agent Web UI — Streamlit interface for architects.

Run: streamlit run ui/app.py
"""

import sys
import os
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st

from gdl_agent.hsf_project import HSFProject, ScriptType, GDLParameter
from gdl_agent.gdl_parser import parse_gdl_source, parse_gdl_file
from gdl_agent.paramlist_builder import build_paramlist_xml, validate_paramlist
from gdl_agent.compiler import MockHSFCompiler, HSFCompiler, CompileResult
from gdl_agent.core import GDLAgent, Status
from gdl_agent.knowledge import KnowledgeBase
from gdl_agent.skills_loader import SkillsLoader


# ── Page Config ───────────────────────────────────────────

st.set_page_config(
    page_title="gdl-agent",
    page_icon="🏗️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ────────────────────────────────────────────

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=Noto+Sans+SC:wght@300;400;600&display=swap');

.stApp { font-family: 'Noto Sans SC', sans-serif; }
code, .stCodeBlock { font-family: 'JetBrains Mono', monospace !important; }

.main-header {
    font-family: 'JetBrains Mono', monospace;
    font-size: 2rem; font-weight: 600;
    background: linear-gradient(135deg, #22d3ee, #34d399);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    margin-bottom: 0;
}
.sub-header { color: #94a3b8; font-size: 0.9rem; margin-top: -0.5rem; margin-bottom: 2rem; }
</style>
""", unsafe_allow_html=True)


# ── Session State ─────────────────────────────────────────

if "project" not in st.session_state:
    st.session_state.project = None
if "compile_log" not in st.session_state:
    st.session_state.compile_log = []
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "work_dir" not in st.session_state:
    st.session_state.work_dir = str(Path.home() / "gdl-agent-workspace")


# ── Sidebar Config ────────────────────────────────────────

with st.sidebar:
    st.markdown('<p class="main-header">gdl-agent</p>', unsafe_allow_html=True)
    st.markdown('<p class="sub-header">v0.4.0 · HSF-native · AI-powered</p>', unsafe_allow_html=True)
    st.divider()

    st.subheader("📁 工作目录")
    work_dir = st.text_input("Work Directory", value=st.session_state.work_dir, label_visibility="collapsed")
    st.session_state.work_dir = work_dir

    st.divider()
    st.subheader("🔧 编译器 / Compiler")

    compiler_mode = st.radio(
        "编译模式",
        ["Mock (无需 ArchiCAD)", "LP_XMLConverter (真实编译)"],
        index=0,
    )

    converter_path = ""
    if compiler_mode.startswith("LP"):
        converter_path = st.text_input(
            "LP_XMLConverter 路径",
            placeholder="/Applications/GRAPHISOFT/ArchiCAD 28/LP_XMLConverter",
        )

    st.divider()
    st.subheader("🧠 AI 模型 / LLM")

    model_name = st.selectbox("模型 / Model", [
        # ── Anthropic Claude ──
        "claude-haiku-4-5-20251001",       # 最快最便宜
        "claude-sonnet-4-5-20250929",      # 性价比首选
        "claude-opus-4-5-20250918",        # 强推理
        "claude-opus-4-6",                 # 最强旗舰
        # ── 智谱 GLM (Z.ai) ──
        "glm-4.7",
        "glm-4.7-flash",
        "glm-4-plus",
        "glm-4-flash",
        # ── OpenAI ──
        "gpt-4o",
        "gpt-4o-mini",
        "o3-mini",
        # ── DeepSeek ──
        "deepseek-chat",
        "deepseek-reasoner",
        # ── Google Gemini ──
        "gemini/gemini-2.5-flash",
        "gemini/gemini-2.5-pro",
        # ── Ollama 本地 ──
        "ollama/qwen2.5:14b",
        "ollama/qwen3:8b",
        "ollama/deepseek-coder-v2:16b",
    ])

    api_key = st.text_input("API Key", type="password", help="Ollama 本地模式不需要 Key")

    # API Key guidance
    if "claude" in model_name:
        st.caption("🔑 [获取 Claude API Key →](https://console.anthropic.com/settings/keys)")
        st.caption("⚠️ API Key 需单独充值，与 Claude Pro 订阅额度无关")
    elif "glm" in model_name:
        st.caption("🔑 [获取智谱 API Key →](https://bigmodel.cn/usercenter/apikeys)")
    elif "gpt" in model_name or "o3" in model_name:
        st.caption("🔑 [获取 OpenAI API Key →](https://platform.openai.com/api-keys)")
    elif "deepseek" in model_name and "ollama" not in model_name:
        st.caption("🔑 [获取 DeepSeek API Key →](https://platform.deepseek.com/api_keys)")
    elif "gemini" in model_name:
        st.caption("🔑 [获取 Gemini API Key →](https://aistudio.google.com/apikey)")
    elif "ollama" in model_name:
        st.caption("🖥️ 本地运行，无需 Key。确保 Ollama 已启动。")

    # Provider-specific base URL
    api_base = ""
    if "glm" in model_name:
        api_base = st.text_input("API Base URL", value="https://open.bigmodel.cn/api/paas/v4")
    elif "deepseek" in model_name and "ollama" not in model_name:
        api_base = st.text_input("API Base URL", value="https://api.deepseek.com/v1")
    elif "ollama" in model_name:
        api_base = st.text_input("Ollama URL", value="http://localhost:11434")

    max_retries = st.slider("最大重试次数", 1, 10, 5)

    st.divider()

    # Project info
    if st.session_state.project:
        proj = st.session_state.project
        st.subheader(f"📦 {proj.name}")
        st.caption(f"参数: {len(proj.parameters)} | 脚本: {len(proj.scripts)}")


# ── Helper Functions ──────────────────────────────────────

def get_compiler():
    if compiler_mode.startswith("Mock"):
        return MockHSFCompiler()
    return HSFCompiler(converter_path or None)

def get_llm():
    """Create LLM adapter from sidebar config."""
    from gdl_agent.config import LLMConfig
    from gdl_agent.llm import LLMAdapter

    config = LLMConfig(
        model=model_name,
        api_key=api_key,
        api_base=api_base,
        temperature=0.2,
        max_tokens=4096,
    )
    return LLMAdapter(config)

def load_knowledge():
    kb_dir = Path(st.session_state.work_dir) / "knowledge"
    if not kb_dir.exists():
        # Fallback to project's knowledge dir
        kb_dir = Path(__file__).parent.parent / "knowledge"
    kb = KnowledgeBase(str(kb_dir))
    kb.load()
    return kb.get_all()

def load_skills():
    sk_dir = Path(st.session_state.work_dir) / "skills"
    if not sk_dir.exists():
        sk_dir = Path(__file__).parent.parent / "skills"
    sl = SkillsLoader(str(sk_dir))
    sl.load()
    return sl


# ── Main Tabs ─────────────────────────────────────────────

tab_chat, tab_create, tab_edit, tab_compile, tab_log = st.tabs([
    "💬 AI 对话",
    "🏗️ 创建/导入",
    "📝 编辑",
    "🔧 编译",
    "📋 日志",
])


# ══════════════════════════════════════════════════════════
#  Tab: AI Chat — THE CORE FEATURE
# ══════════════════════════════════════════════════════════

with tab_chat:
    st.subheader("💬 AI 对话 — 用自然语言创建和修改 GDL 对象")

    if not st.session_state.project:
        st.info(
            "⬇️ 请先在「创建/导入」Tab 创建一个项目，或者直接在下方描述你想创建的对象。"
        )
        # Quick create from chat
        quick_create = st.text_input(
            "快速创建 / Quick Create",
            placeholder="输入对象名称，如：MyBookshelf",
            key="quick_create",
        )
        if quick_create and st.button("创建并开始对话", type="primary"):
            proj = HSFProject.create_new(quick_create, work_dir=st.session_state.work_dir)
            st.session_state.project = proj
            st.rerun()

    else:
        proj = st.session_state.project

        # Display current project state
        with st.expander(f"📦 当前项目: {proj.name}", expanded=False):
            st.code(proj.summary(), language="text")

        # Chat history display
        for msg in st.session_state.chat_history:
            if msg["role"] == "user":
                st.chat_message("user").markdown(msg["content"])
            else:
                st.chat_message("assistant").markdown(msg["content"])

        # Chat input
        user_input = st.chat_input(
            "描述你想做的修改，如：给书架加一个材质参数 shelfMat，应用到 3D 模型的侧板",
        )

        if user_input:
            # Display user message
            st.chat_message("user").markdown(user_input)
            st.session_state.chat_history.append({"role": "user", "content": user_input})

            # Check API key
            if not api_key and "ollama" not in model_name:
                st.chat_message("assistant").error(
                    "❌ 请在左侧边栏填入 API Key 后再试。"
                )
                st.session_state.chat_history.append({
                    "role": "assistant",
                    "content": "❌ 请在左侧边栏填入 API Key。"
                })
            else:
                # Run Agent
                with st.chat_message("assistant"):
                    status_area = st.empty()
                    detail_area = st.empty()

                    events = []
                    def on_event(event_type, data):
                        events.append((event_type, data))
                        if event_type == "analyze":
                            scripts = data.get("affected_scripts", [])
                            status_area.info(f"🔍 分析中... 影响脚本: {', '.join(scripts)}")
                        elif event_type == "attempt":
                            status_area.info(f"🧠 第 {data['attempt']} 次尝试，调用 AI...")
                        elif event_type == "compile_start":
                            status_area.info("🔧 编译中...")
                        elif event_type == "compile_error":
                            detail_area.warning(
                                f"⚠️ 第 {data['attempt']} 次编译失败: {data['error'][:200]}"
                            )
                        elif event_type == "success":
                            status_area.success(
                                f"✅ 成功！第 {data['attempt']} 次尝试编译通过。"
                            )

                    try:
                        llm = get_llm()
                        compiler = get_compiler()
                        knowledge = load_knowledge()
                        skills_loader = load_skills()
                        skills_text = skills_loader.get_for_task(user_input)

                        output_gsm = str(
                            Path(st.session_state.work_dir) / "output" / f"{proj.name}.gsm"
                        )

                        agent = GDLAgent(
                            llm=llm,
                            compiler=compiler,
                            max_iterations=max_retries,
                            on_event=on_event,
                        )

                        result = agent.run(
                            instruction=user_input,
                            project=proj,
                            output_gsm=output_gsm,
                            knowledge=knowledge,
                            skills=skills_text,
                        )

                        # Format result message
                        mock_tag = " [Mock]" if compiler_mode.startswith("Mock") else ""
                        if result.status == Status.SUCCESS:
                            msg = (
                                f"✅ **编译成功{mock_tag}** — 第 {result.attempts} 次尝试\n\n"
                                f"📦 输出: `{result.output_path}`\n\n"
                                f"参数: {len(proj.parameters)} | "
                                f"脚本: {', '.join(st_type.value for st_type in proj.scripts)}"
                            )
                            if compiler_mode.startswith("Mock"):
                                msg += "\n\n⚠️ Mock 模式不生成真实 .gsm，切换到 LP_XMLConverter 模式进行真实编译。"
                        elif result.status == Status.FAILED:
                            msg = f"❌ **失败**: {result.error_summary}"
                        elif result.status == Status.EXHAUSTED:
                            msg = (
                                f"⚠️ **{result.attempts} 次尝试后仍未成功**\n\n"
                                f"最后错误: {result.error_summary[:300]}\n\n"
                                f"建议: 换一种描述方式，或手动在「编辑」Tab 修改代码。"
                            )
                        else:
                            msg = f"⛔ 任务被阻止: {result.error_summary}"

                        status_area.empty()
                        detail_area.empty()
                        st.markdown(msg)

                        st.session_state.chat_history.append({
                            "role": "assistant", "content": msg
                        })

                        # Log
                        st.session_state.compile_log.append({
                            "project": proj.name,
                            "instruction": user_input,
                            "success": result.status == Status.SUCCESS,
                            "attempts": result.attempts,
                            "message": result.error_summary or "Success",
                        })

                    except Exception as e:
                        error_msg = f"❌ **错误**: {str(e)}"
                        status_area.empty()
                        st.error(error_msg)
                        st.session_state.chat_history.append({
                            "role": "assistant", "content": error_msg
                        })

        # Clear chat button
        if st.session_state.chat_history:
            if st.button("🗑️ 清除对话", key="clear_chat"):
                st.session_state.chat_history = []
                st.rerun()


# ══════════════════════════════════════════════════════════
#  Tab: Create / Import
# ══════════════════════════════════════════════════════════

with tab_create:
    st.subheader("创建新对象或导入现有文件")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("### 📄 从 .gdl 文件导入")
        uploaded_file = st.file_uploader(
            "拖入 .gdl 文件",
            type=["gdl", "txt"],
            help="支持 AI 生成或手写的 GDL 源码",
        )
        if uploaded_file:
            content = uploaded_file.read().decode("utf-8", errors="replace")
            name = Path(uploaded_file.name).stem
            try:
                project = parse_gdl_source(content, name)
                project.work_dir = Path(st.session_state.work_dir)
                project.root = project.work_dir / project.name
                st.session_state.project = project
                st.success(f"✅ 导入成功: {project.name}")
                st.code(project.summary(), language="text")
            except Exception as e:
                st.error(f"❌ 导入失败: {e}")

    with col2:
        st.markdown("### ✨ 创建新对象")
        new_name = st.text_input("对象名称", value="MyObject")
        ac_version = st.selectbox(
            "ArchiCAD 版本",
            [("AC 25", 44), ("AC 26", 45), ("AC 27", 46), ("AC 28", 47)],
            index=2,
            format_func=lambda x: x[0],
        )
        if st.button("创建", type="primary"):
            project = HSFProject.create_new(
                new_name,
                work_dir=st.session_state.work_dir,
                ac_version=ac_version[1],
            )
            st.session_state.project = project
            st.success(f"✅ 创建成功: {project.name}")
            st.code(project.summary(), language="text")

    st.divider()
    st.markdown("### 📦 从 .gsm 文件导入")
    st.info(
        "拖入 .gsm → LP_XMLConverter 自动解压为 HSF 目录 → 即可编辑。"
        "需要在侧边栏配置 LP_XMLConverter 路径，并选择「真实编译」模式。"
    )


# ══════════════════════════════════════════════════════════
#  Tab: Edit
# ══════════════════════════════════════════════════════════

with tab_edit:
    if not st.session_state.project:
        st.info("请先创建或导入一个项目")
    else:
        proj = st.session_state.project
        st.subheader(f"编辑: {proj.name}")

        # Parameters
        st.markdown("### 📊 参数列表")
        param_data = []
        for p in proj.parameters:
            param_data.append({
                "Type": p.type_tag,
                "Name": p.name,
                "Value": p.value,
                "Description": p.description,
                "Fixed": "✓" if p.is_fixed else "",
            })
        if param_data:
            st.dataframe(param_data, use_container_width=True, hide_index=True)

        with st.expander("➕ 添加参数"):
            pc1, pc2, pc3, pc4 = st.columns(4)
            with pc1:
                p_type = st.selectbox("Type", [
                    "Length", "Integer", "Boolean", "RealNum", "Angle",
                    "String", "Material", "FillPattern", "LineType", "PenColor",
                ])
            with pc2:
                p_name = st.text_input("Name", value="bNewParam")
            with pc3:
                p_value = st.text_input("Value", value="0")
            with pc4:
                p_desc = st.text_input("Description")
            if st.button("添加"):
                try:
                    proj.add_parameter(GDLParameter(p_name, p_type, p_desc, p_value))
                    st.success(f"✅ {p_type} {p_name}")
                    st.rerun()
                except Exception as e:
                    st.error(str(e))

        st.divider()

        # Scripts
        st.markdown("### 📝 脚本")
        script_tabs = st.tabs(["3D", "2D", "Master", "Param", "UI", "Properties"])
        script_map = [
            (ScriptType.SCRIPT_3D, "3d.gdl"),
            (ScriptType.SCRIPT_2D, "2d.gdl"),
            (ScriptType.MASTER, "1d.gdl"),
            (ScriptType.PARAM, "vl.gdl"),
            (ScriptType.UI, "ui.gdl"),
            (ScriptType.PROPERTIES, "pr.gdl"),
        ]
        for tab, (stype, fname) in zip(script_tabs, script_map):
            with tab:
                current = proj.get_script(stype)
                new_content = st.text_area(
                    fname, value=current, height=300, key=f"script_{fname}",
                )
                if new_content != current:
                    proj.set_script(stype, new_content)

        if st.button("🔍 验证参数"):
            issues = validate_paramlist(proj.parameters)
            if issues:
                for i in issues:
                    st.warning(i)
            else:
                st.success("✅ 参数验证通过")


# ══════════════════════════════════════════════════════════
#  Tab: Compile
# ══════════════════════════════════════════════════════════

with tab_compile:
    if not st.session_state.project:
        st.info("请先创建或导入一个项目")
    else:
        proj = st.session_state.project
        st.subheader(f"编译: {proj.name}")

        output_name = st.text_input("输出文件名", value=f"{proj.name}.gsm")

        col_c, col_p = st.columns([1, 1])

        with col_c:
            if st.button("🔧 编译", type="primary"):
                with st.spinner("写入 HSF..."):
                    try:
                        hsf_dir = proj.save_to_disk()
                    except Exception as e:
                        st.error(f"写入失败: {e}")
                        st.stop()

                output_path = str(Path(st.session_state.work_dir) / "output" / output_name)

                with st.spinner("编译中..."):
                    compiler = get_compiler()
                    result = compiler.hsf2libpart(str(hsf_dir), output_path)

                if result.success:
                    if compiler_mode.startswith("Mock"):
                        st.success(
                            f"✅ **[Mock]** 结构验证通过！\n\n"
                            f"Mock 模式不生成真实 .gsm 文件。切换到「LP_XMLConverter」模式进行真实编译。\n\n"
                            f"📁 HSF 目录已写入: `{hsf_dir}`"
                        )
                    else:
                        st.success(f"✅ 编译成功！\n\n📦 `{result.output_path}`")
                else:
                    st.error(f"❌ 编译失败\n\n```\n{result.stderr}\n```")

                st.session_state.compile_log.append({
                    "project": proj.name,
                    "instruction": "(manual compile)",
                    "success": result.success,
                    "attempts": 1,
                    "message": result.stderr or "Success",
                })

        with col_p:
            st.markdown("### 预览")
            with st.expander("paramlist.xml"):
                st.code(build_paramlist_xml(proj.parameters), language="xml")
            with st.expander("HSF 目录", expanded=True):
                tree = [f"📁 {proj.name}/", "  ├── libpartdata.xml",
                        "  ├── paramlist.xml", "  ├── ancestry.xml", "  └── scripts/"]
                for stype in ScriptType:
                    if stype in proj.scripts:
                        n = proj.scripts[stype].count("\n") + 1
                        tree.append(f"       ├── {stype.value} ({n} lines)")
                st.code("\n".join(tree), language="text")


# ══════════════════════════════════════════════════════════
#  Tab: Log
# ══════════════════════════════════════════════════════════

with tab_log:
    st.subheader("操作日志")
    if not st.session_state.compile_log:
        st.info("暂无记录")
    else:
        for entry in reversed(st.session_state.compile_log):
            icon = "✅" if entry["success"] else "❌"
            instr = entry.get("instruction", "")
            st.markdown(f"**{icon} {entry['project']}** — {instr}")
            if entry.get("attempts", 0) > 1:
                st.caption(f"尝试 {entry['attempts']} 次")
            st.code(entry["message"], language="text")
            st.divider()

    if st.button("清除日志"):
        st.session_state.compile_log = []
        st.rerun()


# ── Footer ────────────────────────────────────────────────
st.divider()
st.markdown(
    '<p style="text-align:center; color:#64748b; font-size:0.8rem;">'
    'gdl-agent v0.4.0 · HSF-native · '
    '<a href="https://github.com/byewind/gdl-agent">GitHub</a>'
    '</p>',
    unsafe_allow_html=True,
)
