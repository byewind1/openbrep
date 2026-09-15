"""
tapir_bridge.py — OpenBrep ↔ Archicad 通信桥

依赖：
    pip install archicad watchdog

前提：
    - Archicad 29 保持运行
    - Tapir Add-On 已安装（Add-On Manager 里可见）

功能：
    1. reload_libraries()                    — 编译后自动刷新 Archicad 库
    2. capture_errors()                      — 捕获 GDL 运行期错误（日志文件监听）
    3. get_selected_elements()               — 读取 Archicad 当前选中元素
    4. get_details_of_elements(guids)        — 读取元素详情（类型、楼层、图层等）
    5. highlight_elements(guids)             — 在 Archicad 中高亮元素
    6. get_gdl_parameters_of_elements(...)   — 读取已放置对象 GDL 参数
    7. set_gdl_parameters_of_elements(...)   — 批量写回对象 GDL 参数
    8. get_placed_params / set_placed_params — 兼容旧调用（薄封装）
    9. is_available()                        — 检查 Archicad + Tapir 是否可用
"""

from __future__ import annotations

import os
import re
import time
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Callable, Any

# ── 可选依赖：没有也不崩溃 ─────────────────────────────────────────────────

try:
    from archicad import ACConnection
    _AC_AVAILABLE = True
except ImportError:
    _AC_AVAILABLE = False

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    _WATCHDOG_AVAILABLE = True
except ImportError:
    _WATCHDOG_AVAILABLE = False


# ── GDL 错误数据结构 ───────────────────────────────────────────────────────

@dataclass
class GDLError:
    script_type: str        # "3d" | "2d" | "param" | "master" | "ui" | "properties"
    line: int               # 错误行号
    message: str            # 错误描述
    level: str              # "Error" | "Warning"
    context_lines: list[str] = field(default_factory=list)  # 错误行前后各3行

    def to_chat_text(self) -> str:
        icon = "🔴" if self.level == "Error" else "🟡"
        lines = [f"{icon} **{self.level} — {self.script_type.upper()} 脚本，第 {self.line} 行**"]
        lines.append(f"> {self.message}")
        if self.context_lines:
            lines.append("```gdl")
            lines.extend(self.context_lines)
            lines.append("```")
        return "\n".join(lines)


# ── 错误日志解析 ───────────────────────────────────────────────────────────

# Archicad 错误格式：
#   Error in 3D script, line 14: Non-positive parameter
#   Warning in Parameter script, line 7: Undefined variable 'x'
_ERROR_PATTERN = re.compile(
    r"(Error|Warning)\s+in\s+([\w\s]+?)\s+script[,\s]+line\s+(\d+)[:\s]+(.+)",
    re.IGNORECASE
)

_SCRIPT_NAME_MAP = {
    "3d": "3d",
    "2d": "2d",
    "parameter": "param",
    "param": "param",
    "master": "master",
    "1d": "master",
    "ui": "ui",
    "interface": "ui",
    "properties": "properties",
    "pr": "properties",
}

def parse_gdl_errors(raw_log: str, project=None) -> list[GDLError]:
    """
    从 Archicad 错误日志文本中解析出结构化错误列表。
    project: HSFProject 实例，用于提取错误行上下文（可选）
    """
    errors = []
    for match in _ERROR_PATTERN.finditer(raw_log):
        level, script_raw, line_str, message = match.groups()
        script_key_raw = script_raw.strip().lower().split()[0]
        script_key = _SCRIPT_NAME_MAP.get(script_key_raw, "3d")
        line_num = int(line_str)

        context = []
        if project is not None:
            try:
                from openbrep.hsf_project import ScriptType
                _ST_MAP = {
                    "3d": ScriptType.SCRIPT_3D,
                    "2d": ScriptType.SCRIPT_2D,
                    "param": ScriptType.PARAM,
                    "master": ScriptType.MASTER,
                    "ui": ScriptType.UI,
                    "properties": ScriptType.PROPERTIES,
                }
                st = _ST_MAP.get(script_key)
                if st:
                    script_content = project.get_script(st) or ""
                    context = _extract_context(script_content, line_num, window=3)
            except Exception:
                pass

        errors.append(GDLError(
            script_type=script_key,
            line=line_num,
            message=message.strip(),
            level=level.capitalize(),
            context_lines=context,
        ))
    return errors


