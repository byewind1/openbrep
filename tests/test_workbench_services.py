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
from openbrep.codex.provider import CodexNotSignedInError
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


# ── Codex BYOA（D1）：登录/状态/动态模型/显式保存 ──────────────────────────
# 环境清理清单（P0-5：不得跨测试模块导入私有常量，本地定义最小清单）

_CODEX_TEST_ENV_VARS = (
    "ZHIPU_API_KEY", "ZAI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
    "DEEPSEEK_API_KEY", "GEMINI_API_KEY", "DASHSCOPE_API_KEY", "MOONSHOT_API_KEY",
)


def _clear_codex_test_env(monkeypatch):
    for name in _CODEX_TEST_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


class _FakeCodexProvider:
    """脚本化 CodexProvider 替身：服务层只依赖 status/login*/logout/models/rate_limits/restart。"""

    def __init__(self, status=None, models=None, rate_limits=None):
        self.status_result = status or {
            "state": "signed_out", "connected": False, "codex_available": True, "account": None,
        }
        self.models_result = models
        self.rate_limits_result = rate_limits
        self.login_calls = 0
        self.logout_calls = 0
        self.status_calls = 0
        self.model_calls = 0
        self.device_code_calls = 0
        self.cancel_calls = 0
        self.rate_limits_calls = 0
        self.restart_calls = 0

    def status(self, *, refresh=False):
        self.status_calls += 1
        return dict(self.status_result)

    def login_start(self):
        self.login_calls += 1
        return {"state": "login_started", "method": "chatgpt"}

    def login_start_device_code(self):
        self.device_code_calls += 1
        return {
            "state": "login_started",
            "method": "chatgptDeviceCode",
            "verification_url": "https://example.test/device",
            "user_code": "ABCD-EFGH",
        }

    def login_cancel(self):
        self.cancel_calls += 1
        return {"state": "signed_out"}

    def logout(self):
        self.logout_calls += 1
        return {"state": "signed_out"}

    def restart(self):
        self.restart_calls += 1
        return dict(self.status_result)

    def rate_limits(self, *, refresh=False):
        self.rate_limits_calls += 1
        if self.rate_limits_result is None:
            raise CodexNotSignedInError("尚未连接 ChatGPT，无法读取订阅额度。请先登录。")
        return dict(self.rate_limits_result)

    def models(self, *, refresh=False):
        self.model_calls += 1
        if self.models_result is None:
            raise CodexNotSignedInError("尚未连接 ChatGPT。请先登录。")
        return self.models_result


def _make_codex_service(config, config_path, provider=None, factory=None):
    session = SimpleNamespace(
        llm_model=config.llm.model,
        llm_api_key=config.llm.resolve_api_key() or "",
        llm_api_base=config.llm.resolve_api_base() or "",
        assistant_settings="",
        max_retries=5,
        config=config,
        config_path=config_path,
    )
    return WorkbenchSettingsService(
        session,
        llm_adapter_factory=lambda _c: None,
        codex_provider=provider,
        codex_provider_factory=factory,
    )


def _codex_models_payload():
    return [
        {
            "id": "openai-codex/gpt-5.6-luna",
            "label": "GPT-5.6 Luna",
            "model": "gpt-5.6-luna",
            "display_name": "GPT-5.6 Luna",
            "hidden": False,
            "specialty": None,
            # D6：effort 目录（只来自 model/list.supportedReasoningEfforts）
            "supported_reasoning_efforts": [
                {"effort": "low", "description": "Fastest"},
                {"effort": "medium", "description": "Balanced"},
                {"effort": "high", "description": "Deep"},
            ],
            "default_reasoning_effort": "medium",
        },
        {
            "id": "openai-codex/gpt-5.6-terra",
            "label": "GPT-5.6 Terra",
            "model": "gpt-5.6-terra",
            "display_name": "GPT-5.6 Terra",
            "hidden": False,
            "specialty": "balanced",
            # terra 不支持 low（low 是 luna 独有——用于残留拒绝测试）
            "supported_reasoning_efforts": [
                {"effort": "medium", "description": "Balanced"},
                {"effort": "high", "description": "Deep"},
            ],
            "default_reasoning_effort": "high",
        },
    ]


def _signed_in_status():
    return {
        "state": "signed_in", "connected": True, "codex_available": True,
        "account": {"email_masked": "jo***@example.com", "plan_type": "pro"},
    }


def test_codex_status_signed_out(tmp_path):
    config = GDLAgentConfig()
    provider = _FakeCodexProvider(status={
        "state": "signed_out", "connected": False, "codex_available": True, "account": None,
    })
    service = _make_codex_service(config, tmp_path / "config.toml", provider)

    response = service.codex_status()
    assert response["ok"] is True
    assert response["state"] == "signed_out"
    assert response["connected"] is False
    assert response["account"] is None


def test_codex_status_signed_in_masks_account(tmp_path):
    config = GDLAgentConfig()
    provider = _FakeCodexProvider(status={
        "state": "signed_in", "connected": True, "codex_available": True,
        "account": {"email_masked": "jo***@example.com", "plan_type": "pro"},
    })
    service = _make_codex_service(config, tmp_path / "config.toml", provider)

    response = service.codex_status()
    assert response["state"] == "signed_in"
    assert response["account"]["email_masked"] == "jo***@example.com"
    assert "email" not in response["account"]


