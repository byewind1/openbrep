from pathlib import Path
from types import SimpleNamespace

from openbrep.compiler import CompileResult
from openbrep.config import GDLAgentConfig
from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.workbench.assistant_service import WorkbenchAssistantService
from openbrep.workbench.compiler_service import WorkbenchCompilerService, parse_compile_issue
from openbrep.workbench.git_service import WorkbenchGitService
from openbrep.workbench.memory_service import WorkbenchMemoryService
from openbrep.workbench.preview_service import WorkbenchPreviewService
from openbrep.workbench.project_parameter_service import WorkbenchProjectParameterService
from openbrep.workbench.project_script_service import WorkbenchProjectScriptService
from openbrep.workbench.project_service import WorkbenchProjectService
from openbrep.workbench import settings_service
from openbrep.workbench.settings_service import WorkbenchSettingsService
from openbrep.workbench.tapir_service import WorkbenchTapirService


def test_settings_service_updates_compiler_settings_and_persists_config(tmp_path):
    config_path = tmp_path / "config.toml"
    config = GDLAgentConfig()
    session = SimpleNamespace(
        compiler_mode="mock",
        converter_path="",
        output_dir="",
        config=config,
        config_path=config_path,
    )
    service = WorkbenchSettingsService(session, llm_adapter_factory=lambda _config: None)

    response = service.update_compiler_settings({
        "mode": "lp",
        "converter_path": "/Applications/LP_XMLConverter",
        "output_dir": str(tmp_path / "out"),
    })

    reloaded = GDLAgentConfig.load(str(config_path))
    assert response["ok"] is True
    assert response["compiler"]["mode"] == "lp"
    assert reloaded.compiler.mode == "lp"
    assert reloaded.compiler.path == "/Applications/LP_XMLConverter"
    assert reloaded.output_dir == str(tmp_path / "out")


def test_settings_service_model_switch_resolves_api_key_for_new_model(tmp_path):
    config_path = tmp_path / "config.toml"
    config = GDLAgentConfig()
    config.llm.model = "deepseek-chat"
    config.llm.provider_keys = {"deepseek": "dk-old", "zhipu": "zk-new"}
    session = SimpleNamespace(
        llm_model="deepseek-chat",
        llm_api_key="dk-old",
        llm_api_base="",
        assistant_settings="",
        max_retries=5,
        config=config,
        config_path=config_path,
    )
    service = WorkbenchSettingsService(session, llm_adapter_factory=lambda _config: None)

    response = service.update_llm_model_only({"model": "glm-4-flash"})

    assert response["ok"] is True
    assert session.llm_model == "glm-4-flash"
    assert session.llm_api_key == "zk-new"
    assert response["llm"]["model"] == "glm-4-flash"
    assert response["llm"]["api_key"] == "zk-new"
    reloaded = GDLAgentConfig.load(str(config_path))
    assert reloaded.llm.model == "glm-4-flash"


def _clear_llm_env_keys(monkeypatch):
    for name in ["ZHIPU_API_KEY", "ZAI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY"]:
        monkeypatch.delenv(name, raising=False)


def _make_settings_session(config, config_path):
    return SimpleNamespace(
        llm_model=config.llm.model,
        llm_api_key=config.llm.resolve_api_key() or "",
        llm_api_base=config.llm.resolve_api_base() or "",
        assistant_settings="",
        max_retries=5,
        config=config,
        config_path=config_path,
    )


def test_settings_service_model_switch_reports_availability(tmp_path, monkeypatch):
    _clear_llm_env_keys(monkeypatch)
    config_path = tmp_path / "config.toml"
    config = GDLAgentConfig()
    config.llm.model = "deepseek-chat"
    config.llm.provider_keys = {"deepseek": "dk-old"}
    session = _make_settings_session(config, config_path)
    service = WorkbenchSettingsService(session, llm_adapter_factory=lambda _config: None)

    available = service.update_llm_model_only({"model": "deepseek-chat"})
    assert available["ok"] is True
    assert available["llm"]["model_available"] is True

    unavailable = service.update_llm_model_only({"model": "glm-4-flash"})
    assert unavailable["ok"] is True
    assert unavailable["llm"]["model"] == "glm-4-flash"
    assert unavailable["llm"]["model_available"] is False
    # 写回设置文件后，重新加载仍能看到新模型
    reloaded = GDLAgentConfig.load(str(config_path))
    assert reloaded.llm.model == "glm-4-flash"


def test_settings_service_ollama_model_available_without_api_key(tmp_path, monkeypatch):
    _clear_llm_env_keys(monkeypatch)
    config_path = tmp_path / "config.toml"
    config = GDLAgentConfig()
    config.llm.model = "glm-4-flash"
    session = _make_settings_session(config, config_path)
    service = WorkbenchSettingsService(session, llm_adapter_factory=lambda _config: None)

    response = service.update_llm_model_only({"model": "ollama/qwen3:8b"})
    assert response["ok"] is True
    assert response["llm"]["model_available"] is True