def _extract_context(content: str, line_num: int, window: int = 3) -> list[str]:
    """提取错误行前后 window 行，加行号标注。"""
    lines = content.splitlines()
    start = max(0, line_num - window - 1)
    end = min(len(lines), line_num + window)
    result = []
    for i, line in enumerate(lines[start:end], start=start + 1):
        marker = ">>> " if i == line_num else "    "
        result.append(f"{marker}{i:3d}: {line}")
    return result


# ── Archicad 错误日志路径（macOS）─────────────────────────────────────────

def _find_archicad_error_log() -> Optional[Path]:
    """
    尝试找到 Archicad 的错误日志文件。
    路径因版本和系统配置而异，按优先级逐一尝试。
    """
    candidates = [
        # 用户手动配置的路径（优先）
        Path(os.environ.get("ARCHICAD_ERROR_LOG", "")),
        # 常见默认路径
        Path.home() / "Library" / "Application Support" / "GRAPHISOFT" / "ARCHICAD" / "error_log.txt",
        Path.home() / "Library" / "Logs" / "GRAPHISOFT" / "ArchiCAD 29" / "ArchiCAD_Report.log",
        Path("/tmp/archicad_gdl_errors.txt"),
    ]
    for p in candidates:
        if p and p.exists():
            return p
    return None


# ── 主桥接类 ──────────────────────────────────────────────────────────────

