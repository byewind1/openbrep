from pathlib import Path
from types import SimpleNamespace

from openbrep.compiler import CompileResult
from openbrep.config import GDLAgentConfig
from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.workbench.assistant_service import WorkbenchAssistantService
from openbrep.workbench.compiler_service import WorkbenchCompilerService, parse_compile_issue
from openbrep.workbench.git_service import WorkbenchGitService, run_git
from openbrep.revisions import create_revision
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


def test_settings_service_compiler_save_without_paths_preserves_stored_values(tmp_path):
    """缺键 ≠ 清空：只保存 mode 时不得抹掉已存的 converter_path/output_dir。

    2026-08-13 事故：设置面板保存动作带空字段 → 编译器路径被反复清掉。
    """
    config_path = tmp_path / "config.toml"
    config = GDLAgentConfig()
    session = SimpleNamespace(
        compiler_mode="lp",
        converter_path="/Applications/LP_XMLConverter",
        output_dir="/tmp/out",
        config=config,
        config_path=config_path,
    )
    session.config.compiler.mode = "lp"
    session.config.compiler.path = "/Applications/LP_XMLConverter"
    service = WorkbenchSettingsService(session, llm_adapter_factory=lambda _config: None)

    response = service.update_compiler_settings({"mode": "lp"})

    assert response["ok"] is True
    assert session.converter_path == "/Applications/LP_XMLConverter"
    assert session.output_dir == "/tmp/out"
    reloaded = GDLAgentConfig.load(str(config_path))
    assert reloaded.compiler.path == "/Applications/LP_XMLConverter"


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


def test_compiler_service_archives_successful_compile_artifact(tmp_path):
    project = HSFProject.create_new("ArchivedShelf", str(tmp_path))
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
    artifact_path = response["compile"]["artifact_path"]
    assert artifact_path
    archive = Path(artifact_path)
    assert archive.exists()
    assert "artifacts" in artifact_path
    assert "unversioned" in artifact_path


def test_compiler_service_does_not_archive_failed_compile(tmp_path):
    project = HSFProject.create_new("FailedShelf", str(tmp_path))
    hsf_dir = project.save_to_disk()
    session = SimpleNamespace(
        project=project,
        source_path=hsf_dir,
        output_dir="",
        compiler_mode="mock",
        converter_path="",
        last_compile_output_path="",
    )

    class FailingCompiler:
        def hsf2libpart(self, _hsf_path, output_gsm):
            return CompileResult(success=False, output_path=None, mode="mock")

    service = WorkbenchCompilerService(
        session,
        real_compiler_factory=lambda _path: FailingCompiler(),
        mock_compiler_factory=FailingCompiler,
    )

    response = service.compile_project({})

    assert response["ok"] is False
    assert response["compile"]["artifact_path"] is None
    assert not (hsf_dir / "artifacts").exists()


def test_git_service_gitignore_entries_include_artifacts():
    from openbrep.workbench.git_service import GITIGNORE_ENTRIES

    patterns = [pattern for pattern, _comment in GITIGNORE_ENTRIES]
    assert "artifacts/" in patterns


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


def test_preview_quality_accurate_doubles_frustum_tessellation(tmp_path):
    project = HSFProject.create_new("QualityShelf", str(tmp_path))
    project.set_script(ScriptType.SCRIPT_3D, "CYLIND 1, 0.5\n")
    session = SimpleNamespace(project=project)
    service = WorkbenchPreviewService(session)

    fast = service.preview({"quality": "fast"})
    accurate = service.preview({"quality": "accurate"})

    # fast = 24 段圆柱（50 顶点 / 96 面）；accurate = 48 段（98 顶点 / 192 面）
    fast_mesh = fast["preview"]["meshes"][0]
    acc_mesh = accurate["preview"]["meshes"][0]
    assert len(fast_mesh["vertices"]) == 50
    assert len(acc_mesh["vertices"]) == 98
    assert len(acc_mesh["faces"]) == 2 * len(fast_mesh["faces"])


