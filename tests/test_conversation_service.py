from pathlib import Path
from unittest.mock import Mock

import pytest

from openbrep.config import GDLAgentConfig
from openbrep.hsf_project import HSFProject, ScriptType
from openbrep.workbench_api import WorkbenchSession


def session_at(tmp_path):
    config = tmp_path / 'test.toml'
    cfg = GDLAgentConfig()
    cfg.compiler.mode = 'mock'
    cfg.save(str(config))
    session = WorkbenchSession(config_path=config)
    project = HSFProject.create_new('Shelf', str(tmp_path))
    project.save_to_disk()
    session.project = project
    session.source_path = project.root
    session.assistant_service.generate_with_assistant = Mock(return_value={'ok': True, 'assistant': {'reply': 'done'}})
    return session


def prepare(session, **extra):
    return session.route('POST', '/api/assistant/turn', {'phase': 'prepare', 'client_turn_id': 'c1', 'message': '添加背板', 'project_epoch': session.project_epoch, **extra})


def test_two_phases_and_retry_cannot_replace_server_task(tmp_path):
    session = session_at(tmp_path)
    ready = prepare(session)
    assert ready['result_kind'] == 'ready_to_execute'
    assert prepare(session, message='删除参数')['turn_id'] == ready['turn_id']
    body = {'phase': 'execute', 'turn_id': ready['turn_id'], 'message': '恶意替换'}
    done = session.route('POST', '/api/assistant/turn', body)
    assert done['result_kind'] == 'execution'
    assert session.route('POST', '/api/assistant/turn', body) == done
    call = session.assistant_service.generate_with_assistant
    assert call.call_count == 1
    assert call.call_args.args[0]['message'] == '添加背板'


def test_drafts_only_flush_after_prepare_then_execute(tmp_path):
    session = session_at(tmp_path)
    old = session.project.get_script(ScriptType.SCRIPT_3D)
    ready = prepare(session, draft_scripts={'3d.gdl': 'BLOCK 1, 2, 3\n'})
    assert session.project.get_script(ScriptType.SCRIPT_3D) == old
    session.project.set_script(ScriptType.SCRIPT_3D, 'BLOCK 1, 2, 3\n')
    session.project.save_to_disk()
    result = session.route('POST', '/api/assistant/turn', {'phase': 'execute', 'turn_id': ready['turn_id']})
    assert result['ok']
    assert session.assistant_service.generate_with_assistant.call_count == 1


def test_source_change_and_project_epoch_reject_execution(tmp_path):
    session = session_at(tmp_path)
    ready = prepare(session)
    session.project.parameters[0].value = '2'
    session.project.save_to_disk()
    result = session.route('POST', '/api/assistant/turn', {'phase': 'execute', 'turn_id': ready['turn_id']})
    assert result['code'] == 'SOURCE_CHANGED'
    session.assistant_service.generate_with_assistant.assert_not_called()
    session.project = HSFProject.create_new('Other', str(tmp_path))
    assert prepare(session, client_turn_id='c2', project_epoch=1)['code'] == 'PROJECT_CHANGED'


def test_cancel_expire_and_restart_never_revive_tokens(tmp_path):
    session = session_at(tmp_path)
    ready = prepare(session)
    cancel = {'phase': 'execute', 'turn_id': ready['turn_id'], 'approve': False}
    result = session.route('POST', '/api/assistant/turn', cancel)
    assert result['cancelled']
    assert session.route('POST', '/api/assistant/turn', {**cancel, 'approve': True})['cancelled']
    ready = prepare(session, client_turn_id='c2')
    turn = session.conversation_service.turns[ready['turn_id']]
    turn.created_at -= 1900
    assert session.route('POST', '/api/assistant/turn', {'phase': 'execute', 'turn_id': ready['turn_id']})['code'] == 'TURN_EXPIRED'
    restarted = session_at(tmp_path)
    assert restarted.route('POST', '/api/assistant/turn', {'phase': 'execute', 'turn_id': ready['turn_id']})['code'] == 'TURN_NOT_FOUND'
    session.assistant_service.generate_with_assistant.assert_not_called()


def test_legacy_switch_is_explicit_and_non_default_only(tmp_path):
    session = session_at(tmp_path)
    assert 'conversation_entry' not in Path(session.config_path).read_text()
    session.config.llm.conversation_entry = 'legacy'
    session.config.save(str(session.config_path))
    assert GDLAgentConfig.load(str(session.config_path)).llm.effective_conversation_entry() == 'legacy'
    result = prepare(session)
    assert result['http_status'] == 501
    assert result['code'] == 'UNIFIED_ENTRY_DISABLED'


def test_stream_worker_protects_validation_and_execution_with_session_lock(tmp_path):
    import threading
    session = session_at(tmp_path)
    ready = prepare(session)
    entered, release, changed = threading.Event(), threading.Event(), threading.Event()
    def execute(_body):
        entered.set()
        assert release.wait(2)
        return {'ok': True}
    session.assistant_service.generate_with_assistant.side_effect = execute
    stream = session.route('POST', '/api/assistant/turn', {'phase': 'execute', 'turn_id': ready['turn_id'], 'stream': True})
    consume = threading.Thread(target=lambda: list(stream))
    consume.start()
    assert entered.wait(2)
    def mutate():
        with session._op_lock:
            changed.set()
    writer = threading.Thread(target=mutate)
    writer.start()
    assert not changed.wait(0.05)
    release.set()
    consume.join(2)
    writer.join(2)
    assert changed.is_set()
    assert not consume.is_alive()