def test_codex_login_start_triggers_browser_flow_only(tmp_path):
    config = GDLAgentConfig()
    provider = _FakeCodexProvider()
    session = _make_codex_service(config, tmp_path / "config.toml", provider)

    response = session.codex_login_start()
    assert response == {"ok": True, "state": "login_started", "method": "chatgpt"}
    assert provider.login_calls == 1
    # 登录动作绝不携带/返回任何凭据或 authUrl
    assert not any(k in response for k in ("authUrl", "loginId", "token", "jwt"))


def test_codex_logout(tmp_path):
    config = GDLAgentConfig()
    provider = _FakeCodexProvider()
    service = _make_codex_service(config, tmp_path / "config.toml", provider)

    response = service.codex_logout()
    assert response == {"ok": True, "state": "signed_out"}
    assert provider.logout_calls == 1


def test_codex_models_returned_only_when_signed_in(tmp_path):
    config = GDLAgentConfig()
    provider = _FakeCodexProvider(models=_codex_models_payload())
    service = _make_codex_service(config, tmp_path / "config.toml", provider)

    response = service.codex_models()
    assert response["ok"] is True
    assert [m["id"] for m in response["models"]] == [
        "openai-codex/gpt-5.6-luna", "openai-codex/gpt-5.6-terra",
    ]


def test_codex_models_fail_closed_when_signed_out(tmp_path):
    config = GDLAgentConfig()
    provider = _FakeCodexProvider(models=None)  # 未登录
    service = _make_codex_service(config, tmp_path / "config.toml", provider)

    response = service.codex_models()
    assert response["ok"] is False
    assert response["code"] == "not_signed_in"


def test_llm_settings_codex_block_and_availability(tmp_path, monkeypatch):
    _clear_codex_test_env(monkeypatch)
    config = GDLAgentConfig()
    config.llm.model = "openai-codex/gpt-5.6-luna"
    provider = _FakeCodexProvider(status=_signed_in_status(), models=_codex_models_payload())
    service = _make_codex_service(config, tmp_path / "config.toml", provider)

    llm = service.llm_settings()
    assert llm["model_available"] is True
    assert llm["codex"]["state"] == "signed_in"
    assert llm["codex"]["connected"] is True
    assert llm["codex"]["account"]["email_masked"] == "jo***@example.com"
    # llm_settings 只反映已存在的 provider，不隐式拉起 app-server
    # （known_status 一次 + 目录可用性检查一次，共 2 次；无 factory → 不新建）
    assert provider.status_calls == 2


def test_llm_settings_codex_unavailable_after_logout(tmp_path, monkeypatch):
    _clear_codex_test_env(monkeypatch)
    config = GDLAgentConfig()
    config.llm.model = "openai-codex/gpt-5.6-luna"
    provider = _FakeCodexProvider(status={
        "state": "signed_out", "connected": False, "codex_available": True, "account": None,
    })
    service = _make_codex_service(config, tmp_path / "config.toml", provider)

    llm = service.llm_settings()
    assert llm["model_available"] is False
    assert llm["codex"]["state"] == "signed_out"


def test_llm_settings_codex_fail_closed_without_provider(tmp_path, monkeypatch):
    _clear_codex_test_env(monkeypatch)
    config = GDLAgentConfig()
    config.llm.model = "openai-codex/gpt-5.6-luna"
    session = SimpleNamespace(
        llm_model=config.llm.model,
        llm_api_key="",
        llm_api_base="",
        assistant_settings="",
        max_retries=5,
        config=config,
        config_path=tmp_path / "config.toml",
        # 没有 codex_provider / codex_provider_factory：不隐式拉起，fail closed
    )
    service = WorkbenchSettingsService(session, llm_adapter_factory=lambda _c: None)

    llm = service.llm_settings()
    assert llm["model_available"] is False
    assert llm["codex"] is None


def test_update_llm_model_only_codex_ensures_provider_entry(tmp_path):
    config = GDLAgentConfig()
    config.llm.model = "glm-4-flash"
    provider = _FakeCodexProvider(status=_signed_in_status(), models=_codex_models_payload())
    service = _make_codex_service(config, tmp_path / "config.toml", provider)

    response = service.update_llm_model_only({"model": "openai-codex/gpt-5.6-luna"})
    assert response["ok"] is True
    assert response["llm"]["model"] == "openai-codex/gpt-5.6-luna"
    reloaded = GDLAgentConfig.load(str(tmp_path / "config.toml"))
    entry = reloaded.llm.providers[0]
    assert entry["name"] == "openai-codex"
    assert entry["api_mode"] == "codex_app_server"
    assert reloaded.llm.model == "openai-codex/gpt-5.6-luna"


def test_update_llm_model_only_codex_rejects_model_not_in_catalog(tmp_path):
    """P0-4：只允许保存当前账户 model/list 目录中的模型。"""
    config = GDLAgentConfig()
    config.llm.model = "glm-4-flash"
    provider = _FakeCodexProvider(status=_signed_in_status(), models=_codex_models_payload())
    service = _make_codex_service(config, tmp_path / "config.toml", provider)

    response = service.update_llm_model_only({"model": "openai-codex/not-a-real-account-model"})
    assert response["ok"] is False
    assert response["code"] == "model_not_in_catalog"
    # 未保存：config 仍是旧模型
    reloaded = GDLAgentConfig.load(str(tmp_path / "config.toml"))
    assert reloaded.llm.model == "glm-4-flash"


def test_llm_settings_codex_available_false_when_model_dropped_from_catalog(tmp_path, monkeypatch):
    """P0-4：目录漂移/下架后 model_available 必须为 false。"""
    _clear_codex_test_env(monkeypatch)
    config = GDLAgentConfig()
    config.llm.model = "openai-codex/gpt-5.6-terra"
    # 目录只剩 luna：terra 已被下架
    provider = _FakeCodexProvider(
        status=_signed_in_status(),
        models=[m for m in _codex_models_payload() if m["id"].endswith("gpt-5.6-luna")],
    )
    service = _make_codex_service(config, tmp_path / "config.toml", provider)

    llm = service.llm_settings()
    assert llm["codex"]["state"] == "signed_in"
    assert llm["model_available"] is False


