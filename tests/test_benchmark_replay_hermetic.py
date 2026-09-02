from __future__ import annotations

from pathlib import Path

import benchmark.check_baseline as check_baseline
from benchmark.runner import BenchmarkRunner

REPO_ROOT = Path(__file__).parents[1]


def test_replay_suites_pin_fixture_configs_even_when_root_config_changes(tmp_path, monkeypatch):
    """Suite replay must never derive its model/routing from the checkout config."""
    captured = []

    class SpyRunner:
        def __init__(self, **kwargs):
            captured.append(kwargs)

        def run_suite(self, suite_dir, jobs):
            return []

    root = tmp_path / "checkout"
    root.mkdir()
    (root / "config.toml").write_text(
        '[llm]\nmodel = "openai-codex/gpt-5.6-luna"\ncodex_modify_enabled = true\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(check_baseline, "PROJECT_ROOT", root)
    monkeypatch.setattr(check_baseline, "BenchmarkRunner", SpyRunner)

    check_baseline.run_suites()

    assert [item["config_path"] for item in captured] == [
        str(root / "benchmark/fixtures/replay_config_create.toml"),
        str(root / "benchmark/fixtures/replay_config_modify.toml"),
    ]
    assert all(Path(item["config_path"]).name != "config.toml" for item in captured)


def test_create_replay_hits_without_codex_cli(tmp_path, monkeypatch):
    """Codex's live availability is irrelevant once ReplayLLM owns the call."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("benchmark.runner.shutil.which", lambda _name: None)
    monkeypatch.setattr("openbrep.config.shutil.which", lambda _name: None)

    runner = BenchmarkRunner(
        config_path=str(REPO_ROOT / "benchmark/fixtures/replay_config_create.toml"),
        mode="mock",
        llm_replay=str(REPO_ROOT / "benchmark/fixtures/llm_corpus/create.jsonl"),
    )
    result = runner.run_task(
        str(Path(__file__).parents[1] / "benchmark/tasks/create/C01_simple_box.yaml")
    )

    assert type(runner.llm).__name__ == "ReplayLLM"
    assert result["error_summary"] == ""
    assert result["success"] is True


def test_modify_replay_uses_replayllm_not_codex_bridge(tmp_path, monkeypatch):
    """The per-suite ordinary model keeps MODIFY on the ReplayLLM tool path."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("benchmark.runner.shutil.which", lambda _name: None)
    monkeypatch.setattr("openbrep.config.shutil.which", lambda _name: None)

    runner = BenchmarkRunner(
        config_path=str(REPO_ROOT / "benchmark/fixtures/replay_config_modify.toml"),
        mode="mock",
        llm_replay=str(REPO_ROOT / "benchmark/fixtures/llm_corpus/modify.jsonl"),
        agent_loop=True,
    )
    calls = 0
    replay_generate_with_tools = runner.llm.generate_with_tools

    def counted_generate_with_tools(*args, **kwargs):
        nonlocal calls
        calls += 1
        return replay_generate_with_tools(*args, **kwargs)

    monkeypatch.setattr(runner.llm, "generate_with_tools", counted_generate_with_tools)
    result = runner.run_task(
        str(Path(__file__).parents[1] / "benchmark/tasks/modify/M01_add_shelf_layer.yaml")
    )

    assert calls > 0
    assert result["error_summary"] == ""
    assert result["success"] is True
