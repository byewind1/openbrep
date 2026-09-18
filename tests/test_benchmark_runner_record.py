"""ST04 附带：benchmark runner 在"Codex 真实录制"时初始化共享 Codex provider。

背景：生产入口由 workbench 设置服务创建共享 provider；benchmark 进程没有这一步，
Codex CREATE 录制会 fail closed（provider unavailable）。本测试固定"_make_pipeline
只在 record + Codex 模型时注入 provider，回放绝不拉起 app-server"。
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from benchmark.runner import BenchmarkRunner


def _codex_config(tmp: Path) -> str:
    path = tmp / "record_codex.toml"
    path.write_text(
        '[llm]\nmodel = "openai-codex/gpt-5.6-luna"\ncodex_entry = "local"\n'
        '[compiler]\nmode = "mock"\npath = ""\n',
        encoding="utf-8",
    )
    return str(path)


def test_make_pipeline_injects_codex_provider_when_recording_codex_model():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        fake_provider = object()
        calls = {"default": 0, "bind": 0}

        def _fake_default():
            calls["default"] += 1
            return fake_provider

        def _fake_bind(provider, config):
            calls["bind"] += 1
            assert provider is fake_provider
            return "local"

        runner = BenchmarkRunner(
            config_path=_codex_config(tmp),
            mode="mock",
            llm_record=str(tmp / "corpus.jsonl"),
        )
        with patch("openbrep.codex.provider.default_codex_provider", _fake_default), patch(
            "openbrep.codex.provider.bind_codex_entry", _fake_bind
        ):
            pipeline = runner._make_pipeline()

        assert pipeline.codex_provider is fake_provider
        assert calls == {"default": 1, "bind": 1}


def test_make_pipeline_does_not_start_codex_during_replay():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        corpus = tmp / "corpus.jsonl"
        corpus.write_text("", encoding="utf-8")

        def _boom():
            raise AssertionError("replay must not start a Codex provider")

        runner = BenchmarkRunner(
            config_path=_codex_config(tmp),
            mode="mock",
            llm_replay=str(corpus),
        )
        with patch("openbrep.codex.provider.default_codex_provider", _boom):
            pipeline = runner._make_pipeline()
        assert getattr(pipeline, "codex_provider", None) is None