def test_update_llm_api_key_rejects_codex_model(tmp_path):
    """P0-1：openai-codex 订阅身份拒绝通用 API-key 写入，config 不落盘。"""
    config = GDLAgentConfig()
    config.llm.model = "openai-codex/gpt-5.6-luna"
    provider = _FakeCodexProvider(status=_signed_in_status(), models=_codex_models_payload())
    service = _make_codex_service(config, tmp_path / "config.toml", provider)
    service._codex_provider()  # 确保 provider 存在

    response = service.update_llm_api_key({"model": "openai-codex/gpt-5.6-luna", "api_key": "DEV-SECRET"})
    assert response["ok"] is False
    assert response["code"] == "codex_no_api_key"
    # 拒绝后 config 没有任何 key（未落盘，也未被写入 provider 条目）
    assert "DEV-SECRET" not in config.to_toml_string()
    assert not any(
        str(p.get("api_key", "") or "") == "DEV-SECRET"
        for p in config.llm.providers
    )


def test_update_llm_settings_rejects_codex_api_key(tmp_path):
    """P0-1：update_llm_settings 带 codex 模型 + api_key 同样拒绝。"""
    config = GDLAgentConfig()
    config.llm.model = "glm-4-flash"
    provider = _FakeCodexProvider(status=_signed_in_status(), models=_codex_models_payload())
    session = SimpleNamespace(
        llm_model="openai-codex/gpt-5.6-luna",
        llm_api_key="",
        llm_api_base="",
        assistant_settings="",
        max_retries=5,
        config=config,
        config_path=tmp_path / "config.toml",
    )
    service = WorkbenchSettingsService(session, llm_adapter_factory=lambda _c: None, codex_provider=provider)

    response = service.update_llm_settings({
        "model": "openai-codex/gpt-5.6-luna", "api_key": "DEV-SECRET",
    })
    assert response["ok"] is False
    assert response["code"] == "codex_no_api_key"
    assert "DEV-SECRET" not in config.to_toml_string()


# ── D6：Fixed 模式 effort 保存/校验（draft + 显式 Save；不静默替换）────────


def test_update_llm_model_only_codex_saves_supported_effort(tmp_path):
    """保存 model + 受支持 effort：两者一起落盘（显式 Save）。"""
    config = GDLAgentConfig()
    config.llm.model = "glm-4-flash"
    provider = _FakeCodexProvider(status=_signed_in_status(), models=_codex_models_payload())
    service = _make_codex_service(config, tmp_path / "config.toml", provider)

    response = service.update_llm_model_only({
        "model": "openai-codex/gpt-5.6-luna", "reasoning_effort": "high",
    })
    assert response["ok"] is True
    assert response["llm"]["reasoning_effort"] == "high"
    reloaded = GDLAgentConfig.load(str(tmp_path / "config.toml"))
    assert reloaded.llm.model == "openai-codex/gpt-5.6-luna"
    assert reloaded.llm.reasoning_effort == "high"


def test_update_llm_model_only_codex_rejects_unsupported_effort(tmp_path):
    """effort 注入对抗：model/list 不支持的 effort 保存请求必须拒绝，零落盘。"""
    config = GDLAgentConfig()
    config.llm.model = "glm-4-flash"
    provider = _FakeCodexProvider(status=_signed_in_status(), models=_codex_models_payload())
    service = _make_codex_service(config, tmp_path / "config.toml", provider)

    response = service.update_llm_model_only({
        "model": "openai-codex/gpt-5.6-terra", "reasoning_effort": "low",
    })
    assert response["ok"] is False
    assert response["code"] == "effort_not_supported"
    # 拒绝后 config 未保存任何新值（model 与 effort 都不动）
    reloaded = GDLAgentConfig.load(str(tmp_path / "config.toml"))
    assert reloaded.llm.model == "glm-4-flash"
    assert reloaded.llm.reasoning_effort == ""


def test_update_llm_model_only_codex_switch_rejects_stale_effort(tmp_path):
    """模型切换后旧 effort 残留对抗：luna 的 low 不被 terra 支持——
    切换保存必须显式报错，绝不静默清空/替换 effort。"""
    config = GDLAgentConfig()
    config.llm.model = "openai-codex/gpt-5.6-luna"
    config.llm.reasoning_effort = "low"
    provider = _FakeCodexProvider(status=_signed_in_status(), models=_codex_models_payload())
    service = _make_codex_service(config, tmp_path / "config.toml", provider)

    # 不带 effort 切到 terra：旧 effort low 残留 → 拒绝
    response = service.update_llm_model_only({"model": "openai-codex/gpt-5.6-terra"})
    assert response["ok"] is False
    assert response["code"] == "effort_not_supported"
    assert "low" in response["error"]
    # 原配置未被动过（内存未提交）
    assert config.llm.model == "openai-codex/gpt-5.6-luna"
    assert config.llm.reasoning_effort == "low"

    # 显式给出 terra 支持的 effort → 保存成功
    response2 = service.update_llm_model_only({
        "model": "openai-codex/gpt-5.6-terra", "reasoning_effort": "medium",
    })
    assert response2["ok"] is True
    reloaded2 = GDLAgentConfig.load(str(tmp_path / "config.toml"))
    assert reloaded2.llm.model == "openai-codex/gpt-5.6-terra"
    assert reloaded2.llm.reasoning_effort == "medium"


