"""GDL 领域知识图谱 — 节点与边类型定义。

纯数据类，无外部依赖。用于 GDLGraphManager 和图谱 JSON 序列化。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class APIFunction:
    """GDL 内置命令或函数节点。"""

    name: str
    signature: str
    return_type: str = "void"
    description: str = ""
    param_count_min: int = 0
    param_count_max: int = 0    # 0 = 可变参数数量
    category: str = ""          # geometry | transform | attribute | control | 2d | misc

    def to_prompt_line(self) -> str:
        """单行 prompt 格式，供注入 LLM 上下文。"""
        parts = [f"{self.name} {self.signature}"]
        if self.description:
            parts.append(f"— {self.description}")
        return " ".join(parts)


@dataclass
class BIMConcept:
    """建筑领域概念节点，如 Column / Window / Door / Material。"""

    name: str
    description: str = ""
    required_apis: list[str] = field(default_factory=list)
    init_pattern: str = ""      # 典型初始化代码片段，供 prompt 示例注入

    def to_constraint_prompt(self, api_map: dict[str, APIFunction]) -> str:
        """构建注入 LLM 的约束文本。"""
        lines = [f"[图谱约束] 概念「{self.name}」（{self.description}）"]
        if self.required_apis:
            lines.append("必须使用的 GDL API：")
            for api_name in self.required_apis:
                api = api_map.get(api_name.upper())
                if api:
                    lines.append(f"  - {api.to_prompt_line()}")
                else:
                    lines.append(f"  - {api_name}")
        if self.init_pattern:
            lines.append(f"典型代码模式：\n{self.init_pattern}")
        return "\n".join(lines)
