"""Session-wide test isolation: keep the developer's real config.toml untouched.

``WorkbenchSession()`` without an explicit ``config_path`` resolves the repo's
``./config.toml`` (via git common-dir). Several tests mutate and persist session
config (recent_projects, llm settings), which used to rewrite the developer's
real config.toml — dropping hand-edited values like ``compiler.path`` and
filling ``recent_projects`` with pytest tmp dirs. ``resolve_workbench_config_path``
honors the ``GDL_AGENT_CONFIG`` env var, so point it at a per-session tmp copy
of the real config (a copy, so tests that resolve real credentials keep their
previous behavior while writes land on the copy).
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

_tmp_dir = Path(tempfile.mkdtemp(prefix="obr_test_config_"))
_tmp_config = _tmp_dir / "config.toml"
_real_config = Path(__file__).resolve().parents[1] / "config.toml"
if _real_config.is_file():
    shutil.copy(_real_config, _tmp_config)

os.environ.setdefault("GDL_AGENT_CONFIG", str(_tmp_config))

# 全量测试统一禁用 rich 彩色/加粗输出：cli/main.py 的 ``console = Console()`` 在
# 模块导入时做终端自动检测，tty / TTY_COMPATIBLE / FORCE_COLOR 任一命中都会启用
# color_system，而 rich 默认 ``highlight=True`` 会把数字渲染成 \x1b[1m 加粗，
# 打断 test_cli_main.py 对 CLI 输出的子串断言（同一 commit 时绿时红的根因）。
# NO_COLOR 只剥颜色不剥 bold/dim，必须同时置 TERM=dumb 让 rich 直接判定
# 无色彩系统（_detect_color_system 对 dumb terminal 返回 None）。
os.environ["NO_COLOR"] = "1"
os.environ["TERM"] = "dumb"