def test_update_llm_model_only_rejects_effort_for_non_codex_model(tmp_path):
    """非 codex 模型不接受 effort：显式报错，不静默忽略。"""
    config = GDLAgentConfig()
    config.llm.model = "glm-4-flash"
    provider = _FakeCodexProvider(status=_signed_in_status(), models=_codex_models_payload())
    service = _make_codex_service(config, tmp_path / "config.toml", provider)

    response = service.update_llm_model_only({"model": "glm-4-flash", "reasoning_effort": "high"})
    assert response["ok"] is False
    assert response["code"] == "effort_not_for_codex"
    reloaded = GDLAgentConfig.load(str(tmp_path / "config.toml"))
    assert reloaded.llm.reasoning_effort == ""


def test_update_llm_settings_codex_validates_effort(tmp_path):
    """update_llm_settings 同样校验 effort（全量表单保存路径）。"""
    config = GDLAgentConfig()
    config.llm.model = "glm-4-flash"
    provider = _FakeCodexProvider(status=_signed_in_status(), models=_codex_models_payload())
    session = SimpleNamespace(
        llm_model="openai-codex/gpt-5.6-luna",
        llm_api_key="",
        llm_api_base="",
        assistant_settings="",
        max_retries=5,
        config=config,
        config_path=tmp_path / "config.toml",
    )
    service = WorkbenchSettingsService(
        session, llm_adapter_factory=lambda _c: None, codex_provider=provider
    )

    # 不支持 → 拒绝
    response = service.update_llm_settings({
        "model": "openai-codex/gpt-5.6-terra", "reasoning_effort": "low",
    })
    assert response["ok"] is False
    assert response["code"] == "effort_not_supported"

    # 支持 → 保存
    response2 = service.update_llm_settings({
        "model": "openai-codex/gpt-5.6-luna", "reasoning_effort": "high",
    })
    assert response2["ok"] is True
    assert config.llm.reasoning_effort == "high"


def test_llm_settings_exposes_saved_reasoning_effort(tmp_path, monkeypatch):
    """llm_settings 暴露当前已保存 effort（draft 事实源）。"""
    _clear_codex_test_env(monkeypatch)
    config = GDLAgentConfig()
    config.llm.model = "openai-codex/gpt-5.6-luna"
    config.llm.reasoning_effort = "high"
    provider = _FakeCodexProvider(status=_signed_in_status(), models=_codex_models_payload())
    service = _make_codex_service(config, tmp_path / "config.toml", provider)

    llm = service.llm_settings()
    assert llm["reasoning_effort"] == "high"


def test_codex_routing_mode_explicit_save_and_invalid_request_is_atomic(tmp_path):
    config = GDLAgentConfig()
    config.llm.model = "openai-codex/gpt-5.6-luna"
    config.llm.reasoning_effort = "high"
    provider = _FakeCodexProvider(status=_signed_in_status(), models=_codex_models_payload())
    service = _make_codex_service(config, tmp_path / "config.toml", provider)

    assert service.llm_settings()["codex_routing_mode"] == "fixed"
    saved = service.update_llm_model_only({
        "model": config.llm.model,
        "reasoning_effort": "high",
        "codex_routing_mode": "auto",
    })
    assert saved["ok"] is True
    assert saved["llm"]["codex_routing_mode"] == "auto"
    before = (tmp_path / "config.toml").read_bytes()

    rejected = service.update_llm_model_only({
        "model": config.llm.model,
        "reasoning_effort": "high",
        "codex_routing_mode": "AUTO-DEV-SECRET",
    })
    assert rejected["ok"] is False
    assert rejected["code"] == "invalid_codex_routing_mode"
    assert config.llm.effective_codex_routing_mode() == "auto"
    assert (tmp_path / "config.toml").read_bytes() == before


def test_codex_auto_mode_rejected_for_non_codex_model(tmp_path):
    config = GDLAgentConfig()
    provider = _FakeCodexProvider(status=_signed_in_status(), models=_codex_models_payload())
    service = _make_codex_service(config, tmp_path / "config.toml", provider)
    response = service.update_llm_model_only({
        "model": "glm-4-flash",
        "codex_routing_mode": "auto",
    })
    assert response == {
        "ok": False,
        "code": "auto_routing_requires_codex",
        "error": "Auto 路由仅适用于 ChatGPT Codex（openai-codex）订阅模型。",
    }
    assert config.llm.effective_codex_routing_mode() == "fixed"


def test_codex_models_include_supported_efforts(tmp_path):
    """codex_models 路由把 model/list 的 effort 目录透出（供 UI 选择）。"""
    config = GDLAgentConfig()
    provider = _FakeCodexProvider(models=_codex_models_payload())
    service = _make_codex_service(config, tmp_path / "config.toml", provider)

    response = service.codex_models()
    assert response["ok"] is True
    luna = next(m for m in response["models"] if m["id"].endswith("gpt-5.6-luna"))
    assert [e["effort"] for e in luna["supported_reasoning_efforts"]] == ["low", "medium", "high"]
    assert luna["default_reasoning_effort"] == "medium"


