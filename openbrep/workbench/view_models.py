"""Domain helpers for classifying LLM output and vision errors.

Migrated from ui/view_models.py — no Streamlit dependency.
"""
from __future__ import annotations

import re

from openbrep.gdl_sanitizer import sanitize_llm_script_output


_PARAM_TYPE_RE = re.compile(
    r'^\s*(Length|Angle|RealNum|Integer|Boolean|String|PenColor|FillPattern|LineType|Material)'
    r'\s+\w+\s*=',
    re.IGNORECASE | re.MULTILINE,
)


def infer_code_block_path(block: str) -> str:
    block_up = (block or "").upper()
    if len(_PARAM_TYPE_RE.findall(block or "")) >= 2:
        return "paramlist.xml"
    if re.search(r'\bPROJECT2\b|\bRECT2\b|\bPOLY2\b', block_up):
        return "scripts/2d.gdl"
    if re.search(r'\bVALUES\b|\bLOCK\b', block_up) and not re.search(r'\bBLOCK\b', block_up):
        return "scripts/vl.gdl"
    if re.search(r'\bGLOB_\w+\b', block_up):
        return "scripts/1d.gdl"
    if re.search(r'\bUI_CURRENT\b|\bDEFINE\s+STYLE\b|\bUI_DIALOG\b|\bUI_PAGE\b|\bUI_INFIELD\b|\bUI_OUTFIELD\b|\bUI_BUTTON\b|\bUI_GROUPBOX\b|\bUI_LISTFIELD\b|\bUI_SEPARATOR\b', block_up):
        return "scripts/ui.gdl"
    return "scripts/3d.gdl"


def _looks_like_gdl_script(text: str) -> bool:
    if not (text or "").strip():
        return False
    up = text.upper()
    command_hits = len(re.findall(
        r"(?m)^\s*(BLOCK|ADD|DEL|MATERIAL|FOR|NEXT|IF|ENDIF|END|PROJECT2|RECT2|POLY2|VALUES|PARAMETERS|TOLER)\b",
        up,
    ))
    return command_hits >= 3 and bool(re.search(r"(?m)^\s*END\s*$", up))


def classify_code_blocks(text: str) -> dict[str, str]:
    collected: dict[str, str] = {}
    code_block_pat = re.compile(r"```[a-zA-Z]*[ \t]*\n(.*?)```", re.DOTALL)
    for match in code_block_pat.finditer(text or ""):
        block = match.group(1).strip()
        if not block:
            continue
        path = infer_code_block_path(block)
        collected[path] = sanitize_llm_script_output(block, path)

    if not collected:
        path = infer_code_block_path(text or "")
        candidate = sanitize_llm_script_output(text or "", path)
        if _looks_like_gdl_script(candidate):
            collected[path] = candidate
    return collected


def classify_vision_error(exc: Exception) -> str:
    msg = str(exc).strip() or exc.__class__.__name__
    lower_msg = msg.lower()
    if isinstance(exc, TimeoutError) or "timeout" in lower_msg or "timed out" in lower_msg:
        return "图片分析超时：请换更小的图片，或检查当前模型服务/代理是否响应正常。"
    if "配置错误" in msg or "api key" in lower_msg or "authentication" in lower_msg or "unauthorized" in lower_msg:
        return msg
    if any(token in lower_msg for token in ["payload", "too large", "413", "context length", "image too large", "request entity too large"]):
        return "图片过大或请求体过长：请压缩图片，或减少附带说明后重试。"
    if any(token in lower_msg for token in ["vision", "image_url", "image", "unsupported"]):
        return f"当前模型或网关不支持图片分析：{msg}"
    return f"图片分析失败：{msg}"
