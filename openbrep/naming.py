"""项目命名统一管线：语义名来源 → sanitize（保中文）→ unique（_vN 后缀）。

全仓唯一实现（P7a/P7b）：workbench 服务、mcp_tools、blender_import_service
全部从这里取 `safe_project_name`、`unique_project_name` 与
`project_name_from_prompt`，禁止另起 sanitize/提取逻辑。设计依据：
`项目命名方式设计-2026-08-12.md` §3.1 / §3.2 / §3.3。
"""

from __future__ import annotations

import re
from pathlib import Path

# 文件系统危险字符：Windows 保留字符 + 控制字符。中日韩等 Unicode 文字全部保留。
_FS_DANGEROUS_CHARS = re.compile(r'[/\\:*?"<>|\x00-\x1f]')
_WS_RUN = re.compile(r"\s+")

#: 全部剥光后的统一兜底名（废止 "Imported_GDL" / "Generated_Object"）
DEFAULT_PROJECT_NAME = "未命名构件"


def safe_project_name(name: str) -> str:
    """把任意来源的名字整理成文件系统安全的项目名，保留中日韩等 Unicode 文字。

    规则（P7a §3.1）：
    - 只剥文件系统危险字符：/ \\ : * ? " < > | 与控制字符（\x00-\x1f），
      替换为空格，随后与原有空白一起压成单空格；
    - 首尾空格与点剥离；
    - 其余字符（中文/日文/韩文/emoji 等）逐字符保留；
    - 全部剥光 → DEFAULT_PROJECT_NAME。

    向后兼容：纯 ASCII 合法名（如 "Bookshelf_v1"）逐字符不变。
    """
    cleaned = str(name or "")
    cleaned = _FS_DANGEROUS_CHARS.sub(" ", cleaned)
    cleaned = _WS_RUN.sub(" ", cleaned)
    cleaned = cleaned.strip(" .")
    return cleaned or DEFAULT_PROJECT_NAME


#: AI create 规则提取（二级命名来源）取名前 N 个字符（中英文均按字符计）
PROMPT_NAME_MAX_CHARS = 12
#: 剥 [图N] / 图N token（参考图引用与对象语义无关）
_IMAGE_TOKEN_RE = re.compile(r"\[?图\d+\]?")
#: 句首动词/礼貌前缀（长的排前面，避免 制作 被 做 抢先截断）
_LEADING_VERB_RE = re.compile(r"^(?:帮我|帮忙|请|生成|创建|新建|重做|制作|做)")
#: 句首量词（长的排前面）
_LEADING_QUANTIFIER_RE = re.compile(r"^(?:一个|一只|这款|这个|个|只|把)")


def project_name_from_prompt(prompt: str) -> str:
    """规则提取：从创建 prompt 提炼项目名候选（P7b §3.2，二级来源；一级是 object_type）。

    规则：
    1. 剥 `[图N]` / `图N` token（`[图1][图2]生成…` → `生成…`）；
    2. 反复剥句首动词/礼貌前缀：帮我|帮忙|请|生成|创建|新建|重做|制作|做；
    3. 反复剥句首量词：一个|一只|这款|这个|个|只|把；
    4. 取前 PROMPT_NAME_MAX_CHARS 个字符（中英文均按字符计）；
    5. 全部剥空 → 返回 ""（由上层 safe_project_name 兜底到 未命名构件）。

    例：'参考图1生成中国古建筑斗拱中坐斗gdl构件' → 剥图 token →
    '参考生成中国古建筑斗拱中坐斗gdl构件'（句首非动词不剥）→ 取前 12 字。
    规则提取只是回退，不必追求完美语义；object_type 才是一级来源。
    """
    text = str(prompt or "")
    text = _IMAGE_TOKEN_RE.sub("", text)
    changed = True
    while changed and text:
        changed = False
        stripped = text.lstrip()
        if stripped != text:
            text = stripped
            changed = True
            continue
        match = _LEADING_VERB_RE.match(text)
        if match:
            text = text[match.end():]
            changed = True
            continue
        match = _LEADING_QUANTIFIER_RE.match(text)
        if match:
            text = text[match.end():]
            changed = True
    return text[:PROMPT_NAME_MAX_CHARS]


def unique_project_name(base_name: str, work_dir: Path) -> str:
    """在 work_dir 下给 base_name 找一个不冲突的项目名。

    首个项目保持原名不加后缀；冲突依次加 _v2/_v3……（贴合用户已有 _vN 心智）。
    既有的 _2 风格目录不受影响（只影响新命名）。
    """
    candidate = base_name
    version = 2
    while (work_dir / candidate).exists():
        candidate = f"{base_name}_v{version}"
        version += 1
    return candidate