class TapirBridge:
    """
    OpenBrep ↔ Archicad 通信桥。

    使用方式：
        bridge = TapirBridge()
        if bridge.is_available():
            bridge.reload_libraries()
            errors = bridge.capture_errors(timeout=5)
    """

    TAPIR_ADDON_ID = "TapirCommand"
    # OpenBrep Add-On 的 Add-On Commands 命名空间（openbrep-addon 仓
    # EvaluateLibraryPartCommand::GetNamespace）
    OPENBREP_ADDON_ID = "OpenBrep"

    def __init__(self):
        self._conn = None
        self._last_connection_error = ""
        self._last_log_size = 0
        self._error_log_path: Optional[Path] = None
        self._error_callback: Optional[Callable] = None
        self._observer = None

    # ── 连接管理 ────────────────────────────────────────────────────────

    def connect(self) -> bool:
        """尝试连接 Archicad，返回是否成功。"""
        if not _AC_AVAILABLE:
            self._last_connection_error = "Archicad Python API 未安装"
            return False
        try:
            # ACConnection.connect() 会偷读当前进程的 ``--port`` 参数。工作台
            # API 自身也以 ``--port 8765`` 启动，若直接调用会误把 8765 当成
            # Archicad 端口且不再扫描官方 19723..19743 范围。
            archicad_port = ACConnection.find_first_port()
            self._conn = ACConnection.connect(archicad_port) if archicad_port else None
            self._last_connection_error = "" if self._conn is not None else "未发现可连接的 Archicad 实例"
            return self._conn is not None
        except Exception as exc:
            self._conn = None
            self._last_connection_error = str(exc)
            return False

    def is_available(self) -> bool:
        """检查 Archicad + Tapir 是否可用。"""
        if not self.connect():
            return False
        try:
            # 尝试调用一个轻量命令确认 Tapir 可用
            self._tapir_call("GetArchicadLocation", {})
            return True
        except Exception:
            # Tapir 未安装时会抛异常
            return False

    def get_status(self) -> dict:
        """返回连接状态，用于 UI 显示。"""
        ac_ok = self.connect()
        tapir_ok = False
        version = ""
        if ac_ok:
            try:
                result = self._tapir_call("GetArchicadLocation", {})
                tapir_ok = True
                version = str(result)
            except Exception:
                pass
        return {
            "archicad_connected": ac_ok,
            "tapir_available": tapir_ok,
            "version": version,
            "connection_error": self._last_connection_error,
        }

    # ── 核心命令 ────────────────────────────────────────────────────────

    def reload_libraries(self) -> bool:
        """
        触发 Archicad 重新加载所有库文件。
        编译出新 .gsm 后调用此方法，Archicad 立刻看到更新。
        """
        try:
            self._tapir_call("ReloadLibraries", {})
            return True
        except Exception as e:
            print(f"[TapirBridge] ReloadLibraries 失败: {e}")
            return False

    def capture_errors(self, timeout: float = 6.0) -> list[GDLError]:
        """
        等待 Archicad 渲染后捕获 GDL 错误。
        先记录日志当前大小，等待 timeout 秒，再读取新增内容。
        """
        log_path = _find_archicad_error_log()
        if log_path is None:
            return []

        # 记录当前文件大小（只读新增内容）
        before_size = log_path.stat().st_size if log_path.exists() else 0
        time.sleep(timeout)

        if not log_path.exists():
            return []

        try:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                f.seek(before_size)
                new_content = f.read()
            return parse_gdl_errors(new_content)
        except Exception as e:
            print(f"[TapirBridge] 读取错误日志失败: {e}")
            return []

    def reload_and_capture(
        self,
        timeout: float = 6.0,
        project=None,
    ) -> tuple[bool, list[GDLError]]:
        """
        一步完成：ReloadLibraries + 捕获错误。
        返回 (reload_success, errors)
        """
        log_path = _find_archicad_error_log()
        before_size = 0
        if log_path and log_path.exists():
            before_size = log_path.stat().st_size

        success = self.reload_libraries()
        if not success:
            return False, []

        time.sleep(timeout)

        errors = []
        if log_path and log_path.exists():
            try:
                with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                    f.seek(before_size)
                    new_content = f.read()
                errors = parse_gdl_errors(new_content, project=project)
            except Exception as e:
                print(f"[TapirBridge] 读取错误日志失败: {e}")

        return True, errors

    # ── 选中对象与参数读写（P0）─────────────────────────────────────────

    def get_selected_elements(self) -> list[str]:
        """读取 Archicad 当前选中元素 GUID 列表。"""
        try:
            result = self._tapir_call("GetSelectedElements", {})
            return self._normalize_selected_elements(result)
        except Exception as e:
            print(f"[TapirBridge] GetSelectedElements 失败: {e}")
            return []

    def get_details_of_elements(self, guids: list[str]) -> list[dict]:
        """读取元素详情（类型、楼层、图层等）。"""
        elements = self._build_elements_payload(guids)
        if not elements:
            return []
        try:
            result = self._tapir_call("GetDetailsOfElements", {"elements": elements})
            if isinstance(result, dict):
                details = result.get("detailsOfElements", [])
                if isinstance(details, list):
                    return details
            return []
        except Exception as e:
            print(f"[TapirBridge] GetDetailsOfElements 失败: {e}")
            return []

    def highlight_elements(self, guids: list[str]) -> bool:
        """高亮指定元素。"""
        elements = self._build_elements_payload(guids)
        if not elements:
            return False
        try:
            result = self._tapir_call(
                "HighlightElements",
                {
                    "elements": elements,
                    "highlightedColors": [[255, 196, 0, 255] for _ in elements],
                },
            )
            return self._all_execution_success(result, expected_count=1)
        except Exception as e:
            print(f"[TapirBridge] HighlightElements 失败: {e}")
            return False

    def get_gdl_parameters_of_elements(self, guids: list[str]) -> list[dict]:
        """读取多个元素的 GDL 参数，并规范化输出。"""
        elements = self._build_elements_payload(guids)
        if not elements:
            return []
        try:
            result = self._tapir_call("GetGDLParametersOfElements", {"elements": elements})
            return self._normalize_gdl_parameters(result, requested_guids=guids)
        except Exception as e:
            print(f"[TapirBridge] GetGDLParametersOfElements 失败: {e}")
            return []

    def set_gdl_parameters_of_elements(self, elements_with_params: list[dict]) -> dict:
        """按 Tapir schema 批量写回 GDL 参数。"""
        payload = self._build_set_gdl_payload(elements_with_params)
        if not payload["elementsWithGDLParameters"]:
            return {"executionResults": []}
        try:
            result = self._tapir_call("SetGDLParametersOfElements", payload)
            if isinstance(result, dict):
                return result
            return {"executionResults": []}
        except Exception as e:
            print(f"[TapirBridge] SetGDLParametersOfElements 失败: {e}")
            return {
                "executionResults": [
                    {
                        "success": False,
                        "error": {"code": "Exception", "message": str(e)},
                    }
                ]
            }

    # ── 兼容旧接口（薄封装）───────────────────────────────────────────────

    def get_placed_params(self, element_guid: str) -> Optional[dict]:
        """兼容旧调用：读取单个已放置对象参数。"""
        normalized = self.get_gdl_parameters_of_elements([element_guid])
        if not normalized:
            return None
        return {"gdlParametersOfElements": normalized}

    def set_placed_params(self, element_guid: str, params: dict) -> bool:
        """兼容旧调用：写回单个已放置对象参数。"""
        result = self.set_gdl_parameters_of_elements(
            [
                {
                    "guid": element_guid,
                    "gdlParameters": params,
                }
            ]
        )
        return self._all_execution_success(result, expected_count=1)

    # ── payload / normalize helpers ─────────────────────────────────────

    def _build_elements_payload(self, guids: list[str]) -> list[dict]:
        """统一生成 elements: [{elementId:{guid}}]。"""
        elements = []
        seen = set()
        for guid in guids or []:
            if not isinstance(guid, str):
                continue
            g = guid.strip()
            if not g or g in seen:
                continue
            seen.add(g)
            elements.append({"elementId": {"guid": g}})
        return elements

    def _build_set_gdl_payload(self, elements_with_params: list[dict]) -> dict:
        """统一生成 elementsWithGDLParameters payload。"""
        rows = []
        for item in elements_with_params or []:
            if not isinstance(item, dict):
                continue

            guid = None
            element_id = item.get("elementId")
            if isinstance(element_id, dict):
                _guid = element_id.get("guid")
                if isinstance(_guid, str) and _guid.strip():
                    guid = _guid.strip()
            if guid is None:
                _guid = item.get("guid")
                if isinstance(_guid, str) and _guid.strip():
                    guid = _guid.strip()
            if not guid:
                continue

            raw_params = item.get("gdlParameters")
            if raw_params is None:
                raw_params = item.get("params")

            gdl_parameters = []
            if isinstance(raw_params, dict):
                for name, value in raw_params.items():
                    if not isinstance(name, str) or not name.strip():
                        continue
                    gdl_parameters.append({"name": name, "value": value})
            elif isinstance(raw_params, list):
                for p in raw_params:
                    if not isinstance(p, dict):
                        continue
                    if "name" in p and "value" in p and isinstance(p.get("name"), str) and p["name"].strip():
                        gdl_parameters.append({"name": p["name"], "value": p["value"]})
                        continue
                    idx = p.get("index")
                    if "value" in p and isinstance(idx, int) and not isinstance(idx, bool):
                        gdl_parameters.append({"index": idx, "value": p["value"]})

            if not gdl_parameters:
                continue

            rows.append(
                {
                    "elementId": {"guid": guid},
                    "gdlParameters": gdl_parameters,
                }
            )

        return {"elementsWithGDLParameters": rows}

    def _normalize_selected_elements(self, raw_result: Any) -> list[str]:
        """规范化 GetSelectedElements 输出为 GUID 列表。"""
        if isinstance(raw_result, dict):
            items = raw_result.get("elements", [])
        elif isinstance(raw_result, list):
            items = raw_result
        else:
            items = []

        guids = []
        seen = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            guid = None
            element_id = item.get("elementId")
            if isinstance(element_id, dict):
                guid = element_id.get("guid")
            if guid is None:
                guid = item.get("guid")
            if isinstance(guid, str):
                g = guid.strip()
                if g and g not in seen:
                    seen.add(g)
                    guids.append(g)
        return guids

    def _normalize_gdl_parameters(
        self,
        raw_result: Any,
        requested_guids: Optional[list[str]] = None,
    ) -> list[dict]:
        """规范化 GetGDLParametersOfElements 输出，稳定为 guid + gdlParameters 列表。"""
        if isinstance(raw_result, dict):
            rows = raw_result.get("gdlParametersOfElements", [])
        elif isinstance(raw_result, list):
            rows = raw_result
        else:
            rows = []

        normalized = []
        req = requested_guids or []

        for idx, row in enumerate(rows):
            if not isinstance(row, dict):
                continue

            guid = None
            element_id = row.get("elementId")
            if isinstance(element_id, dict):
                _guid = element_id.get("guid")
                if isinstance(_guid, str) and _guid.strip():
                    guid = _guid.strip()
            if guid is None:
                _guid = row.get("guid")
                if isinstance(_guid, str) and _guid.strip():
                    guid = _guid.strip()
            if guid is None and idx < len(req):
                _guid = req[idx]
                if isinstance(_guid, str) and _guid.strip():
                    guid = _guid.strip()

            raw_params = row.get("gdlParameters")
            if raw_params is None:
                raw_params = row.get("parameters")

            gdl_parameters = []
            if isinstance(raw_params, dict):
                for name, value in raw_params.items():
                    if isinstance(name, str) and name.strip():
                        gdl_parameters.append({"name": name, "value": value})
            elif isinstance(raw_params, list):
                for p in raw_params:
                    if isinstance(p, dict):
                        gdl_parameters.append(dict(p))

            if not gdl_parameters and "name" in row and "value" in row:
                gdl_parameters = [dict(row)]

            entry = {
                "guid": guid or "",
                "gdlParameters": gdl_parameters,
            }
            if guid:
                entry["elementId"] = {"guid": guid}
            normalized.append(entry)

        return normalized

    def _normalize_execution_results(self, raw_result: Any) -> list[dict]:
        """兼容 ExecutionResult / ExecutionResults 两种返回形状。"""
        if isinstance(raw_result, dict):
            if isinstance(raw_result.get("executionResults"), list):
                return [r for r in raw_result["executionResults"] if isinstance(r, dict)]
            if isinstance(raw_result.get("success"), bool):
                return [raw_result]
        return []

    def _all_execution_success(self, raw_result: Any, expected_count: int = 0) -> bool:
        """判断执行结果是否全部成功。"""
        results = self._normalize_execution_results(raw_result)
        if not results:
            return False
        if expected_count > 0 and len(results) < expected_count:
            return False
        return all(r.get("success") is True for r in results)

    # ── 内部工具 ────────────────────────────────────────────────────────

    def _tapir_call(self, command_name: str, params: dict, addon_id: str | None = None):
        """向 Tapir Add-On（默认）或 OpenBrep Add-On 发送命令。"""
        if self._conn is None:
            raise RuntimeError("未连接 Archicad")
        # Graphisoft 的 AddOnCommandParameters 是无字段占位类型；自定义命令的
        # schema 只在 Archicad 侧注册，Python 客户端不会动态生成对应属性。
        # ExecuteAddOnCommand 特意允许这里直接传 dict，由请求容器原样序列化。
        return self._conn.commands.ExecuteAddOnCommand(
            self._conn.types.AddOnCommandId(addon_id or self.TAPIR_ADDON_ID, command_name),
            params,
        )

    def evaluate_library_part(
        self,
        lib_part_name: str = "",
        lib_part_guid: str = "",
        parameters: Optional[dict] = None,
        want: Optional[list[str]] = None,
    ) -> dict:
        """调用 OpenBrep Add-On 的 EvaluateLibraryPart 权威求值命令（P15）。

        Archicad 用当前活动图库 + 覆盖参数真实求值物件，返回
        {success, meshes[{name, vertices(扁平xyz), faces(扁平ijk), color}],
        bounds, appliedParameters, skippedParameters} 或
        {success: False, errorMessage}。
        """
        params: dict = {}
        if lib_part_name:
            params["libPartName"] = lib_part_name
        if lib_part_guid:
            params["libPartGuid"] = lib_part_guid
        if parameters:
            params["parameters"] = parameters
        if want:
            params["want"] = want
        raw = self._tapir_call("EvaluateLibraryPart", params, addon_id=self.OPENBREP_ADDON_ID)
        if hasattr(raw, "__dict__") and not isinstance(raw, dict):
            raw = {k: v for k, v in vars(raw).items() if not k.startswith("_")}
        return raw if isinstance(raw, dict) else {"success": False, "errorMessage": str(raw)}


