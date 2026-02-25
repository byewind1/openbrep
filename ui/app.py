"""
openbrep Web UI — Streamlit interface for architects.

Run: streamlit run ui/app.py
"""

import sys
import re
import os
import time
import base64
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
try:
    from streamlit_ace import st_ace
    _ACE_AVAILABLE = True
except ImportError:
    _ACE_AVAILABLE = False

from openbrep.hsf_project import HSFProject, ScriptType, GDLParameter
from openbrep.gdl_parser import parse_gdl_source, parse_gdl_file
from openbrep.paramlist_builder import build_paramlist_xml, validate_paramlist
from openbrep.compiler import MockHSFCompiler, HSFCompiler, CompileResult
from openbrep.core import GDLAgent, Status
from openbrep.knowledge import KnowledgeBase
from openbrep.skills_loader import SkillsLoader
try:
    from openbrep.tapir_bridge import get_bridge, errors_to_chat_message
    _TAPIR_IMPORT_OK = True
except ImportError:
    _TAPIR_IMPORT_OK = False


# ── Page Config ───────────────────────────────────────────

st.set_page_config(
    page_title="openbrep",
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

/* ── Column gap tighten ─────────────────────────────────── */
/* Streamlit "small" gap still has padding; pull columns closer */
div[data-testid="stHorizontalBlock"] {
    gap: 1rem !important;
}
/* Subtle divider between editor and chat */
div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"]:last-child {
    border-left: 1px solid #1e293b;
    padding-left: 0.75rem;
}
</style>
""", unsafe_allow_html=True)


# ── Session State ─────────────────────────────────────────

if "project" not in st.session_state:
    st.session_state.project = None
if "_import_key_done" not in st.session_state:
    st.session_state._import_key_done = ""   # dedup: skip re-processing same file
if "compile_log" not in st.session_state:
    st.session_state.compile_log = []
if "compile_result" not in st.session_state:
    st.session_state.compile_result = None
if "tapir_status" not in st.session_state:
    st.session_state.tapir_status = None  # None | "checking" | "ok" | "no_tapir" | "no_ac"
if "tapir_test_trigger" not in st.session_state:
    st.session_state.tapir_test_trigger = False
if "adopted_msg_index" not in st.session_state:
    st.session_state.adopted_msg_index = None
if "_debug_mode_active" not in st.session_state:
    st.session_state["_debug_mode_active"] = None  # None | "editor" | "last"
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "work_dir" not in st.session_state:
    st.session_state.work_dir = str(Path.home() / "openbrep-workspace")
if "agent_running" not in st.session_state:
    st.session_state.agent_running = False
if "pending_diffs" not in st.session_state:
    # AI-proposed changes awaiting user review.
    # Keys: "scripts/3d.gdl" etc. + "paramlist.xml" for parameters
    st.session_state.pending_diffs = {}
if "pending_ai_label" not in st.session_state:
    # Human-readable label shown in the confirmation banner
    st.session_state.pending_ai_label = ""
if "pending_gsm_name" not in st.session_state:
    st.session_state.pending_gsm_name = ""
if "confirm_clear" not in st.session_state:
    st.session_state.confirm_clear = False
if "editor_version" not in st.session_state:
    # Increment on import/clear to force text_area widget recreation (avoids stale Streamlit cache)
    st.session_state.editor_version = 0
if "model_api_keys" not in st.session_state:
    # Per-model API Key storage — pre-fill from config.toml provider_keys
    st.session_state.model_api_keys = {}
if "vision_upload_key" not in st.session_state:
    # Increment to reset the file_uploader widget after image is sent
    st.session_state.vision_upload_key = 0


# ── Load config.toml defaults ──────────────────────────

_config_defaults = {}
_provider_keys: dict = {}   # {provider: api_key}

try:
    from openbrep.config import GDLAgentConfig
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
    st.markdown('<p class="main-header">OpenBrep</p>', unsafe_allow_html=True)
    st.markdown('<p class="sub-header">v0.5 · HSF-native · Code Your Boundaries</p>', unsafe_allow_html=True)
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
            placeholder="/Applications/GRAPHISOFT/ArchiCAD 28/LP_XMLConverter.app/Contents/MacOS/LP_XMLConverter",
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
        "glm-4.6v",     # 视觉模型，支持图片识别
        "glm-5",
        "glm-4.7",
        "glm-4.7-flash",
        "glm-4-plus",
        "glm-4-flash",
        # ── OpenAI ──
        "gpt-4o",       # 视觉模型，支持图片识别
        "gpt-4o-mini",
        "o3-mini",
        # ── DeepSeek ──
        "deepseek-chat",
        "deepseek-reasoner",
        # ── Google Gemini ──
        "gemini/gemini-2.5-flash",  # 视觉模型，支持图片识别
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
            _keep_work_dir  = st.session_state.work_dir
            _keep_api_keys  = st.session_state.model_api_keys
            _keep_chat      = st.session_state.chat_history   # preserve chat
            st.session_state.project          = None
            st.session_state.compile_log      = []
            st.session_state.compile_result   = None
            st.session_state.adopted_msg_index = None
            st.session_state.pending_diffs    = {}
            st.session_state.pending_ai_label = ""
            st.session_state.pending_gsm_name = ""
            st.session_state.agent_running    = False
            st.session_state._import_key_done = ""
            st.session_state.editor_version  += 1
            st.session_state.work_dir         = _keep_work_dir
            st.session_state.model_api_keys   = _keep_api_keys
            st.session_state.chat_history     = _keep_chat
            st.rerun()


# ── Helper Functions ──────────────────────────────────────

import json as _json, datetime as _datetime

def _save_feedback(msg_idx: int, rating: str, content: str) -> None:
    """Save 👍/👎 feedback to work_dir/feedback.jsonl (local only, not sent anywhere)."""
    try:
        feedback_path = Path(st.session_state.work_dir) / "feedback.jsonl"
        feedback_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": _datetime.datetime.now().isoformat(),
            "rating": rating,           # "positive" | "negative"
            "msg_idx": msg_idx,
            "preview": content[:300],
        }
        with open(feedback_path, "a", encoding="utf-8") as _f:
            _f.write(_json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass   # never let feedback save break the UI


# ── Fullscreen editor dialog (Streamlit ≥ 1.36) ───────────
_HAS_DIALOG = hasattr(st, "dialog")

if _HAS_DIALOG:
    @st.dialog("⛶ 全屏编辑", width="large")
    def _fullscreen_editor_dialog(stype: "ScriptType", fpath: str, label: str) -> None:
        st.caption(f"**{label}** 脚本 · 全屏模式 — 编辑完成点「✅ 应用」")
        code = (st.session_state.project or HSFProject.create_new("untitled")).get_script(stype) or ""
        if _ACE_AVAILABLE:
            _raw_fs = st_ace(
                value=code, language="fortran", theme="monokai",
                height=580, font_size=14, tab_size=2,
                show_gutter=True, show_print_margin=False,
                key=f"fs_ace_{fpath}",
            )
            new_code = _raw_fs if _raw_fs is not None else code
        else:
            new_code = st.text_area("code", value=code, height=580,
                                    label_visibility="collapsed", key=f"fs_ta_{fpath}") or ""
        c1, c2 = st.columns([2, 6])
        with c1:
            if st.button("✅ 应用", type="primary", use_container_width=True):
                if st.session_state.project:
                    st.session_state.project.set_script(stype, new_code)
                    st.session_state.editor_version += 1
                st.rerun()
        with c2:
            if st.button("❌ 取消", use_container_width=True):
                st.rerun()
else:
    def _fullscreen_editor_dialog(stype, fpath, label):  # type: ignore[misc]
        st.info("全屏编辑需要 Streamlit ≥ 1.36，请升级：`pip install -U streamlit`")


def get_compiler():
    if compiler_mode.startswith("Mock"):
        return MockHSFCompiler()
    return HSFCompiler(converter_path or None)

def get_llm():
    from openbrep.config import LLMConfig
    from openbrep.llm import LLMAdapter
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
<h2 style="color:#22d3ee; margin-top:0; font-family:'JetBrains Mono';">欢迎使用 OpenBrep 🏗️</h2>
<p style="color:#94a3b8;">用自然语言驱动 ArchiCAD GDL 对象的创建与编译。无需了解 GDL 语法，直接描述需求即可。</p>
</div>
""", unsafe_allow_html=True)

    st.markdown("#### 三步快速开始")

    st.info("**① 配置 API Key**  \n在左侧边栏选择 AI 模型，填入对应 API Key。免费的智谱 GLM 可直接使用。")
    st.info("**② 开始对话**  \n在底部输入框描述你想创建的 GDL 对象，例如：  \n「创建一个宽 600mm、深 400mm 的书架，带 iShelves 参数控制层数」")
    st.info("**③ 编译输出**  \nAI 生成代码后自动触发编译。真实编译需在侧边栏配置 LP_XMLConverter 路径。Mock 模式可验证结构，无需安装 ArchiCAD。")

    st.divider()

    st.markdown("#### 或者：导入已有文件")
    uploaded_file = st.file_uploader(
        "拖入 .gdl / .txt / .gsm 文件",
        type=["gdl", "txt", "gsm"],
        help=".gdl / .txt 直接解析脚本；.gsm 需侧边栏切换为 LP 模式",
        key="welcome_upload",
    )
    if uploaded_file:
        ok, msg = _handle_unified_import(uploaded_file)
        if not ok:
            st.error(msg)
        else:
            st.rerun()

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
            "你是 openbrep 的内置助手，专注于 ArchiCAD GDL 对象编辑器的使用指引。\n"
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

