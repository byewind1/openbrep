from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Callable

from openbrep.gdl_sanitizer import sanitize_llm_script_output, strip_md_fences


GDL_KEYWORDS = [
    "创建", "生成", "制作", "做一个", "建一个", "写一个", "写个", "写一",
    "做个", "建个", "来个", "整个", "出一个", "出个",
    "修改", "更新", "添加", "删除", "调整", "优化", "重写", "补充",
    "书架", "柜子", "衣柜", "橱柜", "储物柜", "鞋柜", "电视柜",
    "桌子", "桌", "椅子", "椅", "沙发", "床", "茶几", "柜",
    "窗", "门", "墙", "楼梯", "柱", "梁", "板", "扶手", "栏杆",
    "屋顶", "天花", "地板", "灯", "管道",
    "参数", "parameter", "script", "gdl", "gsm", "hsf",
    "compile", "编译", "build", "create", "make", "add",
    "3d", "2d", "prism", "block", "sphere", "prism_", "body",
    "project2", "rect2", "poly2",
]


CHAT_ONLY_PATTERNS = [
    r"^(你好|hello|hi|hey|嗨|哈喽)[!！。\s]*$",
    r"^(谢谢|感谢|thanks)[!！。\s]*$",
    r"^你(是谁|能做什么|有什么功能)",
    r"^(怎么|如何|什么是).*(gdl|archicad|hsf|构件)",
]


def build_generation_reply(plain_text: str, result_prefix: str = "", code_blocks: list[str] | None = None) -> str:
    reply_parts = []
    if plain_text:
        reply_parts.append(plain_text)
    if result_prefix:
        joined_blocks = "\n\n".join(code_blocks or [])
        reply_parts.append(result_prefix + joined_blocks)
    if reply_parts:
        return "\n\n---\n\n".join(reply_parts)
    return "🤔 AI 未返回代码或分析，请换一种描述方式。"


def is_gdl_intent(text: str) -> bool:
    t = (text or "").lower()
    return any(keyword in t for keyword in GDL_KEYWORDS)


def is_pure_chat(text: str) -> bool:
    return any(re.search(pattern, (text or "").strip(), re.IGNORECASE) for pattern in CHAT_ONLY_PATTERNS)


def classify_and_extract_result(intent: str, obj_name: str) -> tuple[str, str]:
    return (("CHAT" if intent == "CHAT" else "GDL"), obj_name)


def build_chat_respond_request_kwargs(
    user_input: str,
    *,
    project,
    work_dir: str,
    trimmed_history: list,
    assistant_settings: str,
) -> dict:
    return {
        "user_input": user_input,
        "intent": "CHAT",
        "project": project,
        "work_dir": work_dir,
        "history": trimmed_history,
        "assistant_settings": assistant_settings,
    }


def derive_gsm_name_from_filename(filename: str) -> str:
    stem = Path(filename).stem.strip()
    if not stem:
        return ""

    name = stem
    for _ in range(3):
        before = name
        name = re.sub(r'(?i)[\s._-]*v\d+(?:\.\d+)*$', '', name).strip(" _-.")
        name = re.sub(r'[\s._-]*\d+$', '', name).strip(" _-.")
        if name == before:
            break

    return name or stem.strip(" _-.")


def extract_gsm_name_candidate(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""

    if t.startswith("[DEBUG:") and "]" in t:
        t = t.split("]", 1)[1].strip()

    patterns = [
        r'(?:生成|创建|制作|做一个|做个|建一个|建个)\s*(?:一个|个)?\s*([A-Za-z0-9_\-\u4e00-\u9fff]{1,40})',
        r'(?:生成|创建|制作)\s*([A-Za-z0-9_\-\u4e00-\u9fff]{1,40})',
    ]
    for pattern in patterns:
        match = re.search(pattern, t)
        if match:
            return match.group(1).strip(" _-.")
    return ""


def stamp_script_header(script_label: str, content: str, revision: int) -> str:
    body = content or ""
    header = f"! v{revision} {date.today().isoformat()} {script_label} Script"

    lines = body.splitlines()
    if lines and re.match(r'^\!\s*v\d+\s+\d{4}-\d{2}-\d{2}\s+.+\s+Script\s*$', lines[0].strip(), re.IGNORECASE):
        lines[0] = header
        return "\n".join(lines)
    return f"{header}\n{body}" if body else header


def empty_license_record() -> dict:
    return {
        "status": "free",
        "pro_unlocked": False,
    }


def urlsafe_b64decode(data: str) -> bytes:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("utf-8"))