# ── 单例 ──────────────────────────────────────────────────────────────────

_bridge_instance: Optional[TapirBridge] = None

def get_bridge() -> TapirBridge:
    """获取全局 TapirBridge 单例。"""
    global _bridge_instance
    if _bridge_instance is None:
        _bridge_instance = TapirBridge()
    return _bridge_instance


# ── 格式化错误为 chat 文本 ────────────────────────────────────────────────

def errors_to_chat_message(errors: list[GDLError]) -> str:
    """把错误列表格式化为注入 LLM chat 的文本。"""
    if not errors:
        return "✅ Archicad 未报告 GDL 错误"

    lines = [f"## 🔴 Archicad GDL 错误报告（共 {len(errors)} 条）\n"]
    for i, err in enumerate(errors, 1):
        lines.append(f"### 错误 {i}")
        lines.append(err.to_chat_text())
        lines.append("")

    lines.append("---")
    lines.append("请分析以上错误，定位问题原因，输出修复后的脚本。")
    return "\n".join(lines)


# ── CLI 测试入口 ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("测试 TapirBridge 连接...")
    bridge = TapirBridge()
    status = bridge.get_status()
    print(f"Archicad 连接: {'✅' if status['archicad_connected'] else '❌'}")
    print(f"Tapir 可用:    {'✅' if status['tapir_available'] else '❌'}")

    if status["tapir_available"]:
        print("\n触发 ReloadLibraries...")
        ok = bridge.reload_libraries()
        print(f"结果: {'✅ 成功' if ok else '❌ 失败'}")
