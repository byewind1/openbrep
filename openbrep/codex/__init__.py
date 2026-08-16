"""Codex BYOA（ChatGPT 订阅）接入：官方 Codex app-server 的最小 stdio JSON-RPC 客户端。

安全不变量（D1 派单）：
- 独立用户级 CODEX_HOME（默认 ~/.openbrep/codex），绝不读取/导入 ~/.codex。
- 后端永不返回 token / JWT / account id / auth path / authUrl —— 登录只打开
  终端用户浏览器，前端只看到枚举化状态。
- 所有单测使用 fake app-server，不调用真实账号/网络。
"""
from openbrep.codex.app_server import (
    CodexAppServerClient,
    CodexAppServerError,
    CodexCliUnavailableError,
    StdioJsonRpcTransport,
    default_codex_home,
)
from openbrep.codex.provider import (
    CodexNotSignedInError,
    CodexProvider,
    CodexVersionIncompatibleError,
    default_codex_provider,
    get_default_codex_provider,
    mask_email,
    mask_rate_limits,
    set_default_codex_provider,
)
from openbrep.codex.turn import CodexTurnResult, CodexTurnRunner

__all__ = [
    "CodexAppServerClient",
    "CodexAppServerError",
    "CodexCliUnavailableError",
    "CodexNotSignedInError",
    "CodexProvider",
    "CodexTurnResult",
    "CodexTurnRunner",
    "CodexVersionIncompatibleError",
    "StdioJsonRpcTransport",
    "default_codex_home",
    "default_codex_provider",
    "get_default_codex_provider",
    "mask_email",
    "mask_rate_limits",
    "set_default_codex_provider",
]
