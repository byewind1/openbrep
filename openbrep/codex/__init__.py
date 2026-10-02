"""Codex 接入：官方 Codex app-server 的最小 stdio JSON-RPC 客户端 + 双入口。

双入口（2026-09-17）：
- `local`（默认）：只读消费用户自己的 Codex 配置（CODEX_HOME / ~/.codex），
  不接管认证；登录/凭据留在 Codex CLI 那一侧。
- `managed`：独立用户级 CODEX_HOME（~/.openbrep/codex），OpenBrep 托管 ChatGPT
  登录，绝不读取/导入 ~/.codex。

安全不变量（D1 派单）：
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
from openbrep.codex.entry import (
    DEFAULT_CODEX_ENTRY,
    ENTRY_LOCAL,
    ENTRY_MANAGED,
    codex_home_for_entry,
    codex_home_kind,
    entry_auth_source,
    entry_label,
    normalize_codex_entry,
)
from openbrep.codex.local_config import (
    local_entry_verdict,
    read_local_codex_config,
)
from openbrep.codex.provider import (
    CodexEntryManagedOnlyError,
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
    "CodexEntryManagedOnlyError",
    "CodexNotSignedInError",
    "CodexProvider",
    "CodexTurnResult",
    "CodexTurnRunner",
    "CodexVersionIncompatibleError",
    "DEFAULT_CODEX_ENTRY",
    "ENTRY_LOCAL",
    "ENTRY_MANAGED",
    "StdioJsonRpcTransport",
    "codex_home_for_entry",
    "default_codex_home",
    "default_codex_provider",
    "codex_home_kind",
    "entry_auth_source",
    "entry_label",
    "get_default_codex_provider",
    "local_entry_verdict",
    "mask_email",
    "mask_rate_limits",
    "normalize_codex_entry",
    "read_local_codex_config",
    "set_default_codex_provider",
]
