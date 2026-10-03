"""无副作用凭据状态（卡01）：只读描述 provider 条目的凭据配置位置与形态。

合同（评审 §4 / §11 合同 4）：本模块只做配置读取与 os.environ 只读探测，
**绝不调用 ``CredentialPool.select()``**（选择会创建租约、改变运行时状态），
也不为真实运行任务挑选凭据——运行时解析仍走 ``resolve_credentials()``。

已知失真注记：``${ENV}`` 写在条目 api_key 里时，现有 ``resolve_credentials``
的 ``source`` 仍标 ``custom_provider``——本合同按"配置位置"描述，不复用
source 标签，规避该歧义。
"""

from __future__ import annotations

import os
import re
from typing import Any

from openbrep.config import PROVIDER_PROFILES
from openbrep.credential_pool import CredentialPool

_ENV_REF_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")

CREDENTIAL_LOCATIONS = ("entry", "provider_keys", "top_level", "env", "none")
CREDENTIAL_FORMS = ("direct", "env_ref", "pool")


def is_env_ref(value: Any) -> bool:
    """整串是否为 ``${VAR}`` 形式的环境变量引用。"""
    return bool(_ENV_REF_RE.match(str(value or "").strip()))


def mask_secret(value: Any) -> str:
    """key 脱敏展示：无 → ""；明文 → 前 3 + … + 末 4；${ENV} 引用 → 原样。

    环境变量名不是秘密；过短明文（<8 字符）无法安全掐头去尾，整体掩码。
    """
    text = str(value or "").strip()
    if not text:
        return ""
    if is_env_ref(text):
        return text
    if len(text) < 8:
        return "•••"
    return text[:3] + "…" + text[-4:]


def _profile_for_entry_name(name: Any):
    """按条目名精确匹配官方 provider 档案（provider_keys/env 位置探测用）。"""
    target = str(name or "").strip().lower()
    for profile in PROVIDER_PROFILES:
        if profile.name.lower() == target:
            return profile
    return None


def _expanded(value: Any) -> str:
    """${ENV} 引用只读展开（os.environ 查询，绝不写入）；其余去空白原样。"""
    text = str(value or "").strip()
    match = _ENV_REF_RE.match(text)
    if match:
        return os.environ.get(match.group(1), "")
    return text


def pool_entries(entry: dict) -> list:
    """条目上的凭据池原始列表（credentials / api_keys，二者取一）。"""
    raw = entry.get("credentials") or entry.get("api_keys") or []
    if not isinstance(raw, list):
        raw = [raw]
    return raw


def has_credential_pool(entry: dict) -> bool:
    return bool(pool_entries(entry))


def _pool_resolvable(entry: dict) -> bool:
    """池形态只报告存在：复用运行时 from_provider 的同款展开/兜底逻辑，
    看解析结果是否非空——绝不调用 select()。"""
    return bool(CredentialPool.from_provider(entry).credentials)


def credential_status(entry: dict, config) -> dict[str, Any]:
    """一个 provider 条目的只读凭据状态：``{location, form, resolvable}``。

    - ``location`` 判定顺序：条目自带 api_key 或池结构 → ``entry``；
      ``provider_keys[profile]`` → ``provider_keys``；顶层 api_key →
      ``top_level``；profile 环境变量在 os.environ 非空 → ``env``；否则 ``none``。
      （池结构位于条目上，故 location 为 entry，form 为 pool。）
    - ``form``：池结构 → ``pool``；api_key 为 ${ENV} 引用 → ``env_ref``；否则
      ``direct``。
    - ``resolvable``：对应位置展开后存在非空值（env 只读查 os.environ）。
    """
    entry = entry or {}
    raw_key = str(entry.get("api_key") or "").strip()
    pool = has_credential_pool(entry)

    if pool:
        form = "pool"
        resolvable = _pool_resolvable(entry)
    else:
        form = "env_ref" if is_env_ref(raw_key) else "direct"
        resolvable = bool(_expanded(raw_key))

    location = "none"
    if pool or raw_key:
        location = "entry"
    else:
        profile = _profile_for_entry_name(entry.get("name"))
        if profile and any(
            str(config.llm.provider_keys.get(key_name) or "").strip()
            for key_name in profile.provider_key_names
        ):
            # provider_keys 的值同样可能是 ${ENV} 引用：只读展开后判定
            location = "provider_keys"
            resolvable = any(
                bool(_expanded(config.llm.provider_keys.get(key_name)))
                for key_name in profile.provider_key_names
                if str(config.llm.provider_keys.get(key_name) or "").strip()
            )
        elif str(config.llm.api_key or "").strip():
            location = "top_level"
            resolvable = bool(_expanded(config.llm.api_key))
        elif profile and any(os.environ.get(env_var) for env_var in profile.env_vars):
            location = "env"
            resolvable = True  # 位置本身即"环境变量非空"

    return {"location": location, "form": form, "resolvable": resolvable}
