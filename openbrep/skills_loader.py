"""
Skills loader for GDL Agent.

Skills are task-specific prompt strategies stored as Markdown files
in the skills/ directory. Unlike knowledge/ (which provides reference facts),
skills/ provides methodology — HOW the LLM should approach a task.

The skills/ directory is intentionally shipped empty (with README only).
Users develop their own skills based on their ArchiCAD version,
coding standards, and observed LLM behavior.

Skill files may carry an optional YAML frontmatter block at the top:

    ---
    status: active|verified|proposed|deprecated
    skill_version: 1
    pattern_type:
    source_project:
    source_trace_id:
    verified_evidence:
      ...
    reuse_count: 0
    last_used: null
    ---

- Body (without frontmatter) is what gets injected into prompts; files
  without frontmatter keep their full text as body (byte-identical legacy
  behavior).
- Metadata is exposed via skill_meta(name); frontmatter-less files get
  defaults (status=active, skill_version=0, reuse_count=0, ...).
- Only status in {active, verified} is injected. proposed/deprecated are
  listed in skill_names for management but never injected.
- Reuse counting: when a skill is actually injected by get_for_task, its
  reuse_count is incremented and last_used set to today's ISO date, written
  back to the file's frontmatter (only those two fields; frontmatter-less
  files are never rewritten). Counting is de-duplicated per load() lifecycle.
  Only files whose frontmatter already carries a reuse field
  (reuse_count / last_used) are counted — files that never opted into the
  reuse metadata are left byte-identical on disk (存量文件不被强行加头).
- Injection side channel: every get_for_task call records the names it
  actually injected into ``self.last_injected`` (reset at call start; pure
  in-memory, injected text unchanged). The pipeline reads this to expose
  ``metadata["injected_skills"]`` so the GUI channel can write back
  fail_count — fail_count itself is NEVER written here.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# Task type → skill filename mapping
# Users can add their own skill files; these are the default mappings
_TASK_SKILL_MAP: dict[str, list[str]] = {
    "create":   ["create_object"],
    "modify":   ["modify_parameter"],
    "debug":    ["fix_compile_error", "learn_from_errors"],
    "optimize": ["optimize_geometry"],
    "2d":       ["create_2d_symbol"],
    "ui":       ["create_ui_panel"],
}

_DEFAULT_SKILL_NAMES = {name for names in _TASK_SKILL_MAP.values() for name in names}

# Keywords that trigger specific task types
_TASK_KEYWORDS: dict[str, list[str]] = {
    "create":   ["create", "new", "build", "generate", "从零", "新建", "创建", "生成"],
    "modify":   ["add", "change", "modify", "update", "增加", "修改", "添加", "调整"],
    "debug":    ["fix", "error", "bug", "repair", "修复", "报错", "错误", "修正"],
    "optimize": ["optimize", "improve", "simplify", "performance", "优化", "简化", "性能"],
    "2d":       ["2d", "plan", "symbol", "平面", "符号"],
    "ui":       ["ui", "panel", "dialog", "界面", "面板", "对话框"],
}

# 只有 active / verified 的 skill 会被注入 prompt；proposed / deprecated 跳过。
_INJECTABLE_STATUSES = frozenset({"active", "verified"})

# 无 frontmatter 的旧文件默认元数据（skill_meta 返回的基准值）。
_DEFAULT_META: dict[str, Any] = {
    "skill_version": 0,
    "status": "active",
    "pattern_type": None,
    "source_project": None,
    "source_trace_id": None,
    "verified_evidence": None,
    "reuse_count": 0,
    "last_used": None,
    "fail_count": 0,
    "last_failed": None,
}


# ── 极简 frontmatter 解析（手写 YAML 子集，无第三方依赖） ─────


def _split_kv(line: str) -> tuple[Optional[str], bool, str]:
    """把 `key: value` 拆成 (key, has_colon, value)。拆不出来返回 (None, False, "")。"""
    stripped = line.strip()
    if ":" not in stripped:
        return None, False, ""
    key, _, value = stripped.partition(":")
    key = key.strip()
    if not key:
        return None, False, ""
    return key, True, value.strip()


def _parse_scalar(value: str) -> Any:
    """把 YAML 标量字符串解析为 Python 值（null/bool/int/float/引号字符串/裸串）。"""
    v = value.strip()
    if not v or v.lower() in ("null", "none", "~"):
        return None
    low = v.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        return v[1:-1]
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    if re.fullmatch(r"-?\d+\.\d+([eE][+-]?\d+)?", v):
        return float(v)
    return v


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip())


def _parse_frontmatter(text: str) -> Optional[dict[str, Any]]:
    """极简 YAML 子集解析：只支持平铺字段 + 简单缩进嵌套 dict（如 verified_evidence）。

    任何顶层/嵌套行无法拆成 `key: value`（如顶层出现 `- foo` 列表项、裸文本）
    都视为坏 YAML，整体返回 None（调用方按无 frontmatter 处理并记 warning）。
    """
    meta: dict[str, Any] = {}
    lines = [
        ln for ln in text.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    i = 0
    while i < len(lines):
        line = lines[i]
        indent = _indent_of(line)
        if indent > 0:
            return None  # 没有父 key 的悬挂缩进行
        key, has_colon, value = _split_kv(line)
        if key is None or not has_colon:
            return None
        if value == "" and i + 1 < len(lines) and _indent_of(lines[i + 1]) > 0:
            nested: dict[str, Any] = {}
            j = i + 1
            while j < len(lines) and _indent_of(lines[j]) > 0:
                nkey, ncolon, nvalue = _split_kv(lines[j])
                if nkey is None or not ncolon:
                    return None
                nested[nkey] = _parse_scalar(nvalue)
                j += 1
            meta[key] = nested
            i = j
        else:
            meta[key] = _parse_scalar(value)
            i += 1
    return meta


def _split_frontmatter(content: str) -> tuple[str, Optional[dict[str, Any]], bool]:
    """拆分 frontmatter：返回 (正文, 元数据, 是否有 frontmatter)。

    - 无 frontmatter（不以 --- 开头，或只有开没有闭）：正文=原内容，meta=None，
      has_fm=False。
    - frontmatter 正常：正文=去除头块后的内容，meta=dict，has_fm=True。
    - frontmatter 存在但 YAML 坏：正文=原内容（按无 frontmatter 对待），
      meta=None，has_fm=True（调用方记 warning）。
    """
    if not content.startswith("---"):
        return content, None, False
    lines = content.splitlines(keepends=True)
    end = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end = idx
            break
    if end is None:
        return content, None, False
    fm_text = "".join(lines[1:end])
    body = "".join(lines[end + 1:])
    try:
        meta = _parse_frontmatter(fm_text)
    except Exception:
        meta = None
    if meta is None:
        return content, None, True
    return body, meta, True


def _line_ending(line: str) -> str:
    for ending in ("\r\n", "\n", "\r"):
        if line.endswith(ending):
            return ending
    return ""


def _set_fm_value(line: str, value_text: str) -> str:
    """就地替换 `key: value` 行里冒号后的值；保留冒号前/后的空白与行尾符。"""
    colon = line.find(":")
    if colon == -1:
        return line
    ending = _line_ending(line)
    rest = line[colon + 1:-len(ending)] if ending else line[colon + 1:]
    lead = rest[:len(rest) - len(rest.lstrip(" \t"))]
    if lead:
        return f"{line[:colon + 1]}{lead}{value_text}{ending}"
    return f"{line[:colon + 1]} {value_text}{ending}"


def _rewrite_fm_lines(inner_lines: list[str], updates: dict[str, str]) -> list[str]:
    """在 frontmatter 内层行上更新/新增指定顶层 key 的值；其余行原样保留。"""
    key_index: dict[str, int] = {}
    for idx, line in enumerate(inner_lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or _indent_of(line) > 0:
            continue
        key, has_colon, _ = _split_kv(stripped)
        if key is not None and has_colon:
            key_index.setdefault(key, idx)
    for key, value_text in updates.items():
        if key in key_index:
            inner_lines[key_index[key]] = _set_fm_value(inner_lines[key_index[key]], value_text)
        else:
            inner_lines.append(f"{key}: {value_text}\n")
    return inner_lines


def _fm_scalar(value: Any) -> str:
    """Python 标量 → 极简 YAML 标量文本（bool/null/int/float/str）。"""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return str(value)
    return str(value)


def _replace_or_append_nested_block(
    inner_lines: list[str], key: str, block: dict[str, Any]
) -> list[str]:
    """整体替换或追加一个顶层嵌套块（如 verified_evidence）。

    已存在的块会连旧缩进子行一起清掉再写新的，绝不留下悬挂缩进行
    （悬挂行会让 _parse_frontmatter 判坏，必须避免）。
    """
    key_idx = None
    for idx, line in enumerate(inner_lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or _indent_of(line) > 0:
            continue
        k, has_colon, _ = _split_kv(stripped)
        if k == key and has_colon:
            key_idx = idx
            break
    block_lines = [f"{key}:\n"] + [
        f"  {sub_key}: {_fm_scalar(sub_value)}\n" for sub_key, sub_value in block.items()
    ]
    if key_idx is None:
        inner_lines.extend(block_lines)
        return inner_lines
    j = key_idx + 1
    while j < len(inner_lines) and _indent_of(inner_lines[j]) > 0:
        j += 1
    inner_lines[key_idx:j] = block_lines
    return inner_lines


def rewrite_skill_frontmatter(
    file_path: Path,
    updates: Optional[dict[str, str]] = None,
    nested_blocks: Optional[dict[str, dict[str, Any]]] = None,
) -> bool:
    """公开写接口：把 frontmatter 顶层标量更新 + 嵌套块整体替换写回磁盘。

    - updates: {顶层 key: 值文本}，复用 _rewrite_fm_lines 行级改写（已有行就地改值，
      新行追加到块尾）。
    - nested_blocks: {顶层 key: {子 key: 标量}}，写成缩进子行；已存在的块整块替换。
    - 文件无 frontmatter / 缺闭合 --- / 读不出 → 返回 False，文件字节不动。
    """
    updates = updates or {}
    nested_blocks = nested_blocks or {}
    try:
        text = file_path.read_text(encoding="utf-8")
    except Exception:
        return False
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return False
    end = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end = idx
            break
    if end is None:
        return False
    inner = _rewrite_fm_lines(list(lines[1:end]), updates)
    for key, block in nested_blocks.items():
        inner = _replace_or_append_nested_block(inner, key, block)
    lines[1:end] = inner
    try:
        file_path.write_text("".join(lines), encoding="utf-8")
    except Exception:
        return False
    return True


class SkillsLoader:
    """
    Loads and manages prompt engineering skills.

    Skills are Markdown files in the skills/ directory that contain
    task-specific strategies, rules, examples, and pitfall warnings.
    """

    def __init__(self, skills_dir: str = "./skills"):
        self.skills_dir = Path(skills_dir)
        self._skills: dict[str, str] = {}
        self._meta: dict[str, dict[str, Any]] = {}
        self._raw_meta: dict[str, dict[str, Any]] = {}
        self._file_paths: dict[str, Path] = {}
        self._has_frontmatter: set[str] = set()
        self._reuse_counted: set[str] = set()
        self._loaded = False
        # 注入侧通道（纯内存旁路，不改注入文本）：每次 get_for_task 调用开头
        # 重置，记录本次实际注入的 skill 名。pipeline 在任务结束时读取它并写进
        # TaskResult.metadata["injected_skills"]；fail_count 回写在 GUI 侧完成。
        self.last_injected: list[str] = []
        # ST04：匹配来源与理由（同样只读旁路，注入文本不变）。每条：
        # {name, source, reason, status, pattern_type, matched_terms}
        # source 取值：task_type / explicit_word / custom_match。
        self.last_injected_details: list[dict[str, Any]] = []

    def load(self) -> None:
        """Load all .md files from the skills directory (excluding README).

        键=文件 stem，值=正文（无 frontmatter 的文件=全文）。元数据见 skill_meta()。
        """
        self._skills.clear()
        self._meta.clear()
        self._raw_meta.clear()
        self._file_paths.clear()
        self._has_frontmatter.clear()
        self._reuse_counted.clear()

        if not self.skills_dir.exists():
            self._loaded = True
            return

        for md_file in _iter_skill_files(self.skills_dir):
            if md_file.stem.upper() == "README":
                continue
            try:
                content = md_file.read_text(encoding="utf-8")
            except Exception:
                continue
            body, meta, has_fm = _split_frontmatter(content)
            if has_fm and meta is None:
                logger.warning(
                    "skill %s frontmatter 解析失败（坏 YAML），按无 frontmatter 处理",
                    md_file.stem,
                )
                body = content
                meta = {}
                has_fm = False
            self._skills[md_file.stem] = body
            self._meta[md_file.stem] = {**_DEFAULT_META, **(meta or {})}
            self._raw_meta[md_file.stem] = dict(meta or {})
            self._file_paths[md_file.stem] = md_file
            if has_fm:
                self._has_frontmatter.add(md_file.stem)

        self._loaded = True

    def detect_task_type(self, instruction: str) -> list[str]:
        """
        Detect task types from user instruction.

        Returns list of task type strings (e.g., ["create"], ["modify", "2d"]).
        """
        instruction_lower = instruction.lower()
        detected = []

        for task_type, keywords in _TASK_KEYWORDS.items():
            if any(kw in instruction_lower for kw in keywords):
                detected.append(task_type)

        return detected if detected else ["modify"]  # default

    def get_for_task(self, instruction: str, error: Optional[str] = None) -> str:
        """
        Get relevant skills for a task.

        Args:
            instruction: User's instruction text.
            error: Compile error message (if retrying).

        Returns:
            Concatenated skill content, or empty string if no skills found.
        """
        if not self._loaded:
            self.load()

        self.last_injected = []  # 本次调用开头重置注入侧通道
        self.last_injected_details = []

        if not self._skills:
            return ""

        # Detect task types
        task_types = self.detect_task_type(instruction)

        # If retrying with an error, always include debug skill
        if error and "debug" not in task_types:
            task_types.append("debug")

        # Collect matching skill files（记录每个名字的来源，供审计输出）
        skill_names: list[str] = []
        sources: dict[str, str] = {}
        match_reasons: dict[str, str] = {}
        matched_terms: dict[str, list[str]] = {}
        strong_signals: dict[str, bool] = {}
        for task_type in task_types:
            if task_type in _TASK_SKILL_MAP:
                for mapped in _TASK_SKILL_MAP[task_type]:
                    skill_names.append(mapped)
                    sources.setdefault(mapped, "task_type")
                    match_reasons.setdefault(mapped, f"任务类型 {task_type} 的内置映射")

        # Also check for exact filename matches
        # (user might have custom skills like "curtain_wall.md")
        instruction_words = instruction.lower().split()
        for word in instruction_words:
            if len(word) > 3 and word in self._skills:
                if word not in skill_names:
                    skill_names.append(word)
                sources.setdefault(word, "explicit_word")
                match_reasons.setdefault(word, "指令里显式出现了该 skill 名")

        for name, detail in self._match_custom_skills(instruction, set(skill_names)):
            skill_names.append(name)
            sources.setdefault(name, "custom_match")
            match_reasons.setdefault(name, str(detail.get("reason") or "自定义内容匹配"))
            matched_terms.setdefault(name, list(detail.get("matched_terms") or []))
            strong_signals.setdefault(name, bool(detail.get("strong")))

        # Load and concatenate (只注入 active / verified；命中即计复用）
        parts = []
        seen = set()
        for name in skill_names:
            if name in self._skills and name not in seen and self._is_injectable(name):
                parts.append(f"## Skill: {name}\n\n{self._skills[name]}")
                seen.add(name)
                self._count_reuse(name)
                self.last_injected.append(name)
                meta = self.skill_meta(name)
                self.last_injected_details.append({
                    "name": name,
                    "source": sources.get(name, "unknown"),
                    "reason": match_reasons.get(name, ""),
                    "status": meta.get("status"),
                    "pattern_type": meta.get("pattern_type"),
                    "matched_terms": matched_terms.get(name, []),
                    "strong": strong_signals.get(name),
                })

        return "\n\n---\n\n".join(parts)

    def _match_custom_skills(
        self, instruction: str, selected: set[str], *, limit: int = 2
    ) -> list[tuple[str, dict[str, Any]]]:
        """自定义 skill 自动匹配：返回 (name, 详情) 列表。

        ST04 返工：本函数**保留旧选择行为**（score >= 1 即注入），只附带
        source/reason/强信号标记供审计。收紧自动匹配（strong 才注入 + 骨架模板
        不自动注入）需要同时重录 create+modify 黄金语料，而当前环境修改套件所依赖
        的 chat-completions provider 没有可用凭据（provider_keys 全是占位符，
        只有 local Codex 可用），因此 item 8 的收紧标记为 BLOCKED，详见实施回执。
        收紧开关已就绪：`detail["strong"]` + name.startswith("_")。
        """
        instruction_lower = instruction.lower()
        instruction_tokens = set(_tokenize(instruction_lower))
        matches: list[tuple[int, str, dict[str, Any]]] = []

        for name, content in self._skills.items():
            if name in selected or name in _DEFAULT_SKILL_NAMES:
                continue
            if not self._is_injectable(name):
                continue  # proposed / deprecated 不参与匹配
            detail = _score_custom_skill_match(
                name, content, instruction_lower, instruction_tokens
            )
            if detail["score"] >= 1:
                matches.append((detail["score"], name, detail))

        matches.sort(key=lambda item: (-item[0], item[1]))
        return [(name, detail) for _, name, detail in matches[:limit]]

    def get_by_name(self, name: str) -> Optional[str]:
        """Get a specific skill by filename (without extension).

        Only injectable skills (status active/verified) are returned;
        proposed/deprecated return None.
        """
        if not self._loaded:
            self.load()
        if not self._is_injectable(name):
            return None
        return self._skills.get(name)

    @property
    def skill_count(self) -> int:
        if not self._loaded:
            self.load()
        return len(self._skills)

    @property
    def skill_names(self) -> list[str]:
        """全部 skill 名（含 proposed/deprecated，管理面可见）。"""
        if not self._loaded:
            self.load()
        return list(self._skills.keys())

    # ── 元数据 / 状态查询 ─────────────────────────────────

    def skill_meta(self, name: str) -> dict[str, Any]:
        """skill 的元数据字典；无 frontmatter 或未知名返回默认值副本。

        默认：{skill_version: 0, status: "active", reuse_count: 0, ...}。
        """
        if not self._loaded:
            self.load()
        return dict(self._meta.get(name, _DEFAULT_META))

    def skill_names_by_status(self, status: str) -> list[str]:
        """列出指定 status 的 skill 名（管理面查询，不做注入过滤）。"""
        if not self._loaded:
            self.load()
        return [name for name in self._skills if self.skill_meta(name).get("status") == status]

    def _is_injectable(self, name: str) -> bool:
        return self.skill_meta(name).get("status") in _INJECTABLE_STATUSES

    # ── 复用计数回写 ───────────────────────────────────────

    def _count_reuse(self, name: str) -> None:
        """实际注入一次：reuse_count += 1、last_used=今天，写回 frontmatter。

        防抖：同一次 load() 生命周期内同一 skill 只计一次。只有 frontmatter
        已带复用字段（reuse_count/last_used）的文件才计数改写——无 frontmatter
        的旧文件与没接入复用元数据的文件一律跳过（不给存量文件强行加头）。
        写回失败静默降级，不影响注入。
        """
        if name in self._reuse_counted or name not in self._has_frontmatter:
            return
        raw = self._raw_meta.get(name, {})
        if "reuse_count" not in raw and "last_used" not in raw:
            return  # 未接入复用元数据：不计数、不改写
        try:
            new_count = int(self._meta[name].get("reuse_count", 0) or 0) + 1
            today = date.today().isoformat()
            self._persist_reuse_meta(name, reuse_count=new_count, last_used=today)
            self._meta[name]["reuse_count"] = new_count
            self._meta[name]["last_used"] = today
            self._reuse_counted.add(name)
        except Exception:
            logger.debug("skill %s 复用计数回写失败，静默降级", name)

    def _persist_reuse_meta(self, name: str, *, reuse_count: int, last_used: str) -> None:
        """把 reuse_count / last_used 写回文件 frontmatter（只动这两行）。"""
        file_path = self._file_paths[name]
        text = file_path.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        if not lines or lines[0].strip() != "---":
            return
        end = None
        for idx in range(1, len(lines)):
            if lines[idx].strip() == "---":
                end = idx
                break
        if end is None:
            return
        inner = _rewrite_fm_lines(
            lines[1:end],
            {"reuse_count": str(int(reuse_count)), "last_used": last_used},
        )
        lines[1:end] = inner
        file_path.write_text("".join(lines), encoding="utf-8")


def _tokenize(text: str) -> list[str]:
    return [token for token in re.split(r"[^a-z0-9_一-鿿]+", text.lower()) if len(token) >= 2]


# ST04：正文/触发词重叠里的"通用词"——出现在任何 GDL 模型里的协议词、命令名、
# 单位数字、预留参数名。这些词命中不构成领域匹配证据（斗拱 skill 曾因此被注入到
# 门窗/书桌/桥架等无关任务）。中文通用词同样列在此。
_MATCH_STOPWORDS: frozenset[str] = frozenset({
    # 英文功能词/协议词
    "the", "and", "for", "with", "from", "not", "are", "you", "your", "use", "used",
    "using", "can", "will", "must", "may", "this", "that", "then", "than", "when",
    "where", "which", "into", "out", "one", "two", "all", "any", "its", "has", "have",
    # GDL / HSF 协议词与常用命令名
    "gdl", "xml", "hsf", "gsm", "script", "scripts", "param", "params", "parameter",
    "parameters", "object", "objects", "value", "values", "name", "names", "type",
    "types", "code", "file", "files", "block", "prism", "rot", "add", "addz", "del",
    "for", "next", "then", "if", "endif", "end", "call", "gosub", "return",
    "a", "b", "c", "zzyzx", "width", "height", "depth", "length",
    # 中文通用词
    "参数", "脚本", "默认", "宽度", "高度", "深度", "长度", "构件", "对象", "模型",
    "生成", "创建", "修改", "尺寸", "单位", "定义", "说明", "示例", "参考", "项目",
    "文件", "结构", "类型", "名称", "使用", "实现", "支持", "需要", "可以", "以及",
})


def _is_meaningful_match_token(token: str) -> bool:
    """通用词/纯数字不作为领域匹配证据。"""
    text = (token or "").strip().lower()
    if len(text) < 2 or text in _MATCH_STOPWORDS:
        return False
    if re.fullmatch(r"[0-9][0-9a-z_.\-]*", text):
        return False
    return True


def _iter_skill_files(skills_dir: Path) -> list[Path]:
    public_files = sorted(skills_dir.glob("*.md"))
    pro_files = sorted((skills_dir / "pro").glob("*.md"))
    return public_files + pro_files


def _score_custom_skill_match(
    name: str, content: str, instruction_lower: str, instruction_tokens: set[str]
) -> dict[str, Any]:
    """自定义 skill 匹配打分 + 证据。

    返回 ``{score, strong, matched_terms, reason}``。score 沿用既有打分（两个正文
    循环各自计分）；``strong`` 决定是否允许自动注入（ST04 item 8）：False 表示这次
    命中没有 skill 名/触发小节强信号，仅靠正文通用词重叠，不注入（斗拱曾被注入到
    无关任务）。
    """
    score = 0
    matched: list[str] = []
    name_tokens = set(_tokenize(name.replace("_", " ").replace("-", " ")))
    name_hits = sorted(name_tokens & instruction_tokens)
    score += 2 * len(name_hits)
    matched.extend(name_hits)

    activation_text = _extract_activation_text(content).lower()
    activation_terms = _activation_terms(activation_text)
    activation_hits: list[str] = []
    for term in activation_terms:
        if term and term in instruction_lower:
            score += 3
            activation_hits.append(term)

    body = content[:3000].lower()
    body_hits: list[str] = []
    for token in instruction_tokens:
        if token in body:
            score += 1
            body_hits.append(token)
    for token in set(_tokenize(body)):
        if token in instruction_lower:
            # 与既有打分保持一致：两个正文循环各自计分（不跨循环去重），
            # 否则会改变注入选择顺序（prompt 变化 → 语料回放未命中）。
            score += 1
            body_hits.append(token)

    # strong=True 表示命中"强信号"（skill 名 token 或触发小节词）。仅正文重叠的命中
    # 会标成"疑似误匹配"供审计，但当前不改变注入判定（ST04 只加诊断）。
    strong = bool(name_hits or activation_hits)
    meaningful_body = sorted({t for t in body_hits if _is_meaningful_match_token(t)})
    generic_body = sorted({t for t in body_hits if not _is_meaningful_match_token(t)})
    if name_hits:
        reason = f"skill 名命中指令词：{'、'.join(name_hits[:5])}"
    elif activation_hits:
        reason = f"触发小节词命中：{'、'.join(activation_hits[:3])}"
    elif meaningful_body:
        reason = f"正文领域词重叠：{'、'.join(meaningful_body[:5])}"
    elif generic_body:
        reason = f"仅正文通用词重叠（疑似误匹配，待重录语料后收紧）：{'、'.join(generic_body[:5])}"
    else:
        reason = "无命中"
    return {
        "score": score,
        "strong": strong,
        "matched_terms": sorted(set(matched) | set(activation_hits)),
        "reason": reason,
    }


def _extract_activation_text(content: str) -> str:
    lines = content.splitlines()
    chunks: list[str] = []
    collecting = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            lower = stripped.lower()
            collecting = any(marker in lower for marker in ("触发关键词", "activation keywords", "适用场景", "when to use"))
            continue
        if collecting:
            if stripped.startswith("#"):
                collecting = False
            elif stripped:
                chunks.append(stripped)
    return "\n".join(chunks)


def _activation_terms(text: str) -> list[str]:
    terms: list[str] = []
    for line in text.splitlines():
        cleaned = line.strip().lstrip("-*").strip()
        if cleaned:
            terms.append(cleaned)
            terms.extend(_tokenize(cleaned))
    return terms