def test_config_direct_edit_unsupported_effort_fails_at_save_and_runtime(tmp_path):
    """直改配置文件绕过 UI：已保存的不支持组合在保存/运行时都 fail closed。

    - 保存侧：目录漂移后（terra 不再支持 high）保存 → effort_not_supported；
    - 运行时侧：provider.validate_reasoning_effort 拒绝（见 test_codex_provider）。
    """
    config = GDLAgentConfig()
    config.llm.model = "openai-codex/gpt-5.6-terra"
    config.llm.reasoning_effort = "high"
    provider = _FakeCodexProvider(status=_signed_in_status(), models=_codex_models_payload())
    service = _make_codex_service(config, tmp_path / "config.toml", provider)

    # 目录漂移：terra 现在只支持 medium（high 被下架）→ 保存拒绝，不静默替换
    drifted = [m for m in _codex_models_payload() if m["id"].endswith("gpt-5.6-luna")]
    provider.models_result = drifted + [
        {
            "id": "openai-codex/gpt-5.6-terra",
            "label": "GPT-5.6 Terra",
            "model": "gpt-5.6-terra",
            "display_name": "GPT-5.6 Terra",
            "hidden": False,
            "specialty": "balanced",
            "supported_reasoning_efforts": [{"effort": "medium", "description": "Balanced"}],
            "default_reasoning_effort": "medium",
        }
    ]
    response = service.update_llm_settings({"model": "openai-codex/gpt-5.6-terra"})
    assert response["ok"] is False
    assert response["code"] == "effort_not_supported"
    # 配置未被改动（无静默替换）
    assert config.llm.reasoning_effort == "high"


def test_codex_login_failure_response_redacts_secrets(tmp_path):
    """P0-2：失败路径（异常原文夹带 authUrl/loginId）也必须脱敏。"""
    config = GDLAgentConfig()

    class _LeakyProvider(_FakeCodexProvider):
        def login_start(self):
            raise RuntimeError(
                'login failed: {"type":"unexpected",'
                '"authUrl":"https://auth.openai.com/oauth?state=SECRET",'
                '"loginId":"a0327bbe-a894-4455-9e96-8c6d19ed2a53"}'
            )

    service = _make_codex_service(config, tmp_path / "config.toml", _LeakyProvider())
    response = service.codex_login_start()
    assert response["ok"] is False
    text = str(response["error"])
    assert "auth.openai.com" not in text
    assert "a0327bbe-a894-4455-9e96-8c6d19ed2a53" not in text
    assert "SECRET" not in text
    assert "authUrl" not in text
    assert "loginId" not in text


def test_codex_status_error_response_redacts_secrets(tmp_path):
    """P0-2：status 异常路径同样脱敏。"""
    config = GDLAgentConfig()

    class _LeakyProvider(_FakeCodexProvider):
        def status(self, *, refresh=False):
            raise RuntimeError(
                "upstream error: https://auth.openai.com/oauth?state=SECRET "
                "loginId=a0327bbe-a894-4455-9e96-8c6d19ed2a53"
            )

    service = _make_codex_service(config, tmp_path / "config.toml", _LeakyProvider())
    response = service.codex_status()
    assert response["ok"] is True  # status 降级为 error 状态，不抛给调用方
    assert response["state"] == "error"
    assert "auth.openai.com" not in str(response.get("error"))
    assert "a0327bbe" not in str(response.get("error"))


def test_codex_service_responses_never_leak_secrets(tmp_path):
    config = GDLAgentConfig()
    provider = _FakeCodexProvider(status=_signed_in_status(), models=_codex_models_payload())
    service = _make_codex_service(config, tmp_path / "config.toml", provider)

    payloads = [
        service.codex_status(),
        service.codex_login_start(),
        service.codex_logout(),
        service.codex_models(),
        service.llm_settings(),
    ]
    forbidden_keys = {"token", "jwt", "authurl", "loginid", "auth_path", "codex_home", "access_token"}

    def walk(value):
        if isinstance(value, dict):
            for key, val in value.items():
                assert str(key).lower() not in forbidden_keys, f"泄露字段 {key}"
                walk(val)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for payload in payloads:
        walk(payload)


def _snapshot_llm_state(session, config_path):
    """P0-R2：session/config/磁盘三层的完整快照，用于失败前后逐字段比对。"""
    config = session.config
    file_bytes = config_path.read_bytes() if config_path.exists() else None
    return {
        "session": {
            "llm_model": session.llm_model,
            "llm_api_key": session.llm_api_key,
            "llm_api_base": session.llm_api_base,
            "assistant_settings": session.assistant_settings,
            "max_retries": session.max_retries,
        },
        "config": {
            "model": config.llm.model,
            "assistant_settings": config.llm.assistant_settings,
            "max_iterations": config.agent.max_iterations,
            "providers": list(config.llm.providers),
            "api_key": config.llm.api_key,
        },
        "file": file_bytes,
    }


def test_update_llm_settings_rejection_leaves_state_untouched(tmp_path):
    """P0-R2：codex 拒绝（API-key / 目录外模型）前后 session/config/磁盘逐字段不变。"""
    config = GDLAgentConfig()
    config.llm.model = "glm-4-flash"
    config.llm.api_key = ""
    provider = _FakeCodexProvider(status=_signed_in_status(), models=_codex_models_payload())
    session = SimpleNamespace(
        llm_model="glm-4-flash",
        llm_api_key="",
        llm_api_base="",
        assistant_settings="OLD",
        max_retries=5,
        config=config,
        config_path=tmp_path / "config.toml",
    )
    service = WorkbenchSettingsService(session, llm_adapter_factory=lambda _c: None, codex_provider=provider)

    # 1) API-key 拒绝
    before = _snapshot_llm_state(session, tmp_path / "config.toml")
    response = service.update_llm_settings({
        "model": "openai-codex/gpt-5.6-luna",
        "api_key": "DEV-SECRET",
        "assistant_settings": "NEW",
        "max_retries": 9,
    })
    assert response["ok"] is False
    assert response["code"] == "codex_no_api_key"
    assert _snapshot_llm_state(session, tmp_path / "config.toml") == before

    # 2) 目录外模型拒绝
    before = _snapshot_llm_state(session, tmp_path / "config.toml")
    response = service.update_llm_settings({
        "model": "openai-codex/not-a-real-account-model",
        "api_key": "",
        "assistant_settings": "NEW",
        "max_retries": 9,
    })
    assert response["ok"] is False
    assert response["code"] == "model_not_in_catalog"
    assert _snapshot_llm_state(session, tmp_path / "config.toml") == before


