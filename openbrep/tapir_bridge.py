"""
tapir_bridge.py — OpenBrep ↔ Archicad 通信桥

依赖：
    pip install archicad watchdog

前提：
    - Archicad 29 保持运行
    - Tapir Add-On 已安装（Add-On Manager 里可见）

功能：
    1. reload_libraries()          — 编译后自动刷新 Archicad 库
    2. capture_errors()            — 捕获 GDL 运行期错误（日志文件监听）
    3. get_placed_params(guid)     — 读取已放置对象的参数值
    4. set_placed_params(guid, p)  — 修改已放置对象的参数值（教学模式）
    5. is_available()              — 检查 Archicad + Tapir 是否可用
"""

from __future__ import annotations

import os
import re
import time
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Callable

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

    def __init__(self):
        self._conn = None
        self._last_log_size = 0
        self._error_log_path: Optional[Path] = None
        self._error_callback: Optional[Callable] = None
        self._observer = None

    # ── 连接管理 ────────────────────────────────────────────────────────

    def connect(self) -> bool:
        """尝试连接 Archicad，返回是否成功。"""
        if not _AC_AVAILABLE:
            return False
        try:
            self._conn = ACConnection.connect()
            return self._conn is not None
        except Exception:
            self._conn = None
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

    # ── 参数读写（教学模式）─────────────────────────────────────────────

    def get_placed_params(self, element_guid: str) -> Optional[dict]:
        """读取已放置对象的 GDL 参数值。"""
        try:
            result = self._tapir_call(
                "GetGDLParametersOfElements",
                {"elementIds": [{"guid": element_guid}]}
            )
            return result
        except Exception as e:
            print(f"[TapirBridge] GetGDLParametersOfElements 失败: {e}")
            return None

    def set_placed_params(self, element_guid: str, params: dict) -> bool:
        """
        修改已放置对象的 GDL 参数值。
        教学模式：不重新编译，实时看到参数变化效果。
        params 格式: {"A": 1.5, "shelfNum": 4}
        """
        try:
            self._tapir_call(
                "SetGDLParametersOfElements",
                {
                    "elementIds": [{"guid": element_guid}],
                    "gdlParameters": params,
                }
            )
            return True
        except Exception as e:
            print(f"[TapirBridge] SetGDLParametersOfElements 失败: {e}")
            return False

    # ── 内部工具 ────────────────────────────────────────────────────────

    def _tapir_call(self, command_name: str, params: dict):
        """向 Tapir Add-On 发送命令。"""
        if self._conn is None:
            raise RuntimeError("未连接 Archicad")
        ac_params = self._conn.types.AddOnCommandParameters()
        for k, v in params.items():
            setattr(ac_params, k, v)
        return self._conn.commands.ExecuteAddOnCommand(
            self._conn.types.AddOnCommandId(self.TAPIR_ADDON_ID, command_name),
            ac_params
        )


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