def test_preview_quality_whitelist_falls_back_to_fast():
    from openbrep.workbench.preview_service import normalize_quality, split_preview_request

    assert normalize_quality("accurate") == "accurate"
    assert normalize_quality("fast") == "fast"
    assert normalize_quality("ultra") == "fast"
    assert normalize_quality(None) == "fast"
    assert normalize_quality(42) == "fast"
    assert normalize_quality("") == "fast"

    # split_preview_request 三元素：parameters / scripts / quality
    assert split_preview_request({"quality": "accurate"})[2] == "accurate"
    assert split_preview_request({"quality": "bogus"})[2] == "fast"
    assert split_preview_request(None)[2] == "fast"
    # 裸参数 dict（legacy 形态）同样回退 fast
    assert split_preview_request({"A": 2.0})[2] == "fast"


def test_preview_2d_quality_chain_accepts_quality(tmp_path):
    project = HSFProject.create_new("Quality2D", str(tmp_path))
    project.set_script(ScriptType.SCRIPT_2D, "LINE2 0, 0, A, B\nCIRCLE2 A / 2, B / 2, 0.1\n")
    session = SimpleNamespace(project=project)
    service = WorkbenchPreviewService(session)

    response = service.preview_2d({"quality": "accurate"})

    assert response["ok"] is True
    # create_new 默认 A=B=1.0：quality 透传不改 2D 几何
    assert response["preview"]["lines"] == [{"from": [0.0, 0.0], "to": [1.0, 1.0]}]
    assert response["preview"]["circles"][0]["r"] == 0.1


def test_preview_2d_payload_includes_project2_projected_lines(tmp_path):
    # P3a：preview_2d_payload 把 3D 脚本传进 preview_2d_script，PROJECT2
    # 顶视图投影进入 2D payload（前端零改动，lines 直接可渲染）。
    project = HSFProject.create_new("Project2Shelf", str(tmp_path))
    project.set_script(ScriptType.SCRIPT_2D, "PROJECT2 3, 0, 2\n")
    project.set_script(ScriptType.SCRIPT_3D, "BLOCK 1, 2, 3\n")
    session = SimpleNamespace(project=project)
    service = WorkbenchPreviewService(session)

    response = service.preview_2d({})

    assert response["ok"] is True
    lines = response["preview"]["lines"]
    # BLOCK 1,2,3 顶视图：12 棱边 + 6 三角化面对角线 = 18 条投影线
    assert len(lines) == 18
    assert {"from": [0.0, 0.0], "to": [1.0, 0.0]} in lines
    assert {"from": [0.0, 0.0], "to": [1.0, 2.0]} in lines
    # method 一次性警告走既有 warnings 通道
    assert any("method" in w for w in response["preview"]["warnings"])
    # 3D 脚本覆盖（editor buffer）同样流入投影
    response = service.preview_2d({"scripts": {"3d.gdl": "BLOCK 2, 1, 1\n"}})
    lines = response["preview"]["lines"]
    assert {"from": [0.0, 0.0], "to": [2.0, 0.0]} in lines
    assert all(len(line["from"]) == 2 and len(line["to"]) == 2 for line in lines)


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


def test_git_service_initialize_writes_gitignore_with_all_entries(tmp_path):
    """git init 时写 .gitignore，含全部 5 条托管忽略项及其说明注释。"""
    project = HSFProject.create_new("GitIgnoreShelf", str(tmp_path))
    hsf_dir = project.save_to_disk()
    service = WorkbenchGitService(SimpleNamespace(source_path=hsf_dir, project=project))

    initialized = service.initialize()

    assert initialized["ok"] is True
    gitignore_path = hsf_dir / ".gitignore"
    assert gitignore_path.exists()
    text = gitignore_path.read_text(encoding="utf-8")
    for pattern in (".openbrep/revisions/", ".openbrep/memory/", ".openbrep/latest", "output/", "artifacts/", "*.gsm"):
        assert pattern in text
    # 每条带一行注释：快照=自动检查点、成品走归档区
    assert "自动检查点" in text
    assert "成品归档区" in text
    assert initialized["gitignore"]["added"] == [
        ".openbrep/revisions/", ".openbrep/memory/", ".openbrep/latest", "output/", "artifacts/", "*.gsm",
    ]
    assert initialized["gitignore"]["present"] == []