def test_codex_generic_exception_uses_stable_code_and_message(tmp_path):
    """P0-R1：未知异常 → 稳定非空错误码 + 稳定产品文案（不传上游原文）。"""
    config = GDLAgentConfig()

    class _GenericBoomProvider(_FakeCodexProvider):
        def login_start(self):
            raise RuntimeError("some internal detail that should not reach the API")

    service = _make_codex_service(config, tmp_path / "config.toml", _GenericBoomProvider())
    response = service.codex_login_start()
    assert response["ok"] is False
    assert response["code"] == "codex_error"
    assert response["error"] == "Codex 操作失败，请稍后重试。"
    assert "internal detail" not in response["error"]


def test_codex_app_server_error_maps_to_stable_message(tmp_path):
    """P0-R1：app-server 超时等异常 → 按 category 映射稳定文案。"""
    from openbrep.codex.app_server import CodexAppServerError

    config = GDLAgentConfig()

    class _TimeoutProvider(_FakeCodexProvider):
        def login_start(self):
            raise CodexAppServerError("codex app-server 请求超时（>10s）：model/list", category="timeout")

    service = _make_codex_service(config, tmp_path / "config.toml", _TimeoutProvider())
    response = service.codex_login_start()
    assert response["ok"] is False
    assert response["code"] == "codex_app_server"
    assert response["error"] == "Codex app-server 响应超时，请稍后重试。"


# ── D2：device-code、取消、额度、重启、登录失败/过期不清空其他 provider ──────


def test_codex_login_device_code_route_returns_verification_info(tmp_path):
    config = GDLAgentConfig()
    provider = _FakeCodexProvider()
    service = _make_codex_service(config, tmp_path / "config.toml", provider)

    response = service.codex_login_device_code()
    assert response == {
        "ok": True,
        "state": "login_started",
        "method": "chatgptDeviceCode",
        "verification_url": "https://example.test/device",
        "user_code": "ABCD-EFGH",
    }
    assert provider.device_code_calls == 1
    # loginId 绝不返回
    assert "loginId" not in response


def test_codex_login_cancel_route(tmp_path):
    config = GDLAgentConfig()
    provider = _FakeCodexProvider()
    service = _make_codex_service(config, tmp_path / "config.toml", provider)

    response = service.codex_login_cancel()
    assert response == {"ok": True, "state": "signed_out"}
    assert provider.cancel_calls == 1


def test_codex_rate_limits_route_masked(tmp_path):
    config = GDLAgentConfig()
    provider = _FakeCodexProvider(
        rate_limits={
            "reached": False,
            "reached_type": None,
            "plan_type": "pro",
            "used_percent": 12,
            "credits": {"has_credits": True, "unlimited": False},
        }
    )
    service = _make_codex_service(config, tmp_path / "config.toml", provider)

    response = service.codex_rate_limits()
    assert response["ok"] is True
    assert response["rate_limits"]["used_percent"] == 12
    assert response["rate_limits"]["plan_type"] == "pro"
    # 脱敏：无余额/limit/used 字符串
    text = str(response)
    for bad in ("balance", "123.45", "limitName", "limitId", "grantedAt"):
        assert bad not in text, f"额度泄漏 {bad}"


def test_codex_rate_limits_fail_closed_signed_out(tmp_path):
    config = GDLAgentConfig()
    provider = _FakeCodexProvider()  # rate_limits=None → 抛 not_signed_in
    service = _make_codex_service(config, tmp_path / "config.toml", provider)

    response = service.codex_rate_limits()
    assert response["ok"] is False
    assert response["code"] == "not_signed_in"


def test_codex_restart_route(tmp_path):
    config = GDLAgentConfig()
    provider = _FakeCodexProvider(status=_signed_in_status())
    service = _make_codex_service(config, tmp_path / "config.toml", provider)

    response = service.codex_restart()
    assert response["ok"] is True
    assert response["state"] == "signed_in"
    assert provider.restart_calls == 1


def test_codex_crashed_status_surfaces_restartable(tmp_path):
    config = GDLAgentConfig()
    provider = _FakeCodexProvider(status={
        "state": "crashed", "connected": False, "codex_available": True,
        "account": None, "restartable": True, "code": "codex_crashed",
        "error": "Codex app-server 进程异常退出。请点击「重启」恢复连接。",
    })
    service = _make_codex_service(config, tmp_path / "config.toml", provider)

    response = service.codex_status()
    assert response["ok"] is True
    assert response["state"] == "crashed"
    assert response["restartable"] is True
    # 错误信息可操作、不含内部细节
    assert "进程异常退出" in response["error"]
    for bad in ("token", "Bearer", "authUrl", "loginId"):
        assert bad not in str(response), f"泄漏 {bad}"