# Keywords that signal debug/analysis intent → inject all scripts + allow plain-text reply
_DEBUG_KEYWORDS = {
    "debug", "fix", "error", "bug", "wrong", "issue", "broken", "fail", "crash",
    "问题", "错误", "调试", "检查", "分析", "为什么", "帮我看", "看看", "出错",
    "不对", "不行", "哪里", "原因", "解释", "explain", "why", "what", "how",
    "review", "看一下", "看下", "告诉我", "这段", "这个脚本",
}

def _is_debug_intent(text: str) -> bool:
    if text.startswith("[DEBUG:editor]") or text.startswith("[DEBUG:last]"):
        return True
    t = text.lower()
    return any(kw in t for kw in _DEBUG_KEYWORDS)

def _get_debug_mode(text: str) -> str:
    """Returns 'editor', 'last', or 'keyword' (fallback)."""
    if text.startswith("[DEBUG:editor]"):
        return "editor"
    if text.startswith("[DEBUG:last]"):
        return "last"
    return "keyword"


def run_agent_generate(
    user_input: str,
    proj: HSFProject,
    status_col,
    gsm_name: str = None,
    auto_apply: bool = True,
) -> str:
    """
    Unified chat+generate entry point.

    auto_apply=True  → immediately write changes to project (first creation of empty project).
    auto_apply=False → queue changes in pending_diffs; UI shows confirmation banner in chat column.

    debug_mode (intent-based) controls whether all scripts are injected into LLM context
    and whether LLM is allowed to reply with plain-text analysis in addition to code.
    """
    status_ph = status_col.empty()
    debug_mode = _is_debug_intent(user_input)
    debug_type = _get_debug_mode(user_input)  # 'editor' | 'last' | 'keyword'

    def on_event(event_type, data):
        if event_type == "analyze":
            scripts = data.get("affected_scripts", [])
            mode_tag = f" [Debug:{debug_type}]" if debug_mode else ""
            status_ph.info(f"🔍 分析中{mode_tag}... 脚本: {', '.join(scripts)}")
        elif event_type == "attempt":
            status_ph.info("🧠 调用 AI...")
        elif event_type == "llm_response":
            status_ph.info(f"✏️ 收到 {data['length']} 字符，解析中...")

    try:
        llm = get_llm()
        knowledge = load_knowledge()
        # Strip debug prefix and extract syntax report
        clean_instruction = user_input
        syntax_report = ""
        if user_input.startswith("[DEBUG:editor]") or user_input.startswith("[DEBUG:last]"):
            _after_prefix = user_input.split("]", 1)[-1].strip()
            if "[SYNTAX CHECK REPORT]" in _after_prefix:
                _parts = _after_prefix.split("[SYNTAX CHECK REPORT]", 1)
                clean_instruction = _parts[0].strip()
                syntax_report = _parts[1].strip()
            else:
                clean_instruction = _after_prefix

        skills_text = load_skills().get_for_task(clean_instruction)

        # Pass recent chat history for multi-turn context (last 6 messages, skip heavy code blocks)
        recent_history = [
            m for m in st.session_state.chat_history[-8:]
            if m["role"] in ("user", "assistant")
        ]

        # [DEBUG:last] — inject last assistant code as extra context
        last_code_context = None
        if debug_type == "last":
            for _m in reversed(st.session_state.chat_history):
                if _m.get("role") == "assistant" and "```" in _m.get("content", ""):
                    last_code_context = _m["content"]
                    break

        agent = GDLAgent(llm=llm, compiler=get_compiler(), on_event=on_event)
        changes, plain_text = agent.generate_only(
            instruction=clean_instruction, project=proj,
            knowledge=knowledge, skills=skills_text,
            include_all_scripts=(debug_mode and debug_type != "last"),
            last_code_context=last_code_context,
            syntax_report=syntax_report,
            history=recent_history,
        )
        status_ph.empty()

        reply_parts = []

        # Plain-text analysis from LLM (debug/explanation)
        if plain_text:
            reply_parts.append(plain_text)

        # Code changes — strip fences, apply or queue for confirmation
        if changes:
            cleaned = {k: _strip_md_fences(v) for k, v in changes.items()}

            script_names = ", ".join(
                p.replace("scripts/", "").replace(".gdl", "").upper()
                for p in cleaned if p.startswith("scripts/")
            )
            has_params = "paramlist.xml" in cleaned
            param_count_preview = len(_parse_paramlist_text(cleaned.get("paramlist.xml", "")))

            code_blocks = []
            for fpath, code in cleaned.items():
                lbl = fpath.replace("scripts/", "").replace(".gdl", "").upper()
                code_blocks.append(f"**{lbl}**\n```gdl\n{code}\n```")

            label_parts = []
            if script_names:
                label_parts.append(f"脚本 [{script_names}]")
            if has_params:
                label_parts.append(f"{param_count_preview} 个参数")
            label_str = " + ".join(label_parts) if label_parts else "内容"

            if auto_apply:
                # 全新空项目：直接写入，无需确认
                sc, pc = _apply_scripts_to_project(proj, cleaned)
                st.session_state.editor_version += 1
                if gsm_name:
                    st.session_state.pending_gsm_name = gsm_name
                reply_parts.append(
                    f"✏️ **已写入 {label_str}** — 可直接「🔧 编译」\n\n"
                    + "\n\n".join(code_blocks)
                )
            else:
                # 已有项目修改：暂存，聊天栏内显示确认按钮
                st.session_state.pending_diffs    = cleaned
                st.session_state.pending_ai_label = label_str
                if gsm_name:
                    st.session_state.pending_gsm_name = gsm_name
                reply_parts.append(
                    f"🤖 **AI 已生成 {label_str}** — 请在下方确认是否写入编辑器。\n\n"
                    + "\n\n".join(code_blocks)
                )

        if reply_parts:
            return "\n\n---\n\n".join(reply_parts)

        return "🤔 AI 未返回代码或分析，请换一种描述方式。"

    except Exception as e:
        status_ph.empty()
        return f"❌ **错误**: {str(e)}"


