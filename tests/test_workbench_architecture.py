from pathlib import Path


def test_workbench_api_stays_below_service_extraction_threshold():
    api_path = Path("openbrep/workbench_api.py")
    project_service_path = Path("openbrep/workbench/project_service.py")

    line_count = len(api_path.read_text(encoding="utf-8").splitlines())
    project_service_line_count = len(project_service_path.read_text(encoding="utf-8").splitlines())

    # 阈值随设计上调：P3-d1 工作区附着 seam（5 条 route + 隐式/显式附着 + snapshot
    # workspace 块 + last_workspace 持久化）为 WorkbenchSession 增加约 135 行；
    # 纯逻辑已下沉 workspace_service（workspace_root_for_project / resolve_workspace）。
    # 若继续增长，下一步应把工作区会话路由抽成独立 service（不得再上调阈值）。
    assert line_count <= 900
    assert project_service_line_count <= 250


def test_workbench_services_are_explicit_modules():
    assert Path("openbrep/workbench/settings_service.py").exists()
    assert Path("openbrep/workbench/compiler_service.py").exists()
    assert Path("openbrep/workbench/project_service.py").exists()
    assert Path("openbrep/workbench/project_session_service.py").exists()
    assert Path("openbrep/workbench/project_script_service.py").exists()
    assert Path("openbrep/workbench/project_parameter_service.py").exists()
    assert Path("openbrep/workbench/preview_service.py").exists()
    assert Path("openbrep/workbench/revision_service.py").exists()
    assert Path("openbrep/workbench/assistant_service.py").exists()
    assert Path("openbrep/workbench/memory_service.py").exists()
    assert Path("openbrep/workbench/tapir_service.py").exists()
    assert Path("openbrep/workbench/git_service.py").exists()
