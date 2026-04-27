from __future__ import annotations

from pathlib import Path

from openbrep.compiler import HSFCompiler, MockHSFCompiler
from openbrep.config import LLMConfig
from openbrep.llm import LLMAdapter
from openbrep.skills_loader import SkillsLoader


def build_compiler(compiler_mode: str, converter_path: str):
    if compiler_mode.startswith("Mock"):
        return MockHSFCompiler()
    return HSFCompiler(converter_path or None)


def build_llm(
    *,
    model_name: str,
    api_key: str,
    api_base: str,
    assistant_settings: str,
    custom_providers: list[dict],
):
    config = LLMConfig(
        model=model_name,
        api_key=api_key,
        api_base=api_base,
        temperature=0.2,
        max_tokens=4096,
        assistant_settings=assistant_settings,
        custom_providers=custom_providers,
    )
    return LLMAdapter(config)


def load_skills(*, project_root: Path, work_dir: str) -> SkillsLoader:
    project_skills_dir = project_root / "skills"
    skills_loader = SkillsLoader(str(project_skills_dir))
    skills_loader.load()

    user_skills_dir = Path(work_dir) / "skills"
    if user_skills_dir.exists() and user_skills_dir != project_skills_dir:
        user_loader = SkillsLoader(str(user_skills_dir))
        user_loader.load()
        skills_loader._skills.update(user_loader._skills)

    return skills_loader
