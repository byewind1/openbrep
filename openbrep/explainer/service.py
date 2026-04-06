from __future__ import annotations

from openbrep.explainer.schema import ExplanationSection, ParameterExplanation, ProjectExplanation, ScriptExplanation


def explain_script_context(context: dict) -> ScriptExplanation:
    parameters = list(context.get("parameters", []))
    script_type = context.get("script_type", "UNKNOWN")
    return ScriptExplanation(
        script_type=script_type,
        goal=f"解释 {script_type} 脚本的主要作用",
        key_commands=_extract_key_commands(context.get("script_text", "")),
        parameters=parameters,
        risks=_detect_basic_risks(context.get("script_text", ""), parameters),
    )


def explain_parameter_context(context: dict) -> ParameterExplanation:
    usage_hits = list(context.get("usage_hits", []))
    used_in_scripts = [item.get("script", "") for item in usage_hits if item.get("script")]
    usage_summaries = []
    for item in usage_hits:
        script = item.get("script", "未知脚本")
        lines = item.get("lines", [])
        if lines:
            usage_summaries.append(f"{script}: {lines[0]}")
        else:
            usage_summaries.append(f"{script}: 命中参数引用")

    risks = []
    if not usage_hits:
        risks.append("暂未在脚本中命中该参数，可能尚未接入几何或控制逻辑")
    if context.get("is_fixed"):
        risks.append("该参数为固定参数，修改时要注意与构件整体尺寸联动")

    return ParameterExplanation(
        name=context.get("name", ""),
        type_tag=context.get("type_tag", ""),
        default_value=context.get("default_value", ""),
        description=context.get("description", ""),
        is_fixed=bool(context.get("is_fixed")),
        used_in_scripts=used_in_scripts,
        usage_summaries=usage_summaries,
        risks=risks,
    )


def explain_project_context(context: dict) -> ProjectExplanation:
    script_roles = [
        ExplanationSection(title=name, summary=f"{name} 脚本负责该构件的一部分行为")
        for name in context.get("scripts", {}).keys()
    ]
    return ProjectExplanation(
        overall_goal=context.get("gsm_name", "未知构件"),
        parameters_summary=[f"{name}: 待进一步解释" for name in context.get("parameters", [])],
        script_roles=script_roles,
        dependencies=_build_basic_dependencies(context.get("scripts", {})),
        baggage=[],
    )


def _extract_key_commands(script_text: str) -> list[str]:
    commands = []
    for line in script_text.splitlines():
        line = line.strip()
        if not line:
            continue
        cmd = line.split()[0].replace(",", "")
        if cmd.isupper() and cmd not in commands:
            commands.append(cmd)
    return commands


def _detect_basic_risks(script_text: str, parameters: list[str]) -> list[str]:
    risks = []
    if "GOTO" in script_text:
        risks.append("存在 GOTO，历史逻辑可能较重")
    if not parameters:
        risks.append("缺少参数上下文，解释可能不完整")
    return risks


def _build_basic_dependencies(scripts: dict[str, str]) -> list[str]:
    names = list(scripts.keys())
    if "MASTER" in names and len(names) > 1:
        return [f"MASTER -> {name}" for name in names if name != "MASTER"]
    return []
