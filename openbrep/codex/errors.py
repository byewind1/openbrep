"""Codex 错误 → 稳定非空 code + 稳定产品文案（D1 P0-R1A/R1B）。

契约：任何进入 provider 返回、缓存、API 响应、日志的文本都必须是稳定文案，
绝不透传上游原文（可能含 access_token / loginId / Bearer / auth URL / 路径）。
redact_secrets 只作为最后一道纵深防御。
"""

from __future__ import annotations

from openbrep.codex.app_server import CodexAppServerError
from openbrep.codex.redact import redact_secrets

DEFAULT_FALLBACK = "Codex 操作失败，请稍后重试。"

# 稳定文案表：按异常 code（类属性）与 CodexAppServerError.category 映射
STABLE_MESSAGES: dict[str, str] = {
    # 异常 code
    "codex_cli_unavailable": "未检测到 Codex CLI，请先安装 Codex CLI 后重试。",
    "not_signed_in": "尚未连接 ChatGPT。请先在 AI 设置中点击「连接我的 ChatGPT」完成登录。",
    "version_incompatible": "Codex CLI 版本与 OpenBrep 不兼容，请升级 Codex CLI 后重试。",
    "codex_crashed": "Codex app-server 进程异常退出。请点击「重启」恢复连接。",
    "quota_exhausted": "ChatGPT 订阅额度已耗尽或已达到用量上限。请稍后重试、等待重置，或切换到其他模型/提供商。",
    # D6：Fixed 模式 effort 门禁（保存与运行时共用，稳定文案零回显）
    "unsupported_reasoning_effort": (
        "当前模型不支持所选 reasoning effort（推理强度），请求已拒绝。"
        "请到 AI 设置中重新选择该模型支持的 effort。"
    ),
    # P0-1 状态门禁（CodexAppServerError.category）
    "already_signed_in": "已连接 ChatGPT 账号。切换账号请先点击「断开连接」退出当前账号，再登录新账号。",
    "login_already_pending": "已有登录流程正在进行。请先取消当前登录，再重新发起。",
    # CodexAppServerError.category
    "codex_app_server": "Codex app-server 请求失败，请稍后重试。",
    "not_started": "Codex app-server 尚未就绪，请稍后重试。",
    "process_exited": "Codex app-server 进程已退出，请重启工作台后重试。",
    "write_failed": "Codex app-server 通信失败，请稍后重试。",
    "timeout": "Codex app-server 响应超时，请稍后重试。",
    "rpc_error": "Codex app-server 请求失败，请稍后重试。",
    "login_failed": "登录服务返回异常，请稍后重试或重新连接。",
    "closed": "Codex app-server 已关闭，请重启工作台后重试。",
}


def error_response(exc: BaseException, fallback: str = DEFAULT_FALLBACK) -> dict[str, str]:
    """异常 → {code, error}：稳定非空 code + 稳定产品文案（不传上游原文）。"""
    if isinstance(exc, CodexAppServerError):
        # 先按 category 细分（CodexAppServerError.code 恒为 codex_app_server，
        # 若先查 code 会盖掉 timeout/process_exited 等细分文案）
        category = getattr(exc, "category", None) or "codex_app_server"
        return {
            "code": "codex_app_server",
            "error": STABLE_MESSAGES.get(category, fallback),
        }
    code = getattr(exc, "code", None)
    if code in STABLE_MESSAGES:
        return {"code": str(code), "error": STABLE_MESSAGES[code]}
    return {"code": str(code or "codex_error"), "error": fallback}


def stabilize_message(code, raw_text, fallback: str = DEFAULT_FALLBACK) -> str:
    """按 code 取稳定文案；未知 code 一律兜底（绝不返回 raw 原文，P0-R1A）。"""
    if code in STABLE_MESSAGES:
        return STABLE_MESSAGES[code]
    return fallback
