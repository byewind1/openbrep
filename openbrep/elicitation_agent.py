from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional


class ElicitationState(Enum):
    IDLE = "idle"
    ELICITING = "eliciting"
    SPEC_READY = "spec_ready"
    HANDOFF = "handoff"


@dataclass
class ParamDef:
    name: str
    type: str
    description: str
    default: str


@dataclass
class GDLSpec:
    object_name: str
    geometry_intent: str
    parameters: list[ParamDef]
    materials: list[str]
    has_2d: bool
    special_behaviors: list[str]
    confirmed: bool = False

    def to_instruction(self) -> str:
        param_lines = []
        for param in self.parameters:
            param_lines.append(
                f"- {param.name} ({param.type}) = {param.default}：{param.description}"
            )
        material_lines = [f"- {item}" for item in self.materials] or ["- 无特殊材质分区"]
        behavior_lines = [f"- {item}" for item in self.special_behaviors] or ["- 无特殊行为"]
        plan_2d = "需要 2D 平面表达" if self.has_2d else "不需要特殊 2D 平面表达"

        return "\n".join([
            f"请创建一个 GDL 对象：{self.object_name}",
            f"几何意图：{self.geometry_intent}",
            "参数要求：",
            *param_lines,
            "材质分区：",
            *material_lines,
            f"2D 表达：{plan_2d}",
            "特殊行为：",
            *behavior_lines,
        ])


class ElicitationAgent:
    DIMENSIONS = [
        "geometry",
        "parameters",
        "materials",
        "plan_2d",
        "behaviors",
    ]

    SYSTEM_PROMPT = (
        "你是 ArchiCAD GDL 对象设计顾问，帮用户把模糊想法转化为精确的 GDL 对象规格。\n"
        "规则：\n"
        "- 全程中文交流\n"
        "- 每次只问一个问题，不要一次问多个\n"
        "- 不猜测用户意图，不确定就问\n"
        "- 问题要具体，给出选项或例子帮助用户回答\n"
        "- 不要生成任何代码\n"
        "当前引导维度：{dimension}\n"
        "已收集信息：{context}"
    )

    def __init__(self, llm_caller: Optional[Callable]):
        self.state = ElicitationState.IDLE
        self.spec: Optional[GDLSpec] = None
        self.current_dimension = 0
        self.conversation_history: list[dict] = []
        self.llm_caller = llm_caller

    def start(self, user_input: str) -> str:
        self._require_llm_caller()
        self.state = ElicitationState.ELICITING
        self.spec = None
        self.current_dimension = 0
        self.conversation_history = [{"role": "user", "content": user_input}]
        return self._ask_dimension(self.DIMENSIONS[self.current_dimension], self._build_context())

    def respond(self, user_input: str) -> tuple[str, bool]:
        if self.state not in (ElicitationState.ELICITING, ElicitationState.SPEC_READY):
            raise RuntimeError("elicitation 未启动，无法继续 respond")

        self.conversation_history.append({"role": "user", "content": user_input})

        if self.state == ElicitationState.SPEC_READY:
            return self._format_spec_summary(), True

        self.current_dimension += 1
        if self.current_dimension < len(self.DIMENSIONS):
            next_dimension = self.DIMENSIONS[self.current_dimension]
            question = self._ask_dimension(next_dimension, self._build_context())
            return question, False

        self.spec = self._extract_spec()
        self.state = ElicitationState.SPEC_READY
        summary = self._format_spec_summary()
        return summary, True

    def confirm(self, user_confirmed: bool) -> Optional[GDLSpec]:
        if self.spec is None:
            return None

        if user_confirmed:
            self.spec.confirmed = True
            self.state = ElicitationState.HANDOFF
            return self.spec

        self.state = ElicitationState.ELICITING
        self.current_dimension = max(0, len(self.DIMENSIONS) - 1)
        return None

    def _ask_dimension(self, dimension: str, context: dict) -> str:
        self._require_llm_caller()
        system_prompt = self.SYSTEM_PROMPT.format(
            dimension=dimension,
            context=json.dumps(context, ensure_ascii=False),
        )
        dimension_prompts = {
            "geometry": "请先聚焦几何形态提一个问题。",
            "parameters": "请只追问可调参数，比如宽度、高度、数量。",
            "materials": "请只追问材质分区。",
            "plan_2d": "请只追问 2D 平面表达。",
            "behaviors": "请只追问特殊行为。",
        }
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": dimension_prompts.get(dimension, f"请针对 {dimension} 继续引导提问。")},
        ]
        reply = self.llm_caller(messages)
        question = reply.content if hasattr(reply, "content") else str(reply)
        self.conversation_history.append({"role": "assistant", "content": question})
        return question

    def _extract_spec(self) -> GDLSpec:
        self._require_llm_caller()
        schema_hint = {
            "object_name": "对象名",
            "geometry_intent": "几何意图",
            "parameters": [
                {
                    "name": "参数名",
                    "type": "Length / Integer / Boolean / Material / RealNum",
                    "description": "参数说明",
                    "default": "默认值",
                }
            ],
            "materials": ["材质分区"],
            "has_2d": True,
            "special_behaviors": ["特殊行为"],
            "confirmed": False,
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "请把以下对话整理为 GDL 对象规格 JSON。"
                    "只返回 JSON，不要解释。"
                    f"JSON 结构示例：{json.dumps(schema_hint, ensure_ascii=False)}"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(self.conversation_history, ensure_ascii=False),
            },
        ]
        raw = self.llm_caller(messages)
        payload = raw.content if hasattr(raw, "content") else str(raw)
        data = json.loads(payload)
        params = [ParamDef(**item) for item in data.get("parameters", [])]
        return GDLSpec(
            object_name=data["object_name"],
            geometry_intent=data["geometry_intent"],
            parameters=params,
            materials=list(data.get("materials", [])),
            has_2d=bool(data.get("has_2d", False)),
            special_behaviors=list(data.get("special_behaviors", [])),
            confirmed=bool(data.get("confirmed", False)),
        )

    def reset(self):
        self.state = ElicitationState.IDLE
        self.spec = None
        self.current_dimension = 0
        self.conversation_history = []

    def _require_llm_caller(self) -> None:
        if self.llm_caller is None:
            raise ValueError("llm_caller is required for ElicitationAgent")

    def _build_context(self) -> dict:
        return {
            "current_dimension": self.DIMENSIONS[self.current_dimension],
            "conversation_history": self.conversation_history,
        }

    def _format_spec_summary(self) -> str:
        if self.spec is None:
            raise RuntimeError("spec 尚未生成，无法确认")
        param_summary = "、".join(p.name for p in self.spec.parameters) or "无"
        material_summary = "、".join(self.spec.materials) or "无"
        behavior_summary = "、".join(self.spec.special_behaviors) or "无"
        plan_2d = "需要" if self.spec.has_2d else "不需要"
        return (
            f"我先整理一下当前规格，请你确认：\n"
            f"- 对象名：{self.spec.object_name}\n"
            f"- 几何意图：{self.spec.geometry_intent}\n"
            f"- 参数：{param_summary}\n"
            f"- 材质：{material_summary}\n"
            f"- 2D：{plan_2d}\n"
            f"- 特殊行为：{behavior_summary}\n"
            "如果没问题，请回复“确认”；如果要改，请直接指出哪里不对。"
        )
