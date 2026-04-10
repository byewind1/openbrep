import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from openbrep.cli import cli
from openbrep.config import GDLAgentConfig


class TestCliConfigure(unittest.TestCase):
    def setUp(self):
        self.runner = CliRunner()

    def test_configure_writes_builtin_provider_key_and_backup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config_path.write_text(
                '[llm]\nmodel = "glm-4-flash"\n\n[llm.provider_keys]\nzhipu = "old-key"\n',
                encoding="utf-8",
            )

            user_input = "claude-opus-4-6\nnew-anthropic-key\nn\ny\n"
            result = self.runner.invoke(
                cli,
                ["configure", "--config", str(config_path)],
                input=user_input,
            )

            self.assertEqual(result.exit_code, 0, msg=result.output)
            self.assertIn("已写入配置", result.output)
            self.assertIn("已备份旧配置", result.output)

            reloaded = GDLAgentConfig.load(str(config_path))
            self.assertEqual(reloaded.llm.model, "claude-opus-4-6")
            self.assertEqual(reloaded.llm.provider_keys.get("anthropic"), "new-anthropic-key")

            backups = list(Path(tmpdir).glob("config.toml.bak.*"))
            self.assertEqual(len(backups), 1)

    def test_configure_writes_custom_provider(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config_path.write_text('[llm]\nmodel = "glm-4-flash"\n', encoding="utf-8")

            user_input = "my-model\nmy-proxy\nhttps://proxy.example.com/v1\nproxy-key\nopenai\nn\ny\n"
            result = self.runner.invoke(
                cli,
                ["configure", "--config", str(config_path)],
                input=user_input,
            )

            self.assertEqual(result.exit_code, 0, msg=result.output)
            reloaded = GDLAgentConfig.load(str(config_path))
            self.assertEqual(reloaded.llm.model, "my-model")
            self.assertEqual(len(reloaded.llm.custom_providers), 1)
            provider = reloaded.llm.custom_providers[0]
            self.assertEqual(provider["name"], "my-proxy")
            self.assertEqual(provider["base_url"], "https://proxy.example.com/v1")
            self.assertEqual(provider["api_key"], "proxy-key")
            self.assertEqual(provider["protocol"], "openai")
            self.assertEqual(provider["models"], ["my-model"])

    def test_doctor_reports_missing_key_as_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config_path.write_text('[llm]\nmodel = "claude-opus-4-6"\n', encoding="utf-8")

            result = self.runner.invoke(cli, ["doctor", "--config", str(config_path)])

            self.assertNotEqual(result.exit_code, 0)
            self.assertIn("未解析到 API Key", result.output)
            self.assertIn("provider key 未配置", result.output)

    def test_doctor_passes_when_builtin_key_exists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config_path.write_text(
                '[llm]\nmodel = "claude-opus-4-6"\n\n[llm.provider_keys]\nanthropic = "test-key"\n',
                encoding="utf-8",
            )

            with patch("openbrep.cli._auto_detect_converter", return_value=None):
                result = self.runner.invoke(cli, ["doctor", "--config", str(config_path)])

            self.assertEqual(result.exit_code, 0, msg=result.output)
            self.assertIn("未发现配置问题", result.output)


if __name__ == "__main__":
    unittest.main()
