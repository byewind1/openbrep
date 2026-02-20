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
if "confirm_clear" not in st.session_state:
    st.session_state.confirm_clear = False
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
    # Always load from project knowledge dir first (contains pro ccgdl_dev_doc)
    project_kb = Path(__file__).parent.parent / "knowledge"
    kb = KnowledgeBase(str(project_kb))
    kb.load()

    # Merge user's custom knowledge from work_dir (if different & exists)
    user_kb_dir = Path(st.session_state.work_dir) / "knowledge"
    if user_kb_dir.exists() and user_kb_dir != project_kb:
        user_kb = KnowledgeBase(str(user_kb_dir))
        user_kb.load()
        kb._docs.update(user_kb._docs)   # user custom overrides project

    return kb.get_by_task_type(task_type)

def load_skills():
    # Always load from project skills dir first
    project_sk = Path(__file__).parent.parent / "skills"
    sl = SkillsLoader(str(project_sk))
    sl.load()

    # Merge user's custom skills from work_dir
    user_sk_dir = Path(st.session_state.work_dir) / "skills"
    if user_sk_dir.exists() and user_sk_dir != project_sk:
        user_sl = SkillsLoader(str(user_sk_dir))
        user_sl.load()
        sl._skills.update(user_sl._skills)   # user custom overrides project

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
    "书架": "Bookshelf", "书柜": "Bookcase", "柜子": "Cabinet",
    "衣柜": "Wardrobe", "橱柜": "Kitchen Cabinet", "储物柜": "StorageUnit",
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
    # 动作
    "创建", "生成", "制作", "做一个", "建一个", "写一个", "写个", "写一",
    "做个", "建个", "来个", "整个", "出一个", "出个",
    "修改", "更新", "添加", "删除", "调整", "优化", "重写", "补充",
    # 建筑/家具对象（中文）
    "书架", "柜子", "衣柜", "橱柜", "储物柜", "鞋柜", "电视柜",
    "桌子", "桌", "椅子", "椅", "沙发", "床", "茶几", "柜",
    "窗", "门", "墙", "楼梯", "柱", "梁", "板", "扶手", "栏杆",
    "屋顶", "天花", "地板", "灯", "管道",
    # 技术词
    "参数", "parameter", "script", "gdl", "gsm", "hsf",
    "compile", "编译", "build", "create", "make", "add",
    "3d", "2d", "prism", "block", "sphere", "prism_", "body",
    "project2", "rect2", "poly2",
]

# Pure chat patterns — greeting / meta questions only
_CHAT_ONLY_PATTERNS = [
    r"^(你好|hello|hi|hey|嗨|哈喽)[!！。\s]*$",
    r"^(谢谢|感谢|thanks)[!！。\s]*$",
    r"^你(是谁|能做什么|有什么功能)",
    r"^(怎么|如何|什么是).*(gdl|archicad|hsf|构件)",
]

def _is_gdl_intent(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in _GDL_KEYWORDS)

def _is_pure_chat(text: str) -> bool:
    return any(re.search(p, text.strip(), re.IGNORECASE) for p in _CHAT_ONLY_PATTERNS)

def classify_and_extract(text: str, llm, project_loaded: bool = False) -> tuple:
    """
    Returns: (intent, obj_name)
    When project is already loaded, default to GDL for anything ambiguous.
    """
    obj_name = _extract_object_name(text)

    # Pure greetings / meta questions always → CHAT regardless of project state
    if _is_pure_chat(text):
        return ("CHAT", obj_name)

    # Keyword fast-path
    if _is_gdl_intent(text):
        return ("GDL", obj_name)

    # Project loaded: assume user wants to edit — treat ambiguous as GDL
    if project_loaded:
        print(f"[classify] project loaded → default GDL for: '{text[:40]}'")
        return ("GDL", obj_name)

    # No project, ambiguous → ask LLM (one word)
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
        print(f"[classify] LLM intent: '{raw}'")
        return ("GDL" if "GDL" in raw else "CHAT", obj_name)

    except Exception as e:
        print(f"[classify] exception: {e}")
        return ("CHAT", obj_name)


