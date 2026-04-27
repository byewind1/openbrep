from __future__ import annotations

import subprocess
from typing import Any


APP_CSS = """
<style>
.stApp { font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif; }
code, .stCodeBlock { font-family: 'SF Mono', 'Menlo', 'Monaco', monospace !important; }

section[data-testid="stSidebar"] .stMarkdown p.main-header {
    font-family: 'SF Mono', 'Menlo', 'Courier New', monospace !important;
    font-size: 2.8rem !important;
    font-weight: 900 !important;
    text-align: center !important;
    display: block !important;
    width: 100% !important;
    white-space: nowrap;
    background: linear-gradient(135deg, #22d3ee, #34d399);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    margin: 0 0 0.12rem 0 !important;
    line-height: 0.95 !important;
}
.intro-header {
    color: #cbd5e1;
    font-size: 0.92rem;
    margin-top: 0.15rem;
    margin-bottom: 0.25rem;
    line-height: 1.45;
}
.sub-header {
    color: #94a3b8;
    font-size: 0.86rem;
    margin-top: 0;
    margin-bottom: 1.2rem;
}

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

div[data-testid="stHorizontalBlock"] {
    gap: 1rem !important;
}
div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"]:last-child {
    border-left: 1px solid #1e293b;
    padding-left: 0.75rem;
}
</style>
"""


def configure_page(st) -> None:
    st.set_page_config(
        page_title="openbrep",
        page_icon="🏗️",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(APP_CSS, unsafe_allow_html=True)


def is_archicad_running() -> bool:
    try:
        result = subprocess.run(
            ["pgrep", "-x", "Archicad"],
            capture_output=True,
            timeout=1,
        )
        return result.returncode == 0
    except Exception:
        return False


def load_streamlit_ace() -> tuple[Any, bool]:
    try:
        from streamlit_ace import st_ace

        return st_ace, True
    except ImportError:
        return None, False


def load_plotly_graph_objects() -> tuple[Any, bool]:
    try:
        import plotly.graph_objects as go

        return go, True
    except ImportError:
        return None, False


def load_tapir_bridge() -> tuple[Any, Any, bool]:
    try:
        from openbrep.tapir_bridge import get_bridge, errors_to_chat_message

        return get_bridge, errors_to_chat_message, True
    except ImportError:
        def _missing_bridge(*_args, **_kwargs):
            raise ImportError("Tapir bridge is not available")

        def _missing_errors_to_chat_message(errors):
            return str(errors or "")

        return _missing_bridge, _missing_errors_to_chat_message, False
