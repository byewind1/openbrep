from __future__ import annotations

from openbrep.explainer.schema import ParameterExplanation, ProjectExplanation, ScriptExplanation


_CODE_ANALYSIS_KEYWORDS = (
    "代码分析",
    "逐行",
    "逐段",
    "命令分析",
    "逻辑分析",
    "分析脚本",
    "分析这段3d",
    "分析这段 3d",
)

_DETAILED_KEYWORDS = (
    "详细",
    "详细讲讲",
    "展开",
    "全面分析",
    "具体一点",
)


def detect_explanation_detail_level(user_input: str) -> str:
    text = (user_input or "").strip().lower()
    if not text:
        return "brief"
    if any(keyword in text for keyword in _CODE_ANALYSIS_KEYWORDS):
        return "code"
    if any(keyword in text for keyword in _DETAILED_KEYWORDS):
        return "detailed"
    return "brief"


def build_chat_explanation_reply(
    explanation: ProjectExplanation | ScriptExplanation | ParameterExplanation,
    detail_level: str | None = None,
    user_input: str = "",
) -> str:
    level = detail_level or detect_explanation_detail_level(user_input)
    if isinstance(explanation, ScriptExplanation):
        return _format_script_reply(explanation, level)
    if isinstance(explanation, ParameterExplanation):
        return _format_parameter_reply(explanation, level)
    return _format_project_reply(explanation, level)


def _format_script_reply(explanation: ScriptExplanation, detail_level: str) -> str:
    if detail_level == "code":
        parts = [
            f"脚本类型：{explanation.script_type}",
            f"主要用途：{explanation.goal}",
            f"关键命令：{', '.join(explanation.key_commands) or '无'}",
            f"逻辑：主要围绕 {', '.join(explanation.parameters) or '少量参数'} 组织几何或控制流程。",
            f"注意点：{', '.join(explanation.risks) or '暂无明显风险'}",
        ]
        return "\n".join(parts)

    if detail_level == "detailed":
        parts = [
            f"脚本类型：{explanation.script_type}",
            f"脚本目标：{explanation.goal}",
            f"关键命令：{', '.join(explanation.key_commands) or '无'}",
            f"相关参数：{', '.join(explanation.parameters) or '无'}",
            f"风险点：{', '.join(explanation.risks) or '无'}",
        ]
        return "\n".join(parts)

    return "\n".join([
        f"脚本类型：{explanation.script_type}",
        f"关键命令：{', '.join(explanation.key_commands[:3]) or '无'}",
        f"核心逻辑：主要用于{explanation.goal.replace('解释 ', '').replace(' 脚本的主要作用', '') or '实现当前脚本目标'}。",
    ])


def _format_parameter_reply(explanation: ParameterExplanation, detail_level: str) -> str:
    if detail_level == "code":
        return "\n".join([
            f"参数：{explanation.name}",
            f"类型/默认值：{explanation.type_tag or '未知'} / {explanation.default_value or '空'}",
            f"命中脚本：{', '.join(explanation.used_in_scripts) or '未命中'}",
            f"代码线索：{'；'.join(explanation.usage_summaries) or '暂无命中代码片段'}",
            f"注意点：{', '.join(explanation.risks) or '暂无明显风险'}",
        ])

    if detail_level == "detailed":
        return "\n".join([
            f"参数：{explanation.name}",
            f"含义：{explanation.description or '暂无描述'}",
            f"类型：{explanation.type_tag or '未知'}",
            f"默认值：{explanation.default_value or '空'}",
            f"主要影响脚本：{', '.join(explanation.used_in_scripts) or '未命中'}",
            f"使用线索：{'；'.join(explanation.usage_summaries) or '暂无命中代码片段'}",
            f"风险点：{', '.join(explanation.risks) or '无'}",
        ])

    return "\n".join([
        f"参数：{explanation.name}",
        f"主要影响：{', '.join(explanation.used_in_scripts) or '暂未命中脚本'}",
        f"核心逻辑：{(explanation.usage_summaries[0] if explanation.usage_summaries else '当前只能确认它参与了局部尺寸或控制逻辑')}。",
    ])


def _format_project_reply(explanation: ProjectExplanation, detail_level: str) -> str:
    roles = [f"{item.title}: {item.summary}" for item in explanation.script_roles]

    if detail_level == "detailed":
        roles_text = "\n".join([f"- {item}" for item in roles]) or "- 无"
        dependencies = "\n".join([f"- {item}" for item in explanation.dependencies]) or "- 无"
        baggage = "\n".join([f"- {item}" for item in explanation.baggage]) or "- 无"
        parameters = "\n".join([f"- {item}" for item in explanation.parameters_summary]) or "- 无"
        return (
            f"构件整体目标：{explanation.overall_goal}\n"
            f"参数说明：\n{parameters}\n"
            f"脚本职责：\n{roles_text}\n"
            f"脚本依赖：\n{dependencies}\n"
            f"历史包袱：\n{baggage}"
        )

    key_roles = "、".join(item.title for item in explanation.script_roles[:3]) or "无"
    logic = "参数驱动 + 脚本分工"
    if explanation.dependencies:
        logic += "，并带有脚本依赖"

    return (
        f"构件类型/用途：{explanation.overall_goal}\n"
        f"关键部分：{key_roles}\n"
        f"核心逻辑：{logic}。"
    )