def chat_respond(user_input: str, history: list, llm) -> str:
    """Simple conversational response. Never outputs GDL code — that goes to the editor."""
    system_msg = {
        "role": "system",
        "content": (
            "你是 gdl-agent 的内置助手，专注于 ArchiCAD GDL 对象编辑器的使用指引。\n"
            "【重要约束】绝对禁止在回复中输出任何 GDL 代码、代码块或脚本片段。"
            "如果用户想创建或修改 GDL 对象，告诉他「直接在底部输入框描述需求，AI 会自动生成并填入编辑器」。\n"
            "不要提及 ArchiCAD 内部操作（如打开 GDL 对象编辑器），因为本工具就是体外的 GDL IDE。\n"
            "回复简洁，使用中文，专业术语保留英文（GDL、HSF、GSM、paramlist 等）。"
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


# ── Script Map (module-level, shared by agent + editor) ───
_SCRIPT_MAP = [
    (ScriptType.SCRIPT_3D, "scripts/3d.gdl",  "3D"),
    (ScriptType.SCRIPT_2D, "scripts/2d.gdl",  "2D"),
    (ScriptType.MASTER,    "scripts/1d.gdl",  "Master"),
    (ScriptType.PARAM,     "scripts/vl.gdl",  "Param"),
    (ScriptType.UI,        "scripts/ui.gdl",  "UI"),
    (ScriptType.PROPERTIES,"scripts/pr.gdl",  "Properties"),
]

# ── Run Agent ─────────────────────────────────────────────

def run_agent_generate(user_input: str, proj: HSFProject, status_col, gsm_name: str = None) -> str:
    """
    Generate code only (no compile).
    Directly applies AI output to project scripts — no confirmation needed.
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

        # Strip markdown fences, then directly apply to project scripts
        cleaned = {k: _strip_md_fences(v) for k, v in changes.items()}
        _apply_scripts_to_project(proj, cleaned)
        if gsm_name:
            st.session_state.pending_gsm_name = gsm_name

        script_names = ", ".join(
            p.replace("scripts/", "").replace(".gdl", "").upper()
            for p in cleaned.keys()
            if p.startswith("scripts/")
        )
        return f"✏️ **AI 已写入脚本** [{script_names}]，点击「🔧 编译」即可。"

    except Exception as e:
        status_ph.empty()
        return f"❌ **错误**: {str(e)}"


def _apply_scripts_to_project(proj: HSFProject, script_map: dict) -> None:
    """Apply a {fpath: content} dict directly to project via set_script."""
    for stype, fpath, _label in _SCRIPT_MAP:
        if fpath in script_map:
            proj.set_script(stype, script_map[fpath])


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


def _strip_md_fences(code: str) -> str:
    """Remove markdown code fences (```gdl / ```) that AI sometimes leaks into scripts."""
    import re as _re
    # Remove opening fence (```gdl, ```GDL, ```)
    code = _re.sub(r'^```[a-zA-Z]*\s*\n?', '', code.strip(), flags=_re.MULTILINE)
    # Remove closing fence
    code = _re.sub(r'\n?```\s*$', '', code.strip(), flags=_re.MULTILINE)
    return code.strip()


def _extract_gdl_from_chat() -> dict:
    """
    Scan chat history for fenced code blocks containing GDL.
    Returns {script_path: content} ready to merge into pending_diffs.
    Heuristic type detection: 3D (has BODY/PRISM_/END), 2D (has PROJECT2),
    Master (has GLOB_ / PARAMETERS keyword), Param (has PARAMETERS section).
    Multiple blocks → last block wins per type.
    """
    import re as _re
    collected: dict[str, str] = {}
    code_block_pat = _re.compile(r"```(?:gdl|GDL)?\s*\n(.*?)```", _re.DOTALL)

    for msg in st.session_state.get("chat_history", []):
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", "")
        for m in code_block_pat.finditer(content):
            block = m.group(1).strip()
            if not block:
                continue
            # Classify by content heuristics
            block_up = block.upper()
            if _re.search(r'\bPROJECT2\b|\bRECT2\b|\bPOLY2\b', block_up):
                path = "scripts/2d.gdl"
            elif _re.search(r'\bPARAMETERS\b', block_up):
                path = "scripts/vl.gdl"
            elif _re.search(r'\bGLOB_\w+\b', block_up):
                path = "scripts/1d.gdl"
            else:
                path = "scripts/3d.gdl"   # default: 3D
            collected[path] = block

    return collected


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

    # ADD/DEL balance — ADDX/ADDY/ADDZ are single-axis variants, count equally
    add_count = sum(1 for l in lines if _re.match(r'\s*ADD(X|Y|Z)?\b', l, _re.I))
    del_count = sum(1 for l in lines if _re.match(r'\s*DEL\b', l, _re.I))
    if add_count != del_count:
        issues.append(f"⚠️ ADD/DEL 不匹配：{add_count} 个 ADD/ADDX/ADDY/ADDZ，{del_count} 个 DEL")

    # Markdown fence leak — common when AI generates code in chat
    if any(l.strip().startswith("```") for l in lines):
        issues.append("⚠️ 脚本含有 ``` 标记 — AI 格式化残留，请删除所有反引号行")

    # 3D: END / subroutine RETURN check
    if script_type == "3d":
        # Detect subroutine labels:  "SubName":
        sub_label_pat = _re.compile(r'^\s*"[^"]+"\s*:')
        has_subs = any(sub_label_pat.match(l) for l in lines)

        if has_subs:
            # Main body = lines before first subroutine label
            main_body = []
            for l in lines:
                if sub_label_pat.match(l):
                    break
                main_body.append(l)
            last_main = next((l.strip() for l in reversed(main_body) if l.strip()), "")
            if not _re.match(r'^END\s*$', last_main, _re.I):
                issues.append("⚠️ 3D 主体部分（第一个子程序之前）最后一行必须是 END")

            # Each subroutine should end with RETURN (not END)
            current_sub = None
            sub_lines: list[str] = []
            for l in lines:
                if sub_label_pat.match(l):
                    if current_sub and sub_lines:
                        last_sub = next((s.strip() for s in reversed(sub_lines) if s.strip()), "")
                        if not _re.match(r'^RETURN\s*$', last_sub, _re.I):
                            issues.append(f"⚠️ 子程序 {current_sub} 末尾应为 RETURN，不是 END")
                    current_sub = l.strip()
                    sub_lines = []
                else:
                    sub_lines.append(l)
            # Check last subroutine
            if current_sub and sub_lines:
                last_sub = next((s.strip() for s in reversed(sub_lines) if s.strip()), "")
                if not _re.match(r'^RETURN\s*$', last_sub, _re.I):
                    issues.append(f"⚠️ 子程序 {current_sub} 末尾应为 RETURN")
        else:
            last_non_empty = next((l.strip() for l in reversed(lines) if l.strip()), "")
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

    # _var 未在本脚本内赋值的中间变量（可能需在 Master 脚本中定义）
    assigned = set(_re.findall(r'\b(_[A-Za-z]\w*)\s*=', content))
    used     = set(_re.findall(r'\b(_[A-Za-z]\w*)\b', content))
    undefined = used - assigned
    if undefined:
        issues.append(
            f"ℹ️ 变量 {', '.join(sorted(undefined))} 在本脚本未赋值 — "
            "若已在 Master 脚本中定义可忽略，否则会导致 ArchiCAD 运行时不显示"
        )

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

        # ── Toolbar ───────────────────────────────────────
        tb_imp, tb_gsm_imp, tb_extract, tb_clear, tb_name, tb_compile, tb_check = st.columns(
            [1.0, 1.3, 1.0, 0.85, 1.8, 1.3, 1.1]
        )

        # 📂 Import GDL (.gdl / .txt) — single script file
        with tb_imp:
            gdl_upload = st.file_uploader(
                "📂 GDL 脚本", type=["gdl", "txt"],
                key="toolbar_gdl_upload", help="导入单个 .gdl 脚本文件（解析为对象）"
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
                        "content": f"✅ 已导入 GDL `{imported.name}` — {len(imported.parameters)} 参数，{len(imported.scripts)} 脚本",
                    })
                    st.rerun()
                except Exception as e:
                    st.error(f"导入失败: {e}")

        # 📦 Import GSM — full object package (LP mode only)
        with tb_gsm_imp:
            if compiler_mode.startswith("LP"):
                gsm_upload = st.file_uploader(
                    "📦 GSM 对象", type=["gsm"],
                    key="toolbar_gsm_upload", help="导入 ArchiCAD .gsm 对象包，需 LP_XMLConverter"
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
                st.caption("GSM 导入\n需 LP 模式")

        # 📥 Extract GDL code blocks from chat history → apply directly
        with tb_extract:
            n_chat_blocks = sum(
                1 for m in st.session_state.chat_history
                if m.get("role") == "assistant" and "```" in m.get("content", "")
            )
            btn_label = f"📥 提取({n_chat_blocks})" if n_chat_blocks else "📥 提取"
            if st.button(btn_label, use_container_width=True,
                         help="从对话记录中提取 GDL 代码块，直接写入对应脚本"):
                extracted = _extract_gdl_from_chat()
                if extracted:
                    _apply_scripts_to_project(proj_now, extracted)
                    st.toast(f"📥 已写入 {len(extracted)} 个脚本", icon="✅")
                    st.rerun()
                else:
                    st.toast("对话中未发现 GDL 代码块", icon="ℹ️")

        # 🗑️ Clear all scripts
        with tb_clear:
            if st.button("🗑️ 清空", use_container_width=True,
                         help="清空所有脚本代码，等待重新导入或 AI 生成"):
                st.session_state.confirm_clear = True

        # GSM output name
        with tb_name:
            gsm_name_input = st.text_input(
                "输出名称", label_visibility="collapsed",
                value=st.session_state.pending_gsm_name or proj_now.name,
                placeholder="输出 GSM 名称",
                key="toolbar_gsm_name",
                help="编译输出文件名（不含版本号和扩展名）",
            )
            st.session_state.pending_gsm_name = gsm_name_input

        # Compile button
        with tb_compile:
            if st.button("🔧 编译", type="primary", use_container_width=True,
                         help="编译当前所有脚本为 .gsm 对象"):
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

        # ── 清空确认 ─────────────────────────────────────
        if st.session_state.get("confirm_clear"):
            st.warning("⚠️ 将清空所有脚本代码，此操作不可撤销。确认继续？")
            cc1, cc2, _ = st.columns([1, 1, 4])
            with cc1:
                if st.button("✅ 确认清空", type="primary"):
                    for stype, _fp, _lb in _SCRIPT_MAP:
                        proj_now.set_script(stype, "")
                    st.session_state.confirm_clear = False
                    st.toast("🗑️ 已清空所有脚本", icon="✅")
                    st.rerun()
            with cc2:
                if st.button("❌ 取消"):
                    st.session_state.confirm_clear = False
                    st.rerun()

        st.divider()

        # ── Tab strip ─────────────────────────────────────
        tab_labels = (
            ["参数"]
            + [label for _, _fp, label in _SCRIPT_MAP]
            + ["📋 日志"]
        )
        all_tabs = st.tabs(tab_labels)
        tab_params = all_tabs[0]
        script_tabs = all_tabs[1:-1]
        tab_log = all_tabs[-1]

        # ── 参数 Tab ──────────────────────────────────────
        with tab_params:
            with st.expander("ℹ️ 参数说明"):
                st.markdown(
                    "**参数列表** — GDL 对象的可调参数，对应 ArchiCAD 对象设置面板中的输入项。\n\n"
                    "- **Type**：数据类型。`Length` 带单位换算，`Integer` 整数，`Boolean` 开关，"
                    "`Material` 材质选择器，`String` 文字\n"
                    "- **Name**：代码中引用的变量名，建议英文 camelCase（如 `iShelves`、`bHasBack`）\n"
                    "- **Value**：默认值，用户未修改时使用\n"
                    "- **Fixed**：勾选后用户无法在 ArchiCAD 中修改此参数\n\n"
                    "参数在 3D / 2D / Master 脚本中直接用变量名引用，无需声明。"
                )

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
        _SCRIPT_HELP = {
            "scripts/3d.gdl": (
                "**3D 脚本** — 三维几何体定义，ArchiCAD 3D 窗口中显示的实体。\n\n"
                "- 使用 `PRISM_`、`BLOCK`、`SPHERE`、`CONE`、`REVOLVE` 等命令建模\n"
                "- `ADD` / `DEL` 管理坐标系变换，必须成对出现\n"
                "- `FOR` / `NEXT` 循环用于重复构件（如格栅、层板）\n"
                "- **最后一行必须是 `END`**，否则编译失败\n"
                "- 参数通过变量名直接引用（如 `A`、`B`、`iShelves`）"
            ),
            "scripts/2d.gdl": (
                "**2D 脚本** — 平面图符号，ArchiCAD 楼层平面图中显示的线条。\n\n"
                "- **必须包含** `PROJECT2 3, 270, 2`（最简投影）或自定义 2D 线条\n"
                "- `PROJECT2` 自动将 3D 几何投影为平面，适合大多数对象\n"
                "- 复杂平面符号可用 `LINE2`、`ARC2`、`POLY2`、`RECT2` 手绘\n"
                "- 不写或留空会导致平面图中对象不可见"
            ),
            "scripts/1d.gdl": (
                "**Master 脚本** — 主控脚本，所有脚本执行前最先运行。\n\n"
                "- 用于全局变量初始化、参数联动逻辑、条件判断\n"
                "- 可引用 `GLOB_` 系列全局变量（如 `GLOB_SCALE`、`GLOB_NORTH`）\n"
                "- 不直接产生几何，只做数据处理\n"
                "- 简单对象通常不需要此脚本"
            ),
            "scripts/vl.gdl": (
                "**Param 脚本** — 参数验证脚本，参数值发生变化时触发。\n\n"
                "- 用于参数范围约束（如宽度不能小于 0）\n"
                "- 派生参数计算（如根据宽度自动计算格栅间距）\n"
                "- `LOCK` 语句可锁定参数防止用户修改\n"
                "- 简单对象通常不需要此脚本"
            ),
            "scripts/ui.gdl": (
                "**UI 脚本** — 自定义参数界面，ArchiCAD 对象设置对话框中的控件布局。\n\n"
                "- 使用 `UI_INFIELD`、`UI_BUTTON`、`UI_SEPARATOR` 等命令\n"
                "- 不写则 ArchiCAD 自动生成默认参数列表界面\n"
                "- 用于需要精细化用户体验的商业构件\n"
                "- 对一般自用构件可留空"
            ),
            "scripts/pr.gdl": (
                "**Properties 脚本** — BIM 属性输出，定义 IFC 属性集和构件属性。\n\n"
                "- 用于输出 IFC 合规数据（如房间面积、材质规格）\n"
                "- `PROPERTY` 语句定义属性名和值\n"
                "- 与模型算量、能耗分析、施工预算直接挂钩\n"
                "- 不做 BIM 数据输出可留空"
            ),
        }

        for tab, (stype, fpath, label) in zip(script_tabs, _SCRIPT_MAP):
            with tab:
                # Help expander — collapsed by default
                with st.expander(f"ℹ️ {label} 脚本说明"):
                    st.markdown(_SCRIPT_HELP.get(fpath, ""))

                current_code = proj_now.get_script(stype) or ""
                skey = fpath.replace("scripts/", "").replace(".gdl", "")

                # ── Single editor (AI writes directly, no diff confirm needed) ──
                new_code = st.text_area(
                    label, value=current_code, height=400,
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
        intent, gdl_obj_name = classify_and_extract(
            user_input, llm_for_classify,
            project_loaded=bool(st.session_state.project),
        )

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