def test_settings_service_update_api_key_official_model(tmp_path, monkeypatch):
    _clear_llm_env_keys(monkeypatch)
    config_path = tmp_path / "config.toml"
    config = GDLAgentConfig()
    config.llm.model = "glm-4-flash"
    session = _make_settings_session(config, config_path)
    service = WorkbenchSettingsService(session, llm_adapter_factory=lambda _config: None)

    assert service.llm_settings()["model_available"] is False
    response = service.update_llm_api_key({"model": "glm-4-flash", "api_key": "zk-123"})
    assert response["ok"] is True
    assert response["llm"]["model_available"] is True
    assert session.llm_api_key == "zk-123"
    reloaded = GDLAgentConfig.load(str(config_path))
    assert reloaded.llm.provider_keys["zhipu"] == "zk-123"


def test_settings_service_update_api_key_custom_provider(tmp_path, monkeypatch):
    _clear_llm_env_keys(monkeypatch)
    config_path = tmp_path / "config.toml"
    config = GDLAgentConfig()
    config.llm.model = "gpt-5.5"
    config.llm.custom_providers = [{
        "name": "ymg",
        "protocol": "openai",
        "base_url": "https://proxy.example.com/v1",
        "api_key": "old-key",
        "models": ["gpt-5.5"],
    }]
    session = _make_settings_session(config, config_path)
    service = WorkbenchSettingsService(session, llm_adapter_factory=lambda _config: None)

    response = service.update_llm_api_key({"model": "gpt-5.5", "api_key": "new-key"})
    assert response["ok"] is True
    assert session.llm_api_key == "new-key"
    reloaded = GDLAgentConfig.load(str(config_path))
    assert reloaded.llm.custom_providers[0]["api_key"] == "new-key"
    assert "openai" not in reloaded.llm.provider_keys


def test_settings_service_update_api_key_rejects_bad_input(tmp_path, monkeypatch):
    _clear_llm_env_keys(monkeypatch)
    config_path = tmp_path / "config.toml"
    config = GDLAgentConfig()
    config.llm.model = "glm-4-flash"
    session = _make_settings_session(config, config_path)
    service = WorkbenchSettingsService(session, llm_adapter_factory=lambda _config: None)

    assert service.update_llm_api_key({"model": "glm-4-flash", "api_key": ""})["ok"] is False
    assert service.update_llm_api_key({"model": "totally-unknown-model-x", "api_key": "k"})["ok"] is False
    assert not config_path.exists()


def test_settings_service_connection_test_preserves_custom_provider_credentials(tmp_path, monkeypatch):
    _clear_llm_env_keys(monkeypatch)
    config_path = tmp_path / "config.toml"
    config = GDLAgentConfig()
    config.llm.model = "gpt-5.5"
    config.llm.custom_providers = [{
        "name": "ymg",
        "protocol": "openai",
        "base_url": "https://proxy.example.com/v1",
        "api_key": "stored-key",
        "models": ["gpt-5.5"],
    }]
    session = _make_settings_session(config, config_path)
    seen: dict[str, str] = {}

    class _FakeAdapter:
        def generate(self, *_args, **_kwargs):
            return SimpleNamespace(model="gpt-5.5")

    def factory(llm_config):
        seen["api_key"] = llm_config.api_key
        seen["api_base"] = llm_config.resolve_api_base("gpt-5.5")
        return _FakeAdapter()

    service = WorkbenchSettingsService(session, llm_adapter_factory=factory)
    response = service.test_llm_settings({"model": "gpt-5.5"})

    # 连接测试不带凭据调用时必须沿用 custom provider 已保存的 key/base，不能抹掉
    assert response["ok"] is True
    assert seen["api_key"] == "stored-key"
    assert seen["api_base"] == "https://proxy.example.com/v1"
    assert config.llm.custom_providers[0]["api_key"] == "stored-key"
    assert config.llm.custom_providers[0]["base_url"] == "https://proxy.example.com/v1"


def test_settings_service_connection_test_failure_returns_full_detail(tmp_path, monkeypatch):
    _clear_llm_env_keys(monkeypatch)
    config_path = tmp_path / "config.toml"
    config = GDLAgentConfig()
    config.llm.model = "deepseek-chat"
    session = _make_settings_session(config, config_path)

    server_body = '{"error":{"message":"Incorrect API key provided","type":"invalid_request_error","code":"invalid_api_key"}}'

    class _FakeAdapter:
        def generate(self, *_args, **_kwargs):
            root = RuntimeError("AuthenticationError - 401")
            root.response = SimpleNamespace(status_code=401, text=server_body)
            raise RuntimeError("LLM 认证失败：API Key 可能无效") from root

    service = WorkbenchSettingsService(session, llm_adapter_factory=lambda _config: _FakeAdapter())
    response = service.test_llm_settings({"model": "deepseek-chat"})

    assert response["ok"] is False
    # error 保留摘要，detail 串起异常链 + 服务器响应体原文，全量不截断
    assert response["error"] == "LLM 认证失败：API Key 可能无效"
    assert "AuthenticationError - 401" in response["detail"]
    assert "HTTP 401 响应原文" in response["detail"]
    assert server_body in response["detail"]