def test_codex_status_quota_exhausted_with_rate_limits(tmp_path):
    config = GDLAgentConfig()
    provider = _FakeCodexProvider(status={
        "state": "quota_exhausted", "connected": True, "codex_available": True,
        "account": {"email_masked": "jo***@example.com", "plan_type": "pro"},
        "rate_limits": {"reached": True, "reached_type": "rate_limit_reached", "used_percent": 100},
        "error": "ChatGPT 订阅额度已耗尽或已达到用量上限。",
    })
    service = _make_codex_service(config, tmp_path / "config.toml", provider)

    response = service.codex_status()
    assert response["state"] == "quota_exhausted"
    assert response["connected"] is True
    assert response["rate_limits"]["reached"] is True


def _config_with_other_providers(tmp_path):
    """带 deepseek key + custom provider 的配置，用于验证 codex 失败不清空。"""
    config = GDLAgentConfig()
    config.llm.model = "deepseek-chat"
    config.llm.provider_keys["deepseek"] = "dk-12345"
    config.llm.custom_providers = [
        {
            "name": "myproxy",
            "api": "https://proxy.example/v1",
            "api_key": "proxy-key-999",
            "models": [{"alias": "my-model", "model": "gpt-5"}],
        }
    ]
    return config


def test_codex_login_failure_does_not_clear_other_providers(tmp_path, monkeypatch):
    """D2：登录失败/过期绝不会清空其他 provider 配置（config 文件与内存都不动）。"""
    _clear_codex_test_env(monkeypatch)
    config = _config_with_other_providers(tmp_path)
    config_path = tmp_path / "config.toml"
    config.save(str(config_path))
    before = config_path.read_bytes()

    class _LeakyProvider(_FakeCodexProvider):
        def login_start(self):
            raise RuntimeError("upstream login failed: access_token=SUPERSECRET")

    service = _make_codex_service(config, config_path, _LeakyProvider())
    response = service.codex_login_start()
    assert response["ok"] is False
    assert "SUPERSECRET" not in str(response)
    # config 文件字节不变
    assert config_path.read_bytes() == before
    # 内存中其他 provider 配置保留
    assert config.llm.provider_keys.get("deepseek") == "dk-12345"
    assert config.llm.custom_providers[0]["api_key"] == "proxy-key-999"
    assert config.llm.model == "deepseek-chat"


def test_codex_login_expiry_does_not_clear_other_providers(tmp_path, monkeypatch):
    """D2：登录过期（status 变 signed_out/error）不影响其他 provider 的保存与配置。"""
    _clear_codex_test_env(monkeypatch)
    config = _config_with_other_providers(tmp_path)
    config_path = tmp_path / "config.toml"
    config.save(str(config_path))

    # 登录过期：codex status 变成 signed_out（过期 → 需重新登录）
    provider = _FakeCodexProvider(status={
        "state": "signed_out", "connected": False, "codex_available": True, "account": None,
    })
    session = SimpleNamespace(
        llm_model="deepseek-chat",
        llm_api_key=config.llm.resolve_api_key() or "",
        llm_api_base=config.llm.resolve_api_base() or "",
        assistant_settings="",
        max_retries=5,
        config=config,
        config_path=config_path,
    )
    service = WorkbenchSettingsService(
        session, llm_adapter_factory=lambda _c: None, codex_provider=provider,
    )
    # 过期后保存其他 provider 的 key 依然成功，且 codex 配置不被触碰
    response = service.update_llm_api_key({"model": "deepseek-chat", "api_key": "dk-NEW"})
    assert response["ok"] is True
    reloaded = GDLAgentConfig.load(str(config_path))
    assert reloaded.llm.provider_keys.get("deepseek") == "dk-NEW"
    assert reloaded.llm.model == "deepseek-chat"
    # codex 登录态失败不影响其他 provider 的可用性判断
    llm = service.llm_settings()
    assert llm["model"] == "deepseek-chat"


def test_codex_login_expiry_does_not_clear_custom_providers(tmp_path, monkeypatch):
    """D2：codex 过期状态保存其他自定义 provider 的 key 时 custom 条目保留。"""
    _clear_codex_test_env(monkeypatch)
    config = _config_with_other_providers(tmp_path)
    config_path = tmp_path / "config.toml"
    config.save(str(config_path))
    provider = _FakeCodexProvider(status={
        "state": "signed_out", "connected": False, "codex_available": True, "account": None,
    })
    session = SimpleNamespace(
        llm_model="my-model",
        llm_api_key="proxy-key-999",
        llm_api_base="https://proxy.example/v1",
        assistant_settings="",
        max_retries=5,
        config=config,
        config_path=config_path,
    )
    service = WorkbenchSettingsService(
        session, llm_adapter_factory=lambda _c: None, codex_provider=provider,
    )
    response = service.update_llm_api_key({"model": "my-model", "api_key": "proxy-key-NEW"})
    assert response["ok"] is True
    reloaded = GDLAgentConfig.load(str(config_path))
    entry = reloaded.llm.custom_providers[0]
    assert entry["api_key"] == "proxy-key-NEW"
    assert entry["api"] == "https://proxy.example/v1"


def test_codex_switch_account_requires_explicit_logout(tmp_path, monkeypatch):
    """D2：切换账号必须显式退出/确认——退出后 fail closed，绝不复用未知凭据。"""
    _clear_codex_test_env(monkeypatch)
    config = GDLAgentConfig()
    config.llm.model = "openai-codex/gpt-5.6-luna"
    provider = _FakeCodexProvider(status=_signed_in_status())
    service = _make_codex_service(config, tmp_path / "config.toml", provider)

    # 账号切换语义：先显式 logout → 模型 fail closed → 再重新 login
    service.codex_logout()
    assert provider.logout_calls == 1
    # 退出后 codex 模型立即不可用（不复用未知/旧凭据，不 fallback 到任何 key）
    provider.status_result = {
        "state": "signed_out", "connected": False, "codex_available": True, "account": None,
    }
    llm = service.llm_settings()
    assert llm["model_available"] is False
    # 重新登录：打开全新的浏览器 flow（新账号），绝不复用旧登录态
    service.codex_login_start()
    assert provider.login_calls == 1


