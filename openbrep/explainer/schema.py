from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class ExplanationSection:
    title: str
    summary: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ScriptExplanation:
    script_type: str
    goal: str
    key_commands: list[str] = field(default_factory=list)
    parameters: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ProjectExplanation:
    overall_goal: str
    parameters_summary: list[str] = field(default_factory=list)
    script_roles: list[ExplanationSection] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    baggage: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["script_roles"] = [section.to_dict() for section in self.script_roles]
        return data
