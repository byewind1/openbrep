"""发布说明存在性契约（v0.10.10 起）。

背景：Release 正文此前由 `gh release create --generate-notes` 生成，而本仓库
提交直推 main、没有 PR，"What's Changed" 只剩一行 Full Changelog —— 连续多个
v0.10.x 版本的 Release 页都没有版本历史说明。发布说明现在以
`docs/releases/vX.Y.Z.md` 为准（工作流用 `--notes-file` 引用，同时作为
`latest.json` 的 notes 供应用内更新对话框解析），本测试保证版本号与发布说明
不会再脱钩。
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _current_version() -> str:
    config = ROOT / "src-tauri" / "tauri.conf.json"
    return json.loads(config.read_text(encoding="utf-8"))["version"]


class TestReleaseNotes(unittest.TestCase):
    def test_release_notes_file_exists_for_current_version(self):
        version = _current_version()
        path = ROOT / "docs" / "releases" / f"v{version}.md"
        self.assertTrue(
            path.exists(),
            f"缺少发布说明 {path.relative_to(ROOT)}；请在打 tag 前补写，"
            "否则 Release 页会没有版本历史",
        )
        self.assertGreater(len(path.read_text(encoding="utf-8").strip()), 200)

    def test_changelog_has_entry_for_current_version(self):
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn(f"## [{_current_version()}]", changelog)

    def test_release_workflow_prefers_notes_file(self):
        workflow = (
            ROOT / ".github" / "workflows" / "release-tauri.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("--notes-file", workflow)
        self.assertIn("docs/releases/${TAG_NAME}.md", workflow)


if __name__ == "__main__":
    unittest.main()
