import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from typer.testing import CliRunner

from cli.main import (
    app,
    obrcli_entry,
    _extract_project_name_from_prompt,
    _resolve_compile_target,
    _resolve_create_target,
    _run_chat_repl,
)
from openbrep.config import GDLAgentConfig


class _FakeResultProject:
    def __init__(self):
        self.name = ""
        self.work_dir = None
        self.root = None

    def save_to_disk(self):
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root


class _FakePipelineResult:
    def __init__(self, project=None):
        self.success = True
        self.error = None
        self.project = project
        self.plain_text = ""
        self.scripts = {}
        self.trace_path = None


class _FakePipeline:
    def __init__(self, result):
        self.result = result
        self.config = type("Cfg", (), {"llm": type("LLM", (), {"model": "fake-model"})()})()
        self.last_request = None

    def execute(self, request):
        self.last_request = request
        return self.result


class _FakeChatPipelineResult:
    def __init__(self):
        self.success = True
        self.error = None
        self.project = None
        self.plain_text = "ok"
        self.scripts = {}
        self.trace_path = None


class _FakeChatPipeline:
    def __init__(self):
        self.requests = []

    def execute(self, request):
        self.requests.append(request)
        return _FakeChatPipelineResult()


class TestCliMainPaths(unittest.TestCase):

    def test_extract_project_name_uses_known_object_keyword(self):
        self.assertEqual(
            _extract_project_name_from_prompt("做一个圆形花盆，直径300mm，高度200mm"),
            "Planter",
        )

    def test_resolve_create_target_defaults_under_output_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target_path, project_name, was_aliased = _resolve_create_target(
                tmpdir,
                "做一个圆形花盆，直径300mm，高度200mm",
            )
            self.assertEqual(target_path, Path(tmpdir).resolve() / "Planter")
            self.assertEqual(project_name, "Planter")
            self.assertFalse(was_aliased)

    def test_resolve_create_target_avoids_overwrite_with_suffix(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            existing = Path(tmpdir).resolve() / "Planter"
            existing.mkdir()
            target_path, project_name, was_aliased = _resolve_create_target(
                tmpdir,
                "做一个圆形花盆，直径300mm，高度200mm",
            )
            self.assertEqual(target_path, Path(tmpdir).resolve() / "Planter-2")
            self.assertEqual(project_name, "Planter-2")
            self.assertTrue(was_aliased)

    def test_resolve_compile_target_defaults_to_gsm_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target_path, was_aliased = _resolve_compile_target(tmpdir, "Planter")
            self.assertEqual(target_path, Path(tmpdir).resolve() / "Planter.gsm")
            self.assertFalse(was_aliased)

    def test_resolve_compile_target_avoids_overwrite_with_suffix(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            existing = Path(tmpdir).resolve() / "Planter.gsm"
            existing.parent.mkdir(parents=True, exist_ok=True)
            existing.write_text("x", encoding="utf-8")
            target_path, was_aliased = _resolve_compile_target(tmpdir, "Planter")
            self.assertEqual(target_path, Path(tmpdir).resolve() / "Planter-2.gsm")
            self.assertTrue(was_aliased)

class TestCliMainCommands(unittest.TestCase):

    def setUp(self):
        self.runner = CliRunner()

    def test_no_subcommand_launches_ui(self):
        with patch("cli.main._launch_ui", return_value=0) as launch:
            result = self.runner.invoke(app, [])
        self.assertEqual(result.exit_code, 0, msg=result.output)
        launch.assert_called_once()

    def test_launch_ui_prints_install_hint_when_streamlit_missing(self):
        with patch("cli.main._has_streamlit", return_value=False):
            result = self.runner.invoke(app, [])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("未安装 UI 依赖 streamlit", result.output)
        self.assertIn("pip install openbrep[ui]", result.output)

    def test_launch_ui_uses_absolute_app_path(self):
        with patch("cli.main._has_streamlit", return_value=True):
            with patch("cli.main.subprocess.call", return_value=0) as call:
                result = self.runner.invoke(app, [])

        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertIn("OpenBrep UI 已启动：http://localhost:8501", result.output)
        self.assertIn("已关闭自动打开浏览器", result.output)
        cmd = call.call_args.args[0]
        self.assertEqual(cmd[1:3], ["-m", "streamlit"])
        self.assertEqual(cmd[3], "run")
        ui_app_path = Path(cmd[4])
        self.assertTrue(ui_app_path.is_absolute())
        self.assertEqual(ui_app_path.name, "app.py")
        self.assertEqual(ui_app_path.parent.name, "ui")
        self.assertEqual(cmd[5:], ["--server.headless", "true"])

    def test_cli_subcommand_enters_repl(self):
        with patch("cli.main._run_chat_repl") as repl:
            result = self.runner.invoke(app, ["cli"])
        self.assertEqual(result.exit_code, 0, msg=result.output)
        repl.assert_called_once_with(None)

    def test_chat_command_reuses_same_repl(self):
        with patch("cli.main._run_chat_repl") as repl:
            result = self.runner.invoke(app, ["chat"])
        self.assertEqual(result.exit_code, 0, msg=result.output)
        repl.assert_called_once_with(None)

    def test_help_command_prints_quick_guide(self):
        result = self.runner.invoke(app, ["help"])
        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertIn("OpenBrep 命令速查", result.output)
        self.assertIn("obr cli", result.output)
        self.assertIn("obrcli", result.output)
        self.assertIn("obr configure", result.output)

    def test_default_help_still_available(self):
        result = self.runner.invoke(app, ["--help"])
        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertIn("Usage", result.output)

    def test_obrcli_entry_reuses_repl(self):
        with patch("cli.main._run_chat_repl") as repl:
            obrcli_entry()
        repl.assert_called_once_with(None)

    def test_obrcli_entry_forwards_extra_args(self):
        with patch("cli.main.app") as app_mock:
            obrcli_entry(["--help"])
        app_mock.assert_called_once_with(prog_name="obrcli", args=["cli", "--help"])

    def test_create_prints_final_directory_and_alias(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_root = Path(tmpdir) / "output"
            (output_root / "Planter").mkdir(parents=True)
            fake_project = _FakeResultProject()
            fake_pipeline = _FakePipeline(_FakePipelineResult(project=fake_project))

            with patch("cli.main._load_pipeline", return_value=fake_pipeline):
                result = self.runner.invoke(
                    app,
                    ["create", "做一个圆形花盆，直径300mm，高度200mm", "--output", str(output_root)],
                )

            self.assertEqual(result.exit_code, 0, msg=result.output)
            self.assertIn("检测到同名目录，已改名为 Planter-2", result.output)
            self.assertIn("Planter-2", result.output)
            self.assertIn("项目目录：", result.output)
            self.assertIn("项目名：Planter-2", result.output)
            self.assertEqual(fake_pipeline.last_request.gsm_name, "Planter-2")

            saved_path = (output_root / "Planter-2").resolve()
            self.assertEqual(fake_project.root, saved_path)
            self.assertTrue(saved_path.exists())
            self.assertEqual(fake_project.work_dir, output_root.resolve())


    def test_compile_prints_final_filename_and_alias(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir) / "Planter"
            project_dir.mkdir(parents=True)
            output_root = Path(tmpdir) / "output"
            output_root.mkdir(parents=True)
            (output_root / "Planter.gsm").write_text("existing", encoding="utf-8")

            fake_project = _FakeResultProject()
            fake_project.name = "Planter"
            fake_project.root = project_dir

            class _FakeCompileResult:
                success = True
                stderr = ""

            class _FakeCompiler:
                def hsf2libpart(self, hsf_dir, gsm_path):
                    Path(gsm_path).write_text("compiled", encoding="utf-8")
                    return _FakeCompileResult()

            with patch("openbrep.hsf_project.HSFProject.load_from_disk", return_value=fake_project):
                with patch("openbrep.compiler.MockHSFCompiler", return_value=_FakeCompiler()):
                    result = self.runner.invoke(
                        app,
                        ["compile", str(project_dir), "--output", str(output_root), "--mock"],
                    )

            self.assertEqual(result.exit_code, 0, msg=result.output)
            self.assertIn("检测到同名文件，已改名为 Planter-2.gsm", result.output)
            self.assertIn("Planter-2.gsm", result.output)
            self.assertIn("文件名：Planter-2.gsm", result.output)
            self.assertTrue((output_root / "Planter-2.gsm").resolve().exists())
            self.assertEqual((output_root / "Planter-2.gsm").read_text(encoding="utf-8"), "compiled")
            self.assertEqual((output_root / "Planter.gsm").read_text(encoding="utf-8"), "existing")

    def test_configure_writes_builtin_provider_key_and_backup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config_path.write_text(
                '[llm]\nmodel = "glm-4-flash"\n\n[llm.provider_keys]\nzhipu = "old-key"\n',
                encoding="utf-8",
            )

            user_input = "claude-opus-4-6\nnew-anthropic-key\nn\ny\n"
            result = self.runner.invoke(
                app,
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
                app,
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

            result = self.runner.invoke(app, ["doctor", "--config", str(config_path)])

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

            with patch("openbrep.config._auto_detect_converter", return_value=None):
                result = self.runner.invoke(app, ["doctor", "--config", str(config_path)])

            self.assertEqual(result.exit_code, 0, msg=result.output)
            self.assertIn("未发现配置问题", result.output)


class TestCliChatImageReferences(unittest.TestCase):

    def _run_chat(self, prompts):
        fake_pipeline = _FakeChatPipeline()
        fake_config = type("Cfg", (), {"llm": type("LLM", (), {"assistant_settings": ""})()})()

        with patch("openbrep.config.GDLAgentConfig.load", return_value=fake_config):
            with patch("openbrep.runtime.pipeline.TaskPipeline", return_value=fake_pipeline):
                with patch("cli.main.typer.prompt", side_effect=prompts):
                    _run_chat_repl(None)
        return fake_pipeline

    def test_chat_pasted_image_path_sets_task_request_image_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            image_path = Path(tmpdir) / "ref.png"
            image_path.write_bytes(b"png")
            pipeline = self._run_chat([f"参考这张图 {image_path}", "exit"])

        self.assertGreaterEqual(len(pipeline.requests), 1)
        self.assertEqual(pipeline.requests[0].image_path, str(image_path.resolve()))
        self.assertEqual(pipeline.requests[0].image_mime, "image/png")

    def test_chat_first_image_prints_img1_alias_notice(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            image_path = Path(tmpdir) / "ref.jpg"
            image_path.write_bytes(b"jpg")
            with patch("cli.main.console.print") as mock_print:
                self._run_chat([f"请参考 {image_path}", "exit"])

        printed = "\n".join(str(call.args[0]) for call in mock_print.call_args_list if call.args)
        self.assertIn("已记录参考图 img1", printed)

    def test_chat_alias_img1_reuses_same_image_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            image_path = Path(tmpdir) / "ref.webp"
            image_path.write_bytes(b"webp")
            pipeline = self._run_chat([f"参考 {image_path}", "把 img1 的底座改厚", "exit"])

        self.assertEqual(pipeline.requests[0].image_path, str(image_path.resolve()))
        self.assertEqual(pipeline.requests[1].image_path, str(image_path.resolve()))

    def test_chat_directory_path_is_rejected_without_upload(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("cli.main.console.print") as mock_print:
                pipeline = self._run_chat([f"参考目录 {tmpdir}", "exit"])

        self.assertIsNone(pipeline.requests[0].image_path)
        printed = "\n".join(str(call.args[0]) for call in mock_print.call_args_list if call.args)
        self.assertIn("请粘贴具体图片文件路径", printed)

    def test_chat_non_image_suffix_is_rejected_without_upload(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            txt_path = Path(tmpdir) / "note.txt"
            txt_path.write_text("x", encoding="utf-8")
            with patch("cli.main.console.print") as mock_print:
                pipeline = self._run_chat([f"参考这个文件 {txt_path}", "exit"])

        self.assertIsNone(pipeline.requests[0].image_path)
        printed = "\n".join(str(call.args[0]) for call in mock_print.call_args_list if call.args)
        self.assertIn("仅支持图片文件", printed)

    def test_chat_plain_text_keeps_previous_behavior(self):
        pipeline = self._run_chat(["做一个书架", "exit"])
        self.assertIsNone(pipeline.requests[0].image_path)


if __name__ == "__main__":
    unittest.main()
