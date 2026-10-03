"""Provider 引用检查（卡02）：删除/导入前统一找出配置里指向某 provider 的引用。

冻结评审 §11 合同 3 与 §3 全清单。覆盖五类后端可判定引用：

1. ``[llm].model``（等于 provider 名、``<provider>/...`` 显式引用、或解析到该
   provider 的裸 alias——与解析器同口径）；
2. 会话级 ``session_llm_model`` 覆盖（只读 getattr 探测，不触发副作用）；
3. ``[llm.retry]`` fallback 链（按 ``model_retry.RetryRouter`` 的规范化读法）；
4. ``enabled_models``（R7 路径策略允许表）；
5. ``disabled_providers``（R7 路径策略拒绝表）。

大小写与解析语义必须与 ``find_custom_provider_match()``（config.py）一致：
本模块不另立比较规则，模型引用→provider 的判定统一复用该解析器。

另提供 ``frontend_visibility_hint()``：前端可见性状态（localStorage
``openbrep.visible-models``）后端不可见，作为非阻塞提示项由调用方并入报告，
不计入本函数返回的引用列表（无引用时本函数返回空列表）。
"""

from __future__ import annotations

from typing import Any

from openbrep.config import find_custom_provider_match
from openbrep.model_retry import RetryRouter

# 前端可见性提示（非阻塞）：位置字段与其余 refs 同形，供 UI 统一渲染。
FRONTEND_VISIBILITY_LOCATION = "frontend_visibility"


def frontend_visibility_hint(name: str) -> dict[str, Any]:
    """构造"前端可能有引用"的非阻塞提示项（卡04 路由并入 refs 报告）。"""
    return {
        "location": FRONTEND_VISIBILITY_LOCATION,
        "blocking": False,
        "context": str(name or ""),
        "detail": "浏览器本地可见性状态可能引用该服务商；删除后旧隐藏项自然失效，不阻塞删除。",
    }


def _providers(config: Any) -> list[dict]:
    return list(getattr(getattr(config, "llm", None), "providers", None) or [])


def model_targets_provider(config: Any, model: Any, name: str) -> bool:
    """一个模型引用是否指向 provider ``name``——复用 find_custom_provider_match 的
    完整解析语义（大小写不敏感、head/显式直连、alias 按配置顺序、裸 provider 名），
    不另立第二套规则。通配符（``*`` / ``all``）不指名任何 provider。
    """
    target = str(model or "").strip()
    if not target or target.lower() in {"*", "all"}:
        return False
    match = find_custom_provider_match(_providers(config), target, include_provider_name=True)
    return bool(match) and str(match.get("provider_name", "") or "").strip().lower() == str(name or "").strip().lower()


def _scoped_values_all(entries: Any, value_keys: tuple[str, ...]) -> list[str]:
    """收集 R7 策略条目的全部值（含路径作用域条目，忽略 path 前缀过滤）。

    引用检查取超集：路径作用域条目即使不作用于当前目录也提示，避免误删
    之后才在其他路径下发现悬空规则。值提取与 model_scope._append 同口径：
    非空、去重、保序。
    """
    values: list[str] = []
    for entry in entries or ():
        if isinstance(entry, str):
            text = entry.strip()
            if text and text not in values:
                values.append(text)
            continue
        if not isinstance(entry, dict):
            continue
        for key in value_keys:
            raw = entry.get(key)
            if isinstance(raw, str):
                raw = [raw]
            if isinstance(raw, (list, tuple)):
                for value in raw:
                    text = str(value or "").strip()
                    if text and text not in values:
                        values.append(text)
    return values


def find_provider_refs(
    config: Any,
    name: str,
    *,
    session: Any = None,
) -> list[dict[str, Any]]:
    """返回配置中指向 provider ``name`` 的全部可判定引用（结构化，供 UI 渲染）。

    每项 ``{location, blocking, context, detail?}``；``blocking`` 恒为 True
    （非阻塞提示见 ``frontend_visibility_hint``）。无引用返回空列表。
    """
    target_name = str(name or "").strip()
    if not target_name:
        return []
    refs: list[dict[str, Any]] = []
    llm = getattr(config, "llm", None)

    # 1. [llm].model
    current_model = str(getattr(llm, "model", "") or "")
    if model_targets_provider(config, current_model, target_name):
        refs.append({
            "location": "llm.model",
            "blocking": True,
            "context": current_model,
            "detail": "当前默认模型指向该服务商。",
        })

    # 2. 会话级覆盖（只读 getattr，不触发副作用；旧式替身 session 无此属性）
    session_model = getattr(session, "session_llm_model", None) if session is not None else None
    if session_model and model_targets_provider(config, session_model, target_name):
        refs.append({
            "location": "session_model",
            "blocking": True,
            "context": str(session_model),
            "detail": "会话级模型覆盖指向该服务商。",
        })

    # 3. [llm.retry] fallback 链（RetryRouter 规范化读法；未知角色/畸形条目已被忽略）
    router = RetryRouter.from_mapping(getattr(llm, "retry", None) or {})
    for role, candidates in (router.fallback_chains or {}).items():
        for candidate in candidates:
            if model_targets_provider(config, candidate.model, target_name):
                refs.append({
                    "location": "llm.retry",
                    "blocking": True,
                    "context": candidate.model,
                    "detail": f"任务角色「{role}」的 fallback 链引用该服务商。",
                })

    # 4. enabled_models（R7 允许表）：模式指名该 provider。判定完全走解析器
    # 同口径（model_targets_provider）；``*`` / ``all`` 通配不指名任何 provider。
    for pattern in _scoped_values_all(getattr(llm, "enabled_models", None) or (), ("models", "values", "items")):
        if model_targets_provider(config, pattern, target_name):
            refs.append({
                "location": "enabled_models",
                "blocking": True,
                "context": pattern,
                "detail": "路径作用域允许表引用该服务商。",
            })

    # 5. disabled_providers（R7 拒绝表）：与 model_enabled 同口径的整串小写比较
    disabled_values = {
        value.lower()
        for value in _scoped_values_all(
            getattr(llm, "disabled_providers", None) or (), ("providers", "values", "items")
        )
    }
    if target_name.lower() in disabled_values:
        refs.append({
            "location": "disabled_providers",
            "blocking": True,
            "context": target_name,
            "detail": "路径作用域拒绝表引用该服务商。",
        })

    return refs