# ── P0-1 三层绕过：service / route 层同样拒绝「已登录再登录 / pending 再登录」──


class _RealProviderFakeClient:
    """真实 CodexProvider + fake client：验证 service/route 层门禁不依赖 UI。"""

    def __init__(self, account=None):
        self.started = False
        self.account = account
        self.login_calls = 0
        self.logout_calls = 0
        self.login_cancel_calls = 0
        self.model_list_calls = 0
        self.rate_limits_calls = 0
        self.closed = False
        self.server_version = (0, 147, 0)

    @property
    def transport(self):
        return None

    def start(self):
        self.started = True

    def initialize(self):
        return {"userAgent": "openbrep/0.147.0 (Mac OS)"}

    def account_read(self):
        return {"account": self.account, "requiresOpenaiAuth": self.account is None}

    def account_login_start(self, login_type):
        self.login_calls += 1
        if login_type == "chatgptDeviceCode":
            return {
                "type": "chatgptDeviceCode",
                "loginId": "L1",
                "verificationUrl": "https://example.test/device",
                "userCode": "ABCD-EFGH",
            }
        return {"type": "chatgpt", "loginId": "L1", "authUrl": "https://auth.example.test/oauth"}

    def account_login_cancel(self, login_id):
        self.login_cancel_calls += 1
        return {"status": "canceled"}

    def account_logout(self):
        self.logout_calls += 1
        self.account = None
        return {}

    def account_rate_limits_read(self):
        self.rate_limits_calls += 1
        raise RuntimeError("not signed in")

    def model_list(self):
        self.model_list_calls += 1
        return {"data": [{"id": "gpt-5.6-luna", "model": "gpt-5.6-luna", "displayName": "GPT-5.6 Luna"}], "nextCursor": None}

    def close(self):
        self.closed = True


def _real_provider_service(tmp_path, client):
    from openbrep.codex.provider import CodexProvider

    config = GDLAgentConfig()
    provider = CodexProvider(
        codex_home=tmp_path / "codex-home",
        client_factory=lambda: client,
        cli_available=True,
        browser_opener=lambda url: None,
    )
    session = SimpleNamespace(
        llm_model=config.llm.model,
        llm_api_key="",
        llm_api_base="",
        assistant_settings="",
        max_retries=5,
        config=config,
        config_path=tmp_path / "config.toml",
    )
    service = WorkbenchSettingsService(
        session, llm_adapter_factory=lambda _c: None, codex_provider=provider,
    )
    return service, provider


def test_service_login_while_signed_in_rejected(tmp_path):
    """P0-1：service 层——已登录时 login/start 返回稳定 already_signed_in，不发 RPC。"""
    client = _RealProviderFakeClient(account={"type": "chatgpt", "email": "jo@example.com", "planType": "pro"})
    service, provider = _real_provider_service(tmp_path, client)
    response = service.codex_login_start()
    assert response["ok"] is False
    assert response["code"] == "codex_app_server"
    assert "已连接" in response["error"]
    assert "断开连接" in response["error"]
    assert client.login_calls == 0
    provider.close()


def test_service_double_start_rejected(tmp_path):
    """P0-1：service 层——pending 中再登录返回稳定 login_already_pending。"""
    client = _RealProviderFakeClient(account=None)
    service, provider = _real_provider_service(tmp_path, client)
    first = service.codex_login_start()
    assert first["ok"] is True
    second = service.codex_login_start()
    assert second["ok"] is False
    assert "正在进行" in second["error"]
    assert client.login_calls == 1
    service.codex_login_cancel()
    provider.close()


def test_route_login_while_signed_in_rejected(tmp_path):
    """P0-1：route 层——已登录时 POST /login/start 拒绝，绝不发 login RPC。"""
    from openbrep.codex.provider import CodexProvider

    client = _RealProviderFakeClient(account={"type": "chatgpt", "email": "jo@example.com", "planType": "pro"})
    provider = CodexProvider(
        codex_home=tmp_path / "codex-home",
        client_factory=lambda: client,
        cli_available=True,
        browser_opener=lambda url: None,
    )
    from openbrep.workbench_api import WorkbenchSession

    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    session.settings_service.codex_provider = provider
    response = session.route("POST", "/api/settings/llm/codex/login/start", {})
    assert response["ok"] is False
    assert "已连接" in response["error"]
    assert client.login_calls == 0
    provider.close()


def test_route_device_code_while_signed_in_rejected(tmp_path):
    """P0-1：route 层——已登录时 device-code 同样拒绝。"""
    from openbrep.codex.provider import CodexProvider

    client = _RealProviderFakeClient(account={"type": "chatgpt", "email": "jo@example.com", "planType": "pro"})
    provider = CodexProvider(
        codex_home=tmp_path / "codex-home",
        client_factory=lambda: client,
        cli_available=True,
        browser_opener=lambda url: None,
    )
    from openbrep.workbench_api import WorkbenchSession

    session = WorkbenchSession(config_path=tmp_path / "config.toml")
    session.settings_service.codex_provider = provider
    response = session.route("POST", "/api/settings/llm/codex/login/device-code", {})
    assert response["ok"] is False
    assert "已连接" in response["error"]
    assert client.login_calls == 0
    provider.close()
