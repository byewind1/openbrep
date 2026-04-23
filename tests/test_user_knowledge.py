"""Tests for openbrep.user_knowledge."""

import tempfile
from pathlib import Path

from openbrep.user_knowledge import load_user_knowledge


def test_load_user_knowledge_empty_dir():
    """Non-existent directory returns empty string."""
    result = load_user_knowledge("/tmp/nonexistent_dir_xyz")
    assert result == ""


def test_load_user_knowledge_empty_dir_path():
    """Empty directory returns empty string."""
    with tempfile.TemporaryDirectory() as tmp:
        result = load_user_knowledge(tmp)
    assert result == ""


def test_load_user_knowledge_single_file():
    """Single .md file is loaded and labeled."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "my_patterns.md").write_text("## Column\n\nBLOCK 1, 2, 3\nEND")
        result = load_user_knowledge(str(d))
        assert "用户知识：my_patterns" in result
        assert "BLOCK 1, 2, 3" in result


def test_load_user_knowledge_multiple_files():
    """Multiple .md files are concatenated in sorted order."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "zzz_last.md").write_text("last")
        (d / "aaa_first.md").write_text("first")
        result = load_user_knowledge(str(d))
        assert result.index("aaa_first") < result.index("zzz_last")


def test_load_user_knowledge_skip_readme():
    """README.md is skipped."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "README.md").write_text("readme content")
        (d / "patterns.md").write_text("real content")
        result = load_user_knowledge(str(d))
        assert "readme" not in result.lower()
        assert "real content" in result


def test_load_user_knowledge_non_md_skipped():
    """Non-.md files are ignored."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "data.txt").write_text("text")
        (d / "notes.json").write_text("{}")
        result = load_user_knowledge(str(d))
        assert result == ""
