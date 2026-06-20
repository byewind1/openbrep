"""GDL 领域知识图谱管理器。

基于 dict 的轻量图存储，无外部依赖。图数据从 knowledge/gdl_graph.json 加载。

主要能力：
- query_api()        — 查验 API 是否存在，返回签名和约束
- suggest_apis()     — 根据 intent 文本匹配 BIM 概念，返回推荐 API 列表
- diagnose_error()   — 从编译错误消息提取变量名，查找图谱中的归因与修复提示
- build_constraint_prompt() — 构建注入 LLM 上下文的约束文本
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

from openbrep.graph_schema import APIFunction, BIMConcept

logger = logging.getLogger(__name__)

_GRAPH_JSON_PATH = Path(__file__).parent.parent / "knowledge" / "gdl_graph.json"


class GDLGraphManager:
    """内存图谱管理器（单例，懒加载）。

    图结构：
      api_map:      {API_NAME_UPPER: APIFunction}
      concept_map:  {concept_name: BIMConcept}
      alias_map:    {alias_lower: concept_name}  ← 多对一
      error_patterns: 编译错误归因规则列表
    """

    def __init__(self, graph_path: Path | None = None) -> None:
        self._path = graph_path or _GRAPH_JSON_PATH
        self.api_map: dict[str, APIFunction] = {}
        self.concept_map: dict[str, BIMConcept] = {}
        self.alias_map: dict[str, str] = {}
        self.error_patterns: list[dict] = []
        self._loaded = False

    # ── 加载 ──────────────────────────────────────────────

    def load(self) -> None:
        """从 JSON 文件加载图数据。可多次调用（幂等）。"""
        if self._loaded:
            return
        if not self._path.exists():
            logger.warning("GDL graph file not found: %s — graph features disabled", self._path)
            self._loaded = True
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._load_apis(data.get("api_functions", []))
            self._load_concepts(data.get("bim_concepts", []))
            self.error_patterns = data.get("known_error_patterns", [])
            self._loaded = True
            logger.info(
                "GDL graph loaded: %d APIs, %d concepts, %d aliases",
                len(self.api_map),
                len(self.concept_map),
                len(self.alias_map),
            )
        except Exception:
            logger.exception("Failed to load GDL graph from %s", self._path)
            self._loaded = True  # 防止反复重试

    def _load_apis(self, raw: list[dict]) -> None:
        for item in raw:
            name = (item.get("name") or "").strip().upper()
            if not name:
                continue
            self.api_map[name] = APIFunction(
                name=name,
                signature=item.get("signature", ""),
                return_type=item.get("return_type", "void"),
                description=item.get("description", ""),
                param_count_min=int(item.get("param_count_min", 0)),
                param_count_max=int(item.get("param_count_max", 0)),
                category=item.get("category", ""),
            )

    def _load_concepts(self, raw: list[dict]) -> None:
        for item in raw:
            name = (item.get("name") or "").strip()
            if not name:
                continue
            concept = BIMConcept(
                name=name,
                description=item.get("description", ""),
                required_apis=item.get("required_apis", []),
                init_pattern=item.get("init_pattern", ""),
            )
            self.concept_map[name] = concept
            # 主名称本身也作为别名
            self.alias_map[name.lower()] = name
            for alias in item.get("aliases", []):
                self.alias_map[alias.lower()] = name

    # ── 查询接口 ─────────────────────────────────────────

    def query_api(self, api_name: str) -> Optional[APIFunction]:
        """查询 API 是否存在于图谱。返回 APIFunction 或 None。"""
        self.load()
        key = api_name.strip().upper()
        result = self.api_map.get(key)
        if result:
            logger.debug("[graph] query_api hit: %s → %s", api_name, result.signature)
        else:
            logger.debug("[graph] query_api miss: %s", api_name)
        return result

    def suggest_apis(self, intent: str, max_results: int = 5) -> list[APIFunction]:
        """根据意图文本匹配最相关的 BIM 概念，返回其必需 API 列表。"""
        self.load()
        intent_lower = intent.lower()
        matched_concept = self._find_concept(intent_lower)
        if matched_concept is None:
            return []
        apis = []
        for api_name in matched_concept.required_apis:
            api = self.api_map.get(api_name.upper())
            if api:
                apis.append(api)
        logger.debug("[graph] suggest_apis for '%s' → concept=%s, apis=%s", intent, matched_concept.name, [a.name for a in apis])
        return apis[:max_results]

    def diagnose_error(self, error_msg: str) -> str:
        """从编译错误消息推断根因，返回可注入 prompt 的诊断文本。

        流程：
          1. 调用 ErrorClassifier 做一次性分类（复用已有正则，不重复造轮子）
          2. 用分类结果在图谱中查对应条目，补充 concept 层面的修复上下文
          3. 若为 UNDEFINED_VAR，额外提取变量名做变量级归因

        返回空字符串表示无图谱诊断可用。
        """
        self.load()
        if not error_msg:
            return ""

        lines: list[str] = []

        # ── 1. 走 ErrorClassifier 分类（阶段3：与 error_classifier.py 深度对齐）──
        try:
            from openbrep.error_classifier import ErrorClassifier, ErrorCategory
            ec = ErrorClassifier()
            case = ec.classify(error_msg)

            if case.category != ErrorCategory.UNKNOWN:
                # 在图谱中查该 error_category 的条目
                graph_entry = self._find_error_pattern_by_category(case.category.value)
                fix_hint = (graph_entry.get("fix_hint") if graph_entry else "") or case.hint
                target = case.target_file or (graph_entry.get("target_file") if graph_entry else "")
                target_str = f"（位置：{target}）" if target else ""
                lines.append(
                    f"[图谱诊断] 错误类型：{case.category.value}{target_str}\n"
                    f"修复建议：{fix_hint}"
                )
                logger.info("[graph] ErrorClassifier hit: %s", case.category.value)

                # UNDEFINED_VAR 追加变量级归因
                if case.category == ErrorCategory.UNDEFINED_VAR:
                    undefined_names = _extract_undefined_names(error_msg)
                    for var_name in undefined_names[:3]:
                        var_diag = self._diagnose_variable(var_name)
                        if var_diag:
                            lines.append(var_diag)
            else:
                logger.debug("[graph] ErrorClassifier: UNKNOWN, falling back to pattern scan")
                # Fallback：变量归因 + 直接匹配 known_error_patterns 中的自定义规则
                undefined_names = _extract_undefined_names(error_msg)
                for var_name in undefined_names[:3]:
                    var_diag = self._diagnose_variable(var_name)
                    if var_diag:
                        lines.append(var_diag)
                for ep in self.error_patterns:
                    pattern = ep.get("pattern", "")
                    if pattern and pattern in error_msg.lower():
                        lines.append(
                            f"[图谱诊断] 错误与 '{pattern}' 相关：{ep.get('diagnosis', '')} "
                            f"修复建议：{ep.get('fix_hint', '')}"
                        )
                        break
        except Exception as exc:
            logger.debug("ErrorClassifier in graph diagnosis failed: %s", exc)
            # 安全降级：纯 pattern 扫描
            undefined_names = _extract_undefined_names(error_msg)
            for var_name in undefined_names[:3]:
                var_diag = self._diagnose_variable(var_name)
                if var_diag:
                    lines.append(var_diag)

        if not lines:
            return ""
        result = "\n".join(lines)
        logger.info("[graph] diagnose_error result:\n%s", result)
        return result

    def _find_error_pattern_by_category(self, category_value: str) -> Optional[dict]:
        """在 error_patterns 中查找匹配 error_category 的条目。"""
        for ep in self.error_patterns:
            if ep.get("error_category") == category_value:
                return ep
        return None

    def build_constraint_prompt(self, intent: str) -> str:
        """根据用户意图构建 API 约束提示，注入 CREATE 路径 prompt。

        返回空字符串表示无图谱约束可注入。
        """
        self.load()
        intent_lower = intent.lower()
        matched_concept = self._find_concept(intent_lower)
        if matched_concept is None:
            return ""
        prompt = matched_concept.to_constraint_prompt(self.api_map)
        logger.info("[graph] build_constraint_prompt for '%s' → concept=%s", intent, matched_concept.name)
        return prompt

    # ── 内部工具 ─────────────────────────────────────────

    def _find_concept(self, text_lower: str) -> Optional[BIMConcept]:
        """在别名表中查找与文本匹配的 BIM 概念。"""
        for alias, concept_name in self.alias_map.items():
            if alias in text_lower:
                return self.concept_map.get(concept_name)
        return None

    def _diagnose_variable(self, var_name: str) -> str:
        """对单个未定义变量名查图谱，返回归因文本。"""
        var_lower = var_name.lower()

        # 在 error_patterns 中匹配
        for ep in self.error_patterns:
            pattern = ep.get("pattern", "")
            if pattern and pattern in var_lower:
                concept_name = ep.get("concept", "")
                concept = self.concept_map.get(concept_name)
                init_hint = ""
                if concept and concept.init_pattern:
                    init_hint = f"\n参考初始化：\n{concept.init_pattern}"
                return (
                    f"[图谱诊断] 缺失变量 `{var_name}` 属于「{concept_name}」概念。"
                    f"{ep.get('diagnosis', '')} {ep.get('fix_hint', '')}{init_hint}"
                )

        # 在 API 名称中查找相近项（变量名与 API 同名）
        api = self.api_map.get(var_name.upper())
        if api:
            return (
                f"[图谱诊断] `{var_name}` 是图谱中合法的 GDL API。"
                f"正确用法：{api.name} {api.signature}"
            )

        return ""


# ── 模块级单例 ────────────────────────────────────────────

_instance: Optional[GDLGraphManager] = None


def get_graph_manager() -> GDLGraphManager:
    """获取 GDLGraphManager 全局单例（懒加载）。"""
    global _instance
    if _instance is None:
        _instance = GDLGraphManager()
        _instance.load()
    return _instance


def reset_graph_manager(path: Path | None = None) -> None:
    """测试专用：替换单例（允许注入自定义 graph_path）。"""
    global _instance
    _instance = GDLGraphManager(graph_path=path) if path else None


# ── 辅助函数 ─────────────────────────────────────────────

def _extract_undefined_names(error_msg: str) -> list[str]:
    """从编译错误消息中提取未定义变量/标识符名称。"""
    patterns = [
        # LP_XMLConverter: "(0) : error: Undefined variable 'mat_01'"
        r"undefined\s+(?:variable|identifier)\s+['\"]?(\w+)['\"]?",
        # "undeclared variable xyz"
        r"undeclared\s+(?:variable|identifier)\s+['\"]?(\w+)['\"]?",
        # "unknown variable 'foo'"
        r"unknown\s+(?:variable|identifier)\s+['\"]?(\w+)['\"]?",
        # generic "variable 'name' not found"
        r"variable\s+['\"](\w+)['\"]",
    ]
    names: list[str] = []
    seen: set[str] = set()
    for pat in patterns:
        for m in re.finditer(pat, error_msg, re.IGNORECASE):
            name = m.group(1)
            if name and name not in seen:
                seen.add(name)
                names.append(name)
    return names
