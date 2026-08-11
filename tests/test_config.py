import os
import tempfile
import unittest
from pathlib import Path

from openbrep.config import (
    GDLAgentConfig,
    LLMConfig,
    PROVIDER_PROFILES,
    provider_profile_for_model,
    model_to_provider,
)

_LLM_ENV_VARS = (
    "ZHIPU_API_KEY", "ZAI_API_KEY", "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "GEMINI_API_KEY",
    "DASHSCOPE_API_KEY", "MOONSHOT_API_KEY",
)


class _CleanEnvMixin:
    """屏蔽真实环境变量，避免开发机上的 key 污染断言。"""

    def _clear_llm_env(self):
        snapshot = {n: os.environ.get(n) for n in _LLM_ENV_VARS}
        for n in _LLM_ENV_VARS:
            os.environ.pop(n, None)
        self.addCleanup(self._restore_env, snapshot)

    @staticmethod
    def _restore_env(snapshot):
        for n, v in snapshot.items():
            if v is None:
                os.environ.pop(n, None)
            else:
                os.environ[n] = v


class TestConfigAssistantSettings(unittest.TestCase):
    def test_custom_model_prefers_custom_provider_credentials_over_top_level_llm_fields(self):
        cfg = LLMConfig(
            model="ymg-gpt-5.3-codex",
            api_key="top-level-key",
            api_base="https://integrate.api.nvidia.com/v1",
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

        self.assertEqual(cfg.resolve_api_base(), "https://api.ymg.com/v1")
        self.assertEqual(cfg.resolve_api_key(), "ymg-key")

    def test_custom_model_does_not_fallback_to_top_level_api_key_when_custom_key_missing(self):
        cfg = LLMConfig(
            model="ymg-gpt-5.3-codex",
            api_key="top-level-key",
            custom_providers=[
                {
                    "name": "ymg",
                    "base_url": "https://api.ymg.com/v1",
                    "api_key": "",
                    "models": [{"alias": "ymg-gpt-5.3-codex", "model": "gpt-5.3-codex"}],
                    "protocol": "openai",
                }
            ],
            provider_keys={"openai": "official-openai-key"},
        )

        self.assertIsNone(cfg.resolve_api_key())

    def test_custom_model_does_not_fallback_to_top_level_api_base_when_custom_base_missing(self):
        cfg = LLMConfig(
            model="ymg-gpt-5.3-codex",
            api_base="https://top-level-base/v1",
            custom_providers=[
                {
                    "name": "ymg",
                    "base_url": "",
                    "api_key": "ymg-key",
                    "models": [{"alias": "ymg-gpt-5.3-codex", "model": "gpt-5.3-codex"}],
                    "protocol": "openai",
                }
            ],
        )

        self.assertIsNone(cfg.resolve_api_base())

    def test_official_openai_model_prefers_provider_keys_over_top_level(self):
        cfg = LLMConfig(
            model="gpt-5.4",
            api_key="top-level-key",
            provider_keys={"openai": "official-openai-key"},
        )

        self.assertEqual(cfg.resolve_api_key(), "official-openai-key")

    def test_assistant_settings_defaults_empty_when_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config_path.write_text("[llm]\nmodel = \"glm-4-flash\"\n", encoding="utf-8")

            config = GDLAgentConfig.load(str(config_path))

            self.assertEqual(config.llm.assistant_settings, "")

    def test_assistant_settings_roundtrip_preserves_unicode_multiline_and_other_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config_path.write_text(
                '''
[llm]
model = "claude-sonnet-4-6"
temperature = 0.3
max_tokens = 1234
assistant_settings = """我是 GDL 初学者，请先解释再给最小修改。
我主要改已有对象。"""

[llm.provider_keys]
anthropic = "test-key"

[compiler]
path = "/tmp/LP_XMLConverter"
timeout = 88
'''.strip(),
                encoding="utf-8",
            )

            config = GDLAgentConfig.load(str(config_path))
            self.assertEqual(
                config.llm.assistant_settings,
                "我是 GDL 初学者，请先解释再给最小修改。\n我主要改已有对象。",
            )
            self.assertEqual(config.llm.provider_keys["anthropic"], "test-key")
            self.assertEqual(config.compiler.path, "/tmp/LP_XMLConverter")

            config.llm.assistant_settings = "现在赶项目，优先给可运行结果。"
            config.save(str(config_path))

            reloaded = GDLAgentConfig.load(str(config_path))
            self.assertEqual(reloaded.llm.assistant_settings, "现在赶项目，优先给可运行结果。")
            self.assertEqual(reloaded.llm.provider_keys["anthropic"], "test-key")
            self.assertEqual(reloaded.compiler.mode, "lp")
            self.assertEqual(reloaded.compiler.path, "/tmp/LP_XMLConverter")
            self.assertEqual(reloaded.llm.model, "claude-sonnet-4-6")


    def test_compiler_mode_persists_explicit_mock_with_saved_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config_path.write_text(
                '''
[compiler]
mode = "mock"
path = "/tmp/LP_XMLConverter"
timeout = 88
'''.strip(),
                encoding="utf-8",
            )

            config = GDLAgentConfig.load(str(config_path))

            self.assertEqual(config.compiler.mode, "mock")
            self.assertEqual(config.compiler.path, "/tmp/LP_XMLConverter")


    def test_custom_providers_loads_array_of_tables_without_inline_override(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config_path.write_text(
                '''
[llm]
model = "gpt-5.4"
temperature = 0.2
max_tokens = 4096

[[llm.custom_providers]]
name = "ymg"
base_url = "https://api.airsim.eu.cc/v1"
api_key = "test-key"
models = ["gpt-5.4"]
protocol = "openai"

[compiler]
path = "/tmp/LP_XMLConverter"
timeout = 60
'''.strip(),
                encoding="utf-8",
            )

            config = GDLAgentConfig.load(str(config_path))

            self.assertEqual(len(config.llm.custom_providers), 1)
            self.assertEqual(config.llm.custom_providers[0]["name"], "ymg")
            self.assertEqual(config.llm.custom_providers[0]["models"], ["gpt-5.4"])

    def test_load_supports_custom_model_object_entry_and_resolves_key_base(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config_path.write_text(
                '''
[llm]
model = "ymg-glm-5.4"

[[llm.custom_providers]]
name = "ymg"
base_url = "https://api.airsim.eu.cc/v1"
api_key = "custom-key"
models = [{ alias = "ymg-glm-5.4", model = "glm-5.4" }]
protocol = "openai"
'''.strip(),
                encoding="utf-8",
            )

            loaded = GDLAgentConfig.load(str(config_path))
            self.assertEqual(loaded.llm.model, "ymg-glm-5.4")
            self.assertIsNone(loaded.llm.api_base)
            self.assertIsNone(loaded.llm.api_key)
            self.assertEqual(loaded.llm.resolve_api_base(), "https://api.airsim.eu.cc/v1")
            self.assertEqual(loaded.llm.resolve_api_key(), "custom-key")

            provider = loaded.llm.get_provider_for_model("ymg-glm-5.4")
            self.assertEqual(provider.get("provider_name"), "ymg")
            self.assertEqual(provider.get("alias"), "ymg-glm-5.4")
            self.assertEqual(provider.get("model"), "glm-5.4")
            self.assertEqual(provider.get("protocol"), "openai")

    def test_load_reflects_disk_changes_on_reload(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config_path.write_text(
                '''
[llm]
model = "deepseek-chat"

[llm.provider_keys]
deepseek = "deepseek-key"
'''.strip(),
                encoding="utf-8",
            )

            first = GDLAgentConfig.load(str(config_path))
            self.assertEqual(first.llm.model, "deepseek-chat")
            self.assertEqual(first.llm.provider_keys["deepseek"], "deepseek-key")
            self.assertEqual(first.llm.custom_providers, [])

            config_path.write_text(
                '''
[llm]
model = "glm-5.1"
assistant_settings = "重新加载后的配置"

[[llm.custom_providers]]
name = "ymg"
base_url = "https://api.airsim.eu.cc/v1"
api_key = "custom-key"
models = ["glm-5.1"]
protocol = "openai"
'''.strip(),
                encoding="utf-8",
            )

            second = GDLAgentConfig.load(str(config_path))
            self.assertEqual(second.llm.model, "glm-5.1")
            self.assertEqual(second.llm.assistant_settings, "重新加载后的配置")
            self.assertEqual(len(second.llm.custom_providers), 1)
            self.assertEqual(second.llm.custom_providers[0]["name"], "ymg")
            self.assertEqual(second.llm.custom_providers[0]["models"], ["glm-5.1"])
            self.assertIsNone(second.llm.api_base)
            self.assertIsNone(second.llm.api_key)
            self.assertEqual(second.llm.resolve_api_base(), "https://api.airsim.eu.cc/v1")
            self.assertEqual(second.llm.resolve_api_key(), "custom-key")


class TestRevisionsConfig(unittest.TestCase):
    """[revisions] keep_last_n 配置解析与非法值回退。"""

    def _load(self, toml_text: str) -> GDLAgentConfig:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config_path.write_text(toml_text, encoding="utf-8")
            return GDLAgentConfig.load(str(config_path))

    def test_keep_last_n_defaults_to_twenty_when_section_missing(self):
        self.assertEqual(self._load("").revisions.keep_last_n, 20)

    def test_keep_last_n_parses_from_toml(self):
        self.assertEqual(self._load("[revisions]\nkeep_last_n = 5\n").revisions.keep_last_n, 5)

    def test_keep_last_n_zero_is_valid(self):
        # 0 = 禁用自动 prune，是合法值，不能被误判为非法回退
        self.assertEqual(self._load("[revisions]\nkeep_last_n = 0\n").revisions.keep_last_n, 0)

    def test_keep_last_n_negative_falls_back_to_twenty(self):
        self.assertEqual(self._load("[revisions]\nkeep_last_n = -3\n").revisions.keep_last_n, 20)

    def test_keep_last_n_non_numeric_falls_back_to_twenty(self):
        self.assertEqual(self._load('[revisions]\nkeep_last_n = "abc"\n').revisions.keep_last_n, 20)
        self.assertEqual(self._load("[revisions]\nkeep_last_n = true\n").revisions.keep_last_n, 20)

    def test_keep_last_n_roundtrips_through_save(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config_path.write_text("[revisions]\nkeep_last_n = 5\n", encoding="utf-8")
            config = GDLAgentConfig.load(str(config_path))
            config.save(str(config_path))
            reloaded = GDLAgentConfig.load(str(config_path))
            self.assertEqual(reloaded.revisions.keep_last_n, 5)


class TestProviderRegistry(_CleanEnvMixin, unittest.TestCase):
    """PROVIDER_PROFILES 注册表：LLM 链路"模型属于哪个 provider"的单一事实来源。"""

    def test_profile_lookup_matrix(self):
        cases = {
            "glm-4-flash": "zhipu",
            "glm-5.1": "zhipu",
            "deepseek-chat": "deepseek",
            "claude-sonnet-4-20250514": "anthropic",
            "gpt-4o": "openai",
            "o1-preview": "openai",
            "o3-mini": "openai",
            "o4-mini": "openai",
            "gemini-2.5-pro": "google",
            "gemini/gemini-2.5-flash": "google",
            "qwen-max": "aliyun",
            "qwq-32b": "aliyun",
            "moonshot-v1-8k": "kimi",
            "kimi-k2.6": "kimi",
            "kimi-k2.7-code": "kimi",
            "ollama/llama3.1": "ollama",
        }
        for model, expected in cases.items():
            with self.subTest(model=model):
                profile = provider_profile_for_model(model)
                self.assertIsNotNone(profile)
                self.assertEqual(profile.name, expected)

    def test_unknown_model_returns_none(self):
        self.assertIsNone(provider_profile_for_model("ymg/some-model"))
        self.assertIsNone(provider_profile_for_model("some-random-model"))
        self.assertIsNone(provider_profile_for_model(""))

    def test_model_to_provider_falls_back_to_custom(self):
        self.assertEqual(model_to_provider("glm-4-flash"), "zhipu")
        self.assertEqual(model_to_provider("qwen-max"), "aliyun")
        self.assertEqual(model_to_provider("ymg-gpt-5.3-codex"), "custom")

    def test_anthropic_native_prefix_is_claude_slash(self):
        # litellm 官方 claude 模型历史解析为 claude/claude-x，不能改成 anthropic/
        profile = provider_profile_for_model("claude-sonnet-4-20250514")
        self.assertEqual(profile.native_prefix, "claude/")

    def test_short_openai_prefixes_do_not_overmatch(self):
        # "o1"/"o3"/"o4" 是短前缀，长前缀优先排序必须保证它们不抢走 qwq/qwen
        self.assertEqual(provider_profile_for_model("qwq-32b").name, "aliyun")
        self.assertEqual(provider_profile_for_model("qwen-max").name, "aliyun")

    def test_resolve_api_key_uses_registry_provider_key_names(self):
        # 注册表修复的能力：qwen 官方模型能查到 aliyun/dashscope/qwen 键
        self._clear_llm_env()
        cfg = LLMConfig(model="qwen-max", provider_keys={"aliyun": "aliyun-key"})
        self.assertEqual(cfg.resolve_api_key(), "aliyun-key")
        cfg = LLMConfig(model="qwen-max", provider_keys={"dashscope": "dashscope-key"})
        self.assertEqual(cfg.resolve_api_key(), "dashscope-key")

    def test_every_profile_key_name_is_honored(self):
        self._clear_llm_env()
        for profile in PROVIDER_PROFILES:
            if not profile.provider_key_names or profile.name == "ollama":
                continue
            model = next(p + "test-model" for p in profile.prefixes if not p.endswith("/"))
            for key_name in profile.provider_key_names:
                with self.subTest(profile=profile.name, key_name=key_name):
                    cfg = LLMConfig(model=model, provider_keys={key_name: f"{key_name}-key"})
                    self.assertEqual(cfg.resolve_api_key(), f"{key_name}-key")


class TestResolveCredentials(_CleanEnvMixin, unittest.TestCase):
    """resolve_credentials：读取侧统一入口，source 标注决定配置语义。"""

    def test_custom_provider_match_wins_with_source_label(self):
        cfg = LLMConfig(
            model="ymg-gpt-5.3-codex",
            api_key="test-top-level-key",
            custom_providers=[
                {
                    "name": "ymg",
                    "base_url": "https://api.ymg.com/v1",
                    "api_key": "ymg-key",
                    "models": [{"alias": "ymg-gpt-5.3-codex", "model": "gpt-5.3-codex"}],
                }
            ],
        )
        cred = cfg.resolve_credentials()
        self.assertEqual(cred.source, "custom_provider")
        self.assertEqual(cred.provider, "ymg")
        self.assertEqual(cred.api_key, "ymg-key")
        self.assertEqual(cred.api_base, "https://api.ymg.com/v1")

    def test_provider_keys_source_carries_console_url(self):
        self._clear_llm_env()
        cfg = LLMConfig(model="qwen-max", provider_keys={"aliyun": "aliyun-key"})
        cred = cfg.resolve_credentials()
        self.assertEqual(cred.source, "provider_keys")
        self.assertEqual(cred.provider, "aliyun")
        self.assertEqual(cred.api_key, "aliyun-key")
        self.assertEqual(cred.console_url, "https://bailian.console.aliyun.com/")

    def test_top_level_source_for_known_provider(self):
        self._clear_llm_env()
        cfg = LLMConfig(model="deepseek-chat", api_key="top-key")
        cred = cfg.resolve_credentials()
        self.assertEqual(cred.source, "top_level")
        self.assertEqual(cred.provider, "deepseek")
        self.assertEqual(cred.api_key, "top-key")
        self.assertEqual(cred.console_url, "https://platform.deepseek.com/api_keys")

    def test_env_source_for_known_provider(self):
        self._clear_llm_env()
        os.environ["DEEPSEEK_API_KEY"] = "env-key"
        self.addCleanup(lambda: os.environ.pop("DEEPSEEK_API_KEY", None))
        cfg = LLMConfig(model="deepseek-chat")
        cred = cfg.resolve_credentials()
        self.assertEqual(cred.source, "env")
        self.assertEqual(cred.api_key, "env-key")

    def test_none_source_when_no_credential_anywhere(self):
        self._clear_llm_env()
        cfg = LLMConfig(model="glm-4-flash")
        cred = cfg.resolve_credentials()
        self.assertEqual(cred.source, "none")
        self.assertEqual(cred.api_key, "")
        self.assertEqual(cred.provider, "zhipu")

    def test_unknown_model_falls_back_to_top_level_then_none(self):
        self._clear_llm_env()
        cfg = LLMConfig(model="some-random-model", api_key="top-key")
        cred = cfg.resolve_credentials()
        self.assertEqual(cred.source, "top_level")
        self.assertEqual(cred.provider, "custom")

        cfg = LLMConfig(model="some-random-model")
        cred = cfg.resolve_credentials()
        self.assertEqual(cred.source, "none")
        self.assertEqual(cred.api_key, "")


class TestUnifiedProviders(_CleanEnvMixin, unittest.TestCase):
    """[[llm.providers]] 统一注册表（Hermes 式）：新键名、显式引用、default_model、${VAR} 插值、保存即迁移。"""

    def _load(self, tmpdir: str, text: str) -> GDLAgentConfig:
        config_path = Path(tmpdir) / "config.toml"
        config_path.write_text(text, encoding="utf-8")
        return GDLAgentConfig.load(str(config_path))

    def test_new_format_providers_load_and_resolve(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = self._load(tmpdir, '''
[llm]
model = "deepseek-v4-flash"

[[llm.providers]]
name = "opencode-go"
api = "https://opencode.ai/zen/go/v1"
api_mode = "chat_completions"
api_key = "oc-key"
models = ["deepseek-v4-flash", "kimi-k3"]
''')
            self.assertEqual(config.llm.resolve_api_key(), "oc-key")
            self.assertEqual(config.llm.resolve_api_base(), "https://opencode.ai/zen/go/v1")
            cred = config.llm.resolve_credentials()
            self.assertEqual(cred.source, "custom_provider")
            self.assertEqual(cred.provider, "opencode-go")
            # 归一化条目同时携带新旧两组键
            entry = config.llm.providers[0]
            self.assertEqual(entry["api"], entry["base_url"])
            self.assertEqual(entry["api_mode"], "chat_completions")
            self.assertEqual(entry["protocol"], "openai")

    def test_explicit_provider_model_ref(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = self._load(tmpdir, '''
[llm]
model = "opencode-go/kimi-k3"

[[llm.providers]]
name = "opencode-go"
api = "https://opencode.ai/zen/go/v1"
api_key = "oc-key"
models = ["deepseek-v4-flash", "kimi-k3"]
''')
            self.assertEqual(config.llm.resolve_api_key(), "oc-key")
            match = config.llm.get_provider_for_model("opencode-go/kimi-k3")
            self.assertEqual(match.get("provider_name"), "opencode-go")
            self.assertEqual(match.get("model"), "kimi-k3")

    def test_explicit_ref_allows_model_not_in_models_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = self._load(tmpdir, '''
[llm]
model = "opencode-go/some-new-model"

[[llm.providers]]
name = "opencode-go"
api = "https://opencode.ai/zen/go/v1"
api_key = "oc-key"
models = ["deepseek-v4-flash"]
''')
            match = config.llm.get_provider_for_model("opencode-go/some-new-model")
            self.assertEqual(match.get("model"), "some-new-model")
            self.assertEqual(config.llm.resolve_api_key(), "oc-key")

    def test_provider_name_ref_uses_default_model(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = self._load(tmpdir, '''
[llm]
model = "opencode-go"

[[llm.providers]]
name = "opencode-go"
api = "https://opencode.ai/zen/go/v1"
api_key = "oc-key"
default_model = "kimi-k3"
models = ["deepseek-v4-flash", "kimi-k3"]
''')
            match = config.llm.get_provider_for_model("opencode-go")
            self.assertEqual(match.get("model"), "kimi-k3")

    def test_env_ref_interpolation_in_provider_api_key(self):
        self._clear_llm_env()
        os.environ["T_OCK"] = "env-oc-key"
        self.addCleanup(lambda: os.environ.pop("T_OCK", None))
        with tempfile.TemporaryDirectory() as tmpdir:
            config = self._load(tmpdir, '''
[llm]
model = "deepseek-v4-flash"

[[llm.providers]]
name = "opencode-go"
api = "https://opencode.ai/zen/go/v1"
api_key = "${T_OCK}"
models = ["deepseek-v4-flash"]
''')
            self.assertEqual(config.llm.resolve_api_key(), "env-oc-key")
            self.assertEqual(config.llm.resolve_credentials().api_key, "env-oc-key")

    def test_env_ref_interpolation_in_provider_keys_and_top_level(self):
        self._clear_llm_env()
        os.environ["T_ZK"] = "env-zhipu-key"
        self.addCleanup(lambda: os.environ.pop("T_ZK", None))
        cfg = LLMConfig(model="glm-4-flash", provider_keys={"zhipu": "${T_ZK}"})
        self.assertEqual(cfg.resolve_api_key(), "env-zhipu-key")
        cfg = LLMConfig(model="glm-4-flash", api_key="${T_ZK}")
        self.assertEqual(cfg.resolve_api_key(), "env-zhipu-key")

    def test_legacy_custom_providers_still_resolve(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = self._load(tmpdir, '''
[llm]
model = "gpt-5.4"

[[llm.custom_providers]]
name = "ymg"
base_url = "https://api.ymg.com/v1"
api_key = "ymg-key"
models = ["gpt-5.4"]
protocol = "openai"
''')
            self.assertEqual(config.llm.resolve_api_key(), "ymg-key")
            self.assertEqual(config.llm.resolve_api_base(), "https://api.ymg.com/v1")
            entry = config.llm.custom_providers[0]
            self.assertEqual(entry["api"], "https://api.ymg.com/v1")
            self.assertEqual(entry["api_mode"], "chat_completions")

    def test_save_migrates_to_providers_and_roundtrips(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config_path.write_text('''
[llm]
model = "deepseek-v4-flash"

[[llm.custom_providers]]
name = "opencode-go"
base_url = "https://opencode.ai/zen/go/v1"
api_key = "oc-key"
models = ["deepseek-v4-flash"]
protocol = "openai"
'''.strip(), encoding="utf-8")

            config = GDLAgentConfig.load(str(config_path))
            config.save(str(config_path))

            saved_text = config_path.read_text(encoding="utf-8")
            self.assertIn("[[llm.providers]]", saved_text)
            self.assertNotIn("custom_providers", saved_text)
            self.assertIn('api = "https://opencode.ai/zen/go/v1"', saved_text)
            self.assertIn('api_mode = "chat_completions"', saved_text)

            reloaded = GDLAgentConfig.load(str(config_path))
            self.assertEqual(reloaded.llm.resolve_api_key(), "oc-key")
            self.assertEqual(reloaded.llm.resolve_api_base(), "https://opencode.ai/zen/go/v1")
            self.assertEqual(reloaded.llm.custom_providers[0]["name"], "opencode-go")

    def test_llm_default_alias_for_model(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = self._load(tmpdir, '''
[llm]
default = "deepseek-v4-flash"

[[llm.providers]]
name = "opencode-go"
api = "https://opencode.ai/zen/go/v1"
api_key = "oc-key"
models = ["deepseek-v4-flash"]
''')
            self.assertEqual(config.llm.model, "deepseek-v4-flash")
            self.assertEqual(config.llm.resolve_api_key(), "oc-key")

    def test_anthropic_api_mode_normalizes_protocol(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = self._load(tmpdir, '''
[llm]
model = "claude-fable-5"

[[llm.providers]]
name = "openmodel"
api = "https://api.openmodel.ai/v1"
api_mode = "anthropic_messages"
api_key = "om-key"
models = ["claude-fable-5"]
''')
            entry = config.llm.providers[0]
            self.assertEqual(entry["api_mode"], "anthropic_messages")
            self.assertEqual(entry["protocol"], "anthropic")

    def test_provider_extra_body_overrides_top_level(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = self._load(tmpdir, '''
[llm]
model = "kimi-k2.6"
extra_body = { thinking = { type = "enabled" } }

[[llm.providers]]
name = "kimi"
api = "https://api.moonshot.cn/v1"
api_key = "ms-key"
models = ["kimi-k2.6", "kimi-k2.7-code"]
extra_body = { thinking = { type = "disabled" } }
''')
            # provider 条目级整体覆盖顶层
            self.assertEqual(
                config.llm.resolve_extra_body("kimi-k2.6"),
                {"thinking": {"type": "disabled"}},
            )

    def test_top_level_extra_body_when_provider_has_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = self._load(tmpdir, '''
[llm]
model = "kimi-k2.7-code"
extra_body = { thinking = { type = "disabled" } }

[[llm.providers]]
name = "kimi-code"
api = "https://api.moonshot.cn/v1"
api_key = "ms-key"
models = ["kimi-k2.7-code"]
''')
            # 条目无 extra_body → 回退顶层
            self.assertEqual(
                config.llm.resolve_extra_body("kimi-k2.7-code"),
                {"thinking": {"type": "disabled"}},
            )

    def test_provider_extra_body_save_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config_path.write_text('''
[llm]
model = "kimi-k2.6"

[[llm.providers]]
name = "kimi"
api = "https://api.moonshot.cn/v1"
api_key = "ms-key"
models = ["kimi-k2.6"]
extra_body = { thinking = { type = "disabled" } }
'''.strip(), encoding="utf-8")

            config = GDLAgentConfig.load(str(config_path))
            config.save(str(config_path))

            saved_text = config_path.read_text(encoding="utf-8")
            self.assertIn("extra_body", saved_text)
            self.assertIn("disabled", saved_text)

            reloaded = GDLAgentConfig.load(str(config_path))
            self.assertEqual(
                reloaded.llm.resolve_extra_body("kimi-k2.6"),
                {"thinking": {"type": "disabled"}},
            )
