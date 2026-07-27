"""Session-wide test isolation: keep the developer's real config.toml untouched.

``WorkbenchSession()`` without an explicit ``config_path`` resolves the repo's
``./config.toml`` (via git common-dir). Several tests mutate and persist session
config (recent_projects, llm settings), which used to rewrite the developer's
real config.toml — dropping hand-edited values like ``compiler.path`` and
filling ``recent_projects`` with pytest tmp dirs. ``resolve_workbench_config_path``
honors the ``GDL_AGENT_CONFIG`` env var, so point it at a per-session tmp file
before any test constructs a session.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault(
    "GDL_AGENT_CONFIG",
    str(Path(tempfile.mkdtemp(prefix="obr_test_config_")) / "config.toml"),
)