def test_format_llm_exception_detail_handles_plain_exception():
    from openbrep.workbench.settings_service import format_llm_exception_detail

    detail = format_llm_exception_detail(ValueError("bad input"))
    assert detail == "ValueError: bad input"


def test_settings_service_config_revision_changes_when_file_is_edited(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text("[llm]\nmodel = \"deepseek-chat\"\n", encoding="utf-8")
    session = SimpleNamespace(config_path=config_path)
    service = WorkbenchSettingsService(session, llm_adapter_factory=lambda _config: None)

    first = service.config_revision()
    assert first["ok"] is True
    assert first["revision"] != "missing"

    # Same file, no write -> stable revision.
    assert service.config_revision()["revision"] == first["revision"]

    config_path.write_text("[llm]\nmodel = \"glm-4-flash\"\n", encoding="utf-8")
    second = service.config_revision()
    assert second["revision"] != first["revision"]


def test_settings_service_config_revision_reports_missing_file(tmp_path):
    session = SimpleNamespace(config_path=tmp_path / "does-not-exist.toml")
    service = WorkbenchSettingsService(session, llm_adapter_factory=lambda _config: None)

    assert service.config_revision() == {"ok": True, "revision": "missing"}


def test_workbench_config_path_defaults_to_main_worktree_config(monkeypatch, tmp_path):
    main_root = tmp_path / "repo"
    worktree_root = main_root / ".worktrees" / "react-workbench"
    git_dir = main_root / ".git"
    worktree_root.mkdir(parents=True)
    git_dir.mkdir()
    main_config = main_root / "config.toml"
    main_config.write_text("[llm]\nmodel = \"mimo-v2.5-pro\"\n", encoding="utf-8")
    (worktree_root / "config.toml").write_text("[llm]\nmodel = \"deepseek-chat\"\n", encoding="utf-8")

    monkeypatch.chdir(worktree_root)
    monkeypatch.delenv("GDL_AGENT_CONFIG", raising=False)
    monkeypatch.setattr(
        settings_service.subprocess,
        "check_output",
        lambda *args, **kwargs: str(git_dir),
    )

    assert settings_service.resolve_workbench_config_path() == main_config


def test_compiler_service_compiles_loaded_project_with_injected_mock_compiler(tmp_path):
    project = HSFProject.create_new("ServiceShelf", str(tmp_path))
    hsf_dir = project.save_to_disk()
    session = SimpleNamespace(
        project=project,
        source_path=hsf_dir,
        output_dir="",
        compiler_mode="mock",
        converter_path="",
        last_compile_output_path="",
    )

    class FakeCompiler:
        def hsf2libpart(self, _hsf_path, output_gsm):
            Path(output_gsm).parent.mkdir(parents=True, exist_ok=True)
            Path(output_gsm).write_bytes(b"gsm")
            return CompileResult(success=True, output_path=output_gsm, mode="mock")

    service = WorkbenchCompilerService(
        session,
        real_compiler_factory=lambda _path: FakeCompiler(),
        mock_compiler_factory=FakeCompiler,
    )

    response = service.compile_project({})

    assert response["ok"] is True
    assert response["compile"]["output_path"].endswith("ServiceShelf.gsm")
    assert response["compile"]["gsm_size_bytes"] == 3
    assert session.last_compile_output_path == response["compile"]["output_path"]


def test_compiler_service_parses_lp_compile_issue_locations():
    script, line, message = parse_compile_issue("3d.gdl line 12: missing ENDIF")

    assert script == "scripts/3d.gdl"
    assert line == 12
    assert message == "missing ENDIF"


def test_project_service_loads_hsf_directory_and_updates_session(tmp_path):
    project = HSFProject.create_new("ServiceLoadedShelf", str(tmp_path))
    hsf_dir = project.save_to_disk()
    session = SimpleNamespace(
        project=None,
        source="demo",
        source_path=None,
        recent_project_paths=[],
        config=GDLAgentConfig(),
        config_path=tmp_path / "config.toml",
        snapshot=lambda: {"project": {"name": "ServiceLoadedShelf"}},
    )
    service = WorkbenchProjectService(session, real_compiler_factory=lambda _path: None)

    response = service.load_hsf_directory(str(hsf_dir))

    assert response["ok"] is True
    assert session.source == "hsf"
    assert session.source_path == hsf_dir.resolve()
    assert session.recent_project_paths == [str(hsf_dir.resolve())]


def test_project_script_service_reads_memory_script_content(tmp_path):
    project = HSFProject.create_new("ScriptShelf", str(tmp_path))
    project.set_script(ScriptType.SCRIPT_3D, "BLOCK A, B, ZZYZX\n")
    project.save_to_disk()
    service = WorkbenchProjectScriptService(SimpleNamespace(project=project))

    response = service.get_project_script("3d.gdl")

    assert response["ok"] is True
    assert response["path"] == "scripts/3d.gdl"
    assert response["content"] == "BLOCK A, B, ZZYZX\n"


def test_project_parameter_service_applies_values_and_snapshots(tmp_path):
    project = HSFProject.create_new("ParamShelf", str(tmp_path))
    project.save_to_disk()
    session = SimpleNamespace(
        project=project,
        source_path=project.root,
        snapshot=lambda: {"project": {"name": "ParamShelf"}},
    )
    service = WorkbenchProjectParameterService(session)

    response = service.apply({"A": 2.5})

    assert response["ok"] is True
    assert response["changed"] == {"A": 2.5}
    assert project.get_parameter("A").value == "2.5"


def test_preview_service_returns_3d_payload_for_project(tmp_path):
    project = HSFProject.create_new("PreviewShelf", str(tmp_path))
    project.set_script(ScriptType.SCRIPT_3D, "BLOCK A, B, ZZYZX\n")
    session = SimpleNamespace(project=project)
    service = WorkbenchPreviewService(session)

    response = service.preview({})

    assert response["ok"] is True
    assert response["preview"]["meshes"]


def test_preview_service_can_verify_dirty_editor_buffer_without_saving(tmp_path):
    project = HSFProject.create_new("DirtyPreviewShelf", str(tmp_path))
    project.set_script(ScriptType.SCRIPT_3D, "BLOCK 1, 1, 1\n")
    session = SimpleNamespace(project=project)
    service = WorkbenchPreviewService(session)

    response = service.preview({
        "scripts": {
            "3d.gdl": "BLOCK 2, 1, 1\n",
        },
    })

    assert response["ok"] is True
    assert response["preview"]["verification"] == {
        "source": "editor_buffer",
        "script_overrides": ["3d.gdl"],
    }
    assert project.get_script(ScriptType.SCRIPT_3D) == "BLOCK 1, 1, 1\n"


def test_assistant_service_extracts_classified_code_blocks():
    service = WorkbenchAssistantService(SimpleNamespace())

    response = service.extract_assistant_code_blocks({
        "content": "```gdl\n! scripts/3d.gdl\nBLOCK A, B, ZZYZX\n```",
    })

    assert response["ok"] is True
    assert response["blocks"][0]["script_name"] == "3d.gdl"
    assert "BLOCK A" in response["blocks"][0]["content"]


def test_memory_service_reports_empty_status_without_loaded_project():
    service = WorkbenchMemoryService(SimpleNamespace(source_path=None))

    response = service.memory_status()

    assert response["ok"] is True
    assert response["memory"]["memory_root"] == ""
    assert response["memory"]["lesson_count"] == 0


def test_tapir_service_normalizes_missing_parameter_edits():
    calls = []

    class FakeAdapter:
        def apply_param_edits(self, edits):
            calls.append(edits)
            return {"ok": True}

    service = WorkbenchTapirService(FakeAdapter())

    response = service.apply_param_edits({"param_edits": []})

    assert response == {"ok": True}
    assert calls == [None]


def test_git_service_initializes_enables_and_commits_hsf_project(tmp_path):
    project = HSFProject.create_new("GitShelf", str(tmp_path))
    hsf_dir = project.save_to_disk()
    service = WorkbenchGitService(SimpleNamespace(source_path=hsf_dir, project=project))

    initialized = service.initialize()
    status = service.status()

    assert initialized["ok"] is True
    assert status["git"]["enabled"] is True
    assert status["git"]["initialized"] is True
    assert (hsf_dir / ".git").is_dir()

    committed = service.commit({"message": "Initial HSF source"})

    assert committed["ok"] is True
    assert committed["git"]["last_commit"]
    assert committed["git"]["dirty"] is False


def test_git_service_respects_project_level_enabled_switch(tmp_path):
    project = HSFProject.create_new("GitSwitchShelf", str(tmp_path))
    hsf_dir = project.save_to_disk()
    service = WorkbenchGitService(SimpleNamespace(source_path=hsf_dir, project=project))

    service.initialize()
    disabled = service.set_enabled({"enabled": False})
    committed = service.commit({"message": "Should not commit"})

    assert disabled["git"]["enabled"] is False
    assert committed["ok"] is False
    assert "Enable Git" in committed["error"]
