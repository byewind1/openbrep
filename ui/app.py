"""
gdl-agent Web UI — Streamlit interface for architects.

Run: streamlit run ui/app.py
"""

import sys
import re
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

.welcome-card {
    background: linear-gradient(135deg, #0f172a, #1e293b);
    border: 1px solid #334155;
    border-radius: 12px;
    padding: 2rem;
    margin: 1rem 0;
}
.step-item {
    display: flex;
    align-items: flex-start;
    gap: 0.75rem;
    margin-bottom: 1rem;
    padding: 0.75rem;
    background: #1e293b;
    border-radius: 8px;
    border-left: 3px solid #22d3ee;
}
.diff-current { border-left: 3px solid #475569; padding-left: 0.5rem; }
.diff-ai      { border-left: 3px solid #f59e0b; padding-left: 0.5rem; }
.diff-badge {
    display: inline-block;
    background: #f59e0b22;
    color: #f59e0b;
    border: 1px solid #f59e0b55;
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 0.78rem;
    font-family: 'JetBrains Mono', monospace;
    margin-bottom: 4px;
}
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
if "agent_running" not in st.session_state:
    st.session_state.agent_running = False
if "pending_diffs" not in st.session_state:
    # AI-proposed changes awaiting user review. {script_path: new_content}
    # e.g. {"scripts/3d.gdl": "...", "scripts/2d.gdl": "..."}
    st.session_state.pending_diffs = {}
if "pending_gsm_name" not in st.session_state:
    st.session_state.pending_gsm_name = ""
if "model_api_keys" not in st.session_state:
    # Per-model API Key storage — pre-fill from config.toml provider_keys
    st.session_state.model_api_keys = {}


# ── Load config.toml defaults ──────────────────────────

_config_defaults = {}
_provider_keys: dict = {}   # {provider: api_key}

try:
    from gdl_agent.config import GDLAgentConfig
    import sys as _sys, os as _os
    # Load raw TOML to get provider_keys nested table
    if _sys.version_info >= (3, 11):
        import tomllib as _tomllib
    else:
        import tomli as _tomllib   # type: ignore

    _toml_path = _os.path.join(_os.path.dirname(__file__), "..", "config.toml")
    if _os.path.exists(_toml_path):
        with open(_toml_path, "rb") as _f:
            _raw = _tomllib.load(_f)
        _provider_keys = _raw.get("llm", {}).get("provider_keys", {})

    _config = GDLAgentConfig.load()
    _config_defaults = {
        "llm_model": _config.llm.model,
        "compiler_path": _config.compiler.path or "",
    }
except Exception:
    pass


def _key_for_model(model: str) -> str:
    """Pick the right API Key from provider_keys based on model name."""
    m = model.lower()
    if "glm" in m:
        return _provider_keys.get("zhipu", "")
    elif "deepseek" in m and "ollama" not in m:
        return _provider_keys.get("deepseek", "")
    elif "claude" in m:
        return _provider_keys.get("anthropic", "")
    elif "gpt" in m or "o3" in m or "o1" in m:
        return _provider_keys.get("openai", "")
    elif "gemini" in m:
        return _provider_keys.get("google", "")
    return ""

# ── Sidebar Config ────────────────────────────────────────

with st.sidebar:
    st.markdown('<p class="main-header">gdl-agent</p>', unsafe_allow_html=True)
    st.markdown('<p class="sub-header">v0.5.0 · HSF-native · AI-powered</p>', unsafe_allow_html=True)
    st.divider()

    st.subheader("📁 工作目录")
    work_dir = st.text_input("Work Directory", value=st.session_state.work_dir, label_visibility="collapsed")
    st.session_state.work_dir = work_dir

    st.divider()
    st.subheader("🔧 编译器 / Compiler")

    compiler_mode = st.radio(
        "编译模式",
        ["Mock (无需 ArchiCAD)", "LP_XMLConverter (真实编译)"],
        index=1 if _config_defaults.get("compiler_path") else 0,
    )

    converter_path = ""
    if compiler_mode.startswith("LP"):
        converter_path = st.text_input(
            "LP_XMLConverter 路径",
            value=_config_defaults.get("compiler_path", ""),
            placeholder="/Applications/GRAPHISOFT/ArchiCAD 28/LP_XMLConverter",
        )

    st.divider()
    st.subheader("🧠 AI 模型 / LLM")

    model_options = [
        # ── Anthropic Claude ──
        "claude-haiku-4-5-20251001",
        "claude-sonnet-4-5-20250929",
        "claude-opus-4-5-20250918",
        "claude-opus-4-6",
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
    ]

    default_model = _config_defaults.get("llm_model", "glm-4.7")
    default_index = model_options.index(default_model) if default_model in model_options else 4

    model_name = st.selectbox("模型 / Model", model_options, index=default_index)

    # Load or initialize API Key for this specific model
    if model_name not in st.session_state.model_api_keys:
        # Auto-fill from config.toml provider_keys
        st.session_state.model_api_keys[model_name] = _key_for_model(model_name)

    api_key = st.text_input(
        "API Key",
        value=st.session_state.model_api_keys.get(model_name, ""),
        type="password",
        help="Ollama 本地模式不需要 Key"
    )

    # Auto-save API Key if user manually edited it
    if api_key != st.session_state.model_api_keys.get(model_name, ""):
        st.session_state.model_api_keys[model_name] = api_key

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

    # API Base URL — only needed for OpenAI-compatible custom endpoints
    # zai/ (GLM), deepseek/, anthropic/ are native LiteLLM providers, no api_base needed
    def _get_default_api_base(model: str) -> str:
        m = model.lower()
        if "ollama" in m:
            return "http://localhost:11434"
        # GLM uses zai/ native provider — no api_base
        # DeepSeek uses deepseek/ native provider — no api_base
        return ""

    default_api_base = _get_default_api_base(model_name)
    api_base = ""
    if default_api_base:
        api_base = st.text_input("API Base URL", value=default_api_base)

    max_retries = st.slider("最大重试次数", 1, 10, 5)

    st.divider()

    # Project info + quick reset
    if st.session_state.project:
        proj = st.session_state.project
        st.subheader(f"📦 {proj.name}")
        st.caption(f"参数: {len(proj.parameters)} | 脚本: {len(proj.scripts)}")
        if st.button("🗑️ 清除项目", use_container_width=True):
            st.session_state.project = None
            st.session_state.chat_history = []
            st.rerun()


# ── Helper Functions ──────────────────────────────────────

def get_compiler():
    if compiler_mode.startswith("Mock"):
        return MockHSFCompiler()
    return HSFCompiler(converter_path or None)

def get_llm():
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

def load_knowledge(task_type: str = "all"):
    kb_dir = Path(st.session_state.work_dir) / "knowledge"
    if not kb_dir.exists():
        kb_dir = Path(__file__).parent.parent / "knowledge"
    kb = KnowledgeBase(str(kb_dir))
    kb.load()
    return kb.get_by_task_type(task_type)

def load_skills():
    sk_dir = Path(st.session_state.work_dir) / "skills"
    if not sk_dir.exists():
        sk_dir = Path(__file__).parent.parent / "skills"
    sl = SkillsLoader(str(sk_dir))
    sl.load()
    return sl

def _versioned_gsm_path(proj_name: str, work_dir: str) -> str:
    """
    Return next available versioned GSM path.
    MyShelf_v1.gsm → MyShelf_v2.gsm → ...
    Preserves all previous compilations.
    """
    out_dir = Path(work_dir) / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    v = 1
    while (out_dir / f"{proj_name}_v{v}.gsm").exists():
        v += 1
    return str(out_dir / f"{proj_name}_v{v}.gsm")


# ── Object Name Extraction (dictionary + regex, no LLM) ──

_CN_TO_NAME = {
    # 家具
    "书架": "Bookshelf", "书柜": "Bookcase", "柜子": "Cabinet", "衣柜": "Wardrobe",
    "桌子": "Table", "桌": "Table", "书桌": "Desk", "餐桌": "DiningTable",
    "椅子": "Chair", "椅": "Chair", "沙发": "Sofa", "床": "Bed",
    "茶几": "CoffeeTable", "电视柜": "TVStand", "鞋柜": "ShoeRack",
    # 建筑构件
    "窗": "Window", "窗框": "WindowFrame", "窗户": "Window", "百叶窗": "Louver",
    "门": "Door", "门框": "DoorFrame", "推拉门": "SlidingDoor", "旋转门": "RevolvingDoor",
    "墙": "Wall", "墙板": "WallPanel", "隔墙": "Partition", "幕墙": "CurtainWall",
    "楼梯": "Staircase", "台阶": "StairStep", "扶手": "Handrail", "栏杆": "Railing",
    "柱": "Column", "柱子": "Column", "梁": "Beam", "板": "Slab",
    "屋顶": "Roof", "天花": "Ceiling", "地板": "Floor",
    # 设备
    "灯": "Light", "灯具": "LightFixture", "管道": "Pipe", "风管": "Duct",
    "开关": "Switch", "插座": "Outlet", "空调": "AirConditioner",
    # 景观
    "花盆": "Planter", "树": "Tree", "围栏": "Fence", "长凳": "Bench",
}

def _extract_object_name(text: str) -> str:
    """
    Extract GDL object name from user input.
    Priority: explicit English name > Chinese keyword dict > fallback.
    Zero LLM calls — instant and 100% reliable.
    """
    # 1. Check for explicit English name: "named MyShelf", "叫 MyShelf"
    for pat in [
        r'named?\s+([A-Za-z][A-Za-z0-9]{2,30})',
        r'called\s+([A-Za-z][A-Za-z0-9]{2,30})',
        r'名为\s*([A-Za-z][A-Za-z0-9]{2,30})',
        r'叫\s*([A-Za-z][A-Za-z0-9]{2,30})',
    ]:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(1)

    # 2. Chinese keyword → English CamelCase (longest match first)
    for cn, en in sorted(_CN_TO_NAME.items(), key=lambda x: len(x[0]), reverse=True):
        if cn in text:
            print(f"[name] '{cn}' → {en}")
            return en

    # 3. Pick first CamelCase English word in text (skip short junk like UI, AI, GDL)
    for word in re.findall(r'[A-Z][a-z]{2,}[A-Za-z0-9]*', text):
        if word not in {"The", "For", "And", "Not", "But", "With"}:
            return word

    return "MyObject"


# ── Welcome / Onboarding Panel ────────────────────────────

def show_welcome():
    st.markdown("""
<div class="welcome-card">
<h2 style="color:#22d3ee; margin-top:0; font-family:'JetBrains Mono';">欢迎使用 gdl-agent 🏗️</h2>
<p style="color:#94a3b8;">用自然语言驱动 ArchiCAD GDL 对象的创建与编译。无需了解 GDL 语法，直接描述需求即可。</p>
</div>
""", unsafe_allow_html=True)

    st.markdown("#### 三步快速开始")

    st.info("**① 配置 API Key**  \n在左侧边栏选择 AI 模型，填入对应 API Key。免费的智谱 GLM 可直接使用。")
    st.info("**② 开始对话**  \n在底部输入框描述你想创建的 GDL 对象，例如：  \n「创建一个宽 600mm、深 400mm 的书架，带 iShelves 参数控制层数」")
    st.info("**③ 编译输出**  \nAI 生成代码后自动触发编译。真实编译需在侧边栏配置 LP_XMLConverter 路径。Mock 模式可验证结构，无需安装 ArchiCAD。")

    st.divider()

    st.markdown("#### 或者：导入已有 GDL 文件")
    uploaded_file = st.file_uploader(
        "拖入 .gdl 文件开始编辑",
        type=["gdl", "txt"],
        help="支持 AI 生成或手写的 GDL 源码",
        key="welcome_upload",
    )
    if uploaded_file:
        content = uploaded_file.read().decode("utf-8", errors="replace")
        name = Path(uploaded_file.name).stem
        try:
            project = parse_gdl_source(content, name)
            project.work_dir = Path(st.session_state.work_dir)
            project.root = project.work_dir / project.name
            st.session_state.project = project
            st.session_state.chat_history.append({
                "role": "assistant",
                "content": f"✅ 已导入 `{project.name}` — {len(project.parameters)} 个参数，{len(project.scripts)} 个脚本。可以开始对话修改了。"
            })
            st.rerun()
        except Exception as e:
            st.error(f"❌ 导入失败: {e}")

    st.divider()
    st.caption("💡 提示：第一条消息无需创建项目，直接描述需求，AI 会自动初始化。")


# ── Intent Classification ─────────────────────────────────

_GDL_KEYWORDS = [
    "创建", "生成", "制作", "做一个", "建一个", "写一个",
    "修改", "更新", "添加", "删除", "调整", "优化",
    "书架", "柜子", "窗", "门", "墙", "楼梯", "桌", "椅",
    "参数", "parameter", "script", "gdl", "gsm", "hsf",
    "compile", "编译", "build", "create", "make", "add",
    "3d", "2d", "prism", "block", "sphere",
]

def _is_gdl_intent(text: str) -> bool:
    """Quick keyword check — if obvious GDL request, skip LLM classification."""
    t = text.lower()
    return any(kw in t for kw in _GDL_KEYWORDS)

def classify_and_extract(text: str, llm) -> tuple:
    """
    Returns: (intent, obj_name)
    - intent: via keyword fast-path, LLM only for ambiguous cases
    - obj_name: dictionary + regex, zero LLM calls
    """
    # Name: always from dictionary (instant, no LLM)
    obj_name = _extract_object_name(text)

    # Intent fast-path
    if _is_gdl_intent(text):
        return ("GDL", obj_name)

    # Ambiguous → ask LLM just for GDL/CHAT (one word)
    try:
        resp = llm.generate([
            {
                "role": "system",
                "content": (
                    "你是意图分类器。判断用户是否想创建或修改 ArchiCAD GDL 构件。\n"
                    "只回复一个词：GDL 或 CHAT\n"
                    "GDL = 要创建/修改/编译构件\n"
                    "CHAT = 闲聊/打招呼/问用法"
                ),
            },
            {"role": "user", "content": text},
        ], max_tokens=10, temperature=0.1)

        raw = resp.content.strip().upper()
        print(f"[classify] intent raw: '{raw}'")
        intent = "GDL" if "GDL" in raw else "CHAT"
        return (intent, obj_name)

    except Exception as e:
        print(f"[classify] exception: {e}")
        return ("CHAT", obj_name)


def chat_respond(user_input: str, history: list, llm) -> str:
    """Simple conversational response without triggering Agent."""
    system_msg = {
        "role": "system",
        "content": (
            "你是 gdl-agent 的助手，专注于 ArchiCAD GDL 库构件的创建与编译。"
            "用户可以和你闲聊，也可以让你创建 GDL 对象。"
            "闲聊时自然回应，简洁友好；涉及 GDL 创建需求时提醒用户直接描述需求即可开始生成。"
            "回复使用中文，专业术语保留英文（GDL、HSF、ArchiCAD、paramlist 等）。"
        ),
    }
    messages = [system_msg]
    # Include recent history for context (last 6 messages)
    for msg in history[-6:]:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": user_input})

    try:
        resp = llm.generate(messages)
        return resp.content
    except Exception as e:
        return f"❌ {str(e)}"


# ── Run Agent ─────────────────────────────────────────────

def run_agent_generate(user_input: str, proj: HSFProject, status_col, gsm_name: str = None) -> str:
    """
    Generate code only (no compile).
    Stores result in session_state.pending_diffs — shown inline in script tabs.
    """
    status_ph = status_col.empty()

    def on_event(event_type, data):
        if event_type == "analyze":
            scripts = data.get("affected_scripts", [])
            status_ph.info(f"🔍 分析中... 影响脚本: {', '.join(scripts)}")
        elif event_type == "attempt":
            status_ph.info("🧠 调用 AI 生成代码...")
        elif event_type == "llm_response":
            status_ph.info(f"✏️ 收到 {data['length']} 字符，解析中...")

    try:
        llm = get_llm()
        knowledge = load_knowledge()
        skills_text = load_skills().get_for_task(user_input)

        agent = GDLAgent(llm=llm, compiler=get_compiler(), on_event=on_event)
        changes = agent.generate_only(
            instruction=user_input, project=proj,
            knowledge=knowledge, skills=skills_text,
        )
        status_ph.empty()

        if not changes:
            return "❌ AI 输出无法解析，请重新描述需求。"

        # Merge into pending_diffs (AI may partially update scripts)
        st.session_state.pending_diffs.update(changes)
        if gsm_name:
            st.session_state.pending_gsm_name = gsm_name

        script_names = ", ".join(
            p.replace("scripts/", "").replace(".gdl", "").upper()
            for p in changes.keys()
            if p.startswith("scripts/")
        )
        return f"✏️ **AI 已生成代码** — 脚本 [{script_names}] 右侧出现对比视图，确认后点击「替换」，最后「🔧 编译」。"

    except Exception as e:
        status_ph.empty()
        return f"❌ **错误**: {str(e)}"


def do_compile(proj: HSFProject, gsm_name: str, instruction: str = "") -> tuple:
    """
    Compile current project state → versioned GSM.
    Returns (success: bool, message: str).
    """
    try:
        output_gsm = _versioned_gsm_path(gsm_name or proj.name, st.session_state.work_dir)
        hsf_dir = proj.save_to_disk()
        result = get_compiler().hsf2libpart(str(hsf_dir), output_gsm)
        mock_tag = " [Mock]" if compiler_mode.startswith("Mock") else ""

        if result.success:
            st.session_state.compile_log.append({
                "project": proj.name, "instruction": instruction,
                "success": True, "attempts": 1, "message": "Success",
            })
            msg = f"✅ **编译成功{mock_tag}**\n\n📦 `{output_gsm}`"
            if compiler_mode.startswith("Mock"):
                msg += "\n\n⚠️ Mock 模式不生成真实 .gsm，切换 LP_XMLConverter 进行真实编译。"
            return (True, msg)
        else:
            st.session_state.compile_log.append({
                "project": proj.name, "instruction": instruction,
                "success": False, "attempts": 1, "message": result.stderr,
            })
            return (False, f"❌ **编译失败**\n\n```\n{result.stderr[:500]}\n```")
    except Exception as e:
        return (False, f"❌ **错误**: {str(e)}")


def import_gsm(gsm_bytes: bytes, filename: str) -> tuple:
    """
    Decompile GSM → HSF → HSFProject.
    Returns (project | None, message).
    """
    import tempfile, shutil
    tmp = Path(tempfile.mkdtemp())
    gsm_path = tmp / filename
    gsm_path.write_bytes(gsm_bytes)
    hsf_out = tmp / "hsf_out"
    hsf_out.mkdir()
    result = get_compiler().libpart2hsf(str(gsm_path), str(hsf_out))
    if not result.success:
        shutil.rmtree(tmp, ignore_errors=True)
        return (None, f"❌ GSM 解包失败: {result.stderr}")
    try:
        # LP_XMLConverter creates a subdirectory named after the object
        subdirs = [d for d in hsf_out.iterdir() if d.is_dir()]
        hsf_dir = subdirs[0] if subdirs else hsf_out
        proj = HSFProject.from_hsf(str(hsf_dir))
        proj.work_dir = Path(st.session_state.work_dir)
        proj.root = proj.work_dir / proj.name
        return (proj, f"✅ 已导入 `{proj.name}` — {len(proj.parameters)} 参数，{len(proj.scripts)} 脚本")
    except Exception as e:
        return (None, f"❌ HSF 解析失败: {e}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_gdl_script(content: str, script_type: str = "") -> list:
    """
    Basic GDL syntax check. Returns list of warning strings (empty = OK).
    Checks: IF/ENDIF, FOR/NEXT, ADD/DEL balance, END in 3D, PROJECT2 in 2D.
    """
    import re as _re
    issues = []
    if not content.strip():
        if script_type == "2d":
            issues.append("⚠️ 2D 脚本为空，必须至少包含 PROJECT2 3, 270, 2")
        return issues

    lines = content.splitlines()

    # IF/ENDIF balance (only multi-line IF: IF ... THEN at end of line)
    if_multi = sum(
        1 for l in lines
        if _re.search(r'\bIF\b', l, _re.I)
        and _re.search(r'\bTHEN\s*$', l.strip(), _re.I)
    )
    endif_count = sum(1 for l in lines if _re.match(r'\s*ENDIF\b', l, _re.I))
    if if_multi != endif_count:
        issues.append(f"⚠️ IF/ENDIF 不匹配：{if_multi} 个多行 IF，{endif_count} 个 ENDIF")

    # FOR/NEXT balance
    for_count = sum(1 for l in lines if _re.match(r'\s*FOR\b', l, _re.I))
    next_count = sum(1 for l in lines if _re.match(r'\s*NEXT\b', l, _re.I))
    if for_count != next_count:
        issues.append(f"⚠️ FOR/NEXT 不匹配：{for_count} 个 FOR，{next_count} 个 NEXT")

    # ADD/DEL balance
    add_count = sum(1 for l in lines if _re.match(r'\s*ADD\b', l, _re.I))
    del_count = sum(1 for l in lines if _re.match(r'\s*DEL\b', l, _re.I))
    if add_count != del_count:
        issues.append(f"⚠️ ADD/DEL 不匹配：{add_count} 个 ADD，{del_count} 个 DEL")

    # 3D: must end with END
    if script_type == "3d":
        last_non_empty = next(
            (l.strip() for l in reversed(lines) if l.strip()), ""
        )
        if not _re.match(r'^END\s*$', last_non_empty, _re.I):
            issues.append("⚠️ 3D 脚本最后一行必须是 END")

    # 2D: must have projection
    if script_type == "2d":
        has_proj = any(
            _re.search(r'\bPROJECT2\b|\bRECT2\b|\bPOLY2\b', l, _re.I)
            for l in lines
        )
        if not has_proj:
            issues.append("⚠️ 2D 脚本缺少平面投影语句（PROJECT2 / RECT2）")

    if not issues:
        issues = ["✅ 检查通过"]
    return issues


# ══════════════════════════════════════════════════════════
#  Main Layout: Left Chat | Right Editor
# ══════════════════════════════════════════════════════════

col_chat, col_editor = st.columns([2, 3], gap="large")


# ── Left: Chat History ────────────────────────────────────

with col_chat:
    if not st.session_state.project:
        st.markdown("### 💬 开始创建")
        st.markdown(
            '<p style="color:#64748b; font-size:0.9rem;">在底部输入框描述你想创建的对象，AI 会自动生成并编译。</p>',
            unsafe_allow_html=True,
        )
    else:
        proj_now = st.session_state.project
        st.markdown(f"### 💬 {proj_now.name}")
        st.caption(f"参数: {len(proj_now.parameters)} | 脚本: {len(proj_now.scripts)}")

    # Chat history
    for msg in st.session_state.chat_history:
        st.chat_message(msg["role"]).markdown(msg["content"])

    # Placeholder for live agent output (populated when agent runs)
    live_output = st.empty()


# ── Right: Editor ─────────────────────────────────────────

_SCRIPT_MAP = [
    (ScriptType.SCRIPT_3D, "scripts/3d.gdl",  "3D"),
    (ScriptType.SCRIPT_2D, "scripts/2d.gdl",  "2D"),
    (ScriptType.MASTER,    "scripts/1d.gdl",  "Master"),
    (ScriptType.PARAM,     "scripts/vl.gdl",  "Param"),
    (ScriptType.UI,        "scripts/ui.gdl",  "UI"),
    (ScriptType.PROPERTIES,"scripts/pr.gdl",  "Properties"),
]

def _diff_summary(old: str, new: str) -> str:
    """Return a short +N/-N line diff summary."""
    old_lines = set(old.splitlines())
    new_lines = set(new.splitlines())
    added   = len(new_lines - old_lines)
    removed = len(old_lines - new_lines)
    return f"+{added} / -{removed} lines"


with col_editor:
    if not st.session_state.project:
        show_welcome()
    else:
        proj_now = st.session_state.project
        diffs    = st.session_state.pending_diffs  # live reference

        # ── Toolbar ───────────────────────────────────────
        tb_imp, tb_gsm_imp, tb_sep, tb_name, tb_compile, tb_check = st.columns(
            [1.2, 1.4, 0.2, 2.2, 1.4, 1.2]
        )

        # Import GDL (.gdl / .txt)
        with tb_imp:
            gdl_upload = st.file_uploader(
                "📂 GDL", type=["gdl", "txt"], label_visibility="collapsed",
                key="toolbar_gdl_upload", help="导入 .gdl 文件"
            )
            if gdl_upload:
                content = gdl_upload.read().decode("utf-8", errors="replace")
                name = Path(gdl_upload.name).stem
                try:
                    imported = parse_gdl_source(content, name)
                    imported.work_dir = Path(st.session_state.work_dir)
                    imported.root = imported.work_dir / imported.name
                    st.session_state.project = imported
                    st.session_state.pending_diffs = {}
                    st.session_state.pending_gsm_name = imported.name
                    st.session_state.chat_history.append({
                        "role": "assistant",
                        "content": f"✅ 已导入 `{imported.name}` — {len(imported.parameters)} 参数，{len(imported.scripts)} 脚本",
                    })
                    st.rerun()
                except Exception as e:
                    st.error(f"导入失败: {e}")

        # Import GSM (LP mode only)
        with tb_gsm_imp:
            if compiler_mode.startswith("LP"):
                gsm_upload = st.file_uploader(
                    "📦 GSM", type=["gsm"], label_visibility="collapsed",
                    key="toolbar_gsm_upload", help="导入 .gsm — 需 LP_XMLConverter"
                )
                if gsm_upload:
                    with st.spinner("解包 GSM..."):
                        proj_imp, imp_msg = import_gsm(gsm_upload.read(), gsm_upload.name)
                    if proj_imp:
                        st.session_state.project = proj_imp
                        st.session_state.pending_diffs = {}
                        st.session_state.pending_gsm_name = proj_imp.name
                        st.session_state.chat_history.append({"role": "assistant", "content": imp_msg})
                        st.rerun()
                    else:
                        st.error(imp_msg)
            else:
                st.caption("GSM 导入需 LP 模式")

        # GSM output name
        with tb_name:
            gsm_name_input = st.text_input(
                "📦", label_visibility="collapsed",
                value=st.session_state.pending_gsm_name or proj_now.name,
                placeholder="输出 GSM 名称",
                key="toolbar_gsm_name",
                help="编译输出文件名（不含版本号和扩展名）",
            )
            st.session_state.pending_gsm_name = gsm_name_input

        # Compile button
        with tb_compile:
            n_diffs = len(diffs)
            compile_label = f"🔧 编译 ({n_diffs}↑)" if n_diffs else "🔧 编译"
            if st.button(compile_label, type="primary", use_container_width=True,
                         help="编译当前所有脚本（接受的 AI 建议已自动应用）"):
                with st.spinner("编译中..."):
                    success, result_msg = do_compile(
                        proj_now,
                        gsm_name=gsm_name_input or proj_now.name,
                        instruction="(toolbar compile)",
                    )
                st.session_state.chat_history.append({"role": "assistant", "content": result_msg})
                if success:
                    st.toast("✅ 编译成功", icon="🏗️")
                else:
                    st.error(result_msg)
                st.rerun()

        # Global syntax check
        with tb_check:
            if st.button("🔍 全检查", use_container_width=True):
                all_ok = True
                for stype, fpath, label in _SCRIPT_MAP:
                    content = proj_now.get_script(stype)
                    if not content:
                        continue
                    skey = fpath.replace("scripts/", "").replace(".gdl", "")
                    for iss in check_gdl_script(content, skey):
                        if iss.startswith("✅"):
                            st.success(f"{label}: {iss}")
                        else:
                            st.warning(f"{label}: {iss}")
                            all_ok = False
                if all_ok:
                    st.success("✅ 所有脚本语法正常")

        st.divider()

        # ── Tab strip — labels show ● when diff pending ──
        def _tlabel(name, fpath):
            return f"{name} ●" if fpath in diffs else name

        tab_labels = (
            ["参数"]
            + [_tlabel(label, fpath) for _, fpath, label in _SCRIPT_MAP]
            + ["📋 日志"]
        )
        all_tabs = st.tabs(tab_labels)
        tab_params = all_tabs[0]
        script_tabs = all_tabs[1:-1]
        tab_log = all_tabs[-1]

        # ── 参数 Tab ──────────────────────────────────────
        with tab_params:
            param_data = [
                {"Type": p.type_tag, "Name": p.name, "Value": p.value,
                 "Description": p.description, "Fixed": "✓" if p.is_fixed else ""}
                for p in proj_now.parameters
            ]
            if param_data:
                st.dataframe(param_data, use_container_width=True, hide_index=True)
            else:
                st.caption("暂无参数，通过对话让 AI 添加，或手动添加。")

            with st.expander("➕ 手动添加参数"):
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
                if st.button("添加参数"):
                    try:
                        proj_now.add_parameter(GDLParameter(p_name, p_type, p_desc, p_value))
                        st.success(f"✅ {p_type} {p_name}")
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))

            if st.button("🔍 验证参数"):
                issues = validate_paramlist(proj_now.parameters)
                for i in issues:
                    st.warning(i)
                if not issues:
                    st.success("✅ 参数验证通过")

            with st.expander("paramlist.xml 预览"):
                st.code(build_paramlist_xml(proj_now.parameters), language="xml")

        # ── Script Tabs ───────────────────────────────────
        for tab, (stype, fpath, label) in zip(script_tabs, _SCRIPT_MAP):
            with tab:
                current_code = proj_now.get_script(stype) or ""
                skey = fpath.replace("scripts/", "").replace(".gdl", "")
                has_diff = fpath in diffs

                if has_diff:
                    ai_code = diffs[fpath]
                    summary = _diff_summary(current_code, ai_code)

                    # ── Diff view: current (left) | AI (right) ──
                    col_cur, col_ai = st.columns(2, gap="small")

                    with col_cur:
                        st.markdown(
                            '<div class="diff-current"><b>当前代码</b></div>',
                            unsafe_allow_html=True,
                        )
                        edited_cur = st.text_area(
                            "current", value=current_code, height=340,
                            key=f"cur_{fpath}", label_visibility="collapsed",
                        )
                        if edited_cur != current_code:
                            proj_now.set_script(stype, edited_cur)

                    with col_ai:
                        st.markdown(
                            f'<div class="diff-ai"><b>AI 建议</b> &nbsp;'
                            f'<span class="diff-badge">{summary}</span></div>',
                            unsafe_allow_html=True,
                        )
                        edited_ai = st.text_area(
                            "ai", value=ai_code, height=340,
                            key=f"ai_{fpath}", label_visibility="collapsed",
                        )
                        diffs[fpath] = edited_ai  # track in-place edits to AI side

                    btn_accept, btn_discard, btn_chk = st.columns([2, 1.5, 1.5])
                    with btn_accept:
                        if st.button(f"✅ 替换为 AI 版本", key=f"accept_{fpath}",
                                     type="primary", use_container_width=True):
                            proj_now.set_script(stype, diffs.pop(fpath))
                            st.rerun()
                    with btn_discard:
                        if st.button(f"❌ 丢弃 AI 建议", key=f"discard_{fpath}",
                                     use_container_width=True):
                            diffs.pop(fpath)
                            st.rerun()
                    with btn_chk:
                        if st.button(f"🔍 检查 AI", key=f"chk_ai_{fpath}",
                                     use_container_width=True):
                            for iss in check_gdl_script(diffs.get(fpath, ""), skey):
                                if iss.startswith("✅"):
                                    st.success(iss)
                                else:
                                    st.warning(iss)

                else:
                    # ── Normal single editor ──────────────────
                    new_code = st.text_area(
                        label, value=current_code, height=380,
                        key=f"script_{fpath}", label_visibility="collapsed",
                    )
                    if new_code != current_code:
                        proj_now.set_script(stype, new_code)

                    if st.button(f"🔍 检查", key=f"chk_{fpath}"):
                        for iss in check_gdl_script(new_code, skey):
                            if iss.startswith("✅"):
                                st.success(iss)
                            else:
                                st.warning(iss)

        # ── 日志 Tab ──────────────────────────────────────
        with tab_log:
            if not st.session_state.compile_log:
                st.info("暂无编译记录")
            else:
                for entry in reversed(st.session_state.compile_log):
                    icon = "✅" if entry["success"] else "❌"
                    st.markdown(f"**{icon} {entry['project']}** — {entry.get('instruction','')}")
                    st.code(entry["message"], language="text")
                    st.divider()
            if st.button("清除日志"):
                st.session_state.compile_log = []
                st.rerun()

            with st.expander("HSF 目录结构"):
                tree = [f"📁 {proj_now.name}/", "  ├── libpartdata.xml",
                        "  ├── paramlist.xml", "  ├── ancestry.xml", "  └── scripts/"]
                for stype in ScriptType:
                    if stype in proj_now.scripts:
                        n = proj_now.scripts[stype].count("\n") + 1
                        tree.append(f"       ├── {stype.value} ({n} lines)")
                st.code("\n".join(tree), language="text")


# ══════════════════════════════════════════════════════════
#  Chat Input — Always at Bottom
# ══════════════════════════════════════════════════════════

user_input = st.chat_input(
    "描述你想创建或修改的 GDL 对象，如「创建一个宽 600mm 的书架，iShelves 控制层数」"
)

if user_input:
    # Add user message to history
    st.session_state.chat_history.append({"role": "user", "content": user_input})

    # Check API key first
    if not api_key and "ollama" not in model_name:
        err = "❌ 请在左侧边栏填入 API Key 后再试。"
        st.session_state.chat_history.append({"role": "assistant", "content": err})
        st.rerun()
    else:
        llm_for_classify = get_llm()

        # ── Intent + Name in ONE call ──
        intent, gdl_obj_name = classify_and_extract(user_input, llm_for_classify)

        with live_output.container():
            st.chat_message("user").markdown(user_input)
            with st.chat_message("assistant"):
                if intent == "CHAT":
                    # ── Casual conversation — no project creation, no agent ──
                    msg = chat_respond(
                        user_input,
                        st.session_state.chat_history[:-1],
                        llm_for_classify,
                    )
                    st.markdown(msg)

                else:
                    # ── GDL intent — create project if needed, then run agent ──
                    if not st.session_state.project:
                        new_proj = HSFProject.create_new(gdl_obj_name, work_dir=st.session_state.work_dir)
                        st.session_state.project = new_proj
                        st.session_state.pending_gsm_name = gdl_obj_name
                        st.info(f"📁 已初始化项目 `{gdl_obj_name}`")

                    proj_current = st.session_state.project
                    # Keep existing gsm_name if project already loaded
                    effective_gsm = (
                        st.session_state.pending_gsm_name
                        or proj_current.name
                    )
                    msg = run_agent_generate(user_input, proj_current, st.container(), gsm_name=effective_gsm)
                    st.markdown(msg)

        st.session_state.chat_history.append({"role": "assistant", "content": msg})
        st.rerun()


# ── Footer ────────────────────────────────────────────────
st.divider()
st.markdown(
    '<p style="text-align:center; color:#64748b; font-size:0.8rem;">'
    'gdl-agent v0.5.0 · HSF-native ·'
    '<a href="https://github.com/byewind/gdl-agent">GitHub</a>'
    '</p>',
    unsafe_allow_html=True,
)