def urlsafe_b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def canonical_license_payload(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def normalize_license_record(payload: dict, signature_b64: str) -> dict:
    fingerprint = hashlib.sha256(canonical_license_payload(payload)).hexdigest()[:16]
    return {
        "status": "active",
        "pro_unlocked": True,
        "buyer_id": str(payload.get("buyer_id", "")).strip(),
        "email": str(payload.get("email", "")).strip(),
        "plan": str(payload.get("plan", "")).strip(),
        "issued_at": str(payload.get("issued_at", "")).strip(),
        "expire_date": str(payload.get("expire_date", "")).strip(),
        "activated_at": datetime.now().isoformat(timespec="seconds"),
        "fingerprint": fingerprint,
        "license_payload": payload,
        "license_signature": signature_b64,
    }


def decode_signed_license_code(code: str, *, verify_license_payload: Callable[[dict, str], tuple[bool, str, dict | None]]) -> tuple[bool, str, dict | None]:
    raw = (code or "").strip()
    if not raw:
        return False, "请输入授权码", None

    if not raw.startswith("OBRLIC-"):
        return False, "授权码格式错误", None

    token = raw[len("OBRLIC-"):].strip()
    try:
        decoded = urlsafe_b64decode(token)
        record = json.loads(decoded.decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError, binascii.Error):
        return False, "授权码格式错误", None

    if not isinstance(record, dict):
        return False, "授权码格式错误", None

    payload = record.get("payload")
    signature_b64 = str(record.get("signature", "")).strip()
    if not isinstance(payload, dict) or not signature_b64:
        return False, "授权码格式错误", None

    required_fields = ["buyer_id", "plan", "issued_at"]
    missing = [field for field in required_fields if not str(payload.get(field, "")).strip()]
    if missing:
        return False, f"授权数据缺少字段：{', '.join(missing)}", None

    return verify_license_payload(payload, signature_b64)


def verify_pro_code(code: str, *, decode_signed_license_code_fn: Callable[[str], tuple[bool, str, dict | None]]) -> tuple[bool, str, dict | None]:
    return decode_signed_license_code_fn(code)


def license_record_is_active(data: dict, *, verify_license_payload: Callable[[dict, str], tuple[bool, str, dict | None]]) -> tuple[bool, str, dict | None]:
    payload = data.get("license_payload")
    signature_b64 = str(data.get("license_signature", "")).strip()
    if not isinstance(payload, dict) or not signature_b64:
        return False, "本地授权记录缺失", None
    return verify_license_payload(payload, signature_b64)


def license_matches_package(license_record: dict, package_manifest: dict) -> tuple[bool, str]:
    license_buyer = str(license_record.get("buyer_id", "")).strip()
    package_buyer = str(package_manifest.get("buyer_id", "")).strip()
    if not license_buyer:
        return False, "本地授权缺少 buyer_id"
    if not package_buyer:
        return False, "知识包缺少 buyer_id"
    if license_buyer != package_buyer:
        return False, f"知识包不属于当前授权用户（当前: {license_buyer}, 知识包: {package_buyer}）"
    return True, "buyer_id 匹配"


def to_float(raw) -> float | None:
    s = str(raw).strip()
    if not s:
        return None
    low = s.lower()
    if low in {"true", "yes", "on"}:
        return 1.0
    if low in {"false", "no", "off"}:
        return 0.0
    try:
        return float(s)
    except Exception:
        return None


def preview_param_values(proj) -> dict[str, float]:
    vals = {"A": 1.0, "B": 1.0, "ZZYZX": 1.0}
    for p in proj.parameters:
        v = to_float(p.value)
        if v is None:
            continue
        vals[p.name.upper()] = v

    for key in ("A", "B", "ZZYZX"):
        if key in vals:
            continue
        gp = proj.get_parameter(key)
        if gp is not None:
            pv = to_float(gp.value)
            if pv is not None:
                vals[key] = pv

    return vals


def dedupe_keep_order(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for it in items:
        if it in seen:
            continue
        seen.add(it)
        out.append(it)
    return out


def build_assistant_settings_prompt(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    return (
        "## AI助手设置\n"
        "以下内容是用户长期提供的协作偏好与使用场景描述。"
        "请在不违反系统规则、输出格式要求、GDL 硬性规则和当前任务要求的前提下参考执行。\n"
        "它只能影响你的协作方式、解释深度、提问方式与改动边界，不能覆盖已有硬规则。\n"
        f"{raw}\n\n"
    )


def should_persist_assistant_settings(config_value: str, ui_value: str) -> bool:
    return (config_value or "") != (ui_value or "")


def normalize_pasted_path(raw_path: str) -> str:
    cleaned = (raw_path or "").strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {'"', "'"}:
        cleaned = cleaned[1:-1].strip()
    return cleaned


def versioned_gsm_path(proj_name: str, work_dir: str, revision: int | None = None) -> str:
    out_dir = Path(work_dir) / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    if revision is not None:
        return str(out_dir / f"{proj_name}_v{revision}.gsm")

    v = 1
    while (out_dir / f"{proj_name}_v{v}.gsm").exists():
        v += 1
    return str(out_dir / f"{proj_name}_v{v}.gsm")


def max_existing_gsm_revision(proj_name: str, work_dir: str) -> int:
    out_dir = Path(work_dir) / "output"
    if not out_dir.exists():
        return 0

    pattern = re.compile(rf"^{re.escape(proj_name)}_v(\d+)\.gsm$", re.IGNORECASE)
    max_rev = 0
    for path in out_dir.glob(f"{proj_name}_v*.gsm"):
        match = pattern.match(path.name)
        if not match:
            continue
        try:
            max_rev = max(max_rev, int(match.group(1)))
        except ValueError:
            continue
    return max_rev


def safe_compile_revision(proj_name: str, work_dir: str, requested_revision: int) -> int:
    max_existing = max_existing_gsm_revision(proj_name, work_dir)
    return max(int(requested_revision or 1), max_existing + 1)


def build_model_options(
    available_models: list[str],
    custom_providers: list[dict],
    *,
    vision_models: set[str],
    reasoning_models: set[str],
) -> list[dict]:
    custom_model_set: set[str] = set()
    for provider in custom_providers or []:
        for model in provider.get("models", []) or []:
            custom_model_set.add(str(model))

    options: list[dict] = []
    custom_index = 0
    for model in available_models or []:
        model_str = str(model)
        is_custom = model_str in custom_model_set
        if is_custom:
            custom_index += 1
            label = f"自定义{custom_index}"
        else:
            tags = []
            if model_str in vision_models:
                tags.append("👁")
            if model_str in reasoning_models:
                tags.append("🧠")
            label = f"{model_str}  {''.join(tags)}" if tags else model_str
        options.append({
            "label": label,
            "actual_model": model_str,
            "is_custom": is_custom,
        })
    return options


def resolve_selected_model(selected_label: str, options: list[dict]) -> str:
    for option in options:
        if option.get("label") == selected_label:
            return str(option.get("actual_model", ""))
    return ""


def collect_custom_model_aliases(custom_providers: list[dict], *, iter_entries: Callable[[dict], list[dict]]) -> list[str]:
    aliases: list[str] = []
    for provider in custom_providers or []:
        for entry in iter_entries(provider):
            alias = str(entry.get("alias", "") or "").strip()
            if alias and alias not in aliases:
                aliases.append(alias)
    return aliases


def build_custom_model_options(custom_providers: list[dict], *, iter_entries: Callable[[dict], list[dict]]) -> list[dict]:
    options: list[dict] = []
    fallback_index = 0
    for provider in custom_providers or []:
        provider_name = str(provider.get("name", "") or "").strip()
        entries = iter_entries(provider)
        for entry in entries:
            alias = str(entry.get("alias", "") or "").strip()
            if not alias:
                continue
            if provider_name:
                label = provider_name if len(entries) == 1 else f"{provider_name} / {alias}"
            else:
                fallback_index += 1
                label = f"自定义{fallback_index}"
            options.append({
                "label": label,
                "actual_model": alias,
                "is_custom": True,
            })
    return options


def build_model_source_state(
    builtin_models: list[str],
    custom_providers: list[dict],
    saved_model: str,
    *,
    iter_entries: Callable[[dict], list[dict]],
    vision_models: set[str],
    reasoning_models: set[str],
) -> dict:
    custom_models = collect_custom_model_aliases(custom_providers or [], iter_entries=iter_entries)

    builtin_options = build_model_options(
        list(builtin_models or []),
        [],
        vision_models=vision_models,
        reasoning_models=reasoning_models,
    )
    custom_options = build_custom_model_options(custom_providers or [], iter_entries=iter_entries)

    source_options = []
    if custom_options:
        source_options.append("自定义")
    if builtin_options:
        source_options.append("官方供应商")

    saved_model_str = str(saved_model or "")
    saved_is_custom = saved_model_str in custom_models

    if saved_is_custom:
        default_source = "自定义"
    elif saved_model_str and any(opt.get("actual_model") == saved_model_str for opt in builtin_options):
        default_source = "官方供应商"
    elif custom_options:
        default_source = "自定义"
    elif builtin_options:
        default_source = "官方供应商"
    else:
        default_source = ""

    active_options = custom_options if default_source == "自定义" else builtin_options
    default_model_label = next(
        (str(opt.get("label", "")) for opt in active_options if opt.get("actual_model") == saved_model_str),
        str(active_options[0].get("label", "")) if active_options else "",
    )

    return {
        "source_options": source_options,
        "custom_options": custom_options,
        "builtin_options": builtin_options,
        "default_source": default_source,
        "default_model_label": default_model_label,
    }


_PARAM_TYPE_RE = re.compile(
    r'^\s*(Length|Angle|RealNum|Integer|Boolean|String|PenColor|FillPattern|LineType|Material)'
    r'\s+\w+\s*=',
    re.IGNORECASE | re.MULTILINE,
)


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


def extract_gdl_from_text(text: str) -> dict[str, str]:
    return classify_code_blocks(text)


_INTENT_CLARIFY_ACTION_LABELS = {
    "1": "先快速解释脚本结构",
    "2": "先检查明显错误/风险",
    "3": "先给修改建议",
    "4": "按顺序都做，但先给简版总检",
}


def build_intent_clarification_message(recommended_option: str) -> str:
    recommendation = _INTENT_CLARIFY_ACTION_LABELS.get(
        recommended_option,
        _INTENT_CLARIFY_ACTION_LABELS["2"],
    )
    return (
        f"我猜你现在更像是想{recommendation}。\n"
        "你也可以选：\n"
        "1. 先快速解释脚本结构\n"
        "2. 先检查明显错误/风险\n"
        "3. 先给修改建议\n"
        "4. 按顺序都做，但先给简版总检\n"
        "回复数字就行，我再继续。"
    )


def build_post_clarification_input(original_user_input: str, option: str) -> str:
    label = _INTENT_CLARIFY_ACTION_LABELS[option]
    return (
        "基于刚才的用户确认，按下面目标继续处理：\n"
        f"用户原始请求：{(original_user_input or '').strip()}\n"
        f"本次确认目标：{label}"
    )


def consume_intent_clarification_choice(user_input: str, pending: dict | None) -> str | None:
    normalized = (user_input or "").strip()
    if not pending or normalized not in (pending.get("options") or {}):
        return None
    return build_post_clarification_input(pending.get("original_user_input", ""), normalized)


def clear_pending_intent_clarification(session_state) -> None:
    session_state["pending_intent_clarification"] = None


_EXPLAINER_FOLLOWUP_MODIFY_PATTERNS = (
    re.compile(r"^按你刚才说的改[吧啊呀]?$"),
    re.compile(r"^按这个思路改[吧啊呀]?$"),
    re.compile(r"^那就改吧$"),
    re.compile(r"^就按这个改$"),
    re.compile(r"^按这个修改$"),
)


def is_bridgeable_explainer_message(message: dict) -> bool:
    return (
        (message or {}).get("role") == "assistant"
        and (message or {}).get("bridgeable_action") == "modify_from_explainer"
        and bool((message or {}).get("content", "").strip())
    )


def is_explainer_followup_modify_request(text: str) -> bool:
    normalized = re.sub(r"\s+", "", (text or "").strip())
    if not normalized:
        return False
    return any(pattern.match(normalized) for pattern in _EXPLAINER_FOLLOWUP_MODIFY_PATTERNS)


def find_latest_bridgeable_explainer_message(history: list[dict]) -> dict | None:
    for message in reversed(history or []):
        if is_bridgeable_explainer_message(message):
            return message
    return None


def build_modify_bridge_prompt(message: dict, fallback_request: str = "") -> str:
    explanation = (message or {}).get("content", "").strip()
    source_request = (message or {}).get("bridge_source_user_input", "").strip()
    target_request = (fallback_request or "").strip() or "请按上面的解释做最小必要修改。"
    if source_request:
        return (
            "基于刚才的解释，按下面理解做最小修改：\n"
            f"原解释问题：{source_request}\n"
            f"解释结论：{explanation}\n"
            f"用户修改要求：{target_request}"
        )
    return (
        "基于刚才的解释，按下面理解做最小修改：\n"
        f"解释结论：{explanation}\n"
        f"用户修改要求：{target_request}"
    )


def maybe_build_followup_bridge_input(user_input: str, history: list[dict], has_project: bool) -> str | None:
    if not has_project or not is_explainer_followup_modify_request(user_input):
        return None
    bridge_message = find_latest_bridgeable_explainer_message(history)
    if not bridge_message:
        return None
    return build_modify_bridge_prompt(bridge_message, fallback_request=user_input)


def is_modify_bridge_prompt(text: str) -> bool:
    normalized = (text or "").strip()
    return normalized.startswith("基于刚才的解释，按下面理解做最小修改：")


def is_post_clarification_prompt(text: str) -> bool:
    normalized = (text or "").strip()
    return normalized.startswith("基于刚才的用户确认，按下面目标继续处理：")


def build_assistant_chat_message(content: str, intent: str, has_project: bool, source_user_input: str) -> dict:
    message = {"role": "assistant", "content": content}
    if intent == "CHAT" and has_project:
        message["bridgeable_action"] = "modify_from_explainer"
        message["bridge_source_user_input"] = source_user_input
    return message


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


def validate_chat_image_size(raw_bytes: bytes, image_name: str, max_chat_image_bytes: int) -> str | None:
    if raw_bytes and len(raw_bytes) > max_chat_image_bytes:
        size_mb = len(raw_bytes) / (1024 * 1024)
        return f"图片 `{image_name}` 过大（{size_mb:.1f} MB），请压缩到 5 MB 以内再试。"
    return None


def trim_history_for_image(history: list[dict], limit: int = 4) -> list[dict]:
    if not history:
        return []
    return history[-limit:]


def thumb_image_bytes(image_b64: str) -> bytes | None:
    if not image_b64:
        return None
    try:
        return base64.b64decode(image_b64)
    except Exception:
        return None


def detect_image_task_mode(user_text: str, image_name: str = "", has_project: bool = False) -> str:
    t = (user_text or "").lower()
    n = (image_name or "").lower()

    debug_tokens = [
        "debug", "error", "报错", "错误", "失败", "修复", "定位", "排查", "warning", "line ", "script",
        "screenshot", "截图", "log", "trace", "崩溃", "不显示", "异常",
    ]
    gen_tokens = [
        "生成", "创建", "建模", "构件", "参考", "外观", "照片", "photo", "reference", "design",
    ]

    if any(k in t for k in debug_tokens):
        return "debug"
    if any(k in t for k in gen_tokens):
        return "generate"

    if any(k in n for k in ["screenshot", "screen", "截屏", "截图", "error", "debug", "log"]):
        return "debug"
    if any(k in n for k in ["photo", "img", "image", "参考", "模型", "design"]):
        return "generate"

    if has_project:
        return "debug"
    return "generate"


_DEBUG_INTENT_ARCHICAD_ERROR_PATTERN = re.compile(
    r"(error|warning)\s+in\s+\w[\w\s]*script[,\s]+line\s+\d+",
    re.IGNORECASE,
)


DEBUG_KEYWORDS = {
    "debug", "fix", "error", "bug", "wrong", "issue", "broken", "fail", "crash",
    "问题", "错误", "调试", "为什么", "帮我看", "看看", "出错",
    "不对", "不行", "哪里", "原因", "explain", "why", "what", "how",
    "review", "看一下", "看下", "告诉我", "这段", "这个脚本",
}


def is_debug_intent(text: str) -> bool:
    raw = text or ""
    if raw.startswith("[DEBUG:editor]"):
        return True
    if _DEBUG_INTENT_ARCHICAD_ERROR_PATTERN.search(raw):
        return True
    lowered = raw.lower()
    return any(keyword in lowered for keyword in DEBUG_KEYWORDS)


def get_debug_mode(text: str) -> str:
    return "editor" if (text or "").startswith("[DEBUG:editor]") else "keyword"


def is_positive_confirmation(text: str) -> bool:
    low = (text or "").strip().lower()
    return any(token in low for token in ["确认", "可以", "是", "对", "生成吧", "没问题", "好的", "开始"])


def is_negative_confirmation(text: str) -> bool:
    low = (text or "").strip().lower()
    return any(token in low for token in ["不是", "不对", "重来", "修改", "不", "错了", "再改"])


def is_modify_or_check_intent(text: str, is_debug_intent: bool = False) -> bool:
    raw = (text or "").strip().lower()
    if not raw:
        return False
    if is_debug_intent:
        return False
    if any(token in raw for token in ("检查", "校验", "语法", "语义")):
        return True
    modify_tokens = (
        "改", "修改", "调整", "更新", "优化", "重写", "补充", "添加", "删除", "修正",
    )
    return any(token in raw for token in modify_tokens)


def is_explainer_intent(
    text: str,
    *,
    is_post_clarification_prompt: Callable[[str], bool],
    is_debug_intent: Callable[[str], bool],
    is_modify_or_check_intent: Callable[[str], bool],
    explainer_keywords: set[str],
) -> bool:
    raw = (text or "").strip().lower()
    if not raw:
        return False
    if is_post_clarification_prompt(raw):
        return "本次确认目标：先快速解释脚本结构" in text
    if is_debug_intent(raw):
        explainer_overrides = (
            "解释", "拆解", "分析", "代码分析", "逻辑分析", "命令分析",
            "负责什么", "控制什么", "作用", "什么意思",
        )
        if not any(token in raw for token in explainer_overrides):
            return False
    if is_modify_or_check_intent(raw):
        return False
    if any(token in raw for token in ("代码分析", "逻辑分析", "命令分析")):
        return True
    if re.search(r"\b(?:1d|2d|3d|param|ui|properties|property|master)\b", raw):
        script_question_tokens = ("解释", "分析", "负责什么", "作用", "逻辑", "命令", "脚本")
        if any(token in raw for token in script_question_tokens):
            return True
    return any(token in raw for token in explainer_keywords)


def should_clarify_intent(
    text: str,
    *,
    has_project: bool,
    is_modify_bridge_prompt: Callable[[str], bool],
    has_followup_bridge: bool,
    is_post_clarification_prompt: Callable[[str], bool],
    is_debug_intent: Callable[[str], bool],
    is_explainer_intent: Callable[[str], bool],
) -> bool:
    raw = (text or "").strip()
    if not raw or not has_project:
        return False
    if is_modify_bridge_prompt(raw):
        return False
    if has_followup_bridge:
        return False
    if is_post_clarification_prompt(raw):
        return False
    mixed_tokens = ("解释", "检查", "修改意见")
    if sum(token in raw for token in mixed_tokens) >= 2:
        return True
    if raw in {"看看这个", "这个怎么处理", "这个有问题吗"}:
        return True
    if is_debug_intent(raw) or is_explainer_intent(raw):
        return False
    if re.search(r"改成\s*\d+", raw):
        return False
    return False


def maybe_build_intent_clarification(
    user_input: str,
    *,
    should_clarify_intent: Callable[[str], bool],
    build_intent_clarification_message: Callable[[str], str],
) -> dict | None:
    if not should_clarify_intent(user_input):
        return None
    recommended_option = "2"
    return {
        "original_user_input": user_input,
        "recommended_option": recommended_option,
        "options": {
            "1": "explain",
            "2": "check",
            "3": "suggest",
            "4": "review_summary",
        },
        "message": build_intent_clarification_message(recommended_option),
    }


def should_start_elicitation(user_input: str) -> bool:
    return any(token in (user_input or "") for token in ["创建", "生成", "新建"])


def resolve_skip_elicitation_intent(
    text: str,
    *,
    intent: str | None,
    project_loaded: bool,
    route_main_input: Callable[[str, bool, bool], tuple[str, str]],
) -> str:
    if intent:
        return intent
    return route_main_input(text, project_loaded, False)[0]


def should_skip_elicitation_for_gdl_request(effective_intent: str) -> bool:
    return effective_intent in ("MODIFY", "DEBUG")


def should_skip_elicitation_for_gdl_request_from_text(
    text: str,
    *,
    intent: str | None,
    project_loaded: bool,
    route_main_input: Callable[[str, bool, bool], tuple[str, str]],
) -> bool:
    effective_intent = resolve_skip_elicitation_intent(
        text,
        intent=intent,
        project_loaded=project_loaded,
        route_main_input=route_main_input,
    )
    return should_skip_elicitation_for_gdl_request(effective_intent)
