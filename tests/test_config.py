import tempfile
import unittest
from pathlib import Path

from openbrep.config import GDLAgentConfig


class TestConfigAssistantSettings(unittest.TestCase):
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
            self.assertEqual(reloaded.compiler.path, "/tmp/LP_XMLConverter")
            self.assertEqual(reloaded.llm.model, "claude-sonnet-4-6")


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
            self.assertEqual(second.llm.api_base, "https://api.airsim.eu.cc/v1")
            self.assertEqual(second.llm.api_key, "custom-key")

