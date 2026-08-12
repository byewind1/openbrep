"""项目命名统一管线：语义名来源 → sanitize（保中文）→ unique（_vN 后缀）。

全仓唯一实现（P7a）：workbench 服务、mcp_tools、blender_import_service
全部从这里取 `safe_project_name` 与 `unique_project_name`，禁止另起 sanitize
逻辑。设计依据：`项目命名方式设计-2026-08-12.md` §3.1 / §3.3。
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