def test_git_service_gitignore_appends_without_overwriting_user_content(tmp_path):
    """已有自定义 .gitignore 时只追加缺失条目，用户内容原样保留且位于追加内容之前。"""
    project = HSFProject.create_new("GitIgnoreCustomShelf", str(tmp_path))
    hsf_dir = project.save_to_disk()
    custom = "# 用户自定义忽略\ncustom-notes/\n*.log\n"
    (hsf_dir / ".gitignore").write_text(custom, encoding="utf-8")
    service = WorkbenchGitService(SimpleNamespace(source_path=hsf_dir, project=project))

    initialized = service.initialize()

    assert initialized["ok"] is True
    text = (hsf_dir / ".gitignore").read_text(encoding="utf-8")
    assert "# 用户自定义忽略" in text
    assert "custom-notes/" in text
    assert "*.log" in text
    assert text.index("custom-notes/") < text.index(".openbrep/revisions/")
    for pattern in (".openbrep/revisions/", ".openbrep/memory/", ".openbrep/latest", "output/", "artifacts/", "*.gsm"):
        assert pattern in text
    assert text.count("*.gsm") == 1


def test_git_service_initialize_is_idempotent(tmp_path):
    """重复 init：git init 幂等（exit 0），.gitignore 不重复追加、文件字节不变。"""
    project = HSFProject.create_new("GitIgnoreIdemShelf", str(tmp_path))
    hsf_dir = project.save_to_disk()
    service = WorkbenchGitService(SimpleNamespace(source_path=hsf_dir, project=project))

    first = service.initialize()
    text_after_first = (hsf_dir / ".gitignore").read_text(encoding="utf-8")
    second = service.initialize()
    text_after_second = (hsf_dir / ".gitignore").read_text(encoding="utf-8")

    assert first["ok"] is True
    assert second["ok"] is True
    assert second["gitignore"]["added"] == []
    assert second["gitignore"]["present"] == [
        ".openbrep/revisions/", ".openbrep/memory/", ".openbrep/latest", "output/", "artifacts/", "*.gsm",
    ]
    assert text_after_second == text_after_first
    assert text_after_second.count(".openbrep/revisions/") == 1


def test_git_service_initialize_completes_gitignore_on_existing_repo(tmp_path):
    """项目已是 git repo 但没有 .gitignore：initialize 只做 .gitignore 补齐，不报错。"""
    project = HSFProject.create_new("GitExistingRepoShelf", str(tmp_path))
    hsf_dir = project.save_to_disk()
    assert run_git(hsf_dir, ["init"]).returncode == 0
    assert not (hsf_dir / ".gitignore").exists()
    service = WorkbenchGitService(SimpleNamespace(source_path=hsf_dir, project=project))

    initialized = service.initialize()

    assert initialized["ok"] is True
    text = (hsf_dir / ".gitignore").read_text(encoding="utf-8")
    for pattern in (".openbrep/revisions/", ".openbrep/memory/", ".openbrep/latest", "output/", "artifacts/", "*.gsm"):
        assert pattern in text


