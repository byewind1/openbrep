import unittest
import os
import tempfile
from pathlib import Path

from openbrep.config import GDLAgentConfig, LLMConfig
from ui import config_service


class TestConfigService(unittest.TestCase):
    def test_key_for_model_matches_custom_alias_object_entry(self):
        custom_providers = [{
            "name": "nvidia",
            "api_key": "nv-key",
            "models": [{"alias": "moonshotai/kimi-k2.5", "model": "openai/moonshotai/kimi-k2.5"}],
        }]

        self.assertEqual(
            config_service.key_for_model("moonshotai/kimi-k2.5", {}, custom_providers),
            "nv-key",
        )

    def test_key_for_model_matches_custom_model_object_entry(self):
        custom_providers = [{
            "name": "nvidia",
            "api_key": "nv-key",
            "models": [{"alias": "moonshotai/kimi-k2.5", "model": "openai/moonshotai/kimi-k2.5"}],
        }]

        self.assertEqual(
            config_service.key_for_model("openai/moonshotai/kimi-k2.5", {}, custom_providers),
            "nv-key",
        )

    def test_key_for_model_keeps_builtin_provider_fallback(self):
        self.assertEqual(
            config_service.key_for_model("gpt-5.4", {"openai": "openai-key"}, []),
            "openai-key",
        )

    def test_sync_llm_top_level_fields_updates_custom_provider_runtime_fields(self):
        cfg = GDLAgentConfig(
            llm=LLMConfig(
                model="old-model",
                api_key="top-level-old",
                api_base="https://old-base/v1",
                custom_providers=[
                    {
                        "name": "ymg",
                        "base_url": "https://api.ymg.com/v1",
                        "api_key": "ymg-key",
                        "models": [{"alias": "ymg-gpt-5.3-codex", "model": "gpt-5.3-codex"}],
                        "protocol": "openai",
                    }
                ],
            )
        )

        changed = config_service.sync_llm_top_level_fields_for_model(cfg, "ymg-gpt-5.3-codex")

        self.assertTrue(changed)
        self.assertEqual(cfg.llm.model, "ymg-gpt-5.3-codex")
        self.assertEqual(cfg.llm.api_key, "ymg-key")
        self.assertEqual(cfg.llm.api_base, "https://api.ymg.com/v1")

    def test_sync_llm_top_level_fields_updates_custom_fields_when_model_is_unchanged(self):
        cfg = GDLAgentConfig(
            llm=LLMConfig(
                model="DeepSeek-V4-Pro",
                api_key="old-key",
                api_base="https://api.airsim.eu.cc/v1",
                custom_providers=[
                    {
                        "name": "scnet",
                        "base_url": "https://api.scnet.cn/api/llm/v1",
                        "api_key": "scnet-key",
                        "models": ["DeepSeek-V4-Pro"],
                        "protocol": "openai",
                    }
                ],
            )
        )

        changed = config_service.sync_llm_top_level_fields_for_model(cfg, "DeepSeek-V4-Pro")

        self.assertTrue(changed)
        self.assertEqual(cfg.llm.model, "DeepSeek-V4-Pro")
        self.assertEqual(cfg.llm.api_key, "scnet-key")
        self.assertEqual(cfg.llm.api_base, "https://api.scnet.cn/api/llm/v1")

    def test_refresh_session_model_keys_preserves_existing_blank_fallback(self):
        session_state = _SessionState(model_api_keys={"unknown": "manual-key"})
        config = GDLAgentConfig(llm=LLMConfig(model="gpt-5.4"))

        config_service.refresh_session_model_keys(
            session_state,
            config=config,
            defaults={"assistant_settings": "prefer concise diffs"},
            provider_keys={"openai": "openai-key"},
            custom_providers=[],
            builtin_models=["gpt-5.4"],
        )

        self.assertEqual(session_state.model_api_keys["gpt-5.4"], "openai-key")
        self.assertEqual(session_state.model_api_keys["unknown"], "manual-key")
        self.assertEqual(session_state.assistant_settings, "prefer concise diffs")

    def test_load_runtime_config_uses_explicit_root_even_when_cwd_differs(self):
        with tempfile.TemporaryDirectory() as root_dir, tempfile.TemporaryDirectory() as cwd:
            root = Path(root_dir)
            (root / "config.toml").write_text(
                """
[llm]
model = "mimo-v2.5-pro"
assistant_settings = "prefer compact changes"

[[llm.custom_providers]]
name = "mimo"
base_url = "https://api.example.test/v1"
api_key = "custom-key"
protocol = "openai"
models = ["mimo-v2.5-pro"]
""",
                encoding="utf-8",
            )
            old_cwd = os.getcwd()
            try:
                os.chdir(cwd)
                state = config_service.load_runtime_config(root)
            finally:
                os.chdir(old_cwd)

        self.assertEqual(state.config.llm.model, "mimo-v2.5-pro")
        self.assertEqual(state.defaults["assistant_settings"], "prefer compact changes")
        self.assertEqual(len(state.config.llm.custom_providers), 1)
        self.assertEqual(state.custom_providers[0]["name"], "mimo")
        self.assertIn(
            "mimo-v2.5-pro",
            config_service.available_models(state.config, state.custom_providers, []),
        )

    def test_build_generation_config_uses_ui_root_when_cwd_differs(self):
        with tempfile.TemporaryDirectory() as root_dir, tempfile.TemporaryDirectory() as cwd:
            root = Path(root_dir)
            (root / "config.toml").write_text(
                """
[llm]
model = "mimo-v2.5-pro"

[[llm.custom_providers]]
name = "mimo"
base_url = "https://api.example.test/v1"
api_key = "custom-key"
protocol = "openai"
models = ["mimo-v2.5-pro"]
""",
                encoding="utf-8",
            )
            old_cwd = os.getcwd()
            try:
                os.chdir(cwd)
                cfg = config_service.build_generation_config(root, model_name="mimo-v2.5-pro")
            finally:
                os.chdir(old_cwd)

        self.assertEqual(cfg.llm.model, "mimo-v2.5-pro")
        self.assertEqual(len(cfg.llm.custom_providers), 1)
        self.assertEqual(cfg.llm.resolve_api_key(), "custom-key")
        self.assertEqual(cfg.llm.resolve_api_base(), "https://api.example.test/v1")


class _SessionState(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc

    def __setattr__(self, key, value):
        self[key] = value
