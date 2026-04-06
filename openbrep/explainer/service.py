from __future__ import annotations

from openbrep.explainer.schema import ExplanationSection, ProjectExplanation, ScriptExplanation


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
