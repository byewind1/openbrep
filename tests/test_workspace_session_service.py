from openbrep.workbench.workspace_session_service import WorkbenchWorkspaceSessionService
from openbrep.workbench_api import WorkbenchSession
from openbrep.config import GDLAgentConfig


def test_workspace_session_contract_preserves_attachment_and_explicit_persistence(tmp_path):
    config = tmp_path / 'config.toml'
    GDLAgentConfig().save(str(config))
    session = WorkbenchSession(config_path=config)
    service = session.workspace_session_service
    assert isinstance(service, WorkbenchWorkspaceSessionService)
    root = tmp_path / 'workspace'
    assert service.workspace_init({'path': str(root)})['ok']
    assert service.workspace_open({'path': str(root)})['ok']
    assert service.workspace_snapshot()['path'] == str(root)
    assert service.workspace_scan()['workspace'] == str(root)
    assert GDLAgentConfig.load(str(config)).last_workspace == str(root)
    assert service.workspace_close() == {'ok': True, 'workspace': None}
    assert service.workspace_snapshot() is None
    assert GDLAgentConfig.load(str(config)).last_workspace == ''
