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
