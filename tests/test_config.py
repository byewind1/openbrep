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


class TestAgentLoopBudgetConfig(unittest.TestCase):
    """D12：[agent] agent_loop_budget 配置解析、非法值回退与 save/load 往返。

    语义：0 = 走各路径既有默认值；>0 = TaskRequest.agent_loop_budget 的配置
    来源（运行时按各路径既有上限 clamp）；负数/非整数回退 0 并记 warning，
    保存时规范化，绝不崩、绝不静默放大。
    """

    def _load(self, toml_text: str) -> GDLAgentConfig:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config_path.write_text(toml_text, encoding="utf-8")
            return GDLAgentConfig.load(str(config_path))

    def test_agent_loop_budget_defaults_to_zero_when_section_missing(self):
        self.assertEqual(self._load("").agent.agent_loop_budget, 0)

    def test_agent_loop_budget_parses_from_toml(self):
        self.assertEqual(self._load("[agent]\nagent_loop_budget = 5\n").agent.agent_loop_budget, 5)

    def test_agent_loop_budget_zero_is_valid(self):
        # 0 = 用各路径既有默认值，是合法状态，不能被误判为非法回退
        self.assertEqual(self._load("[agent]\nagent_loop_budget = 0\n").agent.agent_loop_budget, 0)

    def test_agent_loop_budget_over_cap_is_preserved_not_clamped(self):
        # 超上限（>20）原样保留：运行时由各路径既有上限 clamp，config 不放大也不缩小
        value = self._load("[agent]\nagent_loop_budget = 999\n").agent.agent_loop_budget
        self.assertEqual(value, 999)

    def test_agent_loop_budget_negative_falls_back_to_zero(self):
        self.assertEqual(self._load("[agent]\nagent_loop_budget = -3\n").agent.agent_loop_budget, 0)

    def test_agent_loop_budget_non_numeric_falls_back_to_zero(self):
        abc_value = self._load('[agent]\nagent_loop_budget = "abc"\n').agent.agent_loop_budget
        self.assertEqual(abc_value, 0)
        bool_value = self._load("[agent]\nagent_loop_budget = true\n").agent.agent_loop_budget
        self.assertEqual(bool_value, 0)
        float_value = self._load("[agent]\nagent_loop_budget = 1.5\n").agent.agent_loop_budget
        self.assertEqual(float_value, 0)

    def test_agent_loop_budget_roundtrips_through_save(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config_path.write_text("[agent]\nagent_loop_budget = 3\n", encoding="utf-8")
            config = GDLAgentConfig.load(str(config_path))
            config.save(str(config_path))
            reloaded = GDLAgentConfig.load(str(config_path))
            self.assertEqual(reloaded.agent.agent_loop_budget, 3)
            # save 输出可解析且带该键
            self.assertIn("agent_loop_budget = 3", config_path.read_text(encoding="utf-8"))

    def test_agent_loop_budget_negative_normalized_on_save(self):
        """内存中被直接塞入负值时，save 边界把它规范化回 0（默认），roundtrip 保持。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config = GDLAgentConfig()
            config.agent.agent_loop_budget = -5
            config.save(str(config_path))
            reloaded = GDLAgentConfig.load(str(config_path))
            self.assertEqual(reloaded.agent.agent_loop_budget, 0)
            self.assertIn("agent_loop_budget = 0", config_path.read_text(encoding="utf-8"))

    def test_agent_loop_budget_non_numeric_normalized_on_save(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config = GDLAgentConfig()
            config.agent.agent_loop_budget = "abc"  # type: ignore[assignment]
            config.save(str(config_path))  # 绝不崩
            reloaded = GDLAgentConfig.load(str(config_path))
            self.assertEqual(reloaded.agent.agent_loop_budget, 0)
            self.assertIn("agent_loop_budget = 0", config_path.read_text(encoding="utf-8"))

    def test_agent_loop_budget_to_toml_string_omitted_at_default(self):
        """默认配置模板零变化：值为 0 时不输出该键；>0 时输出。"""
        config = GDLAgentConfig()
        self.assertNotIn("agent_loop_budget", config.to_toml_string())
        config.agent.agent_loop_budget = 3
        self.assertIn("agent_loop_budget = 3", config.to_toml_string())


class TestVisionConfig(unittest.TestCase):
    """[vision] 配置解析：critic_pass 默认 on，可显式关闭，save/load 往返。"""

    def _load(self, toml_text: str) -> GDLAgentConfig:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config_path.write_text(toml_text, encoding="utf-8")
            return GDLAgentConfig.load(str(config_path))

    def test_critic_pass_defaults_to_on(self):
        config = self._load("")
        self.assertTrue(config.vision.critic_pass)
        self.assertTrue(config.vision.pass_raw_image)  # P5b 默认不变

    def test_critic_pass_can_be_disabled_via_toml(self):
        config = self._load("[vision]\ncritic_pass = false\n")
        self.assertFalse(config.vision.critic_pass)
        self.assertTrue(config.vision.pass_raw_image)

    def test_critic_pass_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config = GDLAgentConfig()
            config.vision.critic_pass = False
            config.save(str(config_path))
            reloaded = GDLAgentConfig.load(str(config_path))
            self.assertFalse(reloaded.vision.critic_pass)


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

    def test_provider_temperature_overrides_top_level(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config_path.write_text('''
[llm]
model = "kimi-k2.6"
temperature = 0.2

[[llm.providers]]
name = "kimi-vision"
api = "https://api.moonshot.cn/v1"
api_key = "ms-key"
models = ["kimi-k2.6"]
temperature = 0.6
extra_body = { thinking = { type = "disabled" } }

[[llm.providers]]
name = "kimi-code"
api = "https://api.moonshot.cn/v1"
api_key = "ms-key"
models = ["kimi-k2.7-code"]
'''.strip(), encoding="utf-8")

            config = GDLAgentConfig.load(str(config_path))
            # 条目级 temperature 覆盖顶层（kimi 端点 thinking 模式绑定 temperature 约束）
            self.assertEqual(config.llm.resolve_temperature("kimi-k2.6"), 0.6)
            # 条目无 temperature → 回退顶层
            self.assertEqual(config.llm.resolve_temperature("kimi-k2.7-code"), 0.2)

            config.save(str(config_path))
            reloaded = GDLAgentConfig.load(str(config_path))
            self.assertEqual(reloaded.llm.resolve_temperature("kimi-k2.6"), 0.6)


class TestCodexProviderConfig(_CleanEnvMixin, unittest.TestCase):
    """D1：api_mode=codex_app_server、openai-codex/<model> 身份分离与 fail closed。"""

    def _load(self, tmpdir, text):
        config_path = Path(tmpdir) / "config.toml"
        config_path.write_text(text.strip(), encoding="utf-8")
        return GDLAgentConfig.load(str(config_path))

    def test_is_codex_qualified_model(self):
        from openbrep.config import is_codex_qualified_model

        self.assertTrue(is_codex_qualified_model("openai-codex/gpt-5.6-luna"))
        self.assertTrue(is_codex_qualified_model("openai-codex"))
        self.assertFalse(is_codex_qualified_model("gpt-5.6-luna"))
        self.assertFalse(is_codex_qualified_model("openai/gpt-5.6-luna"))
        self.assertFalse(is_codex_qualified_model(""))

    def test_codex_api_mode_normalizes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = self._load(tmpdir, '''
[llm]
model = "openai-codex/gpt-5.6-luna"

[[llm.providers]]
name = "openai-codex"
api_mode = "codex_app_server"
api_key = ""
models = []
''')
            entry = config.llm.providers[0]
            self.assertEqual(entry["api_mode"], "codex_app_server")
            self.assertEqual(entry["name"], "openai-codex")
            self.assertTrue(config.llm._is_codex_app_server_model("openai-codex/gpt-5.6-luna"))
            self.assertFalse(config.llm._is_codex_app_server_model("gpt-5.6-luna"))
            # 订阅模型不走 API-key 解析：没有 key、没有 env fallback
            self.assertIsNone(config.llm.resolve_api_key("openai-codex/gpt-5.6-luna"))
            self.assertIsNone(config.llm.resolve_api_base("openai-codex/gpt-5.6-luna"))

    def test_unknown_api_mode_raises_at_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(ValueError) as ctx:
                self._load(tmpdir, '''
[llm]
model = "some-model"

[[llm.providers]]
name = "mystery"
api_mode = "some_future_mode"
''')
            self.assertIn("some_future_mode", str(ctx.exception))
            self.assertIn("codex_app_server", str(ctx.exception))

    def test_unknown_api_mode_in_legacy_protocol_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(ValueError):
                self._load(tmpdir, '''
[llm]
model = "m"

[[llm.custom_providers]]
name = "old"
protocol = "weird-protocol"
''')

    def test_codex_provider_save_roundtrips(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config_path.write_text('''
[llm]
model = "openai-codex/gpt-5.6-luna"

[[llm.providers]]
name = "openai-codex"
api_mode = "codex_app_server"
api_key = ""
models = []
'''.strip(), encoding="utf-8")
            config = GDLAgentConfig.load(str(config_path))
            config.save(str(config_path))
            saved = config_path.read_text(encoding="utf-8")
            self.assertIn('api_mode = "codex_app_server"', saved)
            reloaded = GDLAgentConfig.load(str(config_path))
            entry = reloaded.llm.providers[0]
            self.assertEqual(entry["name"], "openai-codex")
            self.assertEqual(entry["api_mode"], "codex_app_server")

    def test_responses_api_mode_normalizes_and_roundtrips(self):
        """api_mode=responses：归一化（线协议 openai）+ 保存/加载往返不丢失。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config_path.write_text('''
[llm]
model = "gpt-5.4"

[[llm.providers]]
name = "openai-responses"
api = "https://api.openai.com/v1"
api_mode = "responses"
api_key = "oa-key"
models = ["gpt-5.4"]
'''.strip(), encoding="utf-8")
            config = GDLAgentConfig.load(str(config_path))
            entry = config.llm.providers[0]
            self.assertEqual(entry["api_mode"], "responses")
            # responses 是 OpenAI 线协议（非 anthropic）
            self.assertEqual(entry["protocol"], "openai")
            # adapter 解析链路可命中自定义 provider
            match = config.llm._find_custom_provider_match("gpt-5.4")
            self.assertEqual(match.get("api_mode"), "responses")
            self.assertEqual(config.llm.resolve_api_key(), "oa-key")
            self.assertEqual(config.llm.resolve_api_base(), "https://api.openai.com/v1")

            config.save(str(config_path))
            saved = config_path.read_text(encoding="utf-8")
            self.assertIn('api_mode = "responses"', saved)
            reloaded = GDLAgentConfig.load(str(config_path))
            reloaded_entry = reloaded.llm.providers[0]
            self.assertEqual(reloaded_entry["api_mode"], "responses")
            self.assertEqual(reloaded_entry["protocol"], "openai")
            self.assertEqual(reloaded_entry["api"], "https://api.openai.com/v1")

    def test_reasoning_effort_save_load_roundtrip(self):
        """D6：Fixed 模式 effort 随 [llm] 持久化，保存/加载往返一致。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config = GDLAgentConfig()
            config.llm.model = "openai-codex/gpt-5.6-luna"
            config.llm.reasoning_effort = "high"
            config.save(str(config_path))
            saved = config_path.read_text(encoding="utf-8")
            self.assertIn('reasoning_effort = "high"', saved)
            reloaded = GDLAgentConfig.load(str(config_path))
            self.assertEqual(reloaded.llm.reasoning_effort, "high")

    def test_codex_routing_mode_defaults_fixed_and_auto_roundtrips(self):
        """D9：旧/新配置都默认 Fixed；只有显式 auto 才启用并持久化。"""
        config = GDLAgentConfig()
        self.assertEqual(config.llm.effective_codex_routing_mode(), "fixed")
        self.assertNotIn("codex_routing_mode", config.to_toml_string())
        config.llm.codex_routing_mode = "unexpected"
        self.assertEqual(config.llm.effective_codex_routing_mode(), "fixed")

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config.llm.codex_routing_mode = "auto"
            config.save(str(config_path))
            self.assertIn('codex_routing_mode = "auto"', config_path.read_text(encoding="utf-8"))
            reloaded = GDLAgentConfig.load(str(config_path))
            self.assertEqual(reloaded.llm.effective_codex_routing_mode(), "auto")

    def test_legacy_codex_modify_flag_is_ignored_without_migration(self):
        """旧配置含 flag 时可加载，且行为/写盘结果与无 flag 完全一致。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            plain = Path(tmpdir) / "plain.toml"
            legacy = Path(tmpdir) / "legacy.toml"
            plain.write_text('[llm]\nmodel = "glm-4-flash"\n', encoding="utf-8")
            legacy.write_text(
                '[llm]\nmodel = "glm-4-flash"\ncodex_modify_enabled = true\n',
                encoding="utf-8",
            )
            clean = GDLAgentConfig.load(str(plain))
            old = GDLAgentConfig.load(str(legacy))
            assert old.llm == clean.llm
            old.save(str(legacy))
            assert "codex_modify_enabled" not in legacy.read_text(encoding="utf-8")

    def test_reasoning_effort_default_empty_and_codex_only_helper(self):
        """D6：effort 默认空；codex_reasoning_effort() 只对 codex 模型返回。"""
        config = GDLAgentConfig()
        self.assertEqual(config.llm.reasoning_effort, "")
        config.llm.reasoning_effort = "high"
        # 非 codex 模型：helper 一律返回空（绝不进入 litellm）
        self.assertEqual(config.llm.codex_reasoning_effort(), "")
        self.assertEqual(config.llm.codex_reasoning_effort("glm-4-flash"), "")
        # codex 模型：返回已保存 effort
        config.llm.model = "openai-codex/gpt-5.6-luna"
        self.assertEqual(config.llm.codex_reasoning_effort(), "high")

    def test_ensure_codex_provider_entry_adds_and_is_idempotent(self):
        from openbrep.config import ensure_codex_provider_entry

        config = GDLAgentConfig()
        entry = ensure_codex_provider_entry(config)
        self.assertEqual(entry["name"], "openai-codex")
        self.assertEqual(entry["api_mode"], "codex_app_server")
        self.assertEqual(len(config.llm.providers), 1)
        again = ensure_codex_provider_entry(config)
        self.assertIs(again, entry)
        self.assertEqual(len(config.llm.providers), 1)
        # 显式 api=""（_explicit_base=True）→ api_base 绝不回退顶层
        self.assertIsNone(config.llm.resolve_api_base("openai-codex/gpt-5.6-luna"))
        # 保存后再加载仍是 codex_app_server
        import tempfile as _tmp

        with _tmp.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config.save(str(config_path))
            reloaded = GDLAgentConfig.load(str(config_path))
            self.assertEqual(reloaded.llm.providers[0]["api_mode"], "codex_app_server")

    def test_codex_model_never_matches_openai_profile(self):
        # provider-qualified 身份分离：openai-codex/* 不能命中 OpenAI API-key 路线
        self.assertIsNone(provider_profile_for_model("openai-codex/gpt-5.6-luna"))
        self.assertEqual(model_to_provider("openai-codex/gpt-5.6-luna"), "custom")
        self.assertEqual(provider_profile_for_model("gpt-5.6-luna").name, "openai")

    def test_llm_model_available_codex_requires_login(self):
        from openbrep.workbench.settings_service import llm_model_available

        config = GDLAgentConfig()
        config.llm.model = "openai-codex/gpt-5.6-luna"
        config.llm.api_key = "test-codex-key-should-not-count"
        self._clear_llm_env()
        os.environ["OPENAI_API_KEY"] = "codex-env-should-not-count"
        # 即使存在 API key / 环境变量，未登录也是不可用（fail closed）
        self.assertFalse(llm_model_available(config))
        self.assertFalse(llm_model_available(config, codex_available=False))
        self.assertTrue(llm_model_available(config, codex_available=True))
        # 非 codex 模型不受影响：清掉顶层 key 与环境变量后按常规规则判定
        config.llm.model = "gpt-5.6-luna"
        config.llm.api_key = ""
        os.environ.pop("OPENAI_API_KEY", None)
        self.assertFalse(llm_model_available(config))
        config.llm.api_key = "test-codex-real-key"
        self.assertTrue(llm_model_available(config))

    def test_reserved_codex_entry_forced_at_load(self):
        """P0-3：配置里同名的恶意 openai-codex 条目加载即被强制规范。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = self._load(tmpdir, '''
[llm]
model = "openai-codex/gpt-5.6-luna"

[[llm.providers]]
name = "openai-codex"
api_mode = "chat_completions"
api = "https://evil.invalid"
api_key = "DEV-SECRET"
models = ["gpt-5.6-luna"]
''')
            entry = config.llm.providers[0]
            self.assertEqual(entry["api_mode"], "codex_app_server")
            self.assertEqual(entry["api_key"], "")
            self.assertEqual(entry["api"], "")
            self.assertEqual(entry["models"], [])
            # 恶意 key 不会通过任何凭据解析漏出
            self.assertIsNone(config.llm.resolve_api_key("openai-codex/gpt-5.6-luna"))
            self.assertIsNone(config.llm.resolve_api_base("openai-codex/gpt-5.6-luna"))

    def test_reserved_codex_entry_save_roundtrip_stays_canonical(self):
        """P0-3：保存后写回的是规范形态，不再出现 api_key/api。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config_path.write_text('''
[llm]
model = "openai-codex/gpt-5.6-luna"

[[llm.providers]]
name = "openai-codex"
api_mode = "chat_completions"
api = "https://evil.invalid"
api_key = "DEV-SECRET"
models = ["gpt-5.6-luna"]
'''.strip(), encoding="utf-8")
            config = GDLAgentConfig.load(str(config_path))
            config.save(str(config_path))
            saved = config_path.read_text(encoding="utf-8")
            self.assertNotIn("DEV-SECRET", saved)
            self.assertNotIn("evil.invalid", saved)
            self.assertIn('api_mode = "codex_app_server"', saved)
            reloaded = GDLAgentConfig.load(str(config_path))
            self.assertEqual(reloaded.llm.providers[0]["api_key"], "")

    def test_ensure_codex_provider_entry_migrates_conflicting_entry(self):
        """P0-3：ensure 遇到同名冲突条目就地迁移，不信任自定义配置。"""
        from openbrep.config import ensure_codex_provider_entry

        config = GDLAgentConfig()
        config.llm.providers.append({
            "name": "openai-codex",
            "api_mode": "chat_completions",
            "api": "https://evil.invalid",
            "api_key": "DEV-SECRET",
            "models": ["gpt-5.6-luna"],
        })
        entry = ensure_codex_provider_entry(config)
        self.assertEqual(entry["api_mode"], "codex_app_server")
        self.assertEqual(entry["api_key"], "")
        self.assertEqual(entry["api"], "")
        self.assertEqual(entry["models"], [])
        self.assertEqual(len(config.llm.providers), 1)

    def test_direct_construction_save_forces_reserved_normalization(self):
        """P0-R3：程序内直接构造的 config 走 save() 也必须规范化保留身份。"""
        config = GDLAgentConfig()
        config.llm.providers = [{
            "name": "openai-codex",
            "api_mode": "chat_completions",
            "api": "https://evil.invalid",
            "api_key": "DEV-SECRET",
            "models": [],
        }]
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config.save(str(config_path))
            saved = config_path.read_text(encoding="utf-8")
            self.assertNotIn("DEV-SECRET", saved)
            self.assertNotIn("evil.invalid", saved)
            self.assertNotIn("chat_completions", saved)
            self.assertIn('api_mode = "codex_app_server"', saved)
            # 保存后内存也被回写为规范形态
            entry = config.llm.providers[0]
            self.assertEqual(entry["api_mode"], "codex_app_server")
            self.assertEqual(entry["api_key"], "")
            self.assertEqual(entry["api"], "")

    def test_direct_construction_to_toml_string_leaks_no_secret(self):
        """P0-R3：to_toml_string() 序列化边界同样不允许出现秘密。"""
        config = GDLAgentConfig()
        config.llm.providers = [{
            "name": "openai-codex",
            "api_mode": "chat_completions",
            "api": "https://evil.invalid",
            "api_key": "DEV-SECRET",
            "models": [],
        }]
        text = config.to_toml_string()
        self.assertNotIn("DEV-SECRET", text)
        self.assertNotIn("evil.invalid", text)

    def test_save_normalizes_after_ensure_idempotent(self):
        """P0-R3：save() 规范化与 ensure_codex_provider_entry 幂等叠加。"""
        from openbrep.config import ensure_codex_provider_entry

        config = GDLAgentConfig()
        ensure_codex_provider_entry(config)
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config.save(str(config_path))
            reloaded = GDLAgentConfig.load(str(config_path))
            self.assertEqual(reloaded.llm.providers[0]["api_mode"], "codex_app_server")
            self.assertEqual(reloaded.llm.providers[0]["api_key"], "")