def _parse_paramlist_text(text: str) -> list:
    """
    Parse 'Type Name = Value ! Description' lines → list[GDLParameter].
    Handles LLM output from [FILE: paramlist.xml] sections.
    """
    import re as _re
    _VALID_TYPES = {
        "Length", "Angle", "RealNum", "Integer", "Boolean",
        "String", "PenColor", "FillPattern", "LineType", "Material",
    }
    params = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("!") or line.startswith("#"):
            continue
        # Format: Type Name = Value  [! description]
        m = _re.match(r'(\w+)\s+(\w+)\s*=\s*(.+?)(?:\s*!\s*(.*))?$', line)
        if m:
            ptype, pname, pval, pdesc = m.groups()
            if ptype in _VALID_TYPES:
                params.append(GDLParameter(
                    pname, ptype, (pdesc or "").strip(), pval.strip().strip('"'),
                ))
    return params


def _apply_scripts_to_project(proj: HSFProject, script_map: dict) -> tuple[int, int]:
    """
    Apply {fpath: content} dict to project.
    Handles scripts/3d.gdl etc. + paramlist.xml.
    Returns (script_count, param_count) for notification.
    """
    sc = 0
    for stype, fpath, _label in _SCRIPT_MAP:
        if fpath in script_map:
            proj.set_script(stype, script_map[fpath])
            sc += 1
    pc = 0
    if "paramlist.xml" in script_map:
        new_params = _parse_paramlist_text(script_map["paramlist.xml"])
        if new_params:
            proj.parameters = new_params
            pc = len(new_params)
    return sc, pc


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
    Decompile GSM → HSF → HSFProject via LP_XMLConverter libpart2hsf.
    Returns (project | None, message).
    """
    import tempfile, shutil
    compiler = get_compiler()

    # Guard: must have a real compiler
    if isinstance(compiler, MockHSFCompiler):
        return (None, "❌ GSM 导入需要 LP_XMLConverter，Mock 模式不支持。请在侧边栏选择 LP 模式并指定路径。")

    # Diagnostic: report which binary will be used
    bin_path = compiler.converter_path or "(未检测到)"
    if not compiler.is_available:
        return (
            None,
            f"❌ LP_XMLConverter 未找到\n\n"
            f"检测路径: `{bin_path}`\n\n"
            f"macOS 正确路径示例:\n"
            f"`/Applications/GRAPHISOFT/ArchiCAD 28/LP_XMLConverter.app/Contents/MacOS/LP_XMLConverter`\n\n"
            f"请在侧边栏手动填写正确路径。"
        )

    tmp = Path(tempfile.mkdtemp())
    gsm_path = tmp / filename
    gsm_path.write_bytes(gsm_bytes)
    hsf_out = tmp / "hsf_out"
    hsf_out.mkdir()

    result = compiler.libpart2hsf(str(gsm_path), str(hsf_out))

    if not result.success:
        # Show full diagnostics so user can debug
        diag = result.stderr or result.stdout or "(无输出)"
        shutil.rmtree(tmp, ignore_errors=True)
        return (
            None,
            f"❌ GSM 解包失败 (exit={result.exit_code})\n\n"
            f"**Binary**: `{bin_path}`\n\n"
            f"**输出**:\n```\n{diag[:800]}\n```"
        )

    try:
        # Locate true HSF root — LP_XMLConverter output layout varies by AC version:
        #   AC 27/28 (standard): hsf_out/<LIBPARTNAME>/libpartdata.xml + scripts/
        #   AC 29 (flat):        hsf_out/libpartdata.xml + scripts/  (no named subdir)
        def _find_hsf_root(base: Path) -> Path:
            # 1. base itself has libpartdata.xml → it IS the HSF root
            if (base / "libpartdata.xml").exists():
                return base
            # 2. base itself has a scripts/ subdir → treat base as root
            if (base / "scripts").is_dir():
                return base
            # 3. one named subdir with libpartdata.xml → standard layout
            for d in sorted(base.iterdir()):
                if d.is_dir() and (d / "libpartdata.xml").exists():
                    return d
            # 4. one named subdir with scripts/ → standard layout without metadata
            for d in sorted(base.iterdir()):
                if d.is_dir() and (d / "scripts").is_dir():
                    return d
            # 5. last resort: first subdir (or base itself)
            subdirs = [d for d in base.iterdir() if d.is_dir()]
            return subdirs[0] if subdirs else base

        hsf_dir = _find_hsf_root(hsf_out)

        if not hsf_dir.exists():
            contents = list(hsf_out.iterdir())
            shutil.rmtree(tmp, ignore_errors=True)
            return (
                None,
                f"❌ 无法定位 HSF 根目录\n\n"
                f"hsf_out 内容: `{[str(c.name) for c in contents]}`\n\n"
                f"stdout: {result.stdout[:300]}\nstderr: {result.stderr[:300]}"
            )

        # Snapshot directory tree before rmtree wipes it
        hsf_files = sorted(str(p.relative_to(hsf_dir)) for p in hsf_dir.rglob("*") if p.is_file())

        proj = HSFProject.load_from_disk(str(hsf_dir))
        # AC29 flat layout: hsf_dir == hsf_out → name is "hsf_out", use GSM stem instead
        gsm_stem = Path(filename).stem
        if proj.name in ("hsf_out", "scripts", ""):
            proj.name = gsm_stem
        proj.work_dir = Path(st.session_state.work_dir)
        proj.root = proj.work_dir / proj.name

        scripts_found = [s.value for s in proj.scripts]
        diag = (
            f"\n\n**HSF 文件列表**: `{hsf_files}`"
            f"\n**已识别脚本**: `{scripts_found}`"
        )
        return (proj, f"✅ 已导入 `{proj.name}` — {len(proj.parameters)} 参数，{len(proj.scripts)} 脚本{diag}")
    except Exception as e:
        return (None, f"❌ HSF 解析失败: {e}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _handle_unified_import(uploaded_file) -> tuple[bool, str]:
    """
    Single entry point for importing any GDL-related file.
    - .gsm           → LP_XMLConverter decompile → HSFProject
    - .gdl / .txt    → parse_gdl_source text parse → HSFProject
    Updates session_state.project, pending_gsm_name, editor_version.
    Returns (success, message).
    """
    fname = uploaded_file.name
    ext   = Path(fname).suffix.lower()

    if ext == ".gsm":
        with st.spinner("解包 GSM..."):
            proj, msg = import_gsm(uploaded_file.read(), fname)
        if not proj:
            return (False, msg)
    else:
        # .gdl / .txt — plain text
        try:
            content = uploaded_file.read().decode("utf-8", errors="replace")
            proj = parse_gdl_source(content, Path(fname).stem)
        except Exception as e:
            return (False, f"❌ 导入失败: {e}")
        msg = f"✅ 已导入 GDL `{proj.name}` — {len(proj.parameters)} 参数，{len(proj.scripts)} 脚本"

    proj.work_dir = Path(st.session_state.work_dir)
    proj.root = proj.work_dir / proj.name
    st.session_state.project = proj
    st.session_state.pending_diffs = {}
    st.session_state.pending_gsm_name = proj.name
    st.session_state.editor_version += 1
    st.session_state.chat_history.append({"role": "assistant", "content": msg})
    return (True, msg)


def _strip_md_fences(code: str) -> str:
    """Remove markdown code fences (```gdl / ```) that AI sometimes leaks into scripts."""
    import re as _re
    # Remove opening fence (```gdl, ```GDL, ```)
    code = _re.sub(r'^```[a-zA-Z]*\s*\n?', '', code.strip(), flags=_re.MULTILINE)
    # Remove closing fence
    code = _re.sub(r'\n?```\s*$', '', code.strip(), flags=_re.MULTILINE)
    return code.strip()


def _classify_code_blocks(text: str) -> dict:
    """
    Extract and classify GDL/paramlist code blocks from raw text.
    Returns {script_path_or_"paramlist.xml": code}.  Last block wins per type.

    Classification priority (same as _extract_gdl_from_chat):
      1. paramlist.xml  — ≥2 lines 'Type Name = Value'
      2. scripts/2d.gdl — PROJECT2 / RECT2 / POLY2
      3. scripts/vl.gdl — VALUES or LOCK (no BLOCK)
      4. scripts/1d.gdl — GLOB_ variable
      5. scripts/ui.gdl — UI_CURRENT or DEFINE STYLE
      6. scripts/3d.gdl — default
    """
    import re as _re
    collected: dict[str, str] = {}
    code_block_pat = _re.compile(r"```[a-zA-Z]*[ \t]*\n(.*?)```", _re.DOTALL)
    _PARAM_TYPE_RE = _re.compile(
        r'^\s*(Length|Angle|RealNum|Integer|Boolean|String|PenColor|FillPattern|LineType|Material)'
        r'\s+\w+\s*=', _re.IGNORECASE | _re.MULTILINE
    )
    for m in code_block_pat.finditer(text):
        block = m.group(1).strip()
        if not block:
            continue
        block_up = block.upper()
        if len(_PARAM_TYPE_RE.findall(block)) >= 2:
            path = "paramlist.xml"
        elif _re.search(r'\bPROJECT2\b|\bRECT2\b|\bPOLY2\b', block_up):
            path = "scripts/2d.gdl"
        elif _re.search(r'\bVALUES\b|\bLOCK\b', block_up) and not _re.search(r'\bBLOCK\b', block_up):
            path = "scripts/vl.gdl"
        elif _re.search(r'\bGLOB_\w+\b', block_up):
            path = "scripts/1d.gdl"
        elif _re.search(r'\bUI_CURRENT\b|\bDEFINE\s+STYLE\b', block_up):
            path = "scripts/ui.gdl"
        else:
            path = "scripts/3d.gdl"
        collected[path] = block
    return collected


def _extract_gdl_from_text(text: str) -> dict:
    """Extract GDL code blocks from a single message string."""
    return _classify_code_blocks(text)


def _extract_gdl_from_chat() -> dict:
    """Scan all assistant messages in chat history; last block per type wins."""
    collected: dict[str, str] = {}
    for msg in st.session_state.get("chat_history", []):
        if msg.get("role") != "assistant":
            continue
        for path, block in _classify_code_blocks(msg.get("content", "")).items():
            collected[path] = block
    return collected


# ── Vision prompt ─────────────────────────────────────────────────────────────

_VISION_SYSTEM_PROMPT = """\
你是专业 GDL 建筑师，精通 ArchiCAD GDL scripting（GDL Reference v26 标准）。
用户上传了一张建筑构件/家具/设施图片，请按以下结构输出：

