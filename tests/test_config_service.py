import unittest

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

    def test_sync_llm_top_level_fields_only_updates_model(self):
        cfg = GDLAgentConfig(
            llm=LLMConfig(
                model="old-model",
                api_key="top-level-old",
                api_base="https://old-base/v1",
            )
        )

        changed = config_service.sync_llm_top_level_fields_for_model(cfg, "ymg-gpt-5.3-codex")

        self.assertTrue(changed)
        self.assertEqual(cfg.llm.model, "ymg-gpt-5.3-codex")
        self.assertEqual(cfg.llm.api_key, "top-level-old")
        self.assertEqual(cfg.llm.api_base, "https://old-base/v1")

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


class _SessionState(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc

    def __setattr__(self, key, value):
        self[key] = value