def test_git_service_commit_excludes_revisions_memory_and_artifacts(tmp_path):
    """有了 .gitignore 后 add -A 自然干净：快照/memory/编译成品不进 git 历史。"""
    project = HSFProject.create_new("GitCleanShelf", str(tmp_path))
    hsf_dir = project.save_to_disk()
    # 制造旧行为会污染的三种内容：自动快照、项目记忆、编译成品
    create_revision(hsf_dir, message="checkpoint")
    memory_dir = hsf_dir / ".openbrep" / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / "decisions.md").write_text("## decision\n", encoding="utf-8")
    (hsf_dir / "output").mkdir()
    (hsf_dir / "output" / "GitCleanShelf.gsm").write_bytes(b"MOCK-GSM")
    service = WorkbenchGitService(SimpleNamespace(source_path=hsf_dir, project=project))

    service.initialize()
    committed = service.commit({"message": "source only"})

    assert committed["ok"] is True
    assert committed["git"]["last_commit"]
    tracked = run_git(hsf_dir, ["ls-files"]).stdout.splitlines()
    assert not any(".openbrep/revisions/" in line for line in tracked)
    assert not any(".openbrep/memory/" in line for line in tracked)
    assert not any("output/" in line for line in tracked)
    assert not any(line.endswith(".gsm") for line in tracked)
    # 源码本体正常入库
    assert any(line.endswith("paramlist.xml") for line in tracked)
    assert any(line.endswith("scripts/3d.gdl") for line in tracked)


def test_update_llm_model_only_custom_provider_resolves_without_top_level_pollution(tmp_path, monkeypatch):
    """切到自定义 provider：session 拿解析后的 key/base，但顶层兜底字段不被污染。

    顶层 api_key/api_base 是全局兜底，provider_keys / custom_providers 才是按模型
    的凭据表；把解析结果写回顶层会让旧模型的 key 通过兜底"继承"给下一个模型。
    """
    _clear_llm_env_keys(monkeypatch)
    config_path = tmp_path / "config.toml"
    config = GDLAgentConfig()
    config.llm.model = "deepseek-chat"
    config.llm.api_key = "dk-top"
    config.llm.custom_providers = [{
        "name": "qwen",
        "protocol": "openai",
        "base_url": "https://token-plan.example.com/compatible-mode/v1",
        "api_key": "sk-qwen-key",
        "models": ["qwen3.8-max-preview"],
    }]
    session = _make_settings_session(config, config_path)
    service = WorkbenchSettingsService(session, llm_adapter_factory=lambda _config: None)

    response = service.update_llm_model_only({"model": "qwen3.8-max-preview"})

    assert response["ok"] is True
    assert session.llm_api_key == "sk-qwen-key"
    assert session.llm_api_base == "https://token-plan.example.com/compatible-mode/v1"
    assert response["llm"]["api_key"] == "sk-qwen-key"

    reloaded = GDLAgentConfig.load(str(config_path))
    assert reloaded.llm.model == "qwen3.8-max-preview"
    assert reloaded.llm.api_key == "dk-top"  # 顶层兜底不被切换改写
    assert reloaded.llm.custom_providers[0]["api_key"] == "sk-qwen-key"
    assert reloaded.llm.custom_providers[0]["base_url"] == "https://token-plan.example.com/compatible-mode/v1"


def test_settings_service_update_api_key_saves_unified_providers_format(tmp_path, monkeypatch):
    """保存即迁移：UI 存 key 后落盘为 [[llm.providers]] 新键名，不再有 custom_providers。"""
    _clear_llm_env_keys(monkeypatch)
    config_path = tmp_path / "config.toml"
    config = GDLAgentConfig()
    config.llm.model = "deepseek-v4-flash"
    config.llm.custom_providers = [{
        "name": "opencode-go",
        "base_url": "https://opencode.ai/zen/go/v1",
        "api_key": "old-key",
        "models": ["deepseek-v4-flash"],
        "protocol": "openai",
    }]
    session = _make_settings_session(config, config_path)
    service = WorkbenchSettingsService(session, llm_adapter_factory=lambda _config: None)

    response = service.update_llm_api_key({"model": "deepseek-v4-flash", "api_key": "new-key"})
    assert response["ok"] is True

    saved_text = config_path.read_text(encoding="utf-8")
    assert "[[llm.providers]]" in saved_text
    assert "custom_providers" not in saved_text
    assert 'api = "https://opencode.ai/zen/go/v1"' in saved_text
    assert 'api_mode = "chat_completions"' in saved_text

    reloaded = GDLAgentConfig.load(str(config_path))
    assert reloaded.llm.custom_providers[0]["api_key"] == "new-key"
    assert reloaded.llm.resolve_api_key() == "new-key"
