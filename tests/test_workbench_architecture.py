from pathlib import Path


def test_workbench_api_stays_below_service_extraction_threshold():
    api_path = Path("openbrep/workbench_api.py")
    project_service_path = Path("openbrep/workbench/project_service.py")

    line_count = len(api_path.read_text(encoding="utf-8").splitlines())
    project_service_line_count = len(project_service_path.read_text(encoding="utf-8").splitlines())

    assert line_count <= 700
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