## 构件识别
- 类型：（书架 / 桌椅 / 门窗 / 楼梯 / 柱 / 墙面板 / 灯具 / ...）
- 几何形态：（主体形状、结构层次、细部特征，2-4句）
- 材料/表面：（可见材质，用于 Material 参数默认值）

## 参数化分析
以 GDL paramlist 格式列出所有可参数化维度，给出合理默认值（长度单位 mm，转为 m 除以 1000）：

```
Length w  = 0.9     ! 总宽度（m）
Length h  = 2.1     ! 总高度（m）
Length d  = 0.3     ! 总深度（m）
Integer n = 4       ! 重复单元数量
Material mat = "Wood"  ! 主体材质
```

## GDL 3D Script

```gdl
! [构件名称] — AI 从图片生成
! 参数：w h d n mat

MATERIAL mat

! 主体
BLOCK w, d, h

END
```

规则：
- paramlist 代码块内必须有 ≥2 行 `Type Name = value  ! 注释` 格式
- 3D Script 最后一行必须是 `END`（单独一行）
- 所有尺寸由参数驱动，禁止硬编码数字
- GDL 命令必须全大写（BLOCK / CYLIND / LINE3 / ADD / DEL / FOR / NEXT 等）
- 如有重复元素（层板/格栅/百叶）用 FOR/NEXT 循环
"""


# ── Vision generate ───────────────────────────────────────────────────────────

def run_vision_generate(
    image_b64: str,
    image_mime: str,
    extra_text: str,
    proj: HSFProject,
    status_col,
    auto_apply: bool = True,
) -> str:
    """
    Vision pipeline: image → LLM analysis → GDL extraction → pending_diffs or auto-apply.
    Reuses the same confirmation flow as run_agent_generate.
    """
    status_ph = status_col.empty()
    try:
        llm = get_llm()
        status_ph.info("🖼️ AI 正在解析图片...")

        user_text = extra_text.strip() if extra_text else "请分析这张图片，生成对应的 GDL 脚本。"
        resp = llm.generate_with_image(
            text_prompt=user_text,
            image_b64=image_b64,
            image_mime=image_mime,
            system_prompt=_VISION_SYSTEM_PROMPT,
        )
        status_ph.empty()

        raw_text = resp.content
        extracted = _classify_code_blocks(raw_text)

        if extracted:
            script_names = ", ".join(
                k.replace("scripts/", "").replace(".gdl", "").upper()
                for k in extracted if k.startswith("scripts/")
            )
            param_count = len(_parse_paramlist_text(extracted.get("paramlist.xml", "")))
            label_parts = []
            if script_names:
                label_parts.append(f"脚本 [{script_names}]")
            if param_count:
                label_parts.append(f"{param_count} 个参数")
            label_str = " + ".join(label_parts) or "内容"

            if auto_apply:
                _apply_scripts_to_project(proj, extracted)
                st.session_state.editor_version += 1
                prefix = f"🖼️ **图片解析完成，{label_str} 已写入编辑器** — 可直接「🔧 编译」\n\n"
            else:
                st.session_state.pending_diffs    = extracted
                st.session_state.pending_ai_label = label_str
                prefix = f"🖼️ **图片解析完成，AI 生成了 {label_str}** — 请在下方确认是否写入\n\n"

            return prefix + raw_text

        else:
            return f"🖼️ **图片分析完成**（未检测到 GDL 代码块，AI 可能只给了文字分析）\n\n{raw_text}"

    except Exception as e:
        status_ph.empty()
        return (
            f"❌ **图片解析失败**: {str(e)}\n\n"
            "💡 当前模型可能不支持图片输入，请切换到 **Claude Sonnet / GPT-4o / Gemini** 等多模态模型。"
        )


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
#  Layout: Editor (left/main) | AI Chat (right sidebar)
# ══════════════════════════════════════════════════════════

col_editor, col_chat = st.columns([3, 2], gap="small")


# ── Left: Code Editor (always visible) ───────────────────

_SCRIPT_HELP = {
    "scripts/3d.gdl": (
        "**3D 脚本** — 三维几何体定义，ArchiCAD 3D 窗口中显示的实体。\n\n"
        "- 使用 `PRISM_`、`BLOCK`、`SPHERE`、`CONE`、`REVOLVE` 等命令建模\n"
        "- `ADD` / `DEL` 管理坐标系变换，必须成对出现\n"
        "- `FOR` / `NEXT` 循环用于重复构件（如格栅、层板）\n"
        "- **最后一行必须是 `END`**，否则编译失败"
    ),
    "scripts/2d.gdl": (
        "**2D 脚本** — 平面图符号，ArchiCAD 楼层平面图中显示的线条。\n\n"
        "- **必须包含** `PROJECT2 3, 270, 2`（最简投影）或自定义 2D 线条\n"
        "- 不写或留空会导致平面图中对象不可见"
    ),
    "scripts/1d.gdl": (
        "**Master 脚本** — 主控脚本，所有脚本执行前最先运行。\n\n"
        "- 全局变量初始化、参数联动逻辑\n"
        "- 简单对象通常不需要此脚本"
    ),
    "scripts/vl.gdl": (
        "**Param 脚本** — 参数验证脚本，参数值变化时触发。\n\n"
        "- 参数范围约束、派生参数计算\n"
        "- 简单对象通常不需要此脚本"
    ),
    "scripts/ui.gdl": (
        "**UI 脚本** — 自定义参数界面，ArchiCAD 对象设置对话框控件布局。\n\n"
        "- 不写则 ArchiCAD 自动生成默认参数列表界面"
    ),
    "scripts/pr.gdl": (
        "**Properties 脚本** — BIM 属性输出，定义 IFC 属性集和构件属性。\n\n"
        "- 不做 BIM 数据输出可留空"
    ),
}

with col_editor:
    with st.container(height=820, border=False):
        with st.container(height=820, border=False):
            # ── Auto-init empty project so editor is always visible ──
            if not st.session_state.project:
                st.session_state.project = HSFProject.create_new(
                    "untitled", work_dir=st.session_state.work_dir
                )
            proj_now = st.session_state.project
            _ev      = st.session_state.editor_version

            # ── Row 1: Import (left) | 🔧 编译 (right, primary/prominent) ──
            tb_import, tb_compile_top = st.columns([1.8, 2.2])

            with tb_import:
                any_upload = st.file_uploader(
                    "📂 导入 gdl / txt / gsm", type=["gdl", "txt", "gsm"],
                    key="editor_import",
                    help=".gdl/.txt → 解析脚本  |  .gsm → LP_XMLConverter 解包",
                )
                if any_upload:
                    # Dedup: skip if this exact file was already processed this session
                    _fkey = f"{any_upload.name}_{any_upload.size}"
                    if st.session_state._import_key_done != _fkey:
                        ok, _imp_msg = _handle_unified_import(any_upload)
                        if ok:
                            st.session_state._import_key_done = _fkey
                            st.rerun()
                        else:
                            st.error(_imp_msg)

            with tb_compile_top:
                # GSM name input + compile button stacked in this column
                gsm_name_input = st.text_input(
                    "GSM名称", label_visibility="collapsed",
                    value=st.session_state.pending_gsm_name or proj_now.name,
                    placeholder="输出 GSM 名称（不含扩展名）",
                    key="toolbar_gsm_name",
                    help="编译输出文件名",
                )
                st.session_state.pending_gsm_name = gsm_name_input
                if st.button("🔧  编  译  GSM", type="primary", use_container_width=True,
                             help="将当前所有脚本编译为 ArchiCAD .gsm 对象"):
                    with st.spinner("编译中..."):
                        success, result_msg = do_compile(
                            proj_now,
                            gsm_name=gsm_name_input or proj_now.name,
                            instruction="(toolbar compile)",
                        )
                    st.session_state.compile_result = (success, result_msg)
                    if success:
                        st.toast("✅ 编译成功", icon="🏗️")
                    st.rerun()

            # ── Compile result banner ─────────────────────────────
            if st.session_state.compile_result is not None:
                _c_ok, _c_msg = st.session_state.compile_result
                if _c_ok:
                    st.success(_c_msg)
                else:
                    st.error(_c_msg)

            # ── Archicad 测试按钮 ─────────────────────────────
            if _TAPIR_IMPORT_OK:
                _bridge = get_bridge()
                _tapir_ok = _bridge.is_available()
                if _tapir_ok:
                    _ac_col1, _ac_col2 = st.columns([2, 3])
                    with _ac_col1:
                        if st.button("🏗️ 在 Archicad 中测试", use_container_width=True,
                                     help="触发 Archicad 重新加载库，捕获 GDL 运行期错误回传到 chat"):
                            st.session_state.tapir_test_trigger = True
                            st.rerun()
                    with _ac_col2:
                        st.caption("✅ Archicad + Tapir 已连接")
                else:
                    st.caption("⚪ Archicad 未运行或 Tapir 未安装，跳过实时测试")

            # ── Row 2: 全检查 | 清空 | 日志 ──────────────────────
            tb_check, tb_clear, tb_log_btn = st.columns([1.2, 1.0, 1.0])

            with tb_check:
                if st.button("🔍 全检查", use_container_width=True):
                    _check_all_ok = True
                    for _stype, _fpath, _label in _SCRIPT_MAP:
                        _chk_content = proj_now.get_script(_stype)
                        if not _chk_content:
                            continue
                        _skey = _fpath.replace("scripts/", "").replace(".gdl", "")
                        for _iss in check_gdl_script(_chk_content, _skey):
                            if _iss.startswith("✅"):
                                st.success(f"{_label}: {_iss}")
                            else:
                                st.warning(f"{_label}: {_iss}")
                                _check_all_ok = False
                    if _check_all_ok:
                        st.success("✅ 所有脚本语法正常")

            with tb_clear:
                if st.button("🗑️ 清空", use_container_width=True, help="重置项目：脚本、参数、日志全清，保留设置"):
                    st.session_state.confirm_clear = True

            with tb_log_btn:
                if st.button("📋 日志", use_container_width=True):
                    st.session_state["_show_log_dialog"] = True

            # ── 日志弹窗 ──────────────────────────────────────────
            @st.dialog("📋 编译日志")
            def _show_log_dialog():
                if not st.session_state.compile_log:
                    st.info("暂无编译记录")
                else:
                    for _entry in reversed(st.session_state.compile_log):
                        _icon = "✅" if _entry["success"] else "❌"
                        st.markdown(f"**{_icon} {_entry['project']}** — {_entry.get('instruction','')}")
                        st.code(_entry["message"], language="text")
                        st.divider()
                if st.button("清除日志"):
                    st.session_state.compile_log = []
                    st.session_state.compile_result = None
                    st.rerun()

            if st.session_state.get("_show_log_dialog"):
                st.session_state["_show_log_dialog"] = False
                _show_log_dialog()

            # ── 清空确认 ──────────────────────────────────────────
            if st.session_state.get("confirm_clear"):
                st.warning("⚠️ 将重置项目（脚本、参数、编译日志），聊天记录保留。确认继续？")
                cc1, cc2, _ = st.columns([1, 1, 4])
                with cc1:
                    if st.button("✅ 确认清空", type="primary"):
                        _keep_work_dir = st.session_state.work_dir
                        _keep_api_keys = st.session_state.model_api_keys
                        _keep_chat     = st.session_state.chat_history   # preserve chat
                        st.session_state.project          = None
                        st.session_state.compile_log      = []
                        st.session_state.compile_result   = None
                        st.session_state.pending_diffs    = {}
                        st.session_state.pending_ai_label = ""
                        st.session_state.pending_gsm_name = ""
                        st.session_state.agent_running    = False
                        st.session_state._import_key_done = ""
                        st.session_state.confirm_clear    = False
                        st.session_state.editor_version  += 1
                        st.session_state.work_dir         = _keep_work_dir
                        st.session_state.model_api_keys   = _keep_api_keys
                        st.session_state.chat_history     = _keep_chat
                        st.toast("🗑️ 已重置项目（脚本、参数、日志），聊天记录保留", icon="✅")
                        st.rerun()
                with cc2:
                    if st.button("❌ 取消"):
                        st.session_state.confirm_clear = False
                        st.rerun()

            st.divider()

            # ── Script / Param Tabs ───────────────────────────────
            tab_labels = ["参数"] + [lbl for _, _, lbl in _SCRIPT_MAP]
            all_tabs   = st.tabs(tab_labels)
            tab_params, *script_tabs = all_tabs

            # Params tab
            with tab_params:
                with st.expander("ℹ️ 参数说明"):
                    st.markdown(
                        "**参数列表** — GDL 对象的可调参数。\n\n"
                        "- **Type**: `Length` / `Integer` / `Boolean` / `Material` / `String`\n"
                        "- **Name**: 代码中引用的变量名（camelCase，如 `iShelves`）\n"
                        "- **Value**: 默认值\n"
                        "- **Fixed**: 勾选后用户无法在 ArchiCAD 中修改"
                    )
                param_data = [
                    {"Type": p.type_tag, "Name": p.name, "Value": p.value,
                     "Description": p.description, "Fixed": "✓" if p.is_fixed else ""}
                    for p in proj_now.parameters
                ]
                if param_data:
                    st.dataframe(param_data, use_container_width=True, hide_index=True)
                else:
                    st.caption("暂无参数，通过 AI 对话添加，或手动添加。")

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

            # Script tabs
            for tab, (stype, fpath, label) in zip(script_tabs, _SCRIPT_MAP):
                with tab:
                    _tab_help_col, _tab_fs_col = st.columns([6, 1])
                    with _tab_help_col:
                        with st.expander(f"ℹ️ {label} 脚本说明"):
                            st.markdown(_SCRIPT_HELP.get(fpath, ""))
                    with _tab_fs_col:
                        if st.button("⛶", key=f"fs_{fpath}_v{_ev}",
                                     help="全屏编辑", use_container_width=True):
                            _fullscreen_editor_dialog(stype, fpath, label)

                    current_code = proj_now.get_script(stype) or ""
                    skey = fpath.replace("scripts/", "").replace(".gdl", "")

                    if _ACE_AVAILABLE:
                        _raw_ace = st_ace(
                            value=current_code,
                            language="fortran",   # closest built-in: `!` comments + keyword structure
                            theme="monokai",
                            height=280,
                            font_size=13,
                            tab_size=2,
                            show_gutter=True,
                            show_print_margin=False,
                            wrap=False,
                            key=f"ace_{fpath}_v{_ev}",
                        )
                        # st_ace returns None on first render (widget not yet initialized).
                        # NEVER let None → "" silently overwrite real script content.
                        new_code = _raw_ace if _raw_ace is not None else current_code
                    else:
                        new_code = st.text_area(
                            label, value=current_code, height=280,
                            key=f"script_{fpath}_v{_ev}", label_visibility="collapsed",
                        ) or ""  # text_area never returns None; empty string is a valid clear

                    if new_code != current_code:
                        proj_now.set_script(stype, new_code)

            # Log tab



        # ── Right: AI Chat panel ──────────────────────────────────

with col_chat:
    with st.container(height=820, border=False):
        with st.container(height=820, border=False):
            _chat_proj = st.session_state.project
            _chat_title_col, _chat_clear_col = st.columns([3, 1])
            with _chat_title_col:
                if _chat_proj:
                    st.markdown(f"### 💬 {_chat_proj.name}")
                    st.caption(f"参数: {len(_chat_proj.parameters)} | 脚本: {len(_chat_proj.scripts)}")
                else:
                    st.markdown("### 💬 AI 助手")
                    st.caption("描述需求，AI 自动创建 GDL 对象写入编辑器")
            with _chat_clear_col:
                if st.button("🗑️ 清空对话", use_container_width=True, help="清空聊天记录，不影响脚本和参数"):
                    st.session_state.chat_history = []
                    st.session_state.adopted_msg_index = None
                    st.rerun()

            # Chat history with action bar on each assistant message
            for _i, _msg in enumerate(st.session_state.chat_history):
                with st.chat_message(_msg["role"]):
                    st.markdown(_msg["content"])
                    if _msg["role"] == "assistant":
                        _ca, _cb, _cc, _cd, _ce = st.columns([1, 1, 1, 1, 8])
                        with _ca:
                            if st.button("👍", key=f"like_{_i}", help="有帮助"):
                                _save_feedback(_i, "positive", _msg["content"])
                                st.toast("已记录 👍", icon="✅")
                        with _cb:
                            if st.button("👎", key=f"dislike_{_i}", help="需改进"):
                                _save_feedback(_i, "negative", _msg["content"])
                                st.toast("已记录 👎，感谢反馈")
                        with _cc:
                            if st.button("📋", key=f"copy_{_i}", help="展开可复制内容"):
                                _flag = f"_showcopy_{_i}"
                                st.session_state[_flag] = not st.session_state.get(_flag, False)
                        with _cd:
                            _prev_user = next(
                                (st.session_state.chat_history[j]["content"]
                                 for j in range(_i - 1, -1, -1)
                                 if st.session_state.chat_history[j]["role"] == "user"),
                                None,
                            )
                            if _prev_user and st.button("🔄", key=f"redo_{_i}", help="重新生成"):
                                st.session_state.chat_history = st.session_state.chat_history[:_i]
                                st.session_state["_redo_input"] = _prev_user
                                st.rerun()
                        with _ce:
                            _has_code = "```" in _msg.get("content", "")
                            if _has_code:
                                _is_adopted = st.session_state.adopted_msg_index == _i
                                _adopt_label = "✅ 已采用" if _is_adopted else "📥 采用这套"
                                if st.button(_adopt_label, key=f"adopt_{_i}", use_container_width=True):
                                    st.session_state["_pending_adopt_idx"] = _i
                if st.session_state.get(f"_showcopy_{_i}", False):
                    st.code(_msg["content"], language="text")

            # ── 采用这套：确认弹窗 ─────────────────────────────
            @st.dialog("📥 采用这套代码")
            def _adopt_confirm_dialog(msg_idx):
                st.warning("将用此套代码覆盖对应脚本/参数，消息中未包含的部分保留不变，确认？")
                _da, _db = st.columns(2)
                with _da:
                    if st.button("✅ 确认覆盖", type="primary", use_container_width=True):
                        _msg_content = st.session_state.chat_history[msg_idx]["content"]
                        extracted = _extract_gdl_from_text(_msg_content)
                        if extracted:
                            # 只覆盖此消息中实际包含的脚本/参数，其余保留
                            if st.session_state.project:
                                _apply_scripts_to_project(st.session_state.project, extracted)
                            st.session_state.editor_version += 1
                            st.session_state.adopted_msg_index = msg_idx
                            st.session_state["_pending_adopt_idx"] = None
                            st.toast("✅ 已写入编辑器", icon="📥")
                            st.rerun()
                        else:
                            st.error("未找到可提取的代码块")
                with _db:
                    if st.button("❌ 取消", use_container_width=True):
                        st.session_state["_pending_adopt_idx"] = None
                        st.rerun()

            if st.session_state.get("_pending_adopt_idx") is not None:
                _adopt_confirm_dialog(st.session_state["_pending_adopt_idx"])

            # ── Pending AI changes — confirmation widget (in chat flow) ──
            if st.session_state.pending_diffs:
                _pd = st.session_state.pending_diffs
                _pn_s = sum(1 for k in _pd if k.startswith("scripts/"))
                _pn_p = len(_parse_paramlist_text(_pd.get("paramlist.xml", "")))
                _pd_parts = []
                if _pn_s: _pd_parts.append(f"{_pn_s} 个脚本")
                if _pn_p: _pd_parts.append(f"{_pn_p} 个参数")
                _pd_label = "、".join(_pd_parts) or st.session_state.pending_ai_label or "新内容"

                st.info(f"⬆️ **是否将 AI 生成的 {_pd_label} 写入编辑器？**")
                _pac1, _pac2, _pac3 = st.columns([1.2, 1, 5])
                with _pac1:
                    if st.button("✅ 写入", type="primary", use_container_width=True,
                                 key="chat_pending_apply"):
                        _proj = st.session_state.project
                        if _proj:
                            sc, pc = _apply_scripts_to_project(_proj, _pd)
                            _ok_parts = []
                            if sc: _ok_parts.append(f"{sc} 个脚本")
                            if pc: _ok_parts.append(f"{pc} 个参数")
                            st.session_state.editor_version += 1
                            st.toast(f"✅ 已写入 {'、'.join(_ok_parts)}", icon="✏️")
                        st.session_state.pending_diffs    = {}
                        st.session_state.pending_ai_label = ""
                        st.rerun()
                with _pac2:
                    if st.button("❌ 忽略", use_container_width=True,
                                 key="chat_pending_discard"):
                        st.session_state.pending_diffs    = {}
                        st.session_state.pending_ai_label = ""
                        st.rerun()

            # ── 图片上传（🖼️ 图片即意图）────────────────────────────────
            with st.expander("📷 上传图片 → AI 直接生成 GDL", expanded=False):
                st.caption("支持 JPG / PNG / WebP · 推荐模型：Claude Sonnet / GPT-4o / Gemini")
                _vision_file = st.file_uploader(
                    "",
                    type=["jpg", "jpeg", "png", "webp", "gif"],
                    key=f"vision_upload_{st.session_state.vision_upload_key}",
                    label_visibility="collapsed",
                )
                if _vision_file is not None:
                    _raw_bytes = _vision_file.read()
                    st.image(_raw_bytes, width=220)
                    st.session_state["_vision_b64"]  = base64.b64encode(_raw_bytes).decode()
                    st.session_state["_vision_mime"] = _vision_file.type or "image/jpeg"
                    st.session_state["_vision_name"] = _vision_file.name
                    if st.button(
                        "🖼️ 分析图片 → 生成 GDL",
                        type="primary",
                        use_container_width=True,
                        key="vision_submit_btn",
                    ):
                        st.session_state["_vision_trigger"] = True
                        st.rerun()
                elif "_vision_b64" in st.session_state:
                    # File cleared by user (clicked ✕ on uploader)
                    st.session_state.pop("_vision_b64", None)
                    st.session_state.pop("_vision_mime", None)
                    st.session_state.pop("_vision_name", None)

            # Live agent output placeholder (anchored inside this column)
            live_output = st.empty()

            # ── Debug 模式开关 ────────────────────────────────
            _cur_dbg = st.session_state.get("_debug_mode_active")
            _dbg_col1, _dbg_col2, _dbg_off = st.columns([1.4, 1.4, 1.2])
            with _dbg_col1:
                _e_label = "✅ Debug 编辑器" if _cur_dbg == "editor" else "🔍 Debug 编辑器"
                if st.button(_e_label, use_container_width=True,
                             help="激活后：下次发送将附带编辑器全部脚本+参数+语法检查报告"):
                    st.session_state["_debug_mode_active"] = None if _cur_dbg == "editor" else "editor"
                    st.rerun()
            with _dbg_col2:
                _l_label = "✅ Debug 上条" if _cur_dbg == "last" else "🔍 Debug 上条"
                if st.button(_l_label, use_container_width=True,
                             help="激活后：下次发送将附带 AI 最近一次生成的代码+语法检查报告"):
                    st.session_state["_debug_mode_active"] = None if _cur_dbg == "last" else "last"
                    st.rerun()
            with _dbg_off:
                if _cur_dbg and st.button("✖ 取消", use_container_width=True):
                    st.session_state["_debug_mode_active"] = None
                    st.rerun()

            # Debug激活时显示语法检查报告
            if _cur_dbg == "editor" and _chat_proj:
                _syntax_issues = []
                for _stype, _fpath, _slabel in _SCRIPT_MAP:
                    _sc = _chat_proj.get_script(_stype)
                    if not _sc:
                        continue
                    _sk = _fpath.replace("scripts/", "").replace(".gdl", "")
                    for _iss in check_gdl_script(_sc, _sk):
                        if not _iss.startswith("✅"):
                            _syntax_issues.append(f"{_slabel}: {_iss}")
                if _syntax_issues:
                    _report_str = "\n".join(_syntax_issues)
                    st.warning(f"⚠️ 语法检查报告（将随 debug 发送给 AI）：\n{_report_str}")
                else:
                    st.success("✅ 语法检查通过，输入 debug 方向后发送")
            elif _cur_dbg == "last":
                st.info("💬 将对 AI 最近一次生成的代码进行 debug，输入方向后发送")

            # Chat input — immediately below message list / confirmation widget
            user_input = st.chat_input(
                "描述需求、提问，或搭配图片补充说明…"
            )


        # ══════════════════════════════════════════════════════════
        #  Chat handler (outside columns — session state + rerun)
        # ══════════════════════════════════════════════════════════

        _redo_input      = st.session_state.pop("_redo_input", None)
        _vision_trigger  = st.session_state.pop("_vision_trigger", False)
        _vision_b64      = st.session_state.get("_vision_b64")
        _active_dbg      = st.session_state.get("_debug_mode_active")
        _tapir_trigger   = st.session_state.pop("tapir_test_trigger", False)

        # ── Archicad 测试：ReloadLibraries + 捕获错误注入 chat ──
        if _tapir_trigger and _TAPIR_IMPORT_OK:
            _bridge = get_bridge()
            _proj_for_tapir = st.session_state.project
            with st.spinner("🏗️ 触发 Archicad 重新加载库，等待渲染..."):
                _reload_ok, _gdl_errors = _bridge.reload_and_capture(
                    timeout=6.0,
                    project=_proj_for_tapir,
                )
            if _reload_ok:
                _error_msg = errors_to_chat_message(_gdl_errors)
                st.session_state.chat_history.append({
                    "role": "assistant",
                    "content": _error_msg,
                })
                if _gdl_errors:
                    # 自动触发debug：把错误作为context发给LLM
                    _auto_debug = f"[DEBUG:editor] 请根据以上 Archicad 报错修复脚本"
                    st.session_state.chat_history.append({
                        "role": "user",
                        "content": _auto_debug,
                    })
                    st.session_state["_auto_debug_input"] = _auto_debug
                st.rerun()
            else:
                st.toast("❌ Archicad 连接失败，请确认 Archicad 正在运行", icon="⚠️")

        # Debug模式：用户发送时附带前缀+语法检查报告
        if _active_dbg and user_input:
            _dbg_prefix = f"[DEBUG:{_active_dbg}]"
            _syntax_report_lines = []
            _proj_for_check = st.session_state.project
            if _proj_for_check:
                for _stype, _fpath, _slabel in _SCRIPT_MAP:
                    _sc = _proj_for_check.get_script(_stype)
                    if not _sc:
                        continue
                    _sk = _fpath.replace("scripts/", "").replace(".gdl", "")
                    for _iss in check_gdl_script(_sc, _sk):
                        if not _iss.startswith("✅"):
                            _syntax_report_lines.append(f"{_slabel}: {_iss}")
            _syntax_report = ""
            if _syntax_report_lines:
                _syntax_report = "\n[SYNTAX CHECK REPORT]\n" + "\n".join(_syntax_report_lines)
            effective_input = f"{_dbg_prefix} {user_input.strip()}{_syntax_report}"
            st.session_state["_debug_mode_active"] = None
        else:
            _auto_debug_input = st.session_state.pop("_auto_debug_input", None)
            effective_input = _auto_debug_input or _redo_input or user_input

        # ── Vision path: image uploaded + "分析图片" button clicked ──────────────────
        if _vision_trigger and _vision_b64:
            _vision_mime = st.session_state.get("_vision_mime", "image/jpeg")
            _vision_name = st.session_state.get("_vision_name", "image")
            _extra_text  = user_input or ""  # optional supplementary text from chat_input

            _user_display = f"🖼️ `{_vision_name}`" + (f"  \n{_extra_text}" if _extra_text else "")
            st.session_state.chat_history.append({"role": "user", "content": _user_display})

            if not api_key and "ollama" not in model_name:
                err = "❌ 请在左侧边栏填入 API Key 后再试。"
                st.session_state.chat_history.append({"role": "assistant", "content": err})
                st.rerun()
            else:
                # Ensure project exists
                if not st.session_state.project:
                    _vname = Path(_vision_name).stem or "vision_object"
                    _vproj = HSFProject.create_new(_vname, work_dir=st.session_state.work_dir)
                    st.session_state.project = _vproj
                    st.session_state.pending_gsm_name = _vname

                _proj_v = st.session_state.project
                _has_any_v = any(_proj_v.get_script(s) for s, _, _ in _SCRIPT_MAP)

                with live_output.container():
                    st.chat_message("user").markdown(_user_display)
                    with st.chat_message("assistant"):
                        msg = run_vision_generate(
                            image_b64=_vision_b64,
                            image_mime=_vision_mime,
                            extra_text=_extra_text,
                            proj=_proj_v,
                            status_col=st.container(),
                            auto_apply=not _has_any_v,
                        )
                        st.markdown(msg)

                st.session_state.chat_history.append({"role": "assistant", "content": msg})

                # Reset image uploader by incrementing key, clear stored image
                st.session_state.vision_upload_key += 1
                st.session_state.pop("_vision_b64", None)
                st.session_state.pop("_vision_mime", None)
                st.session_state.pop("_vision_name", None)
                st.rerun()

        # ── Normal text path ─────────────────────────────────────────────────────────
        elif effective_input:
            # Redo: user msg already in history; new: append it
            if not _redo_input:
                st.session_state.chat_history.append({"role": "user", "content": effective_input})
            user_input = effective_input   # alias for rest of handler

            if not api_key and "ollama" not in model_name:
                err = "❌ 请在左侧边栏填入 API Key 后再试。"
                st.session_state.chat_history.append({"role": "assistant", "content": err})
                st.rerun()
            else:
                llm_for_classify = get_llm()
                intent, gdl_obj_name = classify_and_extract(
                    user_input, llm_for_classify,
                    project_loaded=bool(st.session_state.project),
                )

                with live_output.container():
                    st.chat_message("user").markdown(user_input)
                    with st.chat_message("assistant"):
                        if intent == "CHAT":
                            msg = chat_respond(
                                user_input,
                                st.session_state.chat_history[:-1],
                                llm_for_classify,
                            )
                            st.markdown(msg)
                        else:
                            if not st.session_state.project:
                                new_proj = HSFProject.create_new(gdl_obj_name, work_dir=st.session_state.work_dir)
                                st.session_state.project = new_proj
                                st.session_state.pending_gsm_name = gdl_obj_name
                                st.info(f"📁 已初始化项目 `{gdl_obj_name}`")

                            proj_current = st.session_state.project
                            # 只有全新空项目（无任何脚本内容）才自动写入；
                            # 已有脚本的项目修改时显示确认按钮，防止意外覆盖。
                            _has_any_script = any(
                                proj_current.get_script(s) for s, _, _ in _SCRIPT_MAP
                            )
                            effective_gsm = st.session_state.pending_gsm_name or proj_current.name
                            msg = run_agent_generate(
                                user_input, proj_current, st.container(),
                                gsm_name=effective_gsm,
                                auto_apply=not _has_any_script,
                            )
                            st.markdown(msg)

                st.session_state.chat_history.append({"role": "assistant", "content": msg})
                st.rerun()


        # ── Footer ────────────────────────────────────────────────
        st.divider()
        st.markdown(
            '<p style="text-align:center; color:#64748b; font-size:0.8rem;">'
            'OpenBrep v0.5 · HSF-native · Code Your Boundaries ·'
            '<a href="https://github.com/byewind1/openbrep">GitHub</a>'
            '</p>',
            unsafe_allow_html=True,
        )
